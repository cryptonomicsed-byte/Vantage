"""P4-01 -- free Solana curve listener.

Subscribes to the pump.fun program's logs over a free Solana websocket
(`logsSubscribe`, mentions=[program]) and decodes every TradeEvent straight
out of the "Program data:" log lines -- no per-transaction RPC call, so it
does not burn a rate-limited HTTP quota. Measured on the public endpoint:
~36 trade events/s, zero truncated logs.

Pinned layout (Anchor event, discriminator sha256("event:TradeEvent")[:8]),
verified against live transactions (sol matches the buyer's balance change
less fees/rent; timestamp == block time):

    0   [8]  discriminator  bddb7fd34ee661ee
    8   [32] mint
    40  u64  sol_amount       lamports
    48  u64  token_amount     raw (6 decimals)
    56  u8   is_buy
    57  [32] user
    89  i64  timestamp
    97  u64  virtual_sol_reserves
    105 u64  virtual_token_reserves
    ... newer program versions append fields; they are ignored.

Storage is its own SQLite file (CURVE_DB, default data/curve.db), not
vantage.db: ~3M trades/day would otherwise contend for the main write lock.
Retention (measured ~365 B/row): every mint's first KEEP_FIRST (100) trades
are kept FIRST_DAYS (7); after that only for mints that reached KEEP_MCAP_USD
(~graduation) or are pinned as runners (P4-02). Later trades are kept
RECENT_HOURS (6). Without this it grows ~2 GB/day. curve_mints keeps a per-mint summary (first slot, whether we saw the
create, trades seen, last/max mcap).

Gaps: a disconnect longer than GAP_S is written to curve_gaps. Backfill is
best-effort over HTTP (getSignaturesForAddress + getTransaction, ~1 rps) and
each gap records how far it got; the coverage endpoint reports them as they
are, never as covered.

Backup: CURVE_USE_PUMPPORTAL=1 reads PumpPortal's free data websocket instead
(new tokens + their trades; no slot numbers, source 'pumpportal').

Flag: CURVE_LISTENER_ENABLED (default off), read each loop.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import sqlite3
import struct
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("vantage.curve")

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
TRADE_DISC = hashlib.sha256(b"event:TradeEvent").digest()[:8]
CREATE_DISC = hashlib.sha256(b"event:CreateEvent").digest()[:8]
TOTAL_SUPPLY_TOKENS = 1_000_000_000  # pump.fun mints 1B (6 decimals)
KEEP_FIRST = int(os.environ.get("CURVE_KEEP_FIRST", "100"))       # P4-06 scores the first 100
RECENT_HOURS = float(os.environ.get("CURVE_RECENT_HOURS", "6"))
FIRST_DAYS = float(os.environ.get("CURVE_FIRST_DAYS", "7"))
KEEP_MCAP_USD = float(os.environ.get("CURVE_KEEP_MCAP_USD", "25000"))  # ~graduation range
GAP_S = 20.0
FLUSH_S = 1.0
FLUSH_MAX = 500

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def enabled() -> bool:
    return os.environ.get("CURVE_LISTENER_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def use_pumpportal() -> bool:
    return os.environ.get("CURVE_USE_PUMPPORTAL", "0").strip().lower() in ("1", "true", "yes", "on")


def db_path() -> Path:
    p = os.environ.get("CURVE_DB")
    if p:
        return Path(p)
    base = os.environ.get("VANTAGE_DATA_DIR") or "data"
    return Path(base) / "curve.db"


# ── decode ──────────────────────────────────────────────────────────────────

def decode_trade_event(raw: bytes) -> Optional[Dict[str, Any]]:
    """TradeEvent bytes -> dict, or None if this isn't one."""
    if len(raw) < 113 or raw[:8] != TRADE_DISC:
        return None
    sol, tok = struct.unpack_from("<QQ", raw, 40)
    ts, vsol, vtok = struct.unpack_from("<qQQ", raw, 89)
    # Curves quoted in something other than SOL (BuyExactQuoteInV3 etc.)
    # carry 0 in the SOL fields; their market cap is not SOL-denominated.
    quote = "sol" if vsol else "other"
    return {
        "quote": quote,
        "mint": b58encode(raw[8:40]),
        "sol": sol / 1e9,
        "tokens": tok / 1e6,
        "side": "buy" if raw[56] else "sell",
        "wallet": b58encode(raw[57:89]),
        "timestamp": ts,
        "v_sol_reserves": vsol / 1e9,
        "v_token_reserves": vtok / 1e6,
    }


def mcap_usd(v_sol: float, v_tok: float, sol_usd: Optional[float]) -> Optional[float]:
    if not sol_usd or not v_tok or not v_sol:
        return None
    return (v_sol / v_tok) * TOTAL_SUPPLY_TOKENS * sol_usd


def events_from_logs(logs: List[str]) -> List[Dict[str, Any]]:
    out = []
    for line in logs or []:
        if not line.startswith("Program data: "):
            continue
        try:
            ev = decode_trade_event(base64.b64decode(line[14:]))
        except (ValueError, struct.error):
            continue
        if ev:
            out.append(ev)
    return out


def saw_create(logs: List[str]) -> bool:
    """A pump.fun create in this tx: its CreateEvent, or the Create/CreateV2
    instruction log. (Not CreateIdempotent -- that's the token-account program.)"""
    for l in logs or []:
        if l in ("Program log: Instruction: Create", "Program log: Instruction: CreateV2"):
            return True
        if l.startswith("Program data: "):
            try:
                if base64.b64decode(l[14:])[:8] == CREATE_DISC:
                    return True
            except ValueError:
                continue
    return False


# ── storage ─────────────────────────────────────────────────────────────────

DDL = [
    """CREATE TABLE IF NOT EXISTS curve_trades (
        sig TEXT NOT NULL, ix INTEGER NOT NULL,
        slot INTEGER, block_time INTEGER, mint TEXT NOT NULL, wallet TEXT NOT NULL,
        side TEXT NOT NULL, sol REAL, tokens REAL,
        v_sol_reserves REAL, v_token_reserves REAL, mcap_usd REAL,
        n_in_mint INTEGER, source TEXT NOT NULL DEFAULT 'rpc', quote TEXT NOT NULL DEFAULT 'sol',
        PRIMARY KEY (sig, ix))""",
    "CREATE INDEX IF NOT EXISTS curve_trades_mint ON curve_trades(mint, n_in_mint)",
    "CREATE INDEX IF NOT EXISTS curve_trades_time ON curve_trades(block_time)",
    """CREATE TABLE IF NOT EXISTS curve_mints (
        mint TEXT PRIMARY KEY, first_slot INTEGER, first_seen INTEGER,
        created_seen INTEGER NOT NULL DEFAULT 0, trades_seen INTEGER NOT NULL DEFAULT 0,
        last_mcap_usd REAL, max_mcap_usd REAL, max_mcap_at INTEGER, last_trade_at INTEGER,
        pinned INTEGER NOT NULL DEFAULT 0)""",
    "CREATE INDEX IF NOT EXISTS curve_mints_max ON curve_mints(max_mcap_usd)",
    """CREATE TABLE IF NOT EXISTS curve_gaps (
        id INTEGER PRIMARY KEY AUTOINCREMENT, start_ts REAL, end_ts REAL,
        start_slot INTEGER, end_slot INTEGER, last_sig TEXT,
        backfill TEXT NOT NULL DEFAULT 'pending', backfilled_events INTEGER NOT NULL DEFAULT 0,
        note TEXT)""",
]


def connect() -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    for d in DDL:
        c.execute(d)
    return c


def write_batch(c: sqlite3.Connection, rows: List[Dict[str, Any]], created: set) -> int:
    """Insert one batch in a single short transaction. Rows past a mint's
    first KEEP_FIRST are still written (pruned after RECENT_HOURS)."""
    if not rows:
        return 0
    wrote = 0
    with c:
        for r in rows:
            m = c.execute("SELECT trades_seen, max_mcap_usd FROM curve_mints WHERE mint=?", (r["mint"],)).fetchone()
            n = (m[0] if m else 0) + 1
            cur = c.execute(
                "INSERT OR IGNORE INTO curve_trades (sig, ix, slot, block_time, mint, wallet, side, sol, tokens, "
                "v_sol_reserves, v_token_reserves, mcap_usd, n_in_mint, source, quote) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["sig"], r["ix"], r.get("slot"), r.get("block_time"), r["mint"], r["wallet"], r["side"],
                 r.get("sol"), r.get("tokens"), r.get("v_sol_reserves"), r.get("v_token_reserves"),
                 r.get("mcap_usd"), n, r.get("source", "rpc"), r.get("quote", "sol")))
            if not cur.rowcount:
                continue  # already have it (backfill overlap)
            wrote += 1
            mc = r.get("mcap_usd")
            if m is None:
                c.execute(
                    "INSERT INTO curve_mints (mint, first_slot, first_seen, created_seen, trades_seen, last_mcap_usd, "
                    "max_mcap_usd, max_mcap_at, last_trade_at) VALUES (?,?,?,?,1,?,?,?,?)",
                    (r["mint"], r.get("slot"), r.get("block_time"), 1 if r["mint"] in created else 0,
                     mc, mc, r.get("block_time") if mc else None, r.get("block_time")))
            else:
                newmax = mc is not None and (m[1] is None or mc > m[1])
                c.execute(
                    "UPDATE curve_mints SET trades_seen=?, last_mcap_usd=COALESCE(?, last_mcap_usd), "
                    "max_mcap_usd=CASE WHEN ? THEN ? ELSE max_mcap_usd END, "
                    "max_mcap_at=CASE WHEN ? THEN ? ELSE max_mcap_at END, last_trade_at=? WHERE mint=?",
                    (n, mc, newmax, mc, newmax, r.get("block_time"), r.get("block_time"), r["mint"]))
    return wrote


def prune(c: sqlite3.Connection, now: Optional[float] = None) -> int:
    now = now or time.time()
    recent_cut = int(now - RECENT_HOURS * 3600)
    first_cut = int(now - FIRST_DAYS * 86400)
    with c:
        a = c.execute("DELETE FROM curve_trades WHERE block_time < ? AND n_in_mint > ?",
                      (recent_cut, KEEP_FIRST)).rowcount
        b = c.execute(
            "DELETE FROM curve_trades WHERE mint IN (SELECT mint FROM curve_mints WHERE first_seen < ? "
            "AND pinned=0 AND COALESCE(max_mcap_usd, 0) < ?)", (first_cut, KEEP_MCAP_USD)).rowcount
    return a + b


def pin(c: sqlite3.Connection, mint: str) -> None:
    """Keep this mint's early trades past FIRST_DAYS (P4-02 pins runners)."""
    with c:
        c.execute("UPDATE curve_mints SET pinned=1 WHERE mint=?", (mint,))


def rows_from_notification(value: dict, slot: Optional[int], sol_usd: Optional[float]) -> tuple:
    """A logsNotification value -> (rows, created_mints)."""
    if value.get("err"):
        return [], set()
    logs = value.get("logs") or []
    sig = value.get("signature")
    evs = events_from_logs(logs)
    rows = []
    for i, e in enumerate(evs):
        rows.append({**e, "sig": sig, "ix": i, "slot": slot, "block_time": e["timestamp"],
                     "mcap_usd": mcap_usd(e["v_sol_reserves"], e["v_token_reserves"], sol_usd),
                     "source": "rpc"})
    created = {e["mint"] for e in evs} if saw_create(logs) else set()
    return rows, created


# ── live state (read by the coverage endpoint) ───────────────────────────────

STATE: Dict[str, Any] = {
    "running": False, "connected": False, "source": None, "connected_since": None,
    "last_event_at": None, "last_slot": None, "last_sig": None,
    "events_session": 0, "reconnects": 0, "last_error": None, "sol_usd": None,
}


async def _sol_usd() -> Optional[float]:
    try:
        from .market_sources import resolve_price
        return await resolve_price("SOL")
    except Exception:  # noqa: BLE001
        return None


class _Writer:
    def __init__(self) -> None:
        self.buf: List[Dict[str, Any]] = []
        self.created: set = set()
        self.last_flush = time.time()
        self.last_prune = 0.0
        self.c: Optional[sqlite3.Connection] = None

    async def add(self, rows, created) -> None:
        self.buf.extend(rows)
        self.created |= created
        if len(self.buf) >= FLUSH_MAX or time.time() - self.last_flush >= FLUSH_S:
            await self.flush()

    async def flush(self) -> None:
        if not self.buf:
            self.last_flush = time.time()
            return
        rows, created = self.buf, self.created
        self.buf, self.created = [], set()
        self.last_flush = time.time()

        def _do():
            if self.c is None:
                self.c = connect()
            n = write_batch(self.c, rows, created)
            if time.time() - self.last_prune > 600:
                prune(self.c)
                self.last_prune = time.time()
            return n
        await asyncio.to_thread(_do)


async def _run_rpc(writer: _Writer) -> None:
    import websockets
    url = os.environ.get("CURVE_WS_URL", "wss://api.mainnet-beta.solana.com")
    async with websockets.connect(url, max_size=2 ** 23, ping_interval=20, ping_timeout=30) as ws:
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                                  "params": [{"mentions": [PUMP_PROGRAM]}, {"commitment": "confirmed"}]}))
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        if ack.get("result") is None:
            raise RuntimeError(f"logsSubscribe refused: {str(ack)[:200]}")
        STATE.update(connected=True, source="rpc", connected_since=time.time())
        while enabled() and not use_pumpportal():
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=FLUSH_S)
            except asyncio.TimeoutError:
                await writer.flush()
                continue
            m = json.loads(msg)
            res = (m.get("params") or {}).get("result") or {}
            value = res.get("value")
            if not value:
                continue
            slot = (res.get("context") or {}).get("slot")
            rows, created = rows_from_notification(value, slot, STATE["sol_usd"])
            if rows:
                STATE.update(last_event_at=time.time(), last_slot=slot, last_sig=value.get("signature"),
                             events_session=STATE["events_session"] + len(rows))
                await writer.add(rows, created)


async def _run_pumpportal(writer: _Writer) -> None:
    """Backup feed: PumpPortal's free data websocket. New tokens, then each
    token's trades (newest MAX_TRACKED). No slot numbers."""
    import websockets
    MAX_TRACKED = 400
    tracked: "OrderedDict[str, None]" = OrderedDict()
    async with websockets.connect("wss://pumpportal.fun/api/data", ping_interval=20) as ws:
        await ws.send(json.dumps({"method": "subscribeNewToken"}))
        STATE.update(connected=True, source="pumpportal", connected_since=time.time())
        seq = 0
        while enabled() and use_pumpportal():
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=FLUSH_S))
            except asyncio.TimeoutError:
                await writer.flush()
                continue
            mint = m.get("mint")
            if not mint:
                continue
            tx = m.get("txType")
            if tx == "create" and mint not in tracked:
                tracked[mint] = None
                await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))
                if len(tracked) > MAX_TRACKED:
                    old, _ = tracked.popitem(last=False)
                    await ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": [old]}))
            if tx not in ("create", "buy", "sell") or not m.get("traderPublicKey") or not m.get("signature"):
                continue
            vs, vt = m.get("vSolInBondingCurve"), m.get("vTokensInBondingCurve")
            seq += 1
            row = {"sig": m["signature"], "ix": 0, "slot": None, "block_time": int(time.time()),
                   "mint": mint, "wallet": m["traderPublicKey"], "side": "buy" if tx in ("buy", "create") else "sell",
                   "sol": m.get("solAmount"), "tokens": m.get("tokenAmount"),
                   "v_sol_reserves": vs, "v_token_reserves": vt,
                   "mcap_usd": mcap_usd(vs or 0, vt or 0, STATE["sol_usd"]), "source": "pumpportal"}
            if tx == "create" and not m.get("solAmount"):
                continue  # create with no dev buy is not a trade
            STATE.update(last_event_at=time.time(), events_session=STATE["events_session"] + 1)
            await writer.add([row], {mint} if tx == "create" else set())


def _open_gap(start_ts: float, slot: Optional[int], sig: Optional[str]) -> int:
    c = connect()
    try:
        with c:
            cur = c.execute("INSERT INTO curve_gaps (start_ts, start_slot, last_sig) VALUES (?,?,?)",
                            (start_ts, slot, sig))
        return int(cur.lastrowid)
    finally:
        c.close()


def _close_gap(gap_id: int, end_ts: float, slot: Optional[int]) -> None:
    c = connect()
    try:
        with c:
            c.execute("UPDATE curve_gaps SET end_ts=?, end_slot=? WHERE id=?", (end_ts, slot, gap_id))
    finally:
        c.close()


async def backfill_gap(gap_id: int, limit: int = 1000) -> Dict[str, Any]:
    """Best-effort HTTP backfill for one gap, ~1 rps. Records its outcome on
    the gap row ('done', 'partial', 'failed', 'skipped')."""
    import httpx
    c = connect()
    g = c.execute("SELECT start_ts, end_ts, last_sig FROM curve_gaps WHERE id=?", (gap_id,)).fetchone()
    c.close()
    if not g or not g[2]:
        _mark_gap(gap_id, "skipped", 0, "no last signature before the gap")
        return {"status": "skipped"}
    url = os.environ.get("CURVE_BACKFILL_RPC", "https://api.mainnet-beta.solana.com")
    got, status, note = 0, "done", ""
    sol_usd = STATE["sol_usd"] or await _sol_usd()
    writer = _Writer()
    try:
        async with httpx.AsyncClient(timeout=20) as cl:
            r = await cl.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
                                         "params": [PUMP_PROGRAM, {"until": g[2], "limit": limit}]})
            sigs = [s for s in (r.json().get("result") or []) if not s.get("err")]
            if len(sigs) >= limit:
                status, note = "partial", f"more than {limit} signatures in the gap; oldest part not fetched"
            for s in reversed(sigs):
                await asyncio.sleep(1.0)
                tr = await cl.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                                              "params": [s["signature"], {"maxSupportedTransactionVersion": 0,
                                                                          "encoding": "json"}]})
                if tr.status_code == 429:
                    status, note = "partial", "rate limited (429) during backfill"
                    break
                tx = (tr.json() or {}).get("result")
                if not tx:
                    continue
                logs = (tx.get("meta") or {}).get("logMessages") or []
                rows, created = rows_from_notification({"signature": s["signature"], "logs": logs,
                                                        "err": (tx.get("meta") or {}).get("err")},
                                                       tx.get("slot"), sol_usd)
                for row in rows:
                    row["block_time"] = tx.get("blockTime") or row["block_time"]
                    row["source"] = "backfill"
                got += len(rows)
                await writer.add(rows, created)
        await writer.flush()
    except Exception as e:  # noqa: BLE001
        status, note = "failed" if not got else "partial", str(e)[:300]
    _mark_gap(gap_id, status, got, note)
    return {"status": status, "events": got, "note": note}


def _mark_gap(gap_id: int, status: str, n: int, note: str) -> None:
    c = connect()
    try:
        with c:
            c.execute("UPDATE curve_gaps SET backfill=?, backfilled_events=?, note=? WHERE id=?",
                      (status, n, note, gap_id))
    finally:
        c.close()


async def listener_loop() -> None:
    """Lifespan task. Idle while the flag is off; reconnects with backoff."""
    writer = _Writer()
    backoff = 1.0
    down_since: Optional[float] = None
    gap_id: Optional[int] = None
    last_price = 0.0
    while True:
        if not enabled():
            STATE.update(running=False, connected=False)
            await asyncio.sleep(5)
            continue
        STATE["running"] = True
        if time.time() - last_price > 60:
            STATE["sol_usd"] = await _sol_usd() or STATE["sol_usd"]
            last_price = time.time()
        try:
            if gap_id is not None:
                _close_gap(gap_id, time.time(), STATE["last_slot"])
                asyncio.create_task(backfill_gap(gap_id))
                gap_id, down_since = None, None
            # refresh the SOL price on its own cadence while connected
            price_task = asyncio.create_task(_price_refresher())
            try:
                if use_pumpportal():
                    await _run_pumpportal(writer)
                else:
                    await _run_rpc(writer)
            finally:
                price_task.cancel()
            backoff = 1.0
        except asyncio.CancelledError:
            await writer.flush()
            raise
        except Exception as e:  # noqa: BLE001
            STATE.update(connected=False, last_error=f"{type(e).__name__}: {str(e)[:200]}",
                         reconnects=STATE["reconnects"] + 1)
            log.warning("curve listener disconnected: %s", e)
            await writer.flush()
            if down_since is None:
                down_since = time.time()
            if gap_id is None and time.time() - down_since >= GAP_S and STATE["last_sig"]:
                gap_id = await asyncio.to_thread(_open_gap, down_since, STATE["last_slot"], STATE["last_sig"])
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
        STATE["connected"] = False


async def _price_refresher() -> None:
    while True:
        await asyncio.sleep(60)
        STATE["sol_usd"] = await _sol_usd() or STATE["sol_usd"]


def coverage() -> Dict[str, Any]:
    """What the feed has actually covered -- read-only, no network."""
    now = time.time()
    out: Dict[str, Any] = {
        "enabled": enabled(), "source_pref": "pumpportal" if use_pumpportal() else "rpc",
        "state": {k: v for k, v in STATE.items()},
        "keep_first": KEEP_FIRST, "recent_hours": RECENT_HOURS,
    }
    if STATE["last_event_at"]:
        out["last_event_age_s"] = round(now - STATE["last_event_at"], 1)
    p = db_path()
    if not p.exists():
        out.update(db=False, trades=0, mints=0, gaps=[])
        return out
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    try:
        day = int(now - 86400)
        hour = int(now - 3600)
        q = lambda sql, a=(): c.execute(sql, a).fetchone()  # noqa: E731
        out.update(
            db=True, db_bytes=p.stat().st_size,
            trades=q("SELECT COUNT(*) FROM curve_trades")[0],
            trades_1h=q("SELECT COUNT(*) FROM curve_trades WHERE block_time >= ?", (hour,))[0],
            mints=q("SELECT COUNT(*) FROM curve_mints")[0],
            mints_24h=q("SELECT COUNT(*) FROM curve_mints WHERE first_seen >= ?", (day,))[0],
            created_seen_24h=q("SELECT COUNT(*) FROM curve_mints WHERE first_seen >= ? AND created_seen=1", (day,))[0],
            first_block_time=q("SELECT MIN(block_time) FROM curve_trades")[0],
            last_block_time=q("SELECT MAX(block_time) FROM curve_trades")[0],
            by_source={s: n for s, n in c.execute("SELECT source, COUNT(*) FROM curve_trades GROUP BY source")},
        )
        cols = ["id", "start_ts", "end_ts", "start_slot", "end_slot", "backfill", "backfilled_events", "note"]
        out["gaps"] = [dict(zip(cols, r)) for r in c.execute(
            "SELECT id, start_ts, end_ts, start_slot, end_slot, backfill, backfilled_events, note "
            "FROM curve_gaps ORDER BY id DESC LIMIT 20")]
    except sqlite3.OperationalError as e:
        out.update(db=True, error=str(e))
    finally:
        c.close()
    return out
