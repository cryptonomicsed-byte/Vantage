#!/opt/ares/venv/bin/python3
"""pumpfun_scalp_manager — buys pre-migration pump.fun tier-scanner
candidates and manages their exits per the owner's exact spec:

    -60%  -> stop, sell everything remaining
    +100% -> sell 50% of the original buy (lock in initial investment)
    +200% -> sell 25% of the original buy
    +500% -> sell 10% of the original buy
    remaining 15% -> moonbag, held forever (no further scheduled sells)

Candidates come from pumpfun_premigration_tokens (pumpfun_tier_scanner.py's
live table) -- real-time market cap already tracked there per trade, so
that table doubles as this daemon's price feed too (mcap ratio == price
ratio pre-migration, since token supply is fixed on the bonding curve).
No separate Birdeye/DexScreener price calls needed for open positions.

Reuses the exact same wallet + trading-enabled daemon-settings gate the
owner already armed for ares_pumpfun_trader.py (pumpfun_trader_wallet_id /
pumpfun_trader_trading_enabled) -- one on/off switch controls both bots,
same fail-closed behavior (absent/unreadable = disabled).

Buys and sells both go through Vantage's own orders API + execute-live,
exactly like ares_pumpfun_trader.py -- that endpoint already handles
Jupiter quoting/signing/broadcast with Chainstack+Helius RPC redundancy.
"""
import time, json, os, sys, signal, urllib.request, urllib.error

import sys as _vshim_sys
_vshim_sys.path.insert(0, "/opt/ares")
import vantage_db_shim as _vshim

VANTAGE_BASE = os.environ.get("VANTAGE_URL", "http://localhost:8001")
ORDERS_URL = f"{VANTAGE_BASE}/api/trading/orders"
WALLET_SETTING_URL = f"{VANTAGE_BASE}/api/trading/daemon-settings/pumpfun_trader_wallet_id"
ENABLED_SETTING_URL = f"{VANTAGE_BASE}/api/trading/daemon-settings/pumpfun_trader_trading_enabled"
TOOL_TRADING_KEY = os.environ.get("VANTAGE_TOOL_TRADING_KEY", os.environ.get("VANTAGE_TOOL_TRADING", ""))
VANTAGE_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()
HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "")

TRADE_AMOUNT_SOL = 0.01
MAX_DAILY_SOL = 0.3
MAX_OPEN_POSITIONS = 5
MIN_SCORE = 15.0                 # skip weak/likely-noise candidates
CYCLE_SECONDS = 20

TRANCHES = [
    ("tranche1_done", 100.0, 0.50),   # +100% -> sell 50% of original
    ("tranche2_done", 200.0, 0.25),   # +200% -> sell 25% of original
    ("tranche3_done", 500.0, 0.10),   # +500% -> sell 10% of original (-> moonbag)
]
STOP_LOSS_PCT = -60.0


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def db_conn():
    conn = _vshim.get_sync_db()
    conn.row_factory = None
    return conn


def _get_setting(url: str) -> str:
    try:
        req = urllib.request.Request(
            url, headers={"X-Vantage-Tool": "trading", "X-Vantage-Tool-Key": TOOL_TRADING_KEY},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        return str(resp.get("value") or "")
    except Exception:
        return ""


def get_wallet_id() -> str:
    return _get_setting(WALLET_SETTING_URL)


def is_trading_enabled() -> bool:
    return _get_setting(ENABLED_SETTING_URL) == "1"


class RpcCheckFailed(Exception):
    """Raised when a balance check could not be confirmed either way --
    callers must NEVER treat this as '0 held'. That exact silent-failure-
    as-zero bug was already found and fixed once this session in
    trading.py's execute_live_order; this is the same class of bug and
    gets the same fix here."""


def _rpc_getTokenAccountsByOwner(owner: str, mint: str) -> dict:
    """Helius only -- Chainstack's current plan hard-rejects this method
    entirely (-32602 'Method requires plan upgrade'), confirmed live, not
    a transient error worth retrying there. Helius gets its own retry/
    backoff since this key is shared across ~10+ daemons on this VPS and
    routinely 429s under load."""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
        "params": [owner, {"mint": mint}, {"encoding": "jsonParsed"}],
    }).encode()
    last_err = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}",
                data=payload, headers={"Content-Type": "application/json"},
            )
            body = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
            if "result" in body:
                return body
            last_err = body.get("error")
        except Exception as e:
            last_err = e
        time.sleep(2 * (attempt + 1))
    raise RpcCheckFailed(f"getTokenAccountsByOwner failed after retries: {last_err}")


def get_token_balance(owner: str, mint: str) -> tuple[int, int]:
    """Returns (base_units_held, decimals). Raises RpcCheckFailed on any
    real error -- NEVER silently returns (0, 0) for a check that failed,
    only for a check that genuinely succeeded and found nothing."""
    body = _rpc_getTokenAccountsByOwner(owner, mint)
    accounts = (body.get("result") or {}).get("value") or []
    held, decimals = 0, 0
    for acc in accounts:
        amt = acc["account"]["data"]["parsed"]["info"]["tokenAmount"]
        held += int(amt["amount"])
        decimals = int(amt["decimals"])
    return held, decimals


def get_wallet_address(wallet_id: str) -> str:
    conn = db_conn()
    row = conn.execute("SELECT address FROM trading_wallets WHERE id=?", (wallet_id,)).fetchone()
    conn.close()
    return row[0] if row else ""


def create_and_execute_order(mint: str, side: str, quantity: float, wallet_id: str, notes: str):
    payload = json.dumps({
        "symbol": mint, "side": side, "order_type": "market",
        "quantity": quantity, "chain": "solana", "wallet_id": int(wallet_id),
        "notes": notes,
    }).encode()
    try:
        req = urllib.request.Request(ORDERS_URL, data=payload, headers={
            "Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY,
        })
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        order_id = resp.get("id", resp.get("order_id"))
        if not order_id:
            log(f"    order creation failed: {resp}")
            return None
        exec_req = urllib.request.Request(
            f"{VANTAGE_BASE}/api/trading/orders/{order_id}/execute-live",
            data=b"", method="POST",
            headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY},
        )
        exec_resp = json.loads(urllib.request.urlopen(exec_req, timeout=20).read().decode())
        tx_hash = exec_resp.get("tx_hash", "?")
        log(f"    order #{order_id} ({side}) executed -- tx {tx_hash}")
        return order_id
    except urllib.error.HTTPError as e:
        log(f"    order/execute failed: HTTP {e.code} {e.read().decode(errors='ignore')[:300]}")
        return None
    except Exception as e:
        log(f"    order failed: {e}")
        return None


def daily_spent_sol(conn) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(entry_sol_spent),0) FROM pumpfun_scalp_positions WHERE date(opened_at)=date('now')"
    ).fetchone()
    return float(row[0]) if row else 0.0


def open_positions_count(conn) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM pumpfun_scalp_positions WHERE status IN ('open','moonbag')"
    ).fetchone()
    return int(row[0]) if row else 0


def pick_candidate(conn):
    """Best untouched pre-migration candidate: real net buy pressure,
    no manipulation flags, not already in an open position, highest
    score first."""
    rows = conn.execute("""
        SELECT mint, symbol, market_cap_usd, score
        FROM pumpfun_premigration_tokens
        WHERE evicted=0 AND migrated=0 AND tier != ''
          AND score >= ?
          AND manipulation_flags = '[]'
          AND buy_count > sell_count
          AND mint NOT IN (SELECT mint FROM pumpfun_scalp_positions WHERE status IN ('open','moonbag'))
        ORDER BY score DESC LIMIT 1
    """, (MIN_SCORE,)).fetchall()
    return rows[0] if rows else None


def try_buy(conn, wallet_id: str, wallet_addr: str):
    if daily_spent_sol(conn) >= MAX_DAILY_SOL:
        return
    if open_positions_count(conn) >= MAX_OPEN_POSITIONS:
        return

    candidate = pick_candidate(conn)
    if not candidate:
        return
    mint, symbol, entry_mcap, score = candidate
    log(f"  candidate: {symbol} ({mint[:8]}...) mcap=${entry_mcap:.0f} score={score:.1f}")

    order_id = create_and_execute_order(
        mint, "buy", TRADE_AMOUNT_SOL, wallet_id,
        f"Pumpfun scalp entry -- {symbol} -- score={score:.1f} entry_mcap=${entry_mcap:.0f}",
    )
    if not order_id:
        return

    # Money is already spent (order_id above was actually broadcast) -- from
    # here on we must record SOMETHING no matter what, never silently drop
    # a real position just because the balance check itself had trouble.
    try:
        held, decimals = get_token_balance(wallet_addr, mint)
    except RpcCheckFailed as e:
        log(f"    buy broadcast (order #{order_id}) but balance check failed ({e}) -- "
            f"recording as pending_reconcile instead of losing the position")
        conn.execute("""
            INSERT INTO pumpfun_scalp_positions
                (mint, symbol, wallet_id, status, entry_mcap_usd, entry_sol_spent,
                 entry_token_base_units, decimals, buy_order_id, notes, last_checked_at)
            VALUES (?, ?, ?, 'pending_reconcile', ?, ?, 0, 0, ?, ?, datetime('now'))
        """, (mint, symbol, wallet_id, entry_mcap, TRADE_AMOUNT_SOL, order_id, str(e)[:300]))
        conn.commit()
        return

    if held <= 0:
        log(f"    buy tx sent (order #{order_id}) but confirmed on-chain balance is genuinely 0 -- "
            f"recording as pending_reconcile (swap may have failed on-chain)")
        conn.execute("""
            INSERT INTO pumpfun_scalp_positions
                (mint, symbol, wallet_id, status, entry_mcap_usd, entry_sol_spent,
                 entry_token_base_units, decimals, buy_order_id, notes, last_checked_at)
            VALUES (?, ?, ?, 'pending_reconcile', ?, ?, 0, 0, ?, 'confirmed zero balance after buy', datetime('now'))
        """, (mint, symbol, wallet_id, entry_mcap, TRADE_AMOUNT_SOL, order_id))
        conn.commit()
        return

    conn.execute("""
        INSERT INTO pumpfun_scalp_positions
            (mint, symbol, wallet_id, status, entry_mcap_usd, entry_sol_spent,
             entry_token_base_units, decimals, buy_order_id, last_checked_at)
        VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, datetime('now'))
    """, (mint, symbol, wallet_id, entry_mcap, TRADE_AMOUNT_SOL, held, decimals, order_id))
    conn.commit()
    log(f"    position opened: {held} base units ({decimals} dec) at entry mcap ${entry_mcap:.0f}")


def reconcile_pending(conn, wallet_addr: str):
    """Retry balance checks for positions that couldn't be confirmed at
    buy time -- promotes to a real 'open' position as soon as the check
    succeeds, instead of leaving real money unmanaged forever."""
    rows = conn.execute(
        "SELECT id, mint FROM pumpfun_scalp_positions WHERE status='pending_reconcile'"
    ).fetchall()
    for pos_id, mint in rows:
        try:
            held, decimals = get_token_balance(wallet_addr, mint)
        except RpcCheckFailed as e:
            log(f"  reconcile retry failed for position #{pos_id} ({mint[:8]}...): {e}")
            continue
        if held <= 0:
            continue
        conn.execute(
            "UPDATE pumpfun_scalp_positions SET status='open', entry_token_base_units=?, decimals=? WHERE id=?",
            (held, decimals, pos_id),
        )
        conn.commit()
        log(f"  reconciled position #{pos_id} ({mint[:8]}...): {held} base units ({decimals} dec)")


def manage_positions(conn, wallet_addr: str):
    rows = conn.execute("""
        SELECT id, mint, symbol, wallet_id, entry_mcap_usd, entry_token_base_units,
               decimals, tranche1_done, tranche2_done, tranche3_done, status
        FROM pumpfun_scalp_positions WHERE status IN ('open','moonbag')
    """).fetchall()

    for (pos_id, mint, symbol, wallet_id, entry_mcap, entry_base_units,
         decimals, t1, t2, t3, status) in rows:

        cur = conn.execute(
            "SELECT market_cap_usd FROM pumpfun_premigration_tokens WHERE mint=?", (mint,)
        ).fetchone()
        if not cur or not cur[0] or not entry_mcap:
            continue  # no live price data this cycle -- never guess, just wait
        current_mcap = float(cur[0])
        pct_gain = (current_mcap - entry_mcap) / entry_mcap * 100.0

        conn.execute("UPDATE pumpfun_scalp_positions SET last_checked_at=datetime('now') WHERE id=?", (pos_id,))
        conn.commit()  # must land even on a quiet cycle where no branch below commits anything else

        # Stop loss -- sell everything actually held right now.
        if pct_gain <= STOP_LOSS_PCT:
            try:
                held, held_decimals = get_token_balance(wallet_addr, mint)
            except RpcCheckFailed as e:
                log(f"  STOP LOSS trigger for {symbol} but balance check failed ({e}) -- "
                    f"retrying next cycle rather than guessing")
                continue
            if held <= 0:
                conn.execute(
                    "UPDATE pumpfun_scalp_positions SET status='closed_stop', closed_at=datetime('now') WHERE id=?",
                    (pos_id,),
                )
                conn.commit()
                continue
            qty = held / (10 ** held_decimals)
            log(f"  STOP LOSS {symbol} ({mint[:8]}...) {pct_gain:.0f}% -- selling all remaining ({qty:.4f})")
            if create_and_execute_order(mint, "sell", qty, wallet_id,
                                          f"Pumpfun scalp STOP LOSS -- {symbol} -- {pct_gain:.0f}%"):
                conn.execute(
                    "UPDATE pumpfun_scalp_positions SET status='closed_stop', stopped_out=1, closed_at=datetime('now') WHERE id=?",
                    (pos_id,),
                )
                conn.commit()
            continue

        done_flags = {"tranche1_done": t1, "tranche2_done": t2, "tranche3_done": t3}
        for field, threshold, fraction in TRANCHES:
            if done_flags[field]:
                continue
            if pct_gain < threshold:
                break  # tranches are sequential -- can't hit tier 2 logic before tier 1
            qty = (entry_base_units * fraction) / (10 ** decimals)
            log(f"  TAKE PROFIT {symbol} ({mint[:8]}...) +{pct_gain:.0f}% >= {threshold:.0f}% -- selling {fraction*100:.0f}% of original ({qty:.4f})")
            if create_and_execute_order(mint, "sell", qty, wallet_id,
                                          f"Pumpfun scalp tranche {field} -- {symbol} -- +{pct_gain:.0f}%"):
                new_status = "moonbag" if field == "tranche3_done" else status
                conn.execute(
                    f"UPDATE pumpfun_scalp_positions SET {field}=1, status=? WHERE id=?",
                    (new_status, pos_id),
                )
                conn.commit()
            break  # only ever act on one tranche per cycle per position


def run():
    log("pumpfun_scalp_manager -- exit-strategy engine for pre-migration scalp plays")
    log(f"  trade size: {TRADE_AMOUNT_SOL} SOL, daily cap: {MAX_DAILY_SOL} SOL, max open: {MAX_OPEN_POSITIONS}")
    log("  stop -60% | +100% sell 50% | +200% sell 25% | +500% sell 10% | hold 15% moonbag")

    while True:
        try:
            wallet_id = get_wallet_id()
            if not wallet_id:
                log("  no wallet set -- idling")
                time.sleep(30)
                continue
            if not is_trading_enabled():
                log("  trading disabled -- idling")
                time.sleep(30)
                continue

            wallet_addr = get_wallet_address(wallet_id)
            if not wallet_addr:
                log(f"  wallet id {wallet_id} not found in trading_wallets -- idling")
                time.sleep(30)
                continue

            conn = db_conn()
            try:
                reconcile_pending(conn, wallet_addr)
                manage_positions(conn, wallet_addr)
                try_buy(conn, wallet_id, wallet_addr)
            finally:
                conn.close()

            time.sleep(CYCLE_SECONDS)
        except Exception as e:
            log(f"  loop error: {e}")
            time.sleep(30)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    run()
