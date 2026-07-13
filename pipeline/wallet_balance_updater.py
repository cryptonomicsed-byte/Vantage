#!/usr/bin/env python3
"""
Wallet Balance Updater — Polls Helius RPC for SOL balances, updates Vantage DB.

Runs as a daemon (default 60s). No private keys stored. Beyond refreshing each
wallet's SOL balance, it now:
  • writes a real trading_balances row per wallet (token=SOL, value_usd), so the
    Portfolio's holdings/net-worth read from actual on-chain balances; and
  • upserts today's PnL snapshot per agent from linked-wallet net worth, keeping
    the equity curve auto-synced with no manual entry.
"""
import os, urllib.request, urllib.error, json, sqlite3, time, sys
from datetime import datetime, timezone, date

sys.path.insert(0, "/opt/ares")
import api_key_pool

TASK_NAME = "wallet_balance_updater"
DB_PATH = os.environ.get("DB_PATH", "/opt/ares/Vantage/data/vantage.db")
INTERVAL = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("WALLET_POLL_SECONDS", "60"))


def _helius_key():
    return api_key_pool.get_key("helius", TASK_NAME) or os.environ.get("HELIUS_API_KEY", "")


def get_sol_balance(address):
    """Get SOL balance (in SOL) via Helius RPC."""
    key = _helius_key()
    if not key:
        return None
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getBalance",
                          "params": [address]}).encode()
    req = urllib.request.Request(
        f"https://mainnet.helius-rpc.com/?api-key={key}",
        data=payload, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read().decode()).get("result", {}).get("value", 0) / 1e9
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        api_key_pool.report_error("helius", key, e.code, body)
        return None
    except Exception:
        return None


def get_sol_price():
    """Live SOL/USD — Jupiter first, CoinGecko fallback. None on failure."""
    for url, dig in (
        ("https://price.jup.ag/v6/price?ids=SOL", lambda d: d["data"]["SOL"]["price"]),
        ("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd",
         lambda d: d["solana"]["usd"]),
    ):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vantage-balance-updater"})
            d = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
            return float(dig(d))
        except Exception:
            continue
    return None


def snapshot_networth(db):
    """Upsert today's equity point per agent from linked-wallet balance USD."""
    today = date.today().isoformat()
    agents = [r[0] for r in db.execute(
        "SELECT DISTINCT agent_id FROM trading_wallets WHERE agent_id IS NOT NULL").fetchall()]
    for aid in agents:
        row = db.execute(
            """SELECT COALESCE(SUM(b.value_usd), 0)
               FROM trading_balances b JOIN trading_wallets w ON w.id = b.wallet_id
               WHERE w.agent_id = ?""", (aid,)).fetchone()
        value = round(float(row[0] or 0), 2)
        prev = db.execute(
            "SELECT portfolio_value_usd FROM trading_pnl_snapshots WHERE agent_id=? AND snapshot_date<? ORDER BY snapshot_date DESC LIMIT 1",
            (aid, today)).fetchone()
        prev_val = prev[0] if prev else None
        daily_pnl = round(value - prev_val, 2) if prev_val else 0.0
        daily_pct = round((value - prev_val) / prev_val * 100, 2) if prev_val else 0.0
        db.execute(
            """INSERT INTO trading_pnl_snapshots
                 (agent_id, snapshot_date, portfolio_value_usd, daily_pnl_usd, daily_pnl_pct, notes)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(agent_id, snapshot_date) DO UPDATE SET
                 portfolio_value_usd=excluded.portfolio_value_usd,
                 daily_pnl_usd=excluded.daily_pnl_usd,
                 daily_pnl_pct=excluded.daily_pnl_pct""",
            (aid, today, value, daily_pnl, daily_pct, "auto: linked-wallet net worth"))


def update_balances():
    """Refresh SOL balances, write trading_balances, sync equity snapshots."""
    db = sqlite3.connect(DB_PATH)
    price = get_sol_price()
    wallets = db.execute(
        "SELECT id, address FROM trading_wallets WHERE chain = 'solana'").fetchall()

    updated = 0
    for wid, addr in wallets:
        if not addr:
            continue
        balance = get_sol_balance(addr)
        if balance is None:
            continue
        value_usd = round(balance * price, 2) if price else None
        db.execute(
            "UPDATE trading_wallets SET balance_hint=?, last_synced_at=? WHERE id=?",
            (f"{balance} SOL", datetime.now(timezone.utc).isoformat(), wid))
        db.execute(
            """INSERT INTO trading_balances (wallet_id, token, balance, value_usd)
               VALUES (?,?,?,?)
               ON CONFLICT(wallet_id, token) DO UPDATE SET
                 balance=excluded.balance, value_usd=excluded.value_usd, updated_at=datetime('now')""",
            (wid, "SOL", balance, value_usd))
        updated += 1
        print(f"  {addr[:12]}... = {balance} SOL"
              + (f" (${value_usd})" if value_usd is not None else ""), flush=True)

    snapshot_networth(db)
    db.commit()
    db.close()
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
          f"Updated {updated} SOL wallets; equity snapshots synced", flush=True)


if __name__ == "__main__":
    print(f"Wallet Balance Updater — {INTERVAL}s cycle", flush=True)
    if not _helius_key():
        print("  WARNING: no Helius key available (pool empty and HELIUS_API_KEY unset) — idling.", flush=True)
    else:
        status = api_key_pool.pool_status("helius")
        if status:
            print(f"  helius pool: {len(status)} key(s) — {[s['key_suffix'] for s in status]}", flush=True)
    while True:
        try:
            update_balances()
        except Exception as e:
            print(f"Error: {e}", flush=True)
        time.sleep(INTERVAL)
