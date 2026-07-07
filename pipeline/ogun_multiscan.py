#!/usr/bin/env python3
"""
Ògún's Forge v2 — Multi-Token Scanner
Runs full strategy on Kraken pairs + lightweight degen scan on Pump.fun tokens

Tier 1 (Full Forge): BTC, ETH, SOL, KET, BONK, WIF, POPCAT — via Kraken CCXT
Tier 2 (Degen Scan): Pump.fun trending tokens — via Birdeye/Helius price + volume
"""
import numpy as np, ccxt, time, json, os, sys, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "/opt/ares")
from ogun_forge import evaluate as forge_evaluate

VANTAGE_URL = "http://localhost:8001"
VANTAGE_KEY = "os.environ.get("VANTAGE_AGENT_KEY","")"
HELIUS_KEY = os.environ.get("HELIUS_API_KEY", "os.environ.get("HELIUS_API_KEY","")")
BIRDEYE_KEY = "os.environ.get("BIRDEYE_API_KEY","")"

# ── Tier 1: Full Forge on Kraken pairs ────────────────────────
KRAKEN_PAIRS = ["SOL/USD", "BTC/USD", "ETH/USD", "KET/USD", "BONK/USD", "WIF/USD", "POPCAT/USD"]

def post_signal(symbol, source, conviction, stype, detail):
    try:
        req = urllib.request.Request(
            f"{VANTAGE_URL}/api/intel/signals/ingest",
            data=json.dumps({"symbol": symbol, "source": source, "conviction": conviction,
                            "type": stype, "detail": detail}).encode(),
            headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY}
        )
        urllib.request.urlopen(req, timeout=5)
    except: pass

def scan_kraken(exchange):
    """Run full Ògún's Forge on all Kraken pairs."""
    for symbol in KRAKEN_PAIRS:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, "5m", limit=100)
            closes = np.array([c[4] for c in ohlcv])
            highs = np.array([c[2] for c in ohlcv])
            lows = np.array([c[3] for c in ohlcv])
            volumes = np.array([c[5] for c in ohlcv])
            
            daily = exchange.fetch_ohlcv(symbol, "1d", limit=10)
            daily_closes = np.array([c[4] for c in daily])
            
            result = forge_evaluate(closes, highs, lows, volumes, daily_closes)
            
            sym = symbol.split("/")[0]
            if result["signal"] != "none":
                print(f"  ⚔️ {sym}: {result['signal']} @ {result['entry']:.4f} "
                      f"SL={result['sl']:.4f} TP={result['tp']:.4f} conv={result.get('conviction',0):.2f}")
                post_signal(sym, "ogun_forge_v2", result["conviction"], "strategy",
                           f"{result['signal']} | Entry:{result['entry']:.4f} SL:{result['sl']:.4f} TP:{result['tp']:.4f}")
            else:
                hma = "▲" if result.get("hma_bull") else "▼"
                print(f"  {hma} {sym:6s}: ADX={result.get('adx',0):.0f} MOM={result.get('mom_score',0)} "
                      f"CORE={result.get('core_long',False)}", flush=True)
        except Exception as e:
            print(f"  ❌ {symbol}: {e}")

# ── Tier 2: Degen scan on Pump.fun tokens ─────────────────────
def get_trending_tokens():
    """Get trending tokens from GeckoTerminal."""
    try:
        req = urllib.request.Request(
            "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?limit=10",
            headers={"User-Agent": "curl/8.0"}
        )
        data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        tokens = []
        for pool in data.get("data", []):
            attrs = pool.get("attributes", {})
            name = attrs.get("name", "?")
            symbol = name.split(" / ")[0] if " / " in name else name[:10]
            vol = float(attrs.get("volume_usd", {}).get("h24", 0))
            price_change = float(attrs.get("price_change_percentage", {}).get("h24", 0))
            tokens.append({"symbol": symbol, "volume24hUSD": vol, "priceChange24hPercent": price_change, "price": 0})
        return tokens[:10]
    except Exception as e:
        print(f"  GeckoTerminal error: {e}")
        return []

def scan_degen():
    """Lightweight momentum + volume scan for Pump.fun tokens."""
    tokens = get_trending_tokens()
    if not tokens:
        tokens = get_alpha_feed_tokens()
    
    for token in tokens[:8]:
        try:
            addr = token.get("address", "")
            symbol = token.get("symbol", "?")
            price = float(token.get("price", 0))
            change_24h = float(token.get("priceChange24hPercent", 0))
            volume_24h = float(token.get("volume24hUSD", 0))
            
            # Simplified degen signal
            if change_24h > 10 and volume_24h > 50000:
                conviction = min(0.9, change_24h / 50 + volume_24h / 500000)
                print(f"  🚀 {symbol:10s}: +{change_24h:.0f}% vol={volume_24h/1000:.0f}K conv={conviction:.2f}")
                post_signal(symbol, "ogun_degen", conviction, "degen",
                           f"Pump signal: +{change_24h:.0f}% | Vol: {volume_24h/1000:.0f}K | Price: {price:.6f}")
            elif change_24h < -20:
                print(f"  💀 {symbol:10s}: {change_24h:.0f}% — rug risk")
        except Exception as e:
            continue

def get_alpha_feed_tokens():
    """Fallback: get tokens from Vantage alpha_feed signals."""
    try:
        req = urllib.request.Request(
            f"{VANTAGE_URL}/api/intel/signals",
            headers={"X-Agent-Key": VANTAGE_KEY}
        )
        signals = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        signals = signals.get("signals", signals)
        return [{"symbol": s["symbol"], "address": "", "price": 0, 
                 "priceChange24hPercent": s.get("conviction", 0) * 10,
                 "volume24hUSD": 0} 
                for s in signals if s.get("source") in ("alpha_feed", "radar")]
    except:
        return []

# ── Main ──────────────────────────────────────────────────────
def run(interval=60):
    exchange = ccxt.kraken({"enableRateLimit": True, "timeout": 15000})
    
    print(f"Ògún's Forge Multi-Scanner — {interval}s cycle")
    print(f"Tier 1: {len(KRAKEN_PAIRS)} Kraken pairs (full Forge)")
    print(f"Tier 2: Pump.fun degen scan (momentum + volume)")
    
    while True:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] SCAN START")
        
        scan_kraken(exchange)
        scan_degen()
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] SCAN DONE")
        time.sleep(interval)

if __name__ == "__main__":
    run()
