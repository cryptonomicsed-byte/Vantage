#!/usr/bin/env python3
"""
Wallet Balance Updater — Polls Helius RPC for SOL balances, updates Vantage DB.
Runs as daemon every 60s. No private keys stored.
"""
import urllib.request, json, sqlite3, time, sys
from datetime import datetime, timezone

HELIUS_KEY = "os.environ.get("HELIUS_API_KEY","")"
DB_PATH = "/opt/ares/Vantage/data/vantage.db"
INTERVAL = int(sys.argv[1]) if len(sys.argv) > 1 else 60

def get_sol_balance(address):
    """Get SOL balance via Helius RPC."""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "method": "getBalance",
        "params": [address]
    }).encode()
    
    req = urllib.request.Request(
        f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        return data.get("result", {}).get("value", 0) / 1e9
    except:
        return None

def update_balances():
    """Fetch balances for all Solana wallets and update DB."""
    db = sqlite3.connect(DB_PATH)
    wallets = db.execute(
        "SELECT id, address FROM trading_wallets WHERE chain = 'solana'"
    ).fetchall()
    
    updated = 0
    for wid, addr in wallets:
        if not addr:
            continue
        balance = get_sol_balance(addr)
        if balance is not None:
            db.execute(
                "UPDATE trading_wallets SET balance_hint = ?, last_synced_at = ? WHERE id = ?",
                (f"{balance} SOL", datetime.now(timezone.utc).isoformat(), wid)
            )
            updated += 1
            print(f"  {addr[:12]}... = {balance} SOL", flush=True)
    
    db.commit()
    db.close()
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Updated {updated} SOL wallets", flush=True)

if __name__ == "__main__":
    print(f"Wallet Balance Updater — {INTERVAL}s cycle", flush=True)
    while True:
        try:
            update_balances()
        except Exception as e:
            print(f"Error: {e}", flush=True)
        time.sleep(INTERVAL)
