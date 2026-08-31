#!/usr/bin/env python3
"""Bridge: real sportsbook odds -> Vantage intel signals + Mycelium traces.

Two real providers, selected via ODDS_PROVIDER (default "theoddsapi"):

  theoddsapi (api.theoddsapi.com) -- the real, currently-configured
    provider (owner-supplied key, 2026-08-31). Real API facts, confirmed
    accurate by the owner from their own account:
      base: https://api.theoddsapi.com (no /v4/ prefix -- different from
        the legacy provider below, do not reuse that base path)
      auth: x-api-key HEADER (not a query param -- also different from
        the legacy provider)
      endpoint: GET /odds/?sport_key={sport}&markets={markets}&
        regions={regions}&bookmakers={books}&oddsFormat=american
        (bookmakers= is strongly recommended by the provider's own docs
        to keep the response small -- always sent, never omitted)
      response: {success, source, data: [{event_id, sport, league,
        home_team, away_team, start_time, books: [{book, market,
        updated_at, outcomes: [{name, price}]}]}]} -- note this is ONE
        call returning every requested market's data nested under each
        book (not one call per market), and outcomes[].name matches
        event.home_team/away_team literally for h2h, not a fixed
        "Home Team" placeholder string.
      real per-call cost / rate limits: NOT specified by the owner and
      not fabricated here -- INTERVAL below stays conservative (same 4h
      default as before) until real usage is observed, rather than
      inventing a credit-budget number the way the legacy provider's
      docstring could for the-odds-api.com's own published pricing.

  legacy (the-odds-api.com) -- the ORIGINAL provider this bridge targeted
    (v4 API, apiKey query param, ODDS_API_KEY). Never actually got a real
    key (env file shipped blank). Kept fully intact and selectable via
    ODDS_PROVIDER=legacy rather than deleted -- the owner asked not to
    silently break it if still wanted, and it's a real, working,
    independently-documented code path, just not the active one now that
    a real theoddsapi key exists.

Same downstream shape either way: posts real signals via
vantage_signals.post_signal() (system-tool auth, intel pool, never the
order-creating endpoint -- signal ingestion, not bet placement, matching
the owner's explicit "NOT bet placement" instruction), AND (2026-08-31)
emits a real Mycelium observation trace per line-movement signal, same
{agent, session, kind, action, target, outcome, payload} contract
backend/mycelium_bridge.py's emitters use -- this script can't import that
module directly (standalone /opt/ares deployment, no backend.* package on
its path, same constraint that broke the vantage_signals import until
2026-08-30's fix), so _mycelium_trace() below is a small self-contained
equivalent posting to the same real gateway endpoint. Without this, the
cross_domain_signal.py miner (2026-08-30) is structurally blind to
sports-betting line movement -- this closes that gap the moment real odds
data starts flowing.

The real signal (unchanged from the original design): a bare static odds
number is not much of a signal on its own -- MOVEMENT is. An implied-
probability shift between this poll and the last stored one for the same
(event, market, outcome) is a real, observable proxy for "sharp money"
moving the line. conviction scales with the SIZE of that real movement.
"""
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# vantage_signals.py lives in daemons/ alongside this file in the repo, but
# the deployed copy runs standalone from /opt/ares (WorkingDirectory in the
# systemd unit) as a bare top-level script with no daemons/ on sys.path --
# that unqualified import crashed on every start (ModuleNotFoundError, before
# ever reaching the ODDS_API_KEY fail-soft check below). Confirmed live: the
# service crash-looped every 30s (Restart=always) with zero real attempts to
# reach the odds API. Try both layouts so this works whether this file sits
# directly in /opt/ares (deployed) or in daemons/ (repo checkout).
_here = os.path.dirname(os.path.abspath(__file__))
for _candidate in (_here, os.path.join(_here, "daemons"), "/opt/ares/Vantage/daemons"):
    if os.path.exists(os.path.join(_candidate, "vantage_signals.py")):
        sys.path.insert(0, _candidate)
        break
from vantage_signals import post_signal

# ── Provider selection ──────────────────────────────────────────────────
ODDS_PROVIDER = os.environ.get("ODDS_PROVIDER", "theoddsapi").strip().lower()

# ── theoddsapi.com (real, active provider) ──────────────────────────────
THEODDSAPI_BASE = "https://api.theoddsapi.com"
THEODDSAPI_KEY = os.environ.get("THEODDSAPI_KEY", "")
# Scoped per the owner's instruction: h2h/spreads/totals on a couple of
# major sport_keys, bookmakers explicitly scoped to keep responses small
# (the API's own guidance, per the owner-supplied spec).
SPORTS = [s.strip() for s in os.environ.get(
    "ODDS_SPORTS", "basketball_nba,americanfootball_nfl,baseball_mlb"
).split(",") if s.strip()]
MARKETS = [m.strip() for m in os.environ.get("ODDS_MARKETS", "h2h,spreads,totals").split(",") if m.strip()]
REGION = os.environ.get("ODDS_REGION", "us")
BOOKMAKERS = os.environ.get("ODDS_BOOKMAKERS", "draftkings,fanduel")

# ── the-odds-api.com (legacy, kept intact but inactive by default) ──────
LEGACY_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
LEGACY_MARKET = os.environ.get("ODDS_MARKET", "h2h")

INTERVAL = int(os.environ.get("SPORTS_BRIDGE_INTERVAL", "14400"))  # 4h, conservative default -- see docstring
DB_PATH = os.environ.get("SPORTS_BRIDGE_DB", "/opt/ares/sportsbetting_bridge.db")
MYCELIUM_URL = os.environ.get("MYCELIUM_URL", "http://127.0.0.1:8811")

HEADERS = {"User-Agent": "curl/8.0"}

# Real movement thresholds -- an implied-probability shift below this is
# noise (rounding/quoting jitter between bookmakers, not a real signal);
# above MAX_MOVE the conviction is clamped to 1.0. 0.20 (20 points of
# implied probability) is a real, large line move for a moneyline market.
MIN_MEANINGFUL_MOVE = 0.01
MAX_MOVE = 0.20


def _db():
    conn = sqlite3.connect(DB_PATH)
    # Real migration for any pre-2026-08-31 deployed DB (old schema had no
    # `market` column, PRIMARY KEY was (event_id, outcome_name) only).
    # SQLite cannot ALTER a composite PRIMARY KEY in place -- confirmed
    # live: adding the `market` column via ALTER TABLE left the original
    # (event_id, outcome_name) unique constraint in force underneath, so
    # every INSERT ... ON CONFLICT(event_id, market, outcome_name) failed
    # with "ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE
    # constraint" -- real crash, not hypothetical. This table is a pure
    # snapshot cache (last-seen odds for movement comparison), so the safe
    # fix is dropping and recreating under the real schema rather than
    # trying to preserve rows under an in-place key migration -- the only
    # cost is one "first observation, no movement yet" cycle, not a
    # correctness issue.
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='odds_snapshots'"
    ).fetchone()
    if exists:
        # Real bug found live: checking for the `market` COLUMN's presence
        # (via schema text or PRAGMA table_info) isn't enough -- an earlier
        # deploy's failed ALTER TABLE already added the column without
        # touching the PRIMARY KEY, leaving a table that "has market" but
        # still enforces the old (event_id, outcome_name) constraint
        # underneath. Check the real PK columns instead.
        pk_cols = {row[1] for row in conn.execute("PRAGMA table_info(odds_snapshots)") if row[5] > 0}
        if pk_cols != {"event_id", "market", "outcome_name"}:
            conn.execute("DROP TABLE odds_snapshots")
            conn.commit()
    conn.execute("""CREATE TABLE IF NOT EXISTS odds_snapshots (
        event_id TEXT NOT NULL,
        market TEXT NOT NULL DEFAULT 'h2h',
        outcome_name TEXT NOT NULL,
        implied_prob REAL NOT NULL,
        seen_at TEXT NOT NULL,
        PRIMARY KEY (event_id, market, outcome_name)
    )""")
    conn.commit()
    return conn


def _mycelium_trace(action: str, target: str, payload: dict, outcome: str = "info") -> bool:
    """Real, fail-soft observation-trace POST to the Mycelium gateway --
    same {agent, session, kind, action, target, outcome, payload} contract
    backend/mycelium_bridge.py's post_observation() uses, reimplemented
    standalone here since this script has no backend.* package on its
    path (see module docstring). Never raises -- Mycelium being down must
    never break real signal posting to Vantage."""
    body = {
        "agent": "sportsbetting_bridge", "session": "sports-odds-cycle",
        "kind": "observation", "action": action, "target": target,
        "outcome": outcome, "payload": payload,
    }
    try:
        req = urllib.request.Request(
            f"{MYCELIUM_URL}/api/trace",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status in (200, 201)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _american_to_implied_prob(price) -> float:
    """Real American-odds -> implied probability conversion. Positive odds
    (underdog, e.g. +150) and negative odds (favorite, e.g. -200) use the
    two real, different formulas -- treating them the same would invert
    favorite/underdog probability."""
    p = float(price)
    if p > 0:
        return 100.0 / (p + 100.0)
    return -p / (-p + 100.0)


# ── theoddsapi.com client (real, active) ─────────────────────────────────

def fetch_odds_theoddsapi(sport_key: str):
    """Real GET against api.theoddsapi.com for one sport_key, all
    configured markets in a single call. Returns [] on any failure (no
    key, bad sport_key, network error, empty/unsuccessful upstream) --
    never raises."""
    if not THEODDSAPI_KEY:
        print("THEODDSAPI_KEY not set -- skipping", flush=True)
        return []
    url = (
        f"{THEODDSAPI_BASE}/odds/?sport_key={sport_key}"
        f"&markets={','.join(MARKETS)}&regions={REGION}"
        f"&bookmakers={BOOKMAKERS}&oddsFormat=american"
    )
    req = urllib.request.Request(url, headers={**HEADERS, "x-api-key": THEODDSAPI_KEY})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  theoddsapi error ({sport_key}): HTTP {e.code} {e.read().decode(errors='ignore')[:200]}", flush=True)
        return []
    except Exception as e:
        print(f"  theoddsapi error ({sport_key}): {e}", flush=True)
        return []
    if not isinstance(body, dict) or not body.get("success"):
        print(f"  theoddsapi ({sport_key}): unsuccessful response: {str(body)[:200]}", flush=True)
        return []
    data = body.get("data")
    return data if isinstance(data, list) else []


def _lines_by_market_theoddsapi(event: dict) -> dict:
    """Real per-market {market: {outcome_name: american_price}} for one
    theoddsapi.com event. Response nests markets under each book's own
    `books` entry (real shape: books=[{book, market, outcomes}]) rather
    than the legacy provider's bookmakers->markets nesting -- this takes
    the first book carrying each requested market, same "don't blur which
    real book is moving" reasoning as the legacy client's _best_line()."""
    out: dict = {}
    for book in event.get("books", []):
        market = book.get("market")
        if market not in MARKETS or market in out:
            continue
        outcomes = book.get("outcomes") or []
        out[market] = {o["name"]: o["price"] for o in outcomes if "name" in o and "price" in o}
    return out


def cycle_theoddsapi():
    conn = _db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    total_events = 0
    total_signals = 0
    total_traces = 0

    for sport_key in SPORTS:
        events = fetch_odds_theoddsapi(sport_key)
        total_events += len(events)
        for ev in events:
            event_id = ev.get("event_id")
            if not event_id:
                continue
            home, away = ev.get("home_team", "?"), ev.get("away_team", "?")
            league = ev.get("league") or ev.get("sport") or sport_key
            teams = f"{away} @ {home}"

            for market, line in _lines_by_market_theoddsapi(ev).items():
                for outcome_name, price in line.items():
                    try:
                        implied = _american_to_implied_prob(price)
                    except (TypeError, ValueError):
                        continue

                    prior = conn.execute(
                        "SELECT implied_prob FROM odds_snapshots WHERE event_id=? AND market=? AND outcome_name=?",
                        (event_id, market, outcome_name),
                    ).fetchone()
                    conn.execute(
                        """INSERT INTO odds_snapshots (event_id, market, outcome_name, implied_prob, seen_at)
                           VALUES (?,?,?,?,?)
                           ON CONFLICT(event_id, market, outcome_name) DO UPDATE SET
                             implied_prob=excluded.implied_prob, seen_at=excluded.seen_at""",
                        (event_id, market, outcome_name, implied, now),
                    )
                    conn.commit()

                    if prior is None:
                        continue  # first-ever observation -- no movement to report yet

                    move = implied - prior[0]
                    if abs(move) < MIN_MEANINGFUL_MOVE:
                        continue  # real, deliberate noise floor -- see module docstring

                    conviction = min(abs(move) / MAX_MOVE, 1.0)
                    direction = "shortening" if move > 0 else "drifting"

                    detail = (
                        f"{league} | {teams} | {market} {outcome_name} {direction} "
                        f"{prior[0]:.1%}->{implied:.1%} ({move:+.1%})"
                    )
                    post_signal(
                        outcome_name.upper()[:16], "sportsbetting",
                        type_="line_movement",
                        conviction=conviction,
                        direction="BULLISH" if move > 0 else "BEARISH",
                        detail=detail,
                    )
                    total_signals += 1

                    if _mycelium_trace(
                        "line_movement", event_id,
                        {
                            "sport": sport_key, "league": league, "market": market,
                            "home_team": home, "away_team": away,
                            "outcome": outcome_name, "prior_implied_prob": round(prior[0], 4),
                            "implied_prob": round(implied, 4), "move": round(move, 4),
                            "conviction": round(conviction, 4),
                        },
                        outcome="success",
                    ):
                        total_traces += 1

    conn.close()
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] Sports betting (theoddsapi): "
        f"{total_events} events checked, {total_signals} line-movement signals ingested, "
        f"{total_traces} mycelium traces emitted",
        flush=True,
    )


# ── the-odds-api.com client (legacy, inactive unless ODDS_PROVIDER=legacy) ──

def fetch_odds_legacy(sport: str):
    """Real GET against the-odds-api.com v4 for one sport's current
    moneyline odds. Untouched from the original implementation -- kept
    selectable rather than deleted (see module docstring)."""
    if not ODDS_API_KEY:
        print("ODDS_API_KEY not set -- skipping", flush=True)
        return []
    url = (
        f"{LEGACY_ODDS_API_BASE}/sports/{sport}/odds/"
        f"?apiKey={ODDS_API_KEY}&regions={REGION}&markets={LEGACY_MARKET}&oddsFormat=american"
    )
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  Odds API error ({sport}): HTTP {e.code} {e.read().decode(errors='ignore')[:200]}", flush=True)
        return []
    except Exception as e:
        print(f"  Odds API error ({sport}): {e}", flush=True)
        return []
    return data if isinstance(data, list) else []


def _best_line_legacy(event: dict, market: str):
    """Real best (first-bookmaker) outcome prices for one legacy-provider
    event, {outcome_name: american_price}. Unchanged from the original."""
    for bm in event.get("bookmakers", []):
        for mkt in bm.get("markets", []):
            if mkt.get("key") != market:
                continue
            return {o["name"]: o["price"] for o in mkt.get("outcomes", []) if "name" in o and "price" in o}
    return {}


def cycle_legacy():
    conn = _db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    total_events = 0
    total_signals = 0
    total_traces = 0

    for sport in SPORTS:
        events = fetch_odds_legacy(sport)
        total_events += len(events)
        for ev in events:
            event_id = ev.get("id")
            teams = f"{ev.get('away_team', '?')} @ {ev.get('home_team', '?')}"
            if not event_id:
                continue
            line = _best_line_legacy(ev, LEGACY_MARKET)
            for outcome_name, price in line.items():
                try:
                    implied = _american_to_implied_prob(price)
                except (TypeError, ValueError):
                    continue

                prior = conn.execute(
                    "SELECT implied_prob FROM odds_snapshots WHERE event_id=? AND market=? AND outcome_name=?",
                    (event_id, LEGACY_MARKET, outcome_name),
                ).fetchone()
                conn.execute(
                    """INSERT INTO odds_snapshots (event_id, market, outcome_name, implied_prob, seen_at)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(event_id, market, outcome_name) DO UPDATE SET
                         implied_prob=excluded.implied_prob, seen_at=excluded.seen_at""",
                    (event_id, LEGACY_MARKET, outcome_name, implied, now),
                )
                conn.commit()

                if prior is None:
                    continue

                move = implied - prior[0]
                if abs(move) < MIN_MEANINGFUL_MOVE:
                    continue

                conviction = min(abs(move) / MAX_MOVE, 1.0)
                direction = "shortening" if move > 0 else "drifting"
                detail = (
                    f"{sport} | {teams} | {outcome_name} {direction} "
                    f"{prior[0]:.1%}->{implied:.1%} ({move:+.1%})"
                )
                post_signal(
                    outcome_name.upper()[:16], "sportsbetting",
                    type_="line_movement",
                    conviction=conviction,
                    direction="BULLISH" if move > 0 else "BEARISH",
                    detail=detail,
                )
                total_signals += 1

                if _mycelium_trace(
                    "line_movement", event_id,
                    {
                        "sport": sport, "market": LEGACY_MARKET,
                        "outcome": outcome_name, "prior_implied_prob": round(prior[0], 4),
                        "implied_prob": round(implied, 4), "move": round(move, 4),
                        "conviction": round(conviction, 4),
                    },
                    outcome="success",
                ):
                    total_traces += 1

    conn.close()
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] Sports betting (legacy): "
        f"{total_events} events checked, {total_signals} line-movement signals ingested, "
        f"{total_traces} mycelium traces emitted",
        flush=True,
    )


def cycle():
    if ODDS_PROVIDER == "legacy":
        cycle_legacy()
    else:
        cycle_theoddsapi()


if __name__ == "__main__":
    print(f"Sports Betting Bridge ({INTERVAL}s cycle, provider={ODDS_PROVIDER}, sports={SPORTS})", flush=True)
    while True:
        try:
            cycle()
        except Exception as e:
            print(f"Error: {e}", flush=True)
        time.sleep(INTERVAL)
