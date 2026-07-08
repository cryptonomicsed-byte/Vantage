#!/usr/bin/env python3
"""
OKF LIVE TRADING ORCHESTRATOR — Unified execution engine
Wires: Ògún's Forge → Signal Pool → Executor → sol-cli / Pump.fun Bot

Flow:
  Forge signal → Vantage pool → this orchestrator reads →
    Kraken pairs → sol token swap (Jupiter)
    Pump.fun tokens → Pump.fun Bot sniper
    Track all → Vantage DB + feed

Wallet: BIPON39 Soul (85SFCuohae...) — 0.18 SOL + 0.08 USDC
"""
import subprocess, json, sqlite3, time, os, sys, urllib.request
from datetime import datetime, timezone

VANTAGE_URL = "http://localhost:8001"
VANTAGE_KEY = os.environ.get("VANTAGE_KEY","")
DB_PATH = "/opt/ares/Vantage/data/vantage.db"
WALLET = "ogun"
MAX_TRADE_SOL = 0.01   # Conservative: max 0.01 SOL per trade
MIN_CONVICTION = 0.7   # Only trade high-conviction signals

# ── Risk Management ──────────────────────────────────────────
STOP_LOSS = {
    "ogun_forge": -0.05,   # Kraken pairs: -5% SL
    "ogun_degen": -0.30,   # Pump.fun: -30% SL
}
TAKE_PROFIT = {
    "ogun_forge": 0.10,    # Kraken pairs: +10% TP
    "ogun_degen": 0.25,    # Pump.fun: +25% TP
}

def get_token_price(symbol):
    """Get current price for a token."""
    # Kraken pairs
    try:
        k_symbol = {"BONK": "BONK/USD", "WIF": "WIF/USD", "POPCAT": "POPCAT/USD",
                    "KET": "KET/USD", "SOL": "SOL/USD", "BTC": "BTC/USD", "ETH": "ETH/USD"}
        if symbol in k_symbol:
            import ccxt
            k = ccxt.kraken({"enableRateLimit": True})
            ticker = k.fetch_ticker(k_symbol[symbol])
            return ticker["last"]
    except:
        pass
    
    # Solana tokens via sol-cli
    try:
        result = subprocess.run(["sol", "token", "price", symbol.lower()],
                               capture_output=True, text=True, timeout=15)
        for line in result.stdout.split("\n"):
            if "$" in line:
                return float(line.split("$")[-1].strip())
    except:
        pass
    
    return None

def check_exits():
    """Monitor open positions for stop-loss and take-profit."""
    db = sqlite3.connect(DB_PATH)
    trades = db.execute(
        "SELECT id, strategy, symbol, amount_sol, entry_price FROM strategy_trades WHERE status='open'"
    ).fetchall()
    
    closed = 0
    for tid, strategy, symbol, amount, entry in trades:
        if not entry or entry == 0:
            continue
        
        # Get current price
        price = get_token_price(symbol)
        if not price:
            continue
        
        pnl = (price - entry) / entry if entry > 0 else 0
        sl = STOP_LOSS.get(strategy, -0.05)
        tp = TAKE_PROFIT.get(strategy, 0.10)
        
        if pnl <= sl:
            print(f"\n  🛑 STOP LOSS: {symbol} @ {pnl*100:.1f}%")
            # Sell via sol-cli
            token = symbol.lower()
            cmd = f"sol token swap all {token} sol --wallet {WALLET}"
            result = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=60)
            sig = None
            for line in (result.stdout + result.stderr).split("\n"):
                if "Signature:" in line:
                    sig = line.split("Signature:")[-1].strip()
            
            db.execute(
                "UPDATE strategy_trades SET status='closed', exit_price=?, pnl_pct=?, exit_time=?, notes=notes||? WHERE id=?",
                (price, pnl*100, datetime.now(timezone.utc).isoformat(),
                 f" | SL EXIT @ {pnl*100:.1f}% TX:{sig}", tid)
            )
            post_feed(f"🛑 SL Exit: {symbol}", f"Stop-loss triggered\n{symbol}: {pnl*100:.1f}%\nTX: {sig}")
            closed += 1
            
        elif pnl >= tp:
            print(f"\n  🎯 TAKE PROFIT: {symbol} @ +{pnl*100:.1f}%")
            token = symbol.lower()
            cmd = f"sol token swap all {token} sol --wallet {WALLET}"
            result = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=60)
            sig = None
            for line in (result.stdout + result.stderr).split("\n"):
                if "Signature:" in line:
                    sig = line.split("Signature:")[-1].strip()
            
            db.execute(
                "UPDATE strategy_trades SET status='closed', exit_price=?, pnl_pct=?, exit_time=?, notes=notes||? WHERE id=?",
                (price, pnl*100, datetime.now(timezone.utc).isoformat(),
                 f" | TP EXIT @ +{pnl*100:.1f}% TX:{sig}", tid)
            )
            post_feed(f"🎯 TP Exit: {symbol}", f"Take-profit triggered\n{symbol}: +{pnl*100:.1f}%\nTX: {sig}")
            print(f"  ✅ EXIT: {symbol} — {sig[:20] if sig else 'manual'}...")
            closed += 1
        else:
            print(f"  [{symbol}] {pnl*100:+.1f}% (SL:{sl*100:.0f}% TP:{tp*100:.0f}%)", flush=True)
    
    db.commit()
    db.close()
    return closed

# ── Track already-traded symbols ──────────────────────────────
TRADED = set()

def post_feed(title, content):
    try:
        req = urllib.request.Request(
            f"{VANTAGE_URL}/api/agents/posts/text",
            data=json.dumps({"title": title, "content": content, "tags": ["trading","live","ogun"],
                            "status": "published", "content_type": "text"}).encode(),
            headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY}
        )
        urllib.request.urlopen(req, timeout=5)
    except: pass

def log_trade(symbol, side, amount, tx_sig, strategy):
    """Log trade with actual entry price."""
    price = get_token_price(symbol) or 0
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        INSERT INTO strategy_trades (strategy, symbol, side, amount_sol, entry_price, status, entry_time, notes)
        VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
    """, (strategy, symbol, side, amount, price,
          datetime.now(timezone.utc).isoformat(),
          f"TX: {tx_sig}"))
    db.commit()
    db.close()
    print(f"  Entry price: ${price:.8f}" if price else "  Entry price: unknown")

def execute_kraken_swap(symbol, conviction):
    """Execute swap via sol-cli for Kraken-listed tokens."""
    if symbol in TRADED:
        return None
    
    amount = min(MAX_TRADE_SOL, 0.005)  # Start tiny
    
    print(f"\n  ⚔️ EXECUTING: {symbol} (conv={conviction:.2f})")
    
    # Map symbol to sol-cli token name
    token_map = {"SOL": "sol", "BTC": "sol", "ETH": "sol",  # BTC/ETH not on Solana
                 "BONK": "bonk", "WIF": "wif", "POPCAT": "popcat", "KET": "ket"}
    token = token_map.get(symbol, symbol.lower())
    
    # For testing: swap SOL→USDC first to verify (skip BTC/ETH — not on Solana)
    if symbol in ("BTC", "ETH"):
        print(f"  ⏭️ {symbol} not on Solana — skipping (use Kraken CEX)")
        return None
    
    if token == "sol":
        print(f"  ⏭️ {symbol} is SOL — skipping self-swap")
        return None
    
    try:
        cmd = f"sol token swap {amount} sol {token} --wallet {WALLET}"
        print(f"  $ {cmd}")
        result = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=60)
        output = result.stdout + result.stderr
        
        # Extract TX sig
        sig = None
        for line in output.split("\n"):
            if "Signature:" in line:
                sig = line.split("Signature:")[-1].strip()
            if "Explorer:" in line:
                print(f"  {line.strip()}")
        
        if sig and "Error" not in output:
            TRADED.add(symbol)
            log_trade(symbol, "BUY", amount, sig, "ogun_forge")
            post_feed(f"⚔️ Ògún Strike: {symbol}",
                     f"**Live Trade Executed**\n\nSymbol: {symbol}\nAmount: {amount} SOL\nTX: {sig}\nStrategy: Ògún's Forge v2")
            print(f"  ✅ LIVE: {symbol} — {sig[:20]}...")
            return sig
        else:
            print(f"  ❌ Failed: {output[:200]}")
            return None
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return None

def execute_pumpfun_snipe(symbol, conviction):
    """Scalp Pump.fun token via sol-cli swap."""
    if symbol in TRADED:
        return None
    
    amount = 0.002  # Micro scalp — ~$0.16
    
    print(f"\n  🎯 SCALPING: {symbol} (conv={conviction:.2f}, {amount} SOL)")
    
    try:
        # Normalize symbol for sol-cli
        token = symbol.lower().replace(" ", "-").replace("/", "-")[:20]
        
        cmd = f"sol token swap {amount} sol {token} --wallet {WALLET}"
        print(f"  $ {cmd}")
        result = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=60)
        output = result.stdout + result.stderr
        
        sig = None
        for line in output.split("\n"):
            if "Signature:" in line:
                sig = line.split("Signature:")[-1].strip()
            if "Explorer:" in line:
                print(f"  {line.strip()}")
        
        if sig and "Error" not in output:
            TRADED.add(symbol)
            log_trade(symbol, "BUY", amount, sig, "ogun_degen")
            post_feed(f"🎯 Ògún Scalp: {symbol}",
                     f"**Pump.fun Scalp Executed**\n\nToken: {symbol}\nAmount: {amount} SOL\nTX: {sig}\nTP: +20% | SL: -30%")
            print(f"  ✅ LIVE SCALP: {symbol} — {sig[:20]}...")
            return sig
        else:
            # Token might not be on Jupiter — try with full mint address
            print(f"  ⚠️ Token '{token}' not found on Jupiter — trying symbol variants...")
            # Many new tokens aren't immediately on Jupiter
            return None
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return None

def get_forge_signals():
    """Pull Ògún's Forge signals from Vantage pool."""
    try:
        req = urllib.request.Request(
            f"{VANTAGE_URL}/api/intel/signals",
            headers={"X-Agent-Key": VANTAGE_KEY}
        )
        data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        signals = data.get("signals", data)
        return [s for s in signals if s.get("source") in ("ogun_forge_v2", "ogun_degen")]
    except:
        return []

def show_portfolio():
    """Show current portfolio via sol-cli."""
    result = subprocess.run(["sol", "wallet", "balance", WALLET], capture_output=True, text=True, timeout=10)
    print(result.stdout)

# ── Main Loop ─────────────────────────────────────────────────
def run(interval=30):
    print(f"╔══════════════════════════════════════╗")
    print(f"║  ÒGÚN LIVE TRADING ORCHESTRATOR     ║")
    print(f"║  Wallet: {WALLET} ({MAX_TRADE_SOL} SOL max/trade) ║")
    print(f"╚══════════════════════════════════════╝")
    
    # Show starting portfolio
    show_portfolio()
    
    last_portfolio = time.time()
    
    while True:
        try:
            signals = get_forge_signals()
            
            if signals:
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] {len(signals)} forge signals")
                
                for sig in signals:
                    symbol = sig.get("symbol", "?")
                    conviction = sig.get("conviction", 0) or 0
                    stype = sig.get("type", "")
                    source = sig.get("source", "")
                    
                    if conviction < MIN_CONVICTION or symbol in TRADED:
                        continue
                    
                    if signal_contains(sig, "LONG_STRIKE", "SHORT_STRIKE"):
                        execute_kraken_swap(symbol, conviction)
                    elif source == "ogun_degen":
                        execute_pumpfun_snipe(symbol, conviction)
            
            # Show portfolio every 5 min
            if time.time() - last_portfolio > 300:
                show_portfolio()
                last_portfolio = time.time()
            
            # Monitor open positions for SL/TP
            closed = check_exits()
            if closed > 0:
                show_portfolio()
            
        except Exception as e:
            print(f"Error: {e}")
        
        time.sleep(interval)

def signal_contains(sig, *keywords):
    detail = sig.get("detail", "")
    return any(k in detail for k in keywords)

if __name__ == "__main__":
    run()
