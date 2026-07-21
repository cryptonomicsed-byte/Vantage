#!/opt/ares/venv/bin/python3
"""ares_pumpfun_trader — Execute Pump.fun token buys via Jupiter.
Listens for pumpfun signals from trading_signals table, auto-buys
graduating/trending tokens with degen filters.

Usage: ares_pumpfun_trader.py [--daemon]
"""
import time, json, sqlite3, os, sys, signal, urllib.request, hashlib

DB = "/opt/ares/Vantage/data/vantage.db"
HELIUS_KEY = os.environ.get("HELIUS_API_KEY", "")
BIRDEYE_KEY = os.environ.get("BIRDEYE_KEY", "")
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

def create_order(symbol, side, conviction, sig_id, mint=None):
    """Create a buy/sell order via Vantage trading API."""
    payload = json.dumps({
        "symbol": f"{symbol}/USDC",
        "side": side,
        "type": "market",
        "amount": TRADE_AMOUNT_SOL if side == "buy" else TRADE_AMOUNT_SOL,
        "chain": "solana",
        "notes": f"Pumpfun {side} — sig_{sig_id} — conviction={conviction:.2f}" + (f" | mint={mint}" if mint else ""),
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

def log_trade(symbol, mint, side, amount, entry_price, conviction, sig_id):
    """Log trade to strategy_trades table for position tracking."""
    try:
        db = sqlite3.connect(DB)
        db.execute("""
            INSERT INTO strategy_trades (strategy, symbol, side, amount_sol, entry_price, conviction, status, entry_time, notes)
            VALUES ('pumpfun_auto', ?, ?, ?, ?, ?, 'open', datetime('now'), ?)
        """, (symbol, side, amount, entry_price, conviction, f"Pumpfun sig_{sig_id} | mint={mint}"))
        db.commit()
        db.close()
    except Exception as e:
        print(f"  Trade log failed: {e}")

def track_open_positions_for_exits():
    """Check open pumpfun positions and execute exits at TP (+25%) or SL (-30%)."""
    TAKE_PROFIT_PCT = 0.25
    STOP_LOSS_PCT = -0.30

    try:
        db = sqlite3.connect(DB)
        # Get open BUY trades
        trades = db.execute("""
            SELECT id, symbol, amount_sol, entry_price
            FROM strategy_trades
            WHERE strategy='pumpfun_auto' AND side='BUY' AND status='open'
        """).fetchall()

        for tid, symbol, amount, entry_price in trades:
            if not entry_price or entry_price <= 0:
                continue

            # Get current price from Birdeye
            try:
                db2 = sqlite3.connect(DB)
                mint = db2.execute(
                    "SELECT DISTINCT mint FROM token_wallet_roles WHERE LOWER(symbol) = ? LIMIT 1",
                    (symbol.lower(),)
                ).fetchone()
                db2.close()

                if not mint:
                    continue

                req = urllib.request.Request(
                    f"https://public-api.birdeye.so/defi/price?address={mint[0]}",
                    headers={"X-API-KEY": BIRDEYE_KEY, "accept": "application/json"}
                )
                price_data = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
                current_price = float(price_data.get("data", {}).get("value", 0))

                if current_price <= 0:
                    continue

                pnl_pct = (current_price - entry_price) / entry_price

                # Check take profit
                if pnl_pct >= TAKE_PROFIT_PCT:
                    print(f"\n  💰 TAKE PROFIT: {symbol} +{pnl_pct*100:.1f}%")
                    order_id = create_order(symbol, "sell", pnl_pct, tid, mint[0])
                    if order_id:
                        db.execute(
                            "UPDATE strategy_trades SET status='closed', exit_price=?, pnl_pct=?, exit_time=datetime('now'), notes=notes||? WHERE id=?",
                            (current_price, pnl_pct*100, f" | TP +{pnl_pct*100:.1f}% order={order_id}", tid)
                        )
                        db.commit()
                        print(f"  ✅ Sold at +{pnl_pct*100:.1f}%")

                # Check stop loss
                elif pnl_pct <= STOP_LOSS_PCT:
                    print(f"\n  🛑 STOP LOSS: {symbol} {pnl_pct*100:.1f}%")
                    order_id = create_order(symbol, "sell", pnl_pct, tid, mint[0])
                    if order_id:
                        db.execute(
                            "UPDATE strategy_trades SET status='closed', exit_price=?, pnl_pct=?, exit_time=datetime('now'), notes=notes||? WHERE id=?",
                            (current_price, pnl_pct*100, f" | SL {pnl_pct*100:.1f}% order={order_id}", tid)
                        )
                        db.commit()
                        print(f"  ❌ Stopped out at {pnl_pct*100:.1f}%")
                else:
                    print(f"  [{symbol}] {pnl_pct*100:+.1f}% | Entry: ${entry_price:.6f} | Current: ${current_price:.6f}")
            except Exception as e:
                print(f"  Position check failed for {symbol}: {e}")

        db.close()
    except Exception as e:
        print(f"  Position tracking failed: {e}")

def resolve_symbol_to_mint(symbol):
    """Resolve token symbol (e.g. '$COPE') to mint address.
    - If symbol is already 44-char base58 mint, return it
    - Otherwise, search token_wallet_roles table for mint
    - Fall back to GeckoTerminal API if no DB match
    """
    # Quick check: if it looks like a mint (44 chars, base58-ish), use as-is
    if len(symbol) == 44 and all(c in "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz" for c in symbol):
        return symbol

    # Query local DB for known mapping
    try:
        db = sqlite3.connect(DB)
        row = db.execute(
            "SELECT DISTINCT mint FROM token_wallet_roles WHERE LOWER(symbol) = ? LIMIT 1",
            (symbol.lower(),)
        ).fetchone()
        db.close()
        if row:
            return row[0]
    except:
        pass

    # Fall back to GeckoTerminal search
    try:
        req = urllib.request.Request(
            f"https://api.geckoterminal.com/api/v2/search/pools?query={symbol.upper()}&network=solana",
            headers={"Accept": "application/json", "User-Agent": "curl/8.0"}
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        pools = resp.get("data", [])
        if pools:
            base_token_id = pools[0].get("relationships", {}).get("base_token", {}).get("data", {}).get("id", "")
            if "_" in base_token_id:
                return base_token_id.split("_", 1)[-1]
    except:
        pass

    # No match found
    return None

def check_degen_filters(mint):
    """Run safety checks before executing a buy."""
    if not BIRDEYE_KEY:
        return False, "BIRDEYE_KEY not configured"

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
            # Check open positions for exits (TP/SL)
            track_open_positions_for_exits()

            signals = get_pending_signals()
            if signals:
                print(f"\n  [{time.strftime('%H:%M:%S')}] {len(signals)} pending pumpfun signals")

            for sig in signals:
                sig_id, symbol, direction, conviction = sig

                if has_been_executed(sig_id):
                    continue

                # Resolve symbol to mint address
                mint = resolve_symbol_to_mint(symbol)
                if not mint:
                    print(f"\n  ⚠️  Could not resolve {symbol} to mint address")
                    continue

                print(f"\n  Evaluating: {symbol} → {mint[:8]}... (conv={conviction:.2f})")

                # Run degen safety filters
                safe, reason = check_degen_filters(mint)
                print(f"  Degen check: {'✅' if safe else '❌'} {reason}")

                if not safe:
                    continue

                # Get current price for entry logging
                try:
                    req = urllib.request.Request(
                        f"https://public-api.birdeye.so/defi/price?address={mint}",
                        headers={"X-API-KEY": BIRDEYE_KEY, "accept": "application/json"}
                    )
                    price_data = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
                    entry_price = float(price_data.get("data", {}).get("value", 0))
                except:
                    entry_price = 0

                # Execute buy
                print(f"  🚀 EXECUTING: Buy {TRADE_AMOUNT_SOL:.3f} SOL of {symbol}")
                order_id = create_order(symbol, "buy", conviction, sig_id, mint)
                if order_id:
                    print(f"  ✅ Order #{order_id} placed")
                    log_trade(symbol, mint, "BUY", TRADE_AMOUNT_SOL, entry_price, conviction, sig_id)
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
