#!/opt/ares/venv/bin/python3
"""
Vantage Strategy Bots — paper-trades EVERY strategy, all the time; live stays
behind the toggle.

Why this replaced /opt/ares/strategy_bots.py (untracked, 2026-11-12):
  - It only ran strategies with armed=1, so most strategies never produced a
    single data point.
  - Entries required a token to be in BOTH /intel/degen/must-buy-20 AND the
    money-flow graph. Those two sets did not overlap (0 of 15 signal-backed
    mints were graph nodes), so it had not placed one paper trade since at
    least 2026-11-01 while logging "1 armed strategy(ies)" every minute.
  - `if conn.total_changes:` is connection-cumulative, so it was always true
    after the first write and could place an order for a position that
    INSERT OR IGNORE had just skipped.
  - Position bookkeeping branched on strategy.live even after place_order had
    downgraded the order to paper.

WHAT RUNS EACH CYCLE
  1. Candidate pool (once, shared by every strategy): Solana BUY calls from
     signal_ledger (every producer: dex_new, degen_alpha_fusion, ogun_degen,
     ...) grouped by mint, plus must-buy-20 entries whose contract address
     DexScreener confirms has the same symbol (must-buy resolves by symbol and
     sometimes picks a different token with the same ticker). Every candidate
     is then checked on DexScreener: liquidity floor, not being dumped, and a
     lifecycle tier (pre_migration / pumpfun_<lo>k_<hi>k / just_launch /
     migrated) derived from the pair itself.
  2. Every strategy, regardless of enabled/armed, is paper-traded by its type:
       scalper_5020, bighit_40_800, and any unknown type -> fixed TP/SL on the
         candidate pool (row's take_profit_pct / stop_loss_pct; defaults
         +30 / -12)
       accumulator_tiered, doubler_flip -> their tiered exits, same pool
       momentum, paper, sma, sma_crossover, swing -> SMA fast/slow on
         target_symbols (default SOL) from /api/intel/ohlc, plus TP/SL
       arbitrage, manual -> no paper model; skipped and logged hourly
  3. Saved Pine indicators (this key's agent + shared) are evaluated on
     PINE_SYMBOLS via POST /api/pine/indicators/{id}/signal with execute=true —
     that endpoint only ever paper-fills.
  4. Active copy-trading subscriptions: every new filled/confirmed leader
     order is mirrored as a PAPER order for the follower.
  5. strategy_paper_stats is refreshed: closed trades, win rate, avg and total
     PnL per strategy — the running scoreboard the paper data feeds.
  6. Hourly, strategies with MIN_VALIDATION_TRADES+ closed paper trades are
     run through backend/backtest/validation.py (Monte-Carlo + walk-forward)
     and strategy_validation gets edge / no-edge / inconclusive. Report only:
     it never arms or enables live.

LIVE GATE (unchanged in spirit, now explicit): an order is only created as a
REAL pending order when the strategy is enabled=1 AND armed=1 AND live=1 AND
the global switch (VANTAGE_LIVE_TRADING env brake, else live_mode.enabled) is
on. Everything else is a paper fill. Even then this daemon never signs: the
pending order waits for POST /orders/{id}/execute-live with the owning
agent's key, which re-checks demo mode, the kill switch and balances.
SMA-type (majors) strategies are always paper — there is no live adapter for
them here.

trigger_reason is always "bot:<strategy_type>", "pine:..." (set by the
endpoint) or "copy:<leader_agent_id>". None may contain "moonshot":
ares_jupiter_signer.py auto-signs orders whose trigger_reason matches it.

Paper fills include PAPER_COST_BPS (default 150 = 1.5%) per side for
slippage+fees, so paper PnL is not flattered by mid-price fills.

Usage: strategy_bots.py [interval_seconds]   (default 60)
"""
import json
import os
import sqlite3
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone

# backend.backtest is imported lazily by the validation stage; the unit runs from /opt/ares.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.environ.get("VANTAGE_DB_PATH", "/opt/ares/Vantage/data/vantage.db")
VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://localhost:8001")
KEY_FILE = os.environ.get("VANTAGE_KEY_FILE", os.path.expanduser("~/.vantage_key"))
GLOBAL_LIVE_ENV = "VANTAGE_LIVE_TRADING"

PAPER_COST_BPS = float(os.environ.get("PAPER_COST_BPS", "150"))
MIN_LIQUIDITY_USD = float(os.environ.get("PAPER_MIN_LIQUIDITY_USD", "20000"))
SIGNAL_WINDOW_S = int(os.environ.get("PAPER_SIGNAL_WINDOW_S", "7200"))
REENTRY_COOLDOWN_S = int(os.environ.get("PAPER_REENTRY_COOLDOWN_S", "21600"))
PINE_SYMBOLS = [s for s in os.environ.get("PINE_SYMBOLS", "SOL,BTC,ETH").split(",") if s]
PINE_EVERY_S = int(os.environ.get("PINE_EVERY_S", "300"))
STATS_EVERY_S = int(os.environ.get("STATS_EVERY_S", "300"))
VALIDATE_EVERY_S = int(os.environ.get("VALIDATE_EVERY_S", "3600"))
MIN_VALIDATION_TRADES = int(os.environ.get("MIN_VALIDATION_TRADES", "10"))

DEFAULT_TP, DEFAULT_SL = 30.0, -12.0
POOL_TYPES = {"scalper_5020", "bighit_40_800"}
SMA_TYPES = {"momentum", "paper", "sma", "sma_crossover", "swing"}
NO_PAPER_MODEL = {"arbitrage", "manual"}

_last_note = {}  # (key) -> ts, for rate-limited log lines


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def log_every(key, seconds, msg):
    now = time.time()
    if now - _last_note.get(key, 0) >= seconds:
        _last_note[key] = now
        log(msg)


def vantage_key():
    try:
        return open(KEY_FILE).read().strip()
    except OSError:
        return ""


def vget(path, timeout=30, body=None):
    """GET (or POST when body is given) a Vantage endpoint as the key's agent."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{VANTAGE_URL}{path}", data=data,
        headers={"X-Agent-Key": vantage_key(), "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())


# ── Live gate ───────────────────────────────────────────────────────────────
def global_live_enabled() -> bool:
    raw = os.environ.get(GLOBAL_LIVE_ENV)
    v = None if raw is None else str(raw).strip().lower()
    if v in {"0", "false", "no", "off"}:
        return False
    if v in {"1", "true", "yes", "on"}:
        return True
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2.0)
        try:
            row = con.execute("SELECT enabled FROM live_mode WHERE id=1").fetchone()
        finally:
            con.close()
        return bool(row and row[0])
    except Exception:
        return False  # missing table or locked DB reads as OFF, never as "go"


def wants_live(strat) -> bool:
    """True only if this order may be created as a real pending order."""
    return bool(strat.get("enabled") and strat.get("armed") and strat.get("live")) and global_live_enabled()


# ── DB ──────────────────────────────────────────────────────────────────────
POSITIONS_DDL = """
CREATE TABLE IF NOT EXISTS strategy_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER NOT NULL,
    wallet_id INTEGER NOT NULL,
    token_mint TEXT NOT NULL,
    symbol TEXT,
    status TEXT DEFAULT 'open',
    entry_price REAL,
    entry_amount_usd REAL,
    remaining_pct REAL DEFAULT 100,
    dca_steps_done INTEGER DEFAULT 0,
    opened_at INTEGER,
    closed_at INTEGER,
    last_checked_at INTEGER,
    UNIQUE(strategy_id, token_mint)
)
"""
STATS_DDL = """
CREATE TABLE IF NOT EXISTS strategy_paper_stats (
    strategy_id INTEGER PRIMARY KEY,
    n_open INTEGER, n_closed INTEGER, wins INTEGER, win_rate REAL,
    avg_pnl_pct REAL, total_pnl_usd REAL, updated_at TEXT
)
"""
VALIDATION_DDL = """
CREATE TABLE IF NOT EXISTS strategy_validation (
    strategy_id INTEGER PRIMARY KEY, n_trades INTEGER, verdict TEXT,
    avg_pnl_pct REAL, p_value_sharpe REAL, p_value_max_dd REAL, consistency_rate REAL,
    monte_carlo TEXT, walk_forward TEXT, updated_at TEXT
)
"""
COPY_CURSOR_DDL = """
CREATE TABLE IF NOT EXISTS copy_paper_cursor (
    subscription_id INTEGER PRIMARY KEY, last_order_id INTEGER NOT NULL
)
"""


def db_conn(path=None):
    conn = sqlite3.connect(path or DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout=20000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def ensure_schema(conn):
    conn.execute(POSITIONS_DDL)
    conn.execute(STATS_DDL)
    conn.execute(COPY_CURSOR_DDL)
    conn.execute(VALIDATION_DDL)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(strategy_positions)")}
    for col, decl in (("pending_order_id", "INTEGER"), ("exit_price", "REAL"),
                      ("pnl_pct", "REAL"), ("is_paper", "INTEGER DEFAULT 1"),
                      ("entry_source", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE strategy_positions ADD COLUMN {col} {decl}")
    conn.commit()


# ── Market data ─────────────────────────────────────────────────────────────
def dexscreener_pairs(mints):
    """{mint: best_pair} for up to 30 mints per request (highest liquidity pair)."""
    out = {}
    mints = [m for m in dict.fromkeys(mints) if m]
    for i in range(0, len(mints), 30):
        chunk = mints[i:i + 30]
        try:
            req = urllib.request.Request(
                f"https://api.dexscreener.com/latest/dex/tokens/{','.join(chunk)}",
                headers={"User-Agent": "Vantage/1.0"})
            data = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
        except Exception as e:
            log(f"  dexscreener error: {e}")
            continue
        for p in data.get("pairs") or []:
            m = (p.get("baseToken") or {}).get("address")
            if m not in chunk:
                continue
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if m not in out or liq > ((out[m].get("liquidity") or {}).get("usd") or 0):
                out[m] = p
    return out


def pair_price(p):
    try:
        return float(p.get("priceUsd")) if p and p.get("priceUsd") else None
    except (TypeError, ValueError):
        return None


def pair_tiers(p, now_ms=None):
    """Lifecycle tiers a DexScreener pair falls into."""
    now_ms = now_ms or time.time() * 1000
    tiers = set()
    mc = p.get("marketCap") or p.get("fdv") or 0
    on_curve = (p.get("dexId") or "").lower() == "pumpfun"
    if on_curve:
        tiers.add("pre_migration")
        lo = int(mc // 10000) * 10
        if lo < 100:
            tiers.add(f"pumpfun_{lo}k_{lo + 10}k")
    else:
        tiers.add("migrated")
    created = p.get("pairCreatedAt") or 0
    if created and now_ms - created < 6 * 3600 * 1000:
        tiers.add("just_launch")
    return tiers


def passes_quality(p):
    """Tradable and not being dumped right now."""
    on_curve = (p.get("dexId") or "").lower() == "pumpfun"
    liq = (p.get("liquidity") or {}).get("usd") or 0
    if not on_curve and liq < MIN_LIQUIDITY_USD:
        return False
    if on_curve and (p.get("marketCap") or 0) < 5000:
        return False
    h1 = (p.get("txns") or {}).get("h1") or {}
    buys, sells = h1.get("buys") or 0, h1.get("sells") or 0
    if buys + sells < 10 or sells > buys * 1.5:
        return False
    m5 = (p.get("priceChange") or {}).get("m5")
    return not (m5 is not None and m5 < -15)


def build_candidate_pool(conn, now=None):
    """Ranked list of {mint, symbol, sources, conviction, price, tiers}."""
    now = int(now or time.time())
    rows = conn.execute(
        """SELECT address, MAX(symbol) symbol, GROUP_CONCAT(DISTINCT source) srcs,
                  COUNT(DISTINCT source) n_src, MAX(COALESCE(conviction,0)) conv
           FROM signal_ledger
           WHERE UPPER(direction)='BUY' AND address IS NOT NULL AND address != ''
             AND (chain='solana' OR address LIKE '%pump') AND created_at > ?
           GROUP BY address""", (now - SIGNAL_WINDOW_S,)).fetchall()
    cands = {r["address"]: {"mint": r["address"], "symbol": (r["symbol"] or "").strip(),
                            "sources": set((r["srcs"] or "").split(",")) - {""},
                            "conviction": r["conv"] or 0, "must_buy": 0}
             for r in rows}
    must_buy = {}
    try:
        for c in vget("/api/intel/degen/must-buy-20?limit=20&hours=24", timeout=45).get("must_buy", []):
            if c.get("ca"):
                must_buy[c["ca"]] = c
    except Exception as e:
        log_every("mustbuy", 600, f"  must-buy-20 unavailable: {e}")
    # Rank before the market lookup so DexScreener only sees the top slice.
    def pre_score(c):
        return len(c["sources"]) * 10 + c["conviction"] * 5
    ranked = sorted(cands.values(), key=pre_score, reverse=True)[:60]
    lookup = [c["mint"] for c in ranked] + list(must_buy)
    pairs = dexscreener_pairs(lookup)
    for ca, c in must_buy.items():
        p = pairs.get(ca)
        sym = ((p or {}).get("baseToken") or {}).get("symbol", "")
        if not p or sym.strip().lower() != (c.get("symbol") or "").strip().lower():
            continue  # must-buy resolved the ticker to a different contract
        entry = cands.setdefault(ca, {"mint": ca, "symbol": sym, "sources": set(),
                                      "conviction": 0, "must_buy": 0})
        entry["sources"].add("must_buy_20")
        entry["must_buy"] = c.get("score") or 0
        if entry not in ranked:
            ranked.append(entry)
    pool = []
    for c in ranked:
        p = pairs.get(c["mint"])
        if not p or not passes_quality(p):
            continue
        price = pair_price(p)
        if not price:
            continue
        c = dict(c, price=price, tiers=pair_tiers(p),
                 symbol=c["symbol"] or (p.get("baseToken") or {}).get("symbol", ""))
        c["score"] = pre_score(c) + c["must_buy"] / 50
        pool.append(c)
    pool.sort(key=lambda c: c["score"], reverse=True)
    return pool


def sma(values, n):
    return sum(values[-n:]) / n if len(values) >= n else None


def candles_close(symbol, interval="1h", limit=80):
    try:
        d = vget(f"/api/intel/ohlc/{symbol}?interval={interval}&limit={limit}")
        return [float(c["close"]) for c in d.get("candles", []) if c.get("close") is not None]
    except Exception as e:
        log_every(f"ohlc:{symbol}", 600, f"  ohlc {symbol} unavailable: {e}")
        return []


# ── Orders ──────────────────────────────────────────────────────────────────
def paper_fill_price(price, side):
    c = PAPER_COST_BPS / 10000.0
    return price * (1 + c) if side == "buy" else price * (1 - c)


def _insert_order(conn, agent_id, wallet_id, strategy_id, side, symbol, chain, quantity, price,
                  trigger_reason, note, extra_cols=None):
    cols = ["agent_id", "wallet_id", "order_type", "side", "symbol", "chain", "quantity", "price",
            "trigger_reason", "strategy_id", "notes", "status"]
    vals = [agent_id, wallet_id, "market", side.upper(), symbol, chain, quantity, price,
            trigger_reason, strategy_id, note, "pending"]
    for k, v in (extra_cols or {}).items():
        cols.append(k)
        vals.append(v)
    cur = conn.execute(
        f"INSERT INTO trading_orders ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
    return cur.lastrowid


def paper_order(conn, agent_id, wallet_id, strategy_id, side, symbol, chain, quantity, price,
                trigger_reason, note, extra_cols=None):
    fill = paper_fill_price(price, side)
    order_id = _insert_order(conn, agent_id, wallet_id, strategy_id, side, symbol, chain, quantity,
                             fill, trigger_reason, note, extra_cols)
    tx_hash = f"paper:{uuid.uuid4().hex[:16]}"
    conn.execute(
        """UPDATE trading_orders SET status='filled', filled_quantity=quantity, avg_fill_price=?,
           tx_hash=?, executed_at=datetime('now'), settled_at=datetime('now') WHERE id=?""",
        (fill, tx_hash, order_id))
    conn.execute(
        "INSERT INTO trading_trade_journal (order_id, agent_id, entry_reasoning, tags) VALUES (?,?,?,?)",
        (order_id, agent_id, f"[SIMULATED] {note} @ {fill:.10g} ({tx_hash})",
         json.dumps(["simulated", "paper-fill", trigger_reason])))
    conn.commit()
    return order_id, fill


def place_order(conn, strat, symbol, chain, side, quantity, price, note, allow_live=True):
    """Returns (order_id, is_live, fill_price). The only place paper vs live is decided."""
    trigger = f"bot:{strat['strategy_type']}"
    if allow_live and wants_live(strat):
        order_id = _insert_order(conn, strat["agent_id"], strat["wallet_id"], strat["id"], side,
                                 symbol, chain, quantity, price, trigger, note)
        conn.execute(
            "INSERT INTO trading_trade_journal (order_id, agent_id, entry_reasoning, tags) VALUES (?,?,?,?)",
            (order_id, strat["agent_id"],
             f"[LIVE-PENDING] {note} — awaiting POST /orders/{order_id}/execute-live",
             json.dumps(["live-pending", trigger])))
        conn.commit()
        log(f"  [{strat['id']}:{strat['strategy_type']}] {side} {quantity:.6g} {symbol} @ {price:.10g} — LIVE-PENDING (order {order_id})")
        return order_id, True, price
    order_id, fill = paper_order(conn, strat["agent_id"], strat["wallet_id"], strat["id"], side,
                                 symbol, chain, quantity, price, trigger, note)
    log(f"  [{strat['id']}:{strat['strategy_type']}] {side} {quantity:.6g} {symbol} @ {fill:.10g} — PAPER (order {order_id})")
    return order_id, False, fill


# ── Positions ───────────────────────────────────────────────────────────────
def open_position(conn, strat, mint, symbol, price, size_usd, chain, source, allow_live=True):
    """Upsert an open position (re-opening a closed one), then place the buy."""
    now = int(time.time())
    cur = conn.execute(
        """INSERT INTO strategy_positions (strategy_id, wallet_id, token_mint, symbol, status,
               entry_price, entry_amount_usd, remaining_pct, dca_steps_done, opened_at,
               last_checked_at, entry_source)
           VALUES (?,?,?,?, 'open', ?,?,100,0,?,?,?)
           ON CONFLICT(strategy_id, token_mint) DO UPDATE SET
               status='open', entry_price=excluded.entry_price,
               entry_amount_usd=excluded.entry_amount_usd, remaining_pct=100, dca_steps_done=0,
               opened_at=excluded.opened_at, closed_at=NULL, exit_price=NULL, pnl_pct=NULL,
               pending_order_id=NULL, entry_source=excluded.entry_source
           WHERE strategy_positions.status='closed'""",
        (strat["id"], strat["wallet_id"] or 0, mint, symbol, price, size_usd, now, now, source))
    if cur.rowcount != 1:
        return None
    order_id, is_live, fill = place_order(conn, strat, mint, chain, "buy", size_usd / price, price,
                                          f"ENTRY {symbol} via {source}", allow_live)
    conn.execute("UPDATE strategy_positions SET entry_price=?, is_paper=?, pending_order_id=? "
                 "WHERE strategy_id=? AND token_mint=?",
                 (fill, 0 if is_live else 1, order_id if is_live else None, strat["id"], mint))
    conn.commit()
    return order_id


def close_position(conn, strat, pos, price, reason, chain="solana", allow_live=True):
    qty = pos["entry_amount_usd"] * pos["remaining_pct"] / 100 / pos["entry_price"]
    gain_mid = (price - pos["entry_price"]) / pos["entry_price"] * 100
    order_id, is_live, fill = place_order(conn, strat, pos["token_mint"], chain, "sell", qty, price,
                                          f"{reason} {gain_mid:+.1f}% ({pos['symbol']})", allow_live)
    pnl = (fill - pos["entry_price"]) / pos["entry_price"] * 100
    if is_live:
        conn.execute("UPDATE strategy_positions SET remaining_pct=0, pending_order_id=? WHERE id=?",
                     (order_id, pos["id"]))
    else:
        conn.execute("UPDATE strategy_positions SET status='closed', closed_at=?, remaining_pct=0, "
                     "exit_price=?, pnl_pct=? WHERE id=?", (int(time.time()), fill, pnl, pos["id"]))
    conn.commit()
    return order_id


def partial_sell(conn, strat, pos, price, sell_pct, note):
    qty = pos["entry_amount_usd"] * sell_pct / 100 / pos["entry_price"]
    order_id, is_live, _ = place_order(conn, strat, pos["token_mint"], "solana", "sell", qty, price, note)
    return order_id if is_live else None


def reconcile_pending(conn, strat):
    """Clear pending markers once the owning agent's live order resolves."""
    for pos in conn.execute("SELECT * FROM strategy_positions WHERE strategy_id=? AND pending_order_id IS NOT NULL",
                            (strat["id"],)).fetchall():
        order = conn.execute("SELECT status, side FROM trading_orders WHERE id=?",
                             (pos["pending_order_id"],)).fetchone()
        if not order or order["status"] == "pending":
            continue
        if order["status"] in ("submitted", "filled", "confirmed") and order["side"] == "SELL" \
                and pos["remaining_pct"] <= 0:
            conn.execute("UPDATE strategy_positions SET status='closed', closed_at=?, pending_order_id=NULL WHERE id=?",
                         (int(time.time()), pos["id"]))
        else:
            conn.execute("UPDATE strategy_positions SET pending_order_id=NULL WHERE id=?", (pos["id"],))
        conn.commit()


def positions_of(conn, strat):
    return [dict(r) for r in conn.execute("SELECT * FROM strategy_positions WHERE strategy_id=?",
                                          (strat["id"],)).fetchall()]


def tp_sl(strat, default_tp=DEFAULT_TP, default_sl=DEFAULT_SL):
    """Row values win. Stop-loss is stored with either sign across the estate."""
    tp = strat.get("take_profit_pct")
    sl = strat.get("stop_loss_pct")
    tp = abs(float(tp)) if tp not in (None, 0, 0.0) else default_tp
    sl = -abs(float(sl)) if sl not in (None, 0, 0.0) else default_sl
    return tp, sl


def position_size_usd(strat, cfg, default=20.0):
    for v in (cfg.get("position_size_usd"), strat.get("max_position_size_usd")):
        if v:
            return float(v)
    return default


def target_tiers(strat):
    return {t.strip() for t in (strat.get("target_tiers") or "").split(",") if t.strip()}


def recently_closed(positions, now=None):
    now = now or time.time()
    return {p["token_mint"] for p in positions
            if p["status"] == "closed" and (p.get("closed_at") or 0) > now - REENTRY_COOLDOWN_S}


def maybe_enter_from_pool(conn, strat, cfg, positions, pool):
    open_n = sum(1 for p in positions if p["status"] == "open")
    if open_n >= max(1, strat.get("max_concurrent_trades") or 1):
        return
    if any(p.get("pending_order_id") for p in positions):
        return
    held = {p["token_mint"] for p in positions if p["status"] == "open"} | recently_closed(positions)
    tiers = target_tiers(strat)
    size = position_size_usd(strat, cfg)
    for c in pool:
        if c["mint"] in held or (tiers and not (tiers & c["tiers"])):
            continue
        if open_position(conn, strat, c["mint"], c["symbol"], c["price"], size, "solana",
                         "+".join(sorted(c["sources"]))):
            return  # one new position per strategy per cycle


# ── Strategy processors ─────────────────────────────────────────────────────
def process_tp_sl(conn, strat, cfg, pool, prices):
    reconcile_pending(conn, strat)
    tp, sl = tp_sl(strat)
    positions = positions_of(conn, strat)
    for pos in positions:
        if pos["status"] != "open" or pos.get("pending_order_id") or not pos["entry_price"]:
            continue
        price = prices.get(pos["token_mint"])
        if not price:
            continue
        gain = (price - pos["entry_price"]) / pos["entry_price"] * 100
        if gain >= tp or gain <= sl:
            close_position(conn, strat, pos, price, "TAKE-PROFIT" if gain >= tp else "STOP-LOSS")
    maybe_enter_from_pool(conn, strat, cfg, positions_of(conn, strat), pool)


def process_accumulator(conn, strat, cfg, pool, prices):
    reconcile_pending(conn, strat)
    tier1 = cfg.get("tier1", {"at_gain_pct": 100, "sell_pct": 50})
    dca = cfg.get("tier2_dca", {"start_gain_pct": 150, "step_gain_pct": 50, "sell_pct_per_step": 5, "max_steps": 5})
    moonbag_pct = cfg.get("moonbag_pct", 25)
    _, sl = tp_sl(strat, default_sl=-60.0)
    for pos in positions_of(conn, strat):
        if pos["status"] != "open" or pos.get("pending_order_id") or not pos["entry_price"]:
            continue
        price = prices.get(pos["token_mint"])
        if not price:
            continue
        gain = (price - pos["entry_price"]) / pos["entry_price"] * 100
        steps, remaining = pos["dca_steps_done"], pos["remaining_pct"]
        if steps == 0 and gain <= sl:
            close_position(conn, strat, pos, price, "STOP-LOSS")
        elif steps == 0 and gain >= tier1["at_gain_pct"]:
            pend = partial_sell(conn, strat, pos, price, tier1["sell_pct"],
                                f"TIER1 sell {tier1['sell_pct']}% @ {gain:+.0f}% ({pos['symbol']})")
            conn.execute("UPDATE strategy_positions SET dca_steps_done=1, remaining_pct=?, pending_order_id=? WHERE id=?",
                         (remaining - tier1["sell_pct"], pend, pos["id"]))
            conn.commit()
        elif steps >= 1 and remaining > moonbag_pct and steps <= dca["max_steps"]:
            threshold = dca["start_gain_pct"] + (steps - 1) * dca["step_gain_pct"]
            sell_pct = min(dca["sell_pct_per_step"], remaining - moonbag_pct)
            if gain >= threshold and sell_pct > 0:
                pend = partial_sell(conn, strat, pos, price, sell_pct,
                                    f"DCA-OUT step {steps} sell {sell_pct}% @ {gain:+.0f}% ({pos['symbol']})")
                conn.execute("UPDATE strategy_positions SET dca_steps_done=?, remaining_pct=?, pending_order_id=? WHERE id=?",
                             (steps + 1, remaining - sell_pct, pend, pos["id"]))
                conn.commit()
    maybe_enter_from_pool(conn, strat, cfg, positions_of(conn, strat), pool)


def process_doubler(conn, strat, cfg, pool, prices):
    reconcile_pending(conn, strat)
    flip_at = cfg.get("flip_at_gain_pct", 100)
    _, sl = tp_sl(strat, default_sl=-50.0)
    for pos in positions_of(conn, strat):
        if pos["status"] != "open" or pos.get("pending_order_id") or not pos["entry_price"]:
            continue
        price = prices.get(pos["token_mint"])
        if not price:
            continue
        gain = (price - pos["entry_price"]) / pos["entry_price"] * 100
        if gain >= flip_at or gain <= sl:
            close_position(conn, strat, pos, price, "FLIP" if gain >= flip_at else "STOP-LOSS")
    maybe_enter_from_pool(conn, strat, cfg, positions_of(conn, strat), pool)


def strategy_symbols(strat):
    raw = (strat.get("target_symbols") or "SOL").replace("/", ",")
    syms = [s.strip().upper() for s in raw.split(",") if s.strip()]
    quotes = {"USDC", "USDT", "USD"}
    return [s for s in dict.fromkeys(syms) if s not in quotes] or ["SOL"]


def process_sma(conn, strat, cfg, pool, prices, closes_fn=candles_close):
    """Long while SMA(fast) > SMA(slow); exit on cross-down or TP/SL. Paper only."""
    fast, slow = int(cfg.get("fast", 10)), int(cfg.get("slow", 30))
    interval = cfg.get("interval", "1h")
    tp, sl = tp_sl(strat, default_tp=10.0, default_sl=-5.0)
    size = position_size_usd(strat, cfg)
    open_by = {p["token_mint"]: p for p in positions_of(conn, strat) if p["status"] == "open"}
    for sym in strategy_symbols(strat):
        closes = closes_fn(sym, interval, max(slow + 5, 60))
        f, s = sma(closes, fast), sma(closes, slow)
        if f is None or s is None:
            continue
        price = closes[-1]
        pos = open_by.get(sym)
        if pos:
            gain = (price - pos["entry_price"]) / pos["entry_price"] * 100
            if f < s or gain >= tp or gain <= sl:
                reason = "TAKE-PROFIT" if gain >= tp else ("STOP-LOSS" if gain <= sl else "SMA-CROSS-DOWN")
                close_position(conn, strat, pos, price, reason, chain="cex", allow_live=False)
        elif f > s:
            open_position(conn, strat, sym, sym, price, size, "cex", f"sma{fast}/{slow}:{interval}",
                          allow_live=False)


PROCESSORS = {
    "scalper_5020": process_tp_sl,
    "bighit_40_800": process_tp_sl,
    "accumulator_tiered": process_accumulator,
    "doubler_flip": process_doubler,
}


def processor_for(strategy_type):
    if strategy_type in NO_PAPER_MODEL:
        return None
    if strategy_type in SMA_TYPES:
        return process_sma
    return PROCESSORS.get(strategy_type, process_tp_sl)


# ── Pine, copy-trade, stats ─────────────────────────────────────────────────
def run_pine(conn, key_agent_id):
    rows = conn.execute("SELECT id, agent_id, name FROM pine_indicators WHERE agent_id=? OR shared=1",
                        (key_agent_id,)).fetchall()
    wallet = conn.execute("SELECT id FROM trading_wallets WHERE agent_id=? AND chain='solana' ORDER BY id LIMIT 1",
                          (key_agent_id,)).fetchone()
    for ind in rows:
        for sym in PINE_SYMBOLS:
            try:
                r = vget(f"/api/pine/indicators/{ind['id']}/signal", timeout=60, body={
                    "symbol": sym, "interval": "1h", "chain": "solana", "execute": True,
                    "quantity": 1, "wallet_id": wallet["id"] if wallet else None})
                if r.get("triggered"):
                    log(f"  [pine {ind['id']}:{ind['name']}] {sym} triggered -> {r.get('order_id') or r.get('direction')}")
            except Exception as e:
                log_every(f"pine:{ind['id']}:{sym}", 1800, f"  pine {ind['id']} {sym} error: {e}")
    return len(rows)


def run_copy(conn):
    subs = conn.execute("SELECT * FROM copy_trading_subscriptions WHERE status='active'").fetchall()
    for sub in subs:
        cur = conn.execute("SELECT last_order_id FROM copy_paper_cursor WHERE subscription_id=?", (sub["id"],)).fetchone()
        if cur is None:
            # Start from now: never back-fill a leader's whole history on first sight.
            top = conn.execute("SELECT COALESCE(MAX(id),0) FROM trading_orders").fetchone()[0]
            conn.execute("INSERT INTO copy_paper_cursor VALUES (?,?)", (sub["id"], top))
            conn.commit()
            continue
        last = cur["last_order_id"]
        orders = conn.execute(
            """SELECT * FROM trading_orders WHERE agent_id=? AND id>? AND status IN ('filled','confirmed')
               AND copied_from_order_id IS NULL ORDER BY id LIMIT 50""",
            (sub["leader_agent_id"], last)).fetchall()
        for o in orders:
            price = o["avg_fill_price"] or o["price"]
            if price:
                wallet = conn.execute("SELECT id FROM trading_wallets WHERE agent_id=? AND chain=? ORDER BY id LIMIT 1",
                                      (sub["follower_agent_id"], o["chain"])).fetchone()
                oid, _ = paper_order(conn, sub["follower_agent_id"], wallet["id"] if wallet else None, None,
                                     o["side"].lower(), o["symbol"], o["chain"], o["quantity"], price,
                                     f"copy:{sub['leader_agent_id']}",
                                     f"paper copy of agent {sub['leader_agent_id']} order #{o['id']}",
                                     {"copied_from_order_id": o["id"], "copied_from_agent_id": sub["leader_agent_id"]})
                log(f"  [copy {sub['leader_agent_id']}->{sub['follower_agent_id']}] {o['side']} {o['symbol']} — PAPER (order {oid})")
            last = o["id"]
        if orders:
            conn.execute("UPDATE copy_paper_cursor SET last_order_id=? WHERE subscription_id=?", (last, sub["id"]))
            conn.commit()
    return len(subs)


def refresh_stats(conn):
    conn.execute("DELETE FROM strategy_paper_stats")
    conn.execute(
        """INSERT INTO strategy_paper_stats
           SELECT strategy_id,
                  SUM(status='open'), SUM(status='closed' AND pnl_pct IS NOT NULL),
                  SUM(status='closed' AND pnl_pct > 0),
                  ROUND(1.0 * SUM(status='closed' AND pnl_pct > 0) / NULLIF(SUM(status='closed' AND pnl_pct IS NOT NULL),0), 3),
                  ROUND(AVG(CASE WHEN status='closed' THEN pnl_pct END), 2),
                  ROUND(SUM(CASE WHEN status='closed' THEN entry_amount_usd * pnl_pct / 100 END), 4),
                  datetime('now')
           FROM strategy_positions GROUP BY strategy_id""")
    conn.commit()


def validation_verdict(avg_pnl, p_sharpe, consistency):
    """edge only when profitable, unlikely by chance, and consistent over time."""
    if avg_pnl is None or avg_pnl <= 0:
        return "no-edge"
    if p_sharpe is not None and p_sharpe < 0.05 and consistency is not None and consistency >= 0.75:
        return "edge"
    return "inconclusive"


def refresh_validation(conn):
    """Monte-Carlo + walk-forward (backend/backtest/validation.py) on each
    strategy's closed paper trades. Report only: a verdict never arms or
    enables live on anything."""
    import pandas as pd
    from backend.backtest.models import TradeRecord
    from backend.backtest.validation import monte_carlo_test, walk_forward_analysis
    rows = conn.execute(
        """SELECT * FROM strategy_positions WHERE status='closed' AND pnl_pct IS NOT NULL
           AND entry_price > 0 AND closed_at IS NOT NULL ORDER BY strategy_id, closed_at""").fetchall()
    by_strat = {}
    for r in rows:
        by_strat.setdefault(r["strategy_id"], []).append(r)
    results = {}
    for sid, rs in by_strat.items():
        if len(rs) < MIN_VALIDATION_TRADES:
            continue
        recs = [TradeRecord(
            symbol=r["symbol"] or r["token_mint"], direction=1, entry_price=r["entry_price"],
            exit_price=r["exit_price"] or r["entry_price"] * (1 + r["pnl_pct"] / 100),
            entry_time=pd.Timestamp(r["opened_at"] or r["closed_at"], unit="s"),
            exit_time=pd.Timestamp(r["closed_at"], unit="s"),
            size=r["entry_amount_usd"] / r["entry_price"], leverage=1.0,
            pnl=r["entry_amount_usd"] * r["pnl_pct"] / 100, pnl_pct=r["pnl_pct"],
            exit_reason="paper", holding_bars=0, commission=0.0, entry_margin=r["entry_amount_usd"])
            for r in rs]
        capital = max(sum(r["entry_amount_usd"] for r in rs) / len(rs) * 10, 1.0)
        mc = monte_carlo_test(recs, capital, n_simulations=500)
        ordered = sorted(recs, key=lambda t: t.exit_time)
        equity = capital + pd.Series([t.pnl for t in ordered],
                                     index=pd.DatetimeIndex([t.exit_time for t in ordered])).cumsum()
        try:
            wf = walk_forward_analysis(equity, recs, n_windows=max(2, min(4, len(recs) // 3)))
        except Exception as e:
            wf = {"error": str(e)[:200]}
        avg = sum(r["pnl_pct"] for r in rs) / len(rs)
        verdict = validation_verdict(avg, mc.get("p_value_sharpe"), wf.get("consistency_rate"))
        conn.execute(
            "INSERT OR REPLACE INTO strategy_validation VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))",
            (sid, len(rs), verdict, round(avg, 3), mc.get("p_value_sharpe"), mc.get("p_value_max_dd"),
             wf.get("consistency_rate"), json.dumps(mc, default=str), json.dumps(wf, default=str)))
        results[sid] = verdict
    conn.commit()
    return results


# ── Main loop ───────────────────────────────────────────────────────────────
def cycle(conn, state):
    strategies = [dict(r) for r in conn.execute("SELECT * FROM trading_strategies ORDER BY id").fetchall()]
    needs_pool = any(processor_for(s["strategy_type"]) not in (None, process_sma) for s in strategies)
    pool = build_candidate_pool(conn) if needs_pool else []
    # Open on-chain positions (Solana mints are 32-44 chars; SMA positions hold tickers).
    held = {r[0] for r in conn.execute(
        "SELECT DISTINCT token_mint FROM strategy_positions WHERE status='open' AND length(token_mint) > 20")}
    prices = {m: pair_price(p) for m, p in dexscreener_pairs(sorted(held)).items()}
    prices.update({c["mint"]: c["price"] for c in pool})
    log(f"cycle: {len(strategies)} strategies, pool={len(pool)} "
        f"(top: {', '.join(c['symbol'] for c in pool[:5]) or '-'}), live_switch={'ON' if global_live_enabled() else 'off'}")
    for strat in strategies:
        fn = processor_for(strat["strategy_type"])
        if fn is None:
            log_every(f"skip:{strat['id']}", 3600, f"  strategy {strat['id']} ({strat['strategy_type']}): no paper model, skipped")
            continue
        try:
            fn(conn, strat, json.loads(strat.get("config") or "{}"), pool, prices)
        except Exception as e:
            log(f"  strategy {strat['id']} ({strat['strategy_type']}) error: {e}")
    now = time.time()
    if now - state.get("pine", 0) >= PINE_EVERY_S:
        state["pine"] = now
        try:
            if state.get("key_agent") is None:
                state["key_agent"] = vget("/api/copilot/whoami").get("id")
            n = run_pine(conn, state["key_agent"])
            log_every("pine-count", 3600, f"pine: {n} indicator(s) evaluated on {','.join(PINE_SYMBOLS)}")
        except Exception as e:
            log_every("pine", 1800, f"pine stage error: {e}")
    try:
        n = run_copy(conn)
        log_every("copy-count", 3600, f"copy-trade: {n} active subscription(s)")
    except Exception as e:
        log_every("copy", 1800, f"copy stage error: {e}")
    if now - state.get("stats", 0) >= STATS_EVERY_S:
        state["stats"] = now
        refresh_stats(conn)
        for r in conn.execute("SELECT * FROM strategy_paper_stats ORDER BY strategy_id"):
            log(f"stats strategy {r['strategy_id']}: open={r['n_open']} closed={r['n_closed']} "
                f"win_rate={r['win_rate']} avg_pnl={r['avg_pnl_pct']}% total=${r['total_pnl_usd']}")
    if now - state.get("validate", 0) >= VALIDATE_EVERY_S:
        state["validate"] = now
        try:
            for sid, verdict in refresh_validation(conn).items():
                log(f"validation strategy {sid}: {verdict}")
        except Exception as e:
            log(f"validation stage error: {e}")


def run(interval):
    log(f"Strategy Bots — {interval}s cycle — PAPER on every strategy; live only when "
        f"enabled+armed+live and {GLOBAL_LIVE_ENV}/live_mode is on (cost {PAPER_COST_BPS:.0f}bps/side)")
    state = {}
    while True:
        try:
            conn = db_conn()
            try:
                cycle(conn, state)
            finally:
                conn.close()
        except Exception as e:
            log(f"cycle error: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 60)
