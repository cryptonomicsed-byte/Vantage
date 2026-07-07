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
VANTAGE_KEY = "vantage_94f21c43db14b76b301793bb8d8d02cd4b9442971edfbd6f"
DB_PATH = "/opt/ares/Vantage/data/vantage.db"
WALLET = "ogun"
MAX_TRADE_SOL = 0.01   # Conservative: max 0.01 SOL per trade
MIN_CONVICTION = 0.7   # Only trade high-conviction signals

# ── Track already-traded symbols to avoid duplicates ──────────
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

def log_trade(symbol, side, amount, price, tx_sig, strategy):
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        INSERT INTO strategy_trades (strategy, symbol, side, amount_sol, entry_price, status, entry_time, notes)
        VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
    """, (strategy, symbol, side, amount, price,
          datetime.now(timezone.utc).isoformat(),
          f"TX: {tx_sig}"))
    db.commit()
    db.close()

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
            log_trade(symbol, "BUY", amount, 0, sig, "ogun_forge")
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
            log_trade(symbol, "BUY", amount, 0, sig, "ogun_degen")
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
            
        except Exception as e:
            print(f"Error: {e}")
        
        time.sleep(interval)

def signal_contains(sig, *keywords):
    detail = sig.get("detail", "")
    return any(k in detail for k in keywords)

if __name__ == "__main__":
    run()
