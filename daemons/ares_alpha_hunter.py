#!/opt/ares/venv/bin/python3
"""ares_alpha_hunter — ARES-ALPHA hunting loop, coded into Vantage.

Continuously:
  Phase 1 (15 min): refresh wallet universe from GMGN smartmoney/KOL feeds,
                     score + tier every wallet via portfolio stats.
  Phase 2 (30 min): back-track today's biggest 24h runners, pull top holders,
                     discover new wallets to add to the universe.
  Phase 3 (2 min):  live hunt — smartmoney trade feed + 1m trending + new
                     trenches, cross-referenced for cluster detection
                     (3+ S/A-tier wallets entering the same token in a
                     rolling window). On cluster: security-check the token,
                     score conviction, log it, and post to Vantage intel
                     signals so the rest of the pipeline (feed, watchlist,
                     conviction-weighted trading_agents debate) picks it up.
  Phase 4 (24h):    re-score the whole wallet universe, promote/demote tiers.

Does NOT place orders. This is detection/scoring only — execution stays
gated behind /api/trading/orders and human/agent confirmation, same as
every other signal source in this pipeline.

Auth:
  - GMGN_API_KEY: set via `gmgn-cli config --apply <key>` (one-time, VPS-wide)
  - VANTAGE_TOOL_INTEL_KEY: system-tool key for POST /api/intel/signals/ingest
  - ~/.vantage_key: agent key for POST/GET /api/intel/watchlist
"""
import json
import os
import sqlite3
import sys as _vshim_sys
_vshim_sys.path.insert(0, "/opt/ares")
import vantage_db_shim as _vshim
import subprocess
import sys
import time
import traceback
import urllib.request
import urllib.error
from pathlib import Path

DB = "/opt/ares/Vantage/data/vantage.db"
LOG_DIR = Path("/opt/ares/ares_logs")
RAW_SAMPLE_LOG = LOG_DIR / "alpha_hunter_raw_samples.jsonl"
GMGN = "gmgn-cli"
CHAIN = "sol"

VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://localhost:8001")
TOOL_INTEL_KEY = os.environ.get("VANTAGE_TOOL_INTEL_KEY", "")
try:
    VANTAGE_AGENT_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()
except FileNotFoundError:
    VANTAGE_AGENT_KEY = ""

PHASE1_INTERVAL = 15 * 60     # wallet universe refresh
PHASE2_INTERVAL = 30 * 60     # back-track winners
PHASE3_INTERVAL = 2 * 60      # live hunt / cluster detection
PHASE4_INTERVAL = 24 * 60 * 60  # full re-score

CLUSTER_WINDOW_SECONDS = 30 * 60   # rolling window for convergence detection
CLUSTER_MIN_WALLETS = 3            # min distinct S/A-tier wallets to fire
MIN_TRADES_FOR_TRUST = 20          # rule #3: sample-size floor
RUG_RATIO_MAX = 0.3

# ── infra: rate-limit-aware GMGN wrapper ─────────────────────────────────
_last_gmgn_call = 0.0
_gmgn_min_gap = 0.35          # ~3 req/s ceiling, well under the leaky bucket
_raw_samples_logged = {"smartmoney": 0, "kol": 0, "trending": 0, "trenches": 0,
                        "stats": 0, "security": 0, "signal": 0, "holders": 0}
_RAW_SAMPLE_CAP = 3


def _log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def _log_raw_sample(kind, payload):
    if _raw_samples_logged.get(kind, 0) >= _RAW_SAMPLE_CAP:
        return
    _raw_samples_logged[kind] = _raw_samples_logged.get(kind, 0) + 1
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(RAW_SAMPLE_LOG, "a") as f:
            f.write(json.dumps({"kind": kind, "ts": time.time(), "sample": payload})[:4000] + "\n")
    except Exception:
        pass


def run_gmgn(args, kind=""):
    """Run a gmgn-cli command with --raw, return parsed JSON or None.
    Rate-limit + auth-failure aware: never crash the daemon on a bad call."""
    global _last_gmgn_call
    gap = time.time() - _last_gmgn_call
    if gap < _gmgn_min_gap:
        time.sleep(_gmgn_min_gap - gap)
    _last_gmgn_call = time.time()

    try:
        proc = subprocess.run(
            [GMGN, *args, "--raw"],
            capture_output=True, text=True, timeout=25,
        )
    except subprocess.TimeoutExpired:
        _log(f"gmgn-cli TIMEOUT: {' '.join(args)}")
        return None
    except FileNotFoundError:
        _log("gmgn-cli binary not found — is it installed? (npm install -g gmgn-cli)")
        return None

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()

    if "GMGN_API_KEY is required" in err or "GMGN_API_KEY is required" in out:
        _log("GMGN_API_KEY not configured yet — run `gmgn-cli config` and apply the key. Skipping this cycle.")
        return None

    if proc.returncode != 0:
        # Rate-limit handling per skill docs: back off, don't hammer.
        if "429" in err or "RATE_LIMIT" in err:
            reset_at = None
            try:
                blob = json.loads(err[err.index("{"):]) if "{" in err else {}
                reset_at = blob.get("reset_at")
            except Exception:
                pass
            wait = max(5, min(300, (reset_at - time.time()) if reset_at else 30))
            _log(f"gmgn-cli rate-limited, backing off {wait:.0f}s")
            time.sleep(wait)
            return None
        _log(f"gmgn-cli error ({proc.returncode}) for {' '.join(args)}: {err[:300]}")
        return None

    if not out:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        _log(f"gmgn-cli non-JSON output for {' '.join(args)}: {out[:200]}")
        return None

    if kind:
        _log_raw_sample(kind, data)
    return data


# ── infra: Vantage HTTP helpers ──────────────────────────────────────────
def vantage_post(path, payload, headers):
    try:
        req = urllib.request.Request(
            f"{VANTAGE_URL}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        _log(f"POST {path} failed: HTTP {e.code} {e.read()[:200]}")
    except Exception as e:
        _log(f"POST {path} failed: {e}")
    return None


def post_intel_signal(symbol, sig_type, conviction, detail, mint=None):
    if not TOOL_INTEL_KEY:
        _log("VANTAGE_TOOL_INTEL_KEY not set — cannot post signal (detection-only mode)")
        return None
    payload = {
        "symbol": symbol, "source": "ares_alpha_hunter", "type": sig_type,
        "conviction": conviction, "detail": detail,
    }
    if mint:
        payload["mint"] = mint
    return vantage_post(
        "/api/intel/signals/ingest", payload,
        {"X-Vantage-Tool": "intel", "X-Vantage-Tool-Key": TOOL_INTEL_KEY},
    )


def add_to_watchlist(address, chain, label, notes=""):
    if not VANTAGE_AGENT_KEY:
        return None
    return vantage_post(
        "/api/intel/watchlist",
        {"chain": chain, "address": address, "label": label, "notes": notes},
        {"X-Agent-Key": VANTAGE_AGENT_KEY},
    )


# ── DB ─────────────────────────────────────────────────────────────────
def db_init():
    conn = _vshim.get_sync_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alpha_wallets (
                chain TEXT NOT NULL,
                address TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                win_rate REAL DEFAULT 0,
                pnl_7d REAL DEFAULT 0,
                pnl_30d REAL DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                avg_roi REAL DEFAULT 0,
                early_entry_rate REAL DEFAULT 0.5,
                consistency_score REAL DEFAULT 0.5,
                score REAL DEFAULT 0,
                tier TEXT DEFAULT 'C',
                style TEXT DEFAULT 'UNKNOWN',
                best_trade_token TEXT DEFAULT '',
                best_trade_roi REAL DEFAULT 0,
                source TEXT DEFAULT '',
                first_seen REAL DEFAULT (strftime('%s','now')),
                last_updated REAL DEFAULT (strftime('%s','now')),
                PRIMARY KEY (chain, address)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alpha_wallets_tier ON alpha_wallets(tier, score DESC)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alpha_clusters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain TEXT NOT NULL,
                token_address TEXT NOT NULL,
                token_symbol TEXT DEFAULT '',
                wallets_json TEXT DEFAULT '[]',
                wallet_count INTEGER DEFAULT 0,
                total_inflow_usd REAL DEFAULT 0,
                market_cap REAL DEFAULT 0,
                liquidity_usd REAL DEFAULT 0,
                rug_ratio REAL DEFAULT 0,
                conviction REAL DEFAULT 0,
                action TEXT DEFAULT 'WATCH',
                detected_at REAL DEFAULT (strftime('%s','now')),
                UNIQUE(chain, token_address, detected_at)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alpha_clusters_time ON alpha_clusters(detected_at DESC)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alpha_signals_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL DEFAULT (strftime('%s','now')),
                kind TEXT NOT NULL,
                token_or_wallet TEXT DEFAULT '',
                payload TEXT DEFAULT '{}'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alpha_signals_log_ts ON alpha_signals_log(ts DESC)")
        conn.commit()
    finally:
        conn.close()


def db_conn():
    conn = _vshim.get_sync_db()
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


def db_write_retry(fn, *args, retries=5, **kwargs):
    """Retry a write fn on transient 'database is locked' (WAL + ~40 other
    daemons share vantage.db) with jittered backoff instead of crashing the phase."""
    import random
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1) + random.random())
                continue
            raise


def log_event(kind, token_or_wallet, payload):
    def _write():
        conn = db_conn()
        try:
            conn.execute(
                "INSERT INTO alpha_signals_log (kind, token_or_wallet, payload) VALUES (?,?,?)",
                (kind, token_or_wallet, json.dumps(payload)[:4000]),
            )
            conn.commit()
        finally:
            conn.close()
    try:
        db_write_retry(_write)
    except Exception:
        pass


# ── scoring ────────────────────────────────────────────────────────────
def _num(d, *keys, default=0.0):
    for k in keys:
        if k in d and d[k] is not None:
            try:
                return float(d[k])
            except (TypeError, ValueError):
                pass
    return default


def _flatten_stats(d):
    """GMGN wallet_stats nests winrate/token_num/avg_holding_period under
    pnl_stat, and social/tag info under common — confirmed against a live
    --raw sample (RAW_SAMPLE_LOG) after the GMGN key went live. Flatten one
    level so _num() can find fields regardless of nesting."""
    if not isinstance(d, dict):
        return {}
    flat = dict(d)
    for nested_key in ("pnl_stat", "common"):
        nested = d.get(nested_key)
        if isinstance(nested, dict):
            for k, v in nested.items():
                flat.setdefault(k, v)
    return flat


def score_wallet(stats7, stats30):
    """stats7/stats30: raw GMGN wallet_stats dicts (7d/30d periods)."""
    stats7 = _flatten_stats(stats7)
    stats30 = _flatten_stats(stats30)

    winrate = _num(stats7, "winrate", "win_rate", default=0.0)
    if winrate > 1:  # some APIs return 0-100 instead of 0-1
        winrate /= 100.0
    trades = int(_num(stats7, "token_num", "total_trades", "trade_count", default=0))
    if trades == 0:
        trades = int(_num(stats7, "buy", default=0)) + int(_num(stats7, "sell", default=0))
    pnl7 = _num(stats7, "realized_profit_pnl", "pnl", "realized_profit_ratio", "profit_change", default=0.0)
    pnl30 = _num(stats30, "realized_profit_pnl", "pnl", "realized_profit_ratio", "profit_change", default=pnl7)

    # Normalized against a 150% cap, not 500% — a 7d/30d PnL of +150% is
    # already an exceptional wallet; the old 500% cap compressed realistic
    # winners (e.g. +113% -> only 22.7/100) into the C tier, which meant
    # get_tiered_wallets(min_tier="B") returned nothing and Phase 3 cluster
    # detection could never fire. Recalibrated to match the S/A/B/C cutoffs
    # (85/70/50) as originally specified, rather than loosening the cutoffs.
    PNL_CAP = 1.5
    pnl_component = min(max(pnl7, -1.0), PNL_CAP) / PNL_CAP * 100.0
    roi_component = min(max(pnl30, -1.0), PNL_CAP) / PNL_CAP * 100.0
    # consistency: both windows green = 1.0, one green = 0.5, none = 0.0
    if pnl7 > 0 and pnl30 > 0:
        consistency = 1.0
    elif pnl7 > 0 or pnl30 > 0:
        consistency = 0.5
    else:
        consistency = 0.0
    early_entry_rate = 0.5  # neutral placeholder — GMGN stats don't expose this directly

    score = (
        (winrate * 100) * 0.30
        + pnl_component * 0.25
        + roi_component * 0.20
        + (early_entry_rate * 100) * 0.15
        + (consistency * 100) * 0.10
    )

    if trades < MIN_TRADES_FOR_TRUST:
        score *= 0.5  # rule #3: small sample = luck, not skill — halve conviction

    if score > 85:
        tier = "S"
    elif score > 70:
        tier = "A"
    elif score > 50:
        tier = "B"
    else:
        tier = "C"

    hold = _num(stats7, "avg_holding_period", "avg_hold", "holding_period", default=-1)
    if hold < 0:
        style = "UNKNOWN"
    elif hold < 3600:
        style = "SNIPER"
    elif hold < 86400:
        style = "SWING"
    else:
        style = "ACCUMULATOR"

    return {
        "win_rate": round(winrate, 4), "pnl_7d": round(pnl7, 4), "pnl_30d": round(pnl30, 4),
        "total_trades": trades, "avg_roi": round(roi_component / 100, 4),
        "early_entry_rate": early_entry_rate, "consistency_score": consistency,
        "score": round(score, 2), "tier": tier, "style": style,
    }


def upsert_wallet(address, chain, tag, scored, source):
    return db_write_retry(_upsert_wallet_impl, address, chain, tag, scored, source)


def _upsert_wallet_impl(address, chain, tag, scored, source):
    conn = db_conn()
    try:
        conn.execute("""
            INSERT INTO alpha_wallets (chain, address, tags, win_rate, pnl_7d, pnl_30d,
                total_trades, avg_roi, early_entry_rate, consistency_score, score, tier,
                style, source, last_updated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, strftime('%s','now'))
            ON CONFLICT(chain, address) DO UPDATE SET
                win_rate=excluded.win_rate, pnl_7d=excluded.pnl_7d, pnl_30d=excluded.pnl_30d,
                total_trades=excluded.total_trades, avg_roi=excluded.avg_roi,
                early_entry_rate=excluded.early_entry_rate, consistency_score=excluded.consistency_score,
                score=excluded.score, tier=excluded.tier, style=excluded.style,
                last_updated=strftime('%s','now')
        """, (chain, address, json.dumps([tag]), scored["win_rate"], scored["pnl_7d"], scored["pnl_30d"],
              scored["total_trades"], scored["avg_roi"], scored["early_entry_rate"], scored["consistency_score"],
              scored["score"], scored["tier"], scored["style"], source))
        conn.commit()
    finally:
        conn.close()


def get_tiered_wallets(min_tier="A"):
    order = {"S": 3, "A": 2, "B": 1, "C": 0}
    conn = db_conn()
    try:
        rows = conn.execute("SELECT * FROM alpha_wallets").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows if order.get(r["tier"], 0) >= order.get(min_tier, 0)]


# ── Phase 1: wallet universe refresh ─────────────────────────────────────
def fetch_wallet_stats(address):
    """gmgn-cli's portfolio stats, despite documenting multi-wallet batch
    support via repeated --wallet flags, only returns ONE wallet's object per
    call (confirmed live: 2x --wallet returned a single dict, not a list) --
    silently dropping every wallet past the first in a batch. Call one wallet
    at a time instead; slower but the only way that returns complete data."""
    data7 = run_gmgn(["portfolio", "stats", "--chain", CHAIN, "--wallet", address,
                       "--period", "7d"], kind="stats")
    data30 = run_gmgn(["portfolio", "stats", "--chain", CHAIN, "--wallet", address,
                        "--period", "30d"], kind="stats")
    s7 = data7.get("data", data7) if isinstance(data7, dict) else None
    s30 = data30.get("data", data30) if isinstance(data30, dict) else None
    return s7, s30


def phase1_wallet_universe():
    _log("PHASE 1: wallet universe refresh")
    seen = set()
    for track_kind, tag in (("smartmoney", "smart_degen"), ("kol", "renowned")):
        data = run_gmgn(["track", track_kind, "--chain", CHAIN, "--limit", "100"], kind=track_kind)
        if not data:
            continue
        trades = data.get("data", data) if isinstance(data, dict) else data
        if isinstance(trades, dict):
            trades = trades.get("list", trades.get("trades", []))
        if not isinstance(trades, list):
            continue
        for t in trades:
            addr = t.get("maker") or t.get("maker_address") or t.get("address")
            if addr and addr not in seen:
                seen.add(addr)

    _log(f"  {len(seen)} unique wallets in this cycle's feed")
    for a in seen:
        s7, s30 = fetch_wallet_stats(a)
        if not s7:
            continue
        scored = score_wallet(s7, s30 or s7)
        upsert_wallet(a, CHAIN, "smart_degen", scored, "phase1_universe")

    conn = db_conn()
    try:
        counts = conn.execute("SELECT tier, COUNT(*) c FROM alpha_wallets GROUP BY tier").fetchall()
    finally:
        conn.close()
    _log(f"  universe tiers: {[dict(r) for r in counts]}")


# ── Phase 2: back-track today's winners ──────────────────────────────────
def phase2_backtrack_winners():
    _log("PHASE 2: back-track 24h winners")
    data = run_gmgn(["market", "trending", "--chain", CHAIN, "--interval", "24h",
                      "--order-by", "volume", "--limit", "20"], kind="trending")
    if not data:
        return
    tokens = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(tokens, dict):
        tokens = tokens.get("list", tokens.get("rank", []))
    if not isinstance(tokens, list):
        return

    runners = [t for t in tokens if _num(t, "price_change_percent", "change24h", "price_change", default=0) > 200]
    _log(f"  {len(runners)} tokens >200% in 24h")

    for t in runners[:5]:
        addr = t.get("address") or t.get("token_address")
        if not addr:
            continue
        holders = run_gmgn(["token", "holders", "--chain", CHAIN, "--address", addr,
                             "--tag", "smart_degen", "--limit", "20"], kind="holders")
        if not holders:
            continue
        hlist = holders.get("data", holders) if isinstance(holders, dict) else holders
        if isinstance(hlist, dict):
            hlist = hlist.get("list", [])
        if not isinstance(hlist, list):
            continue
        for h in hlist:
            waddr = h.get("address") or h.get("wallet_address")
            if waddr:
                conn = db_conn()
                try:
                    exists = conn.execute("SELECT 1 FROM alpha_wallets WHERE chain=? AND address=?",
                                           (CHAIN, waddr)).fetchone()
                finally:
                    conn.close()
                if not exists:
                    upsert_wallet(waddr, CHAIN, "backtrack_winner",
                                   {"win_rate": 0, "pnl_7d": 0, "pnl_30d": 0, "total_trades": 0,
                                    "avg_roi": 0, "early_entry_rate": 0.5, "consistency_score": 0.5,
                                    "score": 0, "tier": "C", "style": "UNKNOWN"},
                                   "phase2_backtrack")
        log_event("backtrack", addr, {"symbol": t.get("symbol"), "new_wallets_found": len(hlist)})


# ── Phase 3: live hunt / cluster detection ────────────────────────────────
_rolling_window = {}  # token_address -> list of {wallet, tier, ts, amount_usd}


def _prune_window():
    cutoff = time.time() - CLUSTER_WINDOW_SECONDS
    for tok in list(_rolling_window):
        _rolling_window[tok] = [e for e in _rolling_window[tok] if e["ts"] > cutoff]
        if not _rolling_window[tok]:
            del _rolling_window[tok]


def phase3_live_hunt():
    _prune_window()
    data = run_gmgn(["track", "smartmoney", "--chain", CHAIN, "--limit", "100"], kind="smartmoney")
    if not data:
        return
    trades = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(trades, dict):
        trades = trades.get("list", trades.get("trades", []))
    if not isinstance(trades, list):
        return

    wallet_tiers = {w["address"]: w["tier"] for w in get_tiered_wallets(min_tier="B")}

    for t in trades:
        addr = t.get("maker") or t.get("maker_address")
        token = t.get("base_address")
        is_open = t.get("is_open_or_close")  # smartmoney: 0 = open/add
        amount_usd = _num(t, "amount_usd", "usd_value", default=0)
        if not addr or not token or is_open != 0:
            continue
        tier = wallet_tiers.get(addr)
        if tier not in ("S", "A"):
            continue
        _rolling_window.setdefault(token, []).append(
            {"wallet": addr, "tier": tier, "ts": time.time(), "amount_usd": amount_usd}
        )

    for token, entries in list(_rolling_window.items()):
        distinct = {e["wallet"]: e["tier"] for e in entries}
        if len(distinct) < CLUSTER_MIN_WALLETS:
            continue
        _fire_cluster(token, distinct, entries)
        del _rolling_window[token]  # one alert per convergence window


def _fire_cluster(token, distinct_wallets, entries):
    conn = db_conn()
    try:
        already = conn.execute(
            "SELECT 1 FROM alpha_clusters WHERE chain=? AND token_address=? AND detected_at > ?",
            (CHAIN, token, time.time() - CLUSTER_WINDOW_SECONDS),
        ).fetchone()
    finally:
        conn.close()
    if already:
        return

    sec = run_gmgn(["token", "security", "--chain", CHAIN, "--address", token], kind="security")
    sec_data = (sec.get("data", sec) if isinstance(sec, dict) else {}) or {}
    rug_ratio = _num(sec_data, "rug_ratio", default=1.0)  # unknown = treat as risky
    wash = bool(sec_data.get("is_wash_trading", False))

    if rug_ratio > RUG_RATIO_MAX or wash:
        _log(f"CLUSTER SKIPPED (safety fail) {token}: rug={rug_ratio} wash={wash}")
        return

    info = run_gmgn(["token", "info", "--chain", CHAIN, "--address", token], kind="info")
    info_data = (info.get("data", info) if isinstance(info, dict) else {}) or {}
    price = _num(info_data.get("price", {}), "price", default=0) if isinstance(info_data.get("price"), dict) else _num(info_data, "price", default=0)
    supply = _num(info_data, "circulating_supply", "total_supply", default=0)
    mc = price * supply if price and supply else 0
    liq = _num(info_data, "liquidity", default=0)
    symbol = info_data.get("symbol", token[:8])

    total_inflow = sum(e["amount_usd"] for e in entries)
    tier_weight = {"S": 1.0, "A": 0.7}
    conviction = min(100, len(distinct_wallets) * 15 + sum(tier_weight.get(t, 0.3) for t in distinct_wallets.values()) * 10)
    conviction = round(conviction, 1)
    action = "MIRROR" if conviction >= 70 else "WATCH"

    def _write():
        conn = db_conn()
        try:
            conn.execute("""
                INSERT INTO alpha_clusters (chain, token_address, token_symbol, wallets_json, wallet_count,
                    total_inflow_usd, market_cap, liquidity_usd, rug_ratio, conviction, action)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (CHAIN, token, symbol, json.dumps(distinct_wallets), len(distinct_wallets),
                  total_inflow, mc, liq, rug_ratio, conviction, action))
            conn.commit()
        finally:
            conn.close()
    db_write_retry(_write)

    _log(f"⚡ CLUSTER: {symbol} ({token[:12]}...) {len(distinct_wallets)} wallets, "
         f"conviction={conviction}, action={action}")

    post_intel_signal(
        symbol=symbol, sig_type="alpha_cluster", conviction=conviction / 100.0,
        detail={
            "wallets": distinct_wallets, "wallet_count": len(distinct_wallets),
            "total_inflow_usd": round(total_inflow, 2), "market_cap": mc,
            "liquidity_usd": liq, "rug_ratio": rug_ratio, "action": action,
        },
        mint=token,
    )
    add_to_watchlist(token, "solana", f"ARES-ALPHA cluster: {symbol}",
                      notes=f"{len(distinct_wallets)} smart-money wallets converged, conviction {conviction}")
    log_event("cluster", token, {"symbol": symbol, "conviction": conviction, "wallets": len(distinct_wallets)})


# ── Phase 4: full re-score ────────────────────────────────────────────────
def phase4_rescore_all():
    _log("PHASE 4: full wallet universe re-score")
    conn = db_conn()
    try:
        addrs = [r["address"] for r in conn.execute("SELECT address FROM alpha_wallets").fetchall()]
    finally:
        conn.close()
    for a in addrs:
        s7, s30 = fetch_wallet_stats(a)
        if not s7:
            continue
        scored = score_wallet(s7, s30 or s7)
        upsert_wallet(a, CHAIN, "rescored", scored, "phase4_rescore")
    _log("  re-score complete")


# ── status brief ───────────────────────────────────────────────────────
def print_status_brief():
    conn = db_conn()
    try:
        counts = {r["tier"]: r["c"] for r in conn.execute(
            "SELECT tier, COUNT(*) c FROM alpha_wallets GROUP BY tier").fetchall()}
        clusters_today = conn.execute(
            "SELECT COUNT(*) c FROM alpha_clusters WHERE detected_at > ?",
            (time.time() - 86400,)).fetchone()["c"]
        top = conn.execute(
            "SELECT * FROM alpha_clusters ORDER BY detected_at DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    top_line = f"{top['token_symbol']} — conviction {top['conviction']}" if top else "none yet"
    _log("══════════════════════════════════════════════════")
    _log(f" ARES-ALPHA STATUS — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    _log("══════════════════════════════════════════════════")
    _log(f" Tracked Wallets: {sum(counts.values())} | S:{counts.get('S',0)} A:{counts.get('A',0)} "
         f"B:{counts.get('B',0)} C:{counts.get('C',0)}")
    _log(f" Clusters (24h): {clusters_today} | Top Signal: {top_line}")


# ── main loop ──────────────────────────────────────────────────────────
def main():
    db_init()
    _log("ARES-ALPHA hunter starting.")
    if not TOOL_INTEL_KEY:
        _log("WARNING: VANTAGE_TOOL_INTEL_KEY not set — clusters will be detected/logged but NOT posted to Vantage signals.")
    last = {"p1": 0, "p2": 0, "p3": 0, "p4": 0, "status": 0}

    def run_and_time(fn, name):
        try:
            fn()
        except Exception:
            _log(f"{name} crashed:\n{traceback.format_exc()}")

    while True:
        now = time.time()
        if now - last["p1"] >= PHASE1_INTERVAL:
            run_and_time(phase1_wallet_universe, "phase1")
            last["p1"] = now
        if now - last["p2"] >= PHASE2_INTERVAL:
            run_and_time(phase2_backtrack_winners, "phase2")
            last["p2"] = now
        if now - last["p3"] >= PHASE3_INTERVAL:
            run_and_time(phase3_live_hunt, "phase3")
            last["p3"] = now
        if now - last["p4"] >= PHASE4_INTERVAL:
            run_and_time(phase4_rescore_all, "phase4")
            last["p4"] = now
        if now - last["status"] >= 600:
            run_and_time(print_status_brief, "status")
            last["status"] = now
        time.sleep(15)


if __name__ == "__main__":
    main()
