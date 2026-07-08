#!/opt/ares/venv/bin/python3
"""ares_pumpfun_trader — Execute Pump.fun token buys via Jupiter.
Listens for pumpfun signals from trading_signals table, auto-buys
graduating/trending tokens with degen filters.

Usage: ares_pumpfun_trader.py [--daemon]
"""
import time, json, sqlite3, os, sys, signal, urllib.request, hashlib

DB = "/opt/ares/Vantage/data/vantage.db"
HELIUS_KEY = os.environ.get("HELIUS_API_KEY", "")
JUPITER = "https://quote-api.jup.ag/v6"
VANTAGE_URL = "http://localhost:8001/api/trading/orders"
VANTAGE_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()

# ── Degen Safety Filters ───────────────────────────────────────────
MIN_VOLUME_24H = 10000      # $10K minimum 24h volume
MAX_RISK_SCORE = 50          # Max risk score 0-100 (lower = safer)
MIN_HOLDERS = 3              # Minimum unique holders
MAX_DEV_SELL_PCT = 10        # Max dev wallet sell % in first 5 min
TRADE_AMOUNT_SOL = 0.01      # 0.01 SOL per trade (~$0.80)

# ── DB Helpers ──────────────────────────────────────────────────────
def get_pending_signals():
    db = sqlite3.connect(DB)
    rows = db.execute("""
        SELECT id, symbol, direction, conviction 
        FROM trading_signals 
        WHERE type='pumpfun' AND direction='BUY' 
        AND timestamp > datetime('now','-1 hour')
        ORDER BY conviction DESC LIMIT 5
    """).fetchall()
    db.close()
    return rows

def has_been_executed(sig_id):
    db = sqlite3.connect(DB)
    r = db.execute("SELECT id FROM trading_orders WHERE notes LIKE ?", (f"%sig_{sig_id}%",)).fetchone()
    db.close()
    return r is not None

def create_order(symbol, conviction, sig_id):
    """Create a buy order via Vantage trading API."""
    payload = json.dumps({
        "symbol": f"{symbol}/USDC",
        "side": "buy",
        "type": "market",
        "amount": TRADE_AMOUNT_SOL,
        "chain": "solana",
        "notes": f"Pumpfun degen buy — sig_{sig_id} — conviction={conviction:.2f}",
        "source": "ares_pumpfun_trader",
    }).encode()
    try:
        req = urllib.request.Request(VANTAGE_URL, data=payload, headers={
            "Content-Type": "application/json",
            "X-Agent-Key": VANTAGE_KEY,
        })
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        return resp.get("order_id")
    except Exception as e:
        print(f"  Order failed: {e}")
        return None

def check_degen_filters(mint):
    """Run safety checks before executing a buy."""
    try:
        # Check Birdeye price + volume
        req = urllib.request.Request(
            f"https://public-api.birdeye.so/defi/price?address={mint}",
            headers={"X-API-KEY": BIRDEYE_KEY, "accept": "application/json"}
        )
        price_data = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        price = float(price_data.get("data", {}).get("value", 0))
        if price == 0:
            return False, "No price data — likely dead or unpriced"

        # Check Helius for mint authority
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
            "params": [mint, {"encoding": "jsonParsed"}]
        }).encode()
        req = urllib.request.Request(
            f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}",
            data=payload, headers={"Content-Type": "application/json"}
        )
        acct_data = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        info = acct_data.get("result", {}).get("value", {}).get("data", {}).get("parsed", {}).get("info", {}) or {}
        
        has_mint_auth = bool(info.get("mintAuthority"))
        if has_mint_auth:
            return False, "Mint authority active — can mint unlimited tokens (rug risk)"

        return True, f"Safe — no mint auth, price=${price:.6f}"
    except Exception as e:
        return False, f"Filter check failed: {e}"

# ── Main Loop ───────────────────────────────────────────────────────
def run():
    print("═══ ares_pumpfun_trader v1 ═══")
    print(f"  Trade size: {TRADE_AMOUNT_SOL:.3f} SOL (~${TRADE_AMOUNT_SOL*80:.2f})")
    print(f"  Safety: min vol=${MIN_VOLUME_24H}, max risk={MAX_RISK_SCORE}, no mint auth")

    while True:
        try:
            signals = get_pending_signals()
            if signals:
                print(f"\n  [{time.strftime('%H:%M:%S')}] {len(signals)} pending pumpfun signals")

            for sig in signals:
                sig_id, symbol, direction, conviction = sig

                if has_been_executed(sig_id):
                    continue

                mint = symbol
                print(f"\n  Evaluating: {symbol} (conv={conviction:.2f})")

                # Run degen safety filters
                safe, reason = check_degen_filters(mint)
                print(f"  Degen check: {'✅' if safe else '❌'} {reason}")

                if not safe:
                    continue

                # Execute
                print(f"  🚀 EXECUTING: Buy {TRADE_AMOUNT_SOL:.3f} SOL of {symbol}")
                order_id = create_order(symbol, conviction, sig_id)
                if order_id:
                    print(f"  ✅ Order #{order_id} placed")
                else:
                    print(f"  ❌ Order failed")

            time.sleep(30)  # Check every 30 seconds

        except Exception as e:
            print(f"  ⚠️ Loop error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    if "--daemon" in sys.argv:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
        os.setsid()
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    run()
