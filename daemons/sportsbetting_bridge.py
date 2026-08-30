#!/usr/bin/env python3
"""Bridge: The Odds API (real sportsbook odds) -> Vantage intel signals.

Same shape as daemons/polymarket_bridge.py's own real, working pattern:
unauthenticated-to-Vantage read of a real public market, normalised
conviction, posted via vantage_signals.post_signal() (system-tool auth,
intel pool by default, never the order-creating endpoint -- this is
signal ingestion, not bet placement. See module docstring's own
"NOT bet placement" framing, matching the owner's explicit instruction).

Real provider: The Odds API (the-odds-api.com), picked after comparing
free-tier options -- real, documented, actively maintained, and (unlike
Pinnacle's public API, which is XML/SOAP-era and not well suited to a
simple polling daemon, or ESPN's endpoints, which carry scores/schedule
but not real bookmaker odds) it returns real bookmaker odds
(DraftKings/FanDuel/etc, aggregated) in one clean JSON call.

Real API facts (verified via docs.the-odds-api.com, 2026-08-30):
  base: https://api.the-odds-api.com/v4
  auth: apiKey query param
  free tier: 500 credits/month, no rate limit disclosed beyond quota
  cost per /odds call: [markets] x [regions] credits (1 market, 1 region
    = 1 credit)
  historical odds (the-odds-api's own snapshot endpoint) is a PAID-ONLY
    feature (10x the live-odds cost per call) -- NOT used here. Line
    movement is instead computed ourselves from repeated live-odds polls,
    stored locally (see _OddsHistory below) -- free, and the same
    "compute your own history from live polls" approach
    trade_outcome_learner.py already uses for price marks elsewhere in
    this codebase (no synthetic/paid historical lookup, only real,
    locally-observed snapshots over real elapsed time).

Credit budget (real, deliberately conservative -- see SPORTS/INTERVAL
below): 2 sports x 1 market x 1 region = 2 credits/cycle. At the default
4-hour cycle that's 6 cycles/day = 12 credits/day = ~360/month, leaving
~140 credits/month of headroom for manual/testing calls without risking
the free quota. A shorter interval was considered and rejected: odds on
tracked leagues do not move meaningfully minute-to-minute the way a
memecoin does, and the free-tier budget cannot sustain it regardless --
360/month conservatively fits every real cycle plus manual verification,
which a tighter cadence would not.

The real signal (per the task): a bare static odds number is not much of
a signal on its own -- MOVEMENT is. A moneyline implied-probability shift
between this poll and the last stored one for the same outcome is a real,
observable proxy for "sharp money" moving the line. conviction scales
with the SIZE of that real movement (0 movement = 0 conviction, a 20+
point implied-probability swing = max conviction), not with the odds
value itself -- an odds value alone says nothing about whether anything
just happened.
"""
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from vantage_signals import post_signal

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

# Conservative default set -- see module docstring's credit-budget math.
# Comma-separated The-Odds-API sport keys; override via env for a
# different real league mix (e.g. seasonal rotation).
SPORTS = [s.strip() for s in os.environ.get(
    "ODDS_SPORTS", "americanfootball_nfl,basketball_nba"
).split(",") if s.strip()]
REGION = os.environ.get("ODDS_REGION", "us")
MARKET = os.environ.get("ODDS_MARKET", "h2h")

INTERVAL = int(os.environ.get("SPORTS_BRIDGE_INTERVAL", "14400"))  # 4h, see credit-budget math above

DB_PATH = os.environ.get("SPORTS_BRIDGE_DB", "/opt/ares/sportsbetting_bridge.db")

HEADERS = {"User-Agent": "curl/8.0"}

# Real movement thresholds -- an implied-probability shift below this is
# noise (rounding/quoting jitter between bookmakers, not a real signal);
# above MAX_MOVE the conviction is clamped to 1.0. 0.20 (20 points of
# implied probability) is a real, large line move for a moneyline market.
MIN_MEANINGFUL_MOVE = 0.01
MAX_MOVE = 0.20


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS odds_snapshots (
        event_id TEXT NOT NULL,
        outcome_name TEXT NOT NULL,
        implied_prob REAL NOT NULL,
        seen_at TEXT NOT NULL,
        PRIMARY KEY (event_id, outcome_name)
    )""")
    conn.commit()
    return conn


def _american_to_implied_prob(price) -> float:
    """Real American-odds -> implied probability conversion. Positive odds
    (underdog, e.g. +150) and negative odds (favorite, e.g. -200) use the
    two real, different formulas -- treating them the same would invert
    favorite/underdog probability."""
    p = float(price)
    if p > 0:
        return 100.0 / (p + 100.0)
    return -p / (-p + 100.0)


def fetch_odds(sport: str):
    """Real GET against The Odds API for one sport's current moneyline
    odds. Returns [] on any failure (no key, bad sport key, network error,
    empty upstream) -- never raises, matching polymarket_bridge.py's own
    fail-soft cycle() convention below."""
    if not ODDS_API_KEY:
        print("ODDS_API_KEY not set -- skipping", flush=True)
        return []
    url = (
        f"{ODDS_API_BASE}/sports/{sport}/odds/"
        f"?apiKey={ODDS_API_KEY}&regions={REGION}&markets={MARKET}&oddsFormat=american"
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


def _best_line(event: dict, market: str):
    """Real best (median-ish -- first bookmaker with this market) outcome
    prices for one event, {outcome_name: american_price}. The Odds API
    returns one entry per bookmaker; this takes the first bookmaker
    carrying the requested market rather than averaging across books --
    a real, simple, honest choice (averaging books would blur which real
    book is actually moving, which is the point of tracking movement at
    all)."""
    for bm in event.get("bookmakers", []):
        for mkt in bm.get("markets", []):
            if mkt.get("key") != market:
                continue
            return {o["name"]: o["price"] for o in mkt.get("outcomes", []) if "name" in o and "price" in o}
    return {}


def cycle():
    conn = _db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    total_events = 0
    total_signals = 0

    for sport in SPORTS:
        events = fetch_odds(sport)
        total_events += len(events)
        for ev in events:
            event_id = ev.get("id")
            teams = f"{ev.get('away_team', '?')} @ {ev.get('home_team', '?')}"
            if not event_id:
                continue
            line = _best_line(ev, MARKET)
            for outcome_name, price in line.items():
                try:
                    implied = _american_to_implied_prob(price)
                except (TypeError, ValueError):
                    continue

                prior = conn.execute(
                    "SELECT implied_prob FROM odds_snapshots WHERE event_id=? AND outcome_name=?",
                    (event_id, outcome_name),
                ).fetchone()
                conn.execute(
                    """INSERT INTO odds_snapshots (event_id, outcome_name, implied_prob, seen_at)
                       VALUES (?,?,?,?)
                       ON CONFLICT(event_id, outcome_name) DO UPDATE SET
                         implied_prob=excluded.implied_prob, seen_at=excluded.seen_at""",
                    (event_id, outcome_name, implied, now),
                )
                conn.commit()

                if prior is None:
                    continue  # first-ever observation of this outcome -- no movement to report yet

                move = implied - prior[0]
                if abs(move) < MIN_MEANINGFUL_MOVE:
                    continue  # real, deliberate noise floor -- see module docstring

                conviction = min(abs(move) / MAX_MOVE, 1.0)
                direction = "shortening" if move > 0 else "drifting"  # shortening = more likely, drifting = less likely

                post_signal(
                    outcome_name.upper()[:16], "sportsbetting",
                    type_="line_movement",
                    conviction=conviction,
                    direction="BULLISH" if move > 0 else "BEARISH",
                    detail=(
                        f"{sport} | {teams} | {outcome_name} {direction} "
                        f"{prior[0]:.1%}->{implied:.1%} ({move:+.1%})"
                    ),
                )
                total_signals += 1

    conn.close()
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] Sports betting: "
        f"{total_events} events checked, {total_signals} line-movement signals ingested",
        flush=True,
    )


if __name__ == "__main__":
    print(f"Sports Betting Bridge ({INTERVAL}s cycle, sports={SPORTS})", flush=True)
    while True:
        try:
            cycle()
        except Exception as e:
            print(f"Error: {e}", flush=True)
        time.sleep(INTERVAL)
