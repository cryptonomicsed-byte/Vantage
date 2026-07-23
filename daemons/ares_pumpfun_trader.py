#!/opt/ares/venv/bin/python3
"""ares_pumpfun_trader — Execute Pump.fun token buys via Jupiter.

Rewritten: was querying trading_signals WHERE source='pumpfun', a value
NOTHING has ever written — this daemon has been running for days as a
complete no-op regardless of its (also hardcoded/stale) Helius key. Real
pump.fun-flavored signals land in signal_pool (the intel pool) from
degen_alpha_fusion.py and ogun_multiscan.py's degen scan, both of which
also just had a mint field added end-to-end — this now reads that.

Also fixes: symbol was passed as 'TICKER/USDC' into the order, not a
resolvable mint, so a "successful" order could never actually swap.
Also fixes: the rug check flagged mint authority alone (normal for every
pre-migration pump.fun token) as high-risk, same bug found and fixed in
degen_alpha_fusion.py — only freeze authority is a real red flag here.
Also fixes: create_order() never called execute-live, so even a
successfully created order just sat pending forever.

Requires a wallet set explicitly — deliberately does not guess one.
Fixing these bugs should not, by itself, turn on live auto-buying; that's
a separate, explicit choice, now made via the app's wallet-picker UI
(Strategies drawer → daemon settings) instead of a systemd env file —
polled from GET /api/trading/daemon-settings/pumpfun_trader_wallet_id
each cycle so changing it in the UI takes effect within one cycle, no
restart needed. PUMPFUN_TRADER_WALLET_ID env var still works as a
fallback if the API is unreachable.

Usage: ares_pumpfun_trader.py [--daemon]
"""
import time, json, sqlite3, os, sys, signal, urllib.request, urllib.error
import sys as _vshim_sys
_vshim_sys.path.insert(0, "/opt/ares")
import vantage_db_shim as _vshim

sys.path.insert(0, "/opt/ares")
import api_key_pool

DB = "/opt/ares/Vantage/data/vantage.db"
TASK_NAME = "ares_pumpfun_trader"
VANTAGE_BASE = os.environ.get("VANTAGE_URL", "http://localhost:8001")
ORDERS_URL = f"{VANTAGE_BASE}/api/trading/orders"
DAEMON_SETTING_URL = f"{VANTAGE_BASE}/api/trading/daemon-settings/pumpfun_trader_wallet_id"
TOOL_TRADING_KEY = os.environ.get("VANTAGE_TOOL_TRADING_KEY", os.environ.get("VANTAGE_TOOL_TRADING", ""))
VANTAGE_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()


def get_wallet_id():
    """Poll the DB-backed setting first (what the UI writes), fall back to
    the env var if the API is unreachable, empty string if neither is set
    — same "explicit choice required" behavior as before, just sourced
    from a place a human can actually change without SSH."""
    try:
        req = urllib.request.Request(
            DAEMON_SETTING_URL,
            headers={"X-Vantage-Tool": "trading", "X-Vantage-Tool-Key": TOOL_TRADING_KEY},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        if resp.get("value"):
            return str(resp["value"])
    except Exception:
        pass
    return os.environ.get("PUMPFUN_TRADER_WALLET_ID", "")

TRADING_ENABLED_URL = f"{VANTAGE_BASE}/api/trading/daemon-settings/pumpfun_trader_trading_enabled"

def is_trading_enabled():
    """Fail-closed live-trading gate, polled fresh each cycle (same
    pattern as get_wallet_id) -- absent or unreadable means DISABLED,
    never enabled. This is the toggle the app's Trade Execution section
    controls; previously this daemon executed the moment its systemd
    service was running, with no way to arm/disarm it short of stopping
    the process."""
    try:
        req = urllib.request.Request(
            TRADING_ENABLED_URL,
            headers={"X-Vantage-Tool": "trading", "X-Vantage-Tool-Key": TOOL_TRADING_KEY},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        return resp.get("value") == "1"
    except Exception:
        return False

# ── Degen Safety Filters ───────────────────────────────────────────
TRADE_AMOUNT_SOL = 0.01      # 0.01 SOL per trade (~$0.80)

# ── Hard Limits (prevents runaway trading) ─────────────────────────
MAX_DAILY_SOL = 0.5           # Hard cap: max SOL spent per calendar day
MAX_OPEN_POSITIONS = 5        # Max concurrent unfilled orders
SIGNAL_SOURCES = ("degen_alpha_fusion", "ogun_degen")


def _helius_key_only():
    return api_key_pool.get_key("helius", TASK_NAME) or os.environ.get("HELIUS_API_KEY", "")


def _birdeye_key():
    return api_key_pool.get_key("birdeye", TASK_NAME) or os.environ.get("BIRDEYE_API_KEY", "")


def _all_birdeye_cooling():
    """True only when every key in the pool is currently on cooldown —
    used to skip a whole cycle's worth of doomed Birdeye calls instead of
    hammering a key everyone already knows is rate-limited. Real bug this
    fixes: 5 signals checked back-to-back in under a second burned through
    all 3 keys' quota simultaneously, then all 3 came off cooldown at the
    same moment and repeated the burst — never actually recovering."""
    try:
        status = api_key_pool.pool_status("birdeye")
        return bool(status) and all(k["cooling_down"] for k in status)
    except Exception:
        return False


def _dexscreener_price(mint):
    """Free, no-key fallback price source — already proven reliable
    elsewhere in this codebase (social_tracker.py's PnL backtracking uses
    the identical call). Used when Birdeye's pool is fully exhausted so a
    real rate-limit outage doesn't block every buy for the next hour."""
    try:
        req = urllib.request.Request(
            f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
            headers={"User-Agent": "Vantage/1.0"}
        )
        data = json.loads(urllib.request.urlopen(req, timeout=8).read().decode())
        pairs = data.get("pairs") or []
        if not pairs:
            return None
        best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
        return float(best["priceUsd"]) if best.get("priceUsd") else None
    except Exception:
        return None


def daily_spent_sol():
    db = _vshim.get_sync_db()
    spent = db.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM trading_orders WHERE notes LIKE '%pumpfun%' AND date(created_at)=date('now')"
    ).fetchone()[0]
    db.close()
    return float(spent) if spent else 0.0


# ── DB Helpers ──────────────────────────────────────────────────────
def get_pending_signals():
    """Real BUY signals with a real mint, from the intel signal pool —
    not the disconnected trading_signals table this used to query."""
    db = _vshim.get_sync_db()
    placeholders = ",".join("?" * len(SIGNAL_SOURCES))
    rows = db.execute(f"""
        SELECT id, symbol, mint, direction, conviction
        FROM signal_pool
        WHERE source IN ({placeholders}) AND direction='BUY' AND mint != ''
        AND ts > CAST(strftime('%s','now','-1 hour') AS INTEGER)
        ORDER BY conviction DESC LIMIT 5
    """, SIGNAL_SOURCES).fetchall()
    db.close()
    return rows


def has_been_executed(sig_id):
    db = _vshim.get_sync_db()
    r = db.execute("SELECT id FROM trading_orders WHERE notes LIKE ?", (f"%sig_{sig_id}%",)).fetchone()
    db.close()
    return r is not None


def open_positions_count():
    db = _vshim.get_sync_db()
    n = db.execute("SELECT COUNT(*) FROM trading_orders WHERE status='pending'").fetchone()[0]
    db.close()
    return n


def create_and_execute_order(mint, symbol, conviction, sig_id, wallet_id):
    """Create a buy order via Vantage's trading API, then immediately
    execute it live — creating alone does nothing, same gap already found
    and fixed today in ExecutionPanel.tsx/telegram_webhook.py/
    degen_alpha_fusion.py's snipe_token()."""
    payload = json.dumps({
        "symbol": mint,
        "side": "buy",
        "order_type": "market",
        "quantity": TRADE_AMOUNT_SOL,
        "chain": "solana",
        "wallet_id": int(wallet_id),
        "notes": f"Pumpfun degen buy — sig_{sig_id} — {symbol} — conviction={conviction:.2f}",
    }).encode()
    try:
        req = urllib.request.Request(ORDERS_URL, data=payload, headers={
            "Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY,
        })
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        order_id = resp.get("id", resp.get("order_id"))
        if not order_id:
            print(f"  ❌ Order creation failed: {resp}")
            return None
        exec_req = urllib.request.Request(
            f"{VANTAGE_BASE}/api/trading/orders/{order_id}/execute-live",
            data=b"", method="POST",
            headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY},
        )
        exec_resp = json.loads(urllib.request.urlopen(exec_req, timeout=15).read().decode())
        print(f"  ✅ Order #{order_id} executed — tx {exec_resp.get('tx_hash','?')}")
        return order_id
    except urllib.error.HTTPError as e:
        print(f"  ❌ Order/execute failed: HTTP {e.code} {e.read().decode(errors='ignore')[:200]}")
        return None
    except Exception as e:
        print(f"  ❌ Order failed: {e}")
        return None


def _get_price(mint):
    """Real price via Birdeye, or DexScreener when the whole Birdeye pool
    is cooling — kept as its own function with its own try/except so a
    failure here is never misattributed to the Helius call below (a real
    bug: one shared try/except around both calls was reporting every
    Helius error as a Birdeye rate-limit, corrupting Birdeye's cooldown
    state for errors it never actually caused)."""
    if _all_birdeye_cooling():
        price = _dexscreener_price(mint)
        if price:
            print(f"  (Birdeye pool exhausted — used DexScreener fallback for price)")
            return price
        # DexScreener has no pair for pre-migration bonding-curve tokens
        # that haven't hit a DEX yet — falls through to try Birdeye anyway
        # rather than give up, since a cooling key can still occasionally
        # succeed (cooldowns aren't perfectly synced across keys).
    birdeye_key = _birdeye_key()
    try:
        req = urllib.request.Request(
            f"https://public-api.birdeye.so/defi/price?address={mint}",
            headers={"X-API-KEY": birdeye_key, "accept": "application/json"}
        )
        price_data = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        return float(price_data.get("data", {}).get("value", 0))
    except urllib.error.HTTPError as e:
        api_key_pool.report_error("birdeye", birdeye_key, e.code, e.read().decode(errors="ignore")[:200])
        raise


def check_degen_filters(mint):
    """Run safety checks before executing a buy."""
    try:
        price = _get_price(mint)
    except urllib.error.HTTPError as e:
        return False, f"Price check failed: HTTP {e.code} (Birdeye)"
    except Exception as e:
        return False, f"Price check failed: {e}"

    if not price:
        return False, "No price data — likely dead or unpriced"

    helius_key = _helius_key_only()
    try:
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
            "params": [mint, {"encoding": "jsonParsed"}]
        }).encode()
        req = urllib.request.Request(
            f"https://mainnet.helius-rpc.com/?api-key={helius_key}",
            data=payload, headers={"Content-Type": "application/json"}
        )
        acct_data = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
    except urllib.error.HTTPError as e:
        api_key_pool.report_error("helius", helius_key, e.code, e.read().decode(errors="ignore")[:200])
        return False, f"Freeze-authority check failed: HTTP {e.code} (Helius)"
    except Exception as e:
        return False, f"Freeze-authority check failed: {e}"

    info = acct_data.get("result", {}).get("value", {}).get("data", {}).get("parsed", {}).get("info", {}) or {}

    # Mint authority alone is normal for every pre-migration pump.fun
    # token (the program itself holds it, not the deployer) — treating
    # it as a hard block was skipping ~100% of what this bot targets,
    # same bug found and fixed in degen_alpha_fusion.py's rug_check().
    # Freeze authority is the actually dangerous primitive.
    has_freeze_auth = bool(info.get("freezeAuthority"))
    if has_freeze_auth:
        return False, "Freeze authority active — holder can freeze buyer wallets (real rug/honeypot risk)"

    return True, f"Safe — no freeze auth, price=${price:.8f}"


# ── Main Loop ───────────────────────────────────────────────────────
def run():
    print("═══ ares_pumpfun_trader v3 ═══")
    print(f"  Trade size:  {TRADE_AMOUNT_SOL:.3f} SOL each")
    print(f"  Daily cap:   {MAX_DAILY_SOL:.2f} SOL")
    print(f"  Max open:    {MAX_OPEN_POSITIONS} positions")
    print(f"  Signal sources: {SIGNAL_SOURCES}")
    print(f"  Wallet setting polled from: {DAEMON_SETTING_URL}")

    while True:
        try:
            wallet_id = get_wallet_id()  # polled fresh each cycle — UI changes take effect within one cycle
            if daily_spent_sol() >= MAX_DAILY_SOL:
                time.sleep(300)
                continue
            open_count = open_positions_count()
            if open_count >= MAX_OPEN_POSITIONS:
                time.sleep(60)
                continue

            signals = get_pending_signals()
            if signals:
                print(f"\n  [{time.strftime('%H:%M:%S')}] {len(signals)} pending pumpfun signals" + ("" if wallet_id else " (no wallet set — execution will be skipped)"))

            for sig_id, symbol, mint, direction, conviction in signals:
                executed = has_been_executed(sig_id)
                if executed:
                    continue

                print(f"\n  Evaluating: {symbol} ({mint[:8]}…) conv={conviction:.2f}")
                safe, reason = check_degen_filters(mint)
                print(f"  Degen check: {'✅' if safe else '❌'} {reason}")
                if not safe:
                    continue

                if not wallet_id:
                    print(f"  🚫 EXECUTION SKIPPED: no wallet set — pick one in the app (Strategies → daemon settings)")
                    continue
                if not is_trading_enabled():
                    print(f"  🚫 EXECUTION SKIPPED: trading disabled — enable in the app (Trade Execution → daemon toggles)")
                    continue

                print(f"  🚀 EXECUTING: Buy {TRADE_AMOUNT_SOL:.3f} SOL of {symbol}")
                create_and_execute_order(mint, symbol, conviction, sig_id, wallet_id)

                # Spread checks across the cycle instead of bursting all 5
                # in under a second — that burst pattern was what exhausted
                # all 3 Birdeye keys simultaneously every cycle.
                time.sleep(3)

            time.sleep(30)

        except Exception as e:
            print(f"  ⚠️ Loop error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    run()
