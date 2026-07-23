#!/opt/ares/venv/bin/python3
"""pumpfun_scalp_manager — buys pre-migration pump.fun tier-scanner
candidates and manages their exits per the owner's exact spec:

    -60%  -> stop, sell everything remaining
    +100% -> sell 50% of the original buy (lock in initial investment)
    +200% -> sell 25% of the original buy
    +500% -> sell 10% of the original buy
    remaining 15% -> moonbag, held forever (no further scheduled sells)

Candidates are discovered via pumpfun_premigration_tokens (pumpfun_tier_scanner.py's
live table) for tiering/scoring, but PRICE for anything we actually hold
money in is read directly on-chain every cycle (get_current_mcap_usd) --
NOT from that table. Found live and the hard way: PumpPortal's per-mint
trade subscription doesn't survive reconnects, and it also misses trades
routed through aggregators (GMGN confirmed live) -- so its mcap tracking
can silently freeze while a position's on-chain price keeps moving. Three
real positions blew through the -60% stop loss undetected for hours
before this was caught and fixed. Direct on-chain reads (bonding-curve
account pre-migration, Jupiter quote post-migration) don't depend on any
one WebSocket feed's coverage or uptime.

Reuses the exact same wallet + trading-enabled daemon-settings gate the
owner already armed for ares_pumpfun_trader.py (pumpfun_trader_wallet_id /
pumpfun_trader_trading_enabled) -- one on/off switch controls both bots,
same fail-closed behavior (absent/unreadable = disabled).

Buys and sells both go through Vantage's own orders API + execute-live,
exactly like ares_pumpfun_trader.py -- that endpoint already handles
Jupiter quoting/signing/broadcast with Chainstack+Helius RPC redundancy.
"""
import time, json, os, sys, signal, struct, base64, urllib.request, urllib.error

import sys as _vshim_sys
_vshim_sys.path.insert(0, "/opt/ares")
import vantage_db_shim as _vshim

from solders.pubkey import Pubkey

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

PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
PUMPFUN_TOKEN_DECIMALS = 6  # protocol-fixed for every pump.fun-launched token
SOL_MINT = "So11111111111111111111111111111111111111112"
SOL_PRICE_REFRESH_SECONDS = 60
_sol_price_cache = {"value": 150.0, "updated_at": 0.0}


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
    a transient error worth retrying there. This is now a genuine last
    resort (get_buy_amount_from_tx covers the common case via Chainstack's
    getTransaction instead) -- a single attempt, no in-call backoff sleep:
    blocking this connection open for up to 20s waiting on a shared,
    routinely-429'd key was itself colliding with this same connection's
    own busy_timeout window on the next write, wedging every cycle. The
    daemon's normal 20s cycle already provides the retry cadence."""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
        "params": [owner, {"mint": mint}, {"encoding": "jsonParsed"}],
    }).encode()
    try:
        req = urllib.request.Request(
            f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}",
            data=payload, headers={"Content-Type": "application/json"},
        )
        body = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        if "result" in body:
            return body
        raise RpcCheckFailed(f"getTokenAccountsByOwner error: {body.get('error')}")
    except RpcCheckFailed:
        raise
    except Exception as e:
        raise RpcCheckFailed(f"getTokenAccountsByOwner failed: {e}")


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


CHAINSTACK_PROXY = "http://localhost:9861"


def fetch_sol_usd_price() -> float:
    """Vantage's own multi-source price endpoint (has its own fallback
    chain already), CoinGecko as a second-level fallback, cached value if
    both fail. Same approach as pumpfun_tier_scanner.py's fetcher."""
    try:
        req = urllib.request.Request(
            "http://localhost:8001/api/trading/markets/SOL/price",
            headers={"User-Agent": "Vantage/1.0"},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        price = float(data.get("price", 0) or 0)
        if price > 0:
            return price
    except Exception:
        pass
    try:
        req = urllib.request.Request(
            "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd",
            headers={"User-Agent": "Vantage/1.0"},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=8).read().decode())
        price = float(data.get("solana", {}).get("usd", 0))
        return price if price > 0 else _sol_price_cache["value"]
    except Exception:
        return _sol_price_cache["value"]


def get_sol_usd_price() -> float:
    if time.time() - _sol_price_cache["updated_at"] > SOL_PRICE_REFRESH_SECONDS:
        _sol_price_cache["value"] = fetch_sol_usd_price()
        _sol_price_cache["updated_at"] = time.time()
    return _sol_price_cache["value"]


def get_bonding_curve_state(mint: str) -> dict | None:
    """Reads pump.fun's own on-chain bonding-curve account directly --
    ground truth regardless of whether any WebSocket feed saw the trade
    that produced it. Returns None if the account doesn't exist (e.g. not
    a pump.fun-launched mint) or the read fails."""
    try:
        mint_pk = Pubkey.from_string(mint)
        bonding_curve, _ = Pubkey.find_program_address([b"bonding-curve", bytes(mint_pk)], PUMP_PROGRAM)
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
            "params": [str(bonding_curve), {"encoding": "base64"}],
        }).encode()
        req = urllib.request.Request(
            f"{CHAINSTACK_PROXY}/api/rpc/solana", data=payload,
            headers={"Content-Type": "application/json"},
        )
        body = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        val = (body.get("result") or {}).get("value")
        if not val:
            return None
        raw = base64.b64decode(val["data"][0])
        if len(raw) < 8 + 40 + 1:
            return None
        vt, vs, rt, rs, supply = struct.unpack_from("<QQQQQ", raw, 8)
        complete = bool(raw[8 + 40])
        return {"vt": vt, "vs": vs, "rt": rt, "rs": rs, "supply": supply, "complete": complete}
    except Exception as e:
        log(f"    bonding-curve read failed for {mint[:8]}...: {e}")
        return None


def get_jupiter_price_sol(mint: str) -> float | None:
    """Quote selling 1 whole token (1e6 raw units -- every pump.fun token
    is 6 decimals) for SOL via Jupiter -- the same aggregator used for
    real trades, so it reflects whatever AMM the token now actually lives
    on post-migration. Returns SOL per whole token, or None on failure."""
    try:
        req = urllib.request.Request(
            "https://api.jup.ag/swap/v1/quote"
            f"?inputMint={mint}&outputMint={SOL_MINT}&amount={10**PUMPFUN_TOKEN_DECIMALS}&slippageBps=300",
            headers={"User-Agent": "Vantage/1.0"},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=8).read().decode())
        out_amount = int(data.get("outAmount") or 0)
        return out_amount / 1e9 if out_amount > 0 else None
    except Exception as e:
        log(f"    Jupiter price quote failed for {mint[:8]}...: {e}")
        return None


def get_current_mcap_usd(mint: str) -> tuple[float, bool] | None:
    """Returns (mcap_usd, migrated) using direct on-chain data, or None if
    it can't be determined this cycle (never guess -- caller should just
    wait for the next cycle rather than act on a stale/fabricated value).
    Pre-migration: bonding-curve virtual reserves (mcap ratio == price
    ratio, since supply is fixed). Post-migration (curve 'complete'):
    reserves are zeroed by the program, so price comes from a live
    Jupiter quote against whatever AMM it graduated to instead."""
    state = get_bonding_curve_state(mint)
    if state is None:
        return None
    sol_usd = get_sol_usd_price()
    supply_ui = state["supply"] / (10 ** PUMPFUN_TOKEN_DECIMALS)
    if state["complete"]:
        price_sol = get_jupiter_price_sol(mint)
        if price_sol is None:
            return None
        return price_sol * supply_ui * sol_usd, True
    if state["vt"] <= 0:
        return None
    mcap_sol = (state["vs"] / state["vt"]) * state["supply"] / 1e9
    return mcap_sol * sol_usd, False


def get_buy_amount_from_tx(tx_hash: str, mint: str, owner: str) -> tuple[int, int] | None:
    """Derive the exact bought amount directly from the buy transaction's
    own postTokenBalances via Chainstack's getTransaction (works fine on
    this plan, unlike getTokenAccountsByOwner) -- avoids the shared,
    heavily-contended Helius key entirely for the common case. Returns
    None if the tx isn't confirmed yet or the mint/owner aren't in it."""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
        "params": [tx_hash, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
    }).encode()
    try:
        req = urllib.request.Request(
            f"{CHAINSTACK_PROXY}/api/rpc/solana", data=payload,
            headers={"Content-Type": "application/json"},
        )
        body = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    except Exception:
        return None
    result = body.get("result")
    if not result:
        return None  # not confirmed/found yet -- caller should retry later
    if (result.get("meta") or {}).get("err") is not None:
        return (0, 0)  # confirmed but the swap itself failed on-chain
    for bal in (result.get("meta") or {}).get("postTokenBalances") or []:
        if bal.get("owner") == owner and bal.get("mint") == mint:
            amt = bal["uiTokenAmount"]
            return int(amt["amount"]), int(amt["decimals"])
    return (0, 0)  # confirmed, no error, but owner holds none -- genuinely empty


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
            return None, None
        exec_req = urllib.request.Request(
            f"{VANTAGE_BASE}/api/trading/orders/{order_id}/execute-live",
            data=b"", method="POST",
            headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY},
        )
        exec_resp = json.loads(urllib.request.urlopen(exec_req, timeout=20).read().decode())
        tx_hash = exec_resp.get("tx_hash", "?")
        log(f"    order #{order_id} ({side}) executed -- tx {tx_hash}")
        return order_id, tx_hash
    except urllib.error.HTTPError as e:
        log(f"    order/execute failed: HTTP {e.code} {e.read().decode(errors='ignore')[:300]}")
        return None, None
    except Exception as e:
        log(f"    order failed: {e}")
        return None, None


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
    mint, symbol, stale_mcap, score = candidate

    # The tier table's mcap can be stale or simply wrong for this mint
    # (PumpPortal reconnect gaps, GMGN-routed trades it never saw) --
    # verify on-chain, right before spending real money, rather than
    # trust it blindly. Also skip anything that already migrated: this
    # strategy targets pre-migration bonding-curve plays specifically.
    result = get_current_mcap_usd(mint)
    if result is None:
        log(f"  candidate {symbol} ({mint[:8]}...) picked but on-chain price check failed -- skipping this cycle")
        return
    entry_mcap, migrated = result
    if migrated:
        log(f"  candidate {symbol} ({mint[:8]}...) already migrated on-chain -- skipping (not a pre-migration play anymore)")
        return

    log(f"  candidate: {symbol} ({mint[:8]}...) tier_mcap=${stale_mcap:.0f} onchain_mcap=${entry_mcap:.0f} score={score:.1f}")

    order_id, tx_hash = create_and_execute_order(
        mint, "buy", TRADE_AMOUNT_SOL, wallet_id,
        f"Pumpfun scalp entry -- {symbol} -- score={score:.1f} entry_mcap=${entry_mcap:.0f}",
    )
    if not order_id:
        return

    # Money is already spent (order_id above was actually broadcast) -- from
    # here on we must record SOMETHING no matter what, never silently drop
    # a real position. Prefer deriving the bought amount straight from the
    # buy tx's own postTokenBalances (Chainstack, uncontended) over a live
    # balance query (Helius, shared VPS-wide key, easily 429s) -- but the
    # tx may not be confirmed yet the instant after broadcast, so fall
    # back to pending_reconcile and let the next cycles retry either way.
    amount = get_buy_amount_from_tx(tx_hash, mint, wallet_addr) if tx_hash and tx_hash != "?" else None
    if amount is None:
        log(f"    buy broadcast (order #{order_id}, tx {tx_hash}) not confirmed yet -- "
            f"recording as pending_reconcile")
        conn.execute("""
            INSERT INTO pumpfun_scalp_positions
                (mint, symbol, wallet_id, status, entry_mcap_usd, entry_sol_spent,
                 entry_token_base_units, decimals, buy_order_id, buy_tx_hash, last_checked_at)
            VALUES (?, ?, ?, 'pending_reconcile', ?, ?, 0, 0, ?, ?, datetime('now'))
        """, (mint, symbol, wallet_id, entry_mcap, TRADE_AMOUNT_SOL, order_id, tx_hash))
        conn.commit()
        return

    held, decimals = amount
    if held <= 0:
        log(f"    buy tx confirmed (order #{order_id}) but the swap itself failed or left a 0 balance -- "
            f"recording as pending_reconcile for a later retry, not discarding")
        conn.execute("""
            INSERT INTO pumpfun_scalp_positions
                (mint, symbol, wallet_id, status, entry_mcap_usd, entry_sol_spent,
                 entry_token_base_units, decimals, buy_order_id, buy_tx_hash, notes, last_checked_at)
            VALUES (?, ?, ?, 'pending_reconcile', ?, ?, 0, 0, ?, ?, 'confirmed zero balance after buy', datetime('now'))
        """, (mint, symbol, wallet_id, entry_mcap, TRADE_AMOUNT_SOL, order_id, tx_hash))
        conn.commit()
        return

    conn.execute("""
        INSERT INTO pumpfun_scalp_positions
            (mint, symbol, wallet_id, status, entry_mcap_usd, entry_sol_spent,
             entry_token_base_units, decimals, buy_order_id, buy_tx_hash, last_checked_at)
        VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (mint, symbol, wallet_id, entry_mcap, TRADE_AMOUNT_SOL, held, decimals, order_id, tx_hash))
    conn.commit()
    log(f"    position opened: {held} base units ({decimals} dec) at entry mcap ${entry_mcap:.0f}")


def reconcile_pending(conn, wallet_addr: str):
    """Retry confirming positions that couldn't be resolved at buy time --
    promotes to a real 'open' position as soon as the tx confirms (or, if
    it has no tx_hash on record for some reason, falls back to a live
    Helius balance check). Never leaves real money unmanaged forever."""
    rows = conn.execute(
        "SELECT id, mint, buy_tx_hash FROM pumpfun_scalp_positions WHERE status='pending_reconcile'"
    ).fetchall()
    for pos_id, mint, tx_hash in rows:
        try:
            held = decimals = None
            if tx_hash and tx_hash != "?":
                amount = get_buy_amount_from_tx(tx_hash, mint, wallet_addr)
                if amount is not None:
                    held, decimals = amount
            if held is None:
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
        except Exception as e:
            log(f"  reconcile of position #{pos_id} ({mint[:8]}...) failed, rolling back: {e}")
            try:
                conn.rollback()
            except Exception:
                pass


def _manage_one_position(conn, wallet_addr, pos_id, mint, symbol, wallet_id,
                          entry_mcap, entry_base_units, decimals, t1, t2, t3, status):
    if not entry_mcap:
        return
    result = get_current_mcap_usd(mint)
    if result is None:
        return  # couldn't determine current price this cycle -- never guess, just wait
    current_mcap, migrated = result
    if migrated:
        # Best-effort only -- this daemon's own exit logic already uses
        # get_current_mcap_usd() directly regardless of this flag, but
        # keeping the tier table's own record in sync helps anything else
        # reading it (e.g. the /pumpfun/premigration API).
        try:
            conn.execute("UPDATE pumpfun_premigration_tokens SET migrated=1 WHERE mint=?", (mint,))
            conn.commit()
        except Exception:
            pass
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
            return
        if held <= 0:
            conn.execute(
                "UPDATE pumpfun_scalp_positions SET status='closed_stop', closed_at=datetime('now') WHERE id=?",
                (pos_id,),
            )
            conn.commit()
            return
        qty = held / (10 ** held_decimals)
        log(f"  STOP LOSS {symbol} ({mint[:8]}...) {pct_gain:.0f}% -- selling all remaining ({qty:.4f})")
        sell_order_id, _ = create_and_execute_order(mint, "sell", qty, wallet_id,
                                      f"Pumpfun scalp STOP LOSS -- {symbol} -- {pct_gain:.0f}%")
        if sell_order_id:
            conn.execute(
                "UPDATE pumpfun_scalp_positions SET status='closed_stop', stopped_out=1, closed_at=datetime('now') WHERE id=?",
                (pos_id,),
            )
            conn.commit()
        return

    done_flags = {"tranche1_done": t1, "tranche2_done": t2, "tranche3_done": t3}
    for field, threshold, fraction in TRANCHES:
        if done_flags[field]:
            continue
        if pct_gain < threshold:
            break  # tranches are sequential -- can't hit tier 2 logic before tier 1
        qty = (entry_base_units * fraction) / (10 ** decimals)
        log(f"  TAKE PROFIT {symbol} ({mint[:8]}...) +{pct_gain:.0f}% >= {threshold:.0f}% -- selling {fraction*100:.0f}% of original ({qty:.4f})")
        sell_order_id, _ = create_and_execute_order(mint, "sell", qty, wallet_id,
                                      f"Pumpfun scalp tranche {field} -- {symbol} -- +{pct_gain:.0f}%")
        if sell_order_id:
            new_status = "moonbag" if field == "tranche3_done" else status
            conn.execute(
                f"UPDATE pumpfun_scalp_positions SET {field}=1, status=? WHERE id=?",
                (new_status, pos_id),
            )
            conn.commit()
        break  # only ever act on one tranche per cycle per position


def manage_positions(conn, wallet_addr: str):
    rows = conn.execute("""
        SELECT id, mint, symbol, wallet_id, entry_mcap_usd, entry_token_base_units,
               decimals, tranche1_done, tranche2_done, tranche3_done, status
        FROM pumpfun_scalp_positions WHERE status IN ('open','moonbag')
    """).fetchall()

    for (pos_id, mint, symbol, wallet_id, entry_mcap, entry_base_units,
         decimals, t1, t2, t3, status) in rows:
        try:
            _manage_one_position(conn, wallet_addr, pos_id, mint, symbol, wallet_id,
                                  entry_mcap, entry_base_units, decimals, t1, t2, t3, status)
        except Exception as e:
            # One position's DB contention or transient error must never
            # abort evaluation of the rest -- found live: a single failed
            # commit here silently skipped every other open position for
            # the whole cycle, delaying real stop-loss/take-profit checks.
            log(f"  position #{pos_id} ({mint[:8]}...) check failed, rolling back: {e}")
            try:
                conn.rollback()
            except Exception:
                pass


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
