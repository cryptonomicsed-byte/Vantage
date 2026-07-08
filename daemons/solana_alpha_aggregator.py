#!/opt/ares/venv/bin/python3
"""solana_alpha_aggregator — Top 5 Degen Plays + Smart Money + Graduation detection.
Generates structured Top 5 JSON for Vantage Degen Trenches.

Data: GeckoTerminal (graduated pools), Birdeye (holders/prices), 
Helius (on-chain wallets), DexScreener (trending)
"""
import time, json, sqlite3, os, sys, urllib.request, signal
from collections import defaultdict

DB = "/opt/ares/Vantage/data/vantage.db"
HELIUS_KEY = os.environ.get("HELIUS_API_KEY", "")
BIRDEYE_KEY = os.environ.get("BIRDEYE_KEY", "")
VANTAGE_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()

def fetch(url, headers=None):
    h = headers or {}; h['User-Agent'] = 'curl/8.0'
    req = urllib.request.Request(url, headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode())

def rpc(method, params):
    payload = json.dumps(dict(jsonrpc="2.0",id=1,method=method,params=params)).encode()
    req = urllib.request.Request(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}", data=payload, headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(req,timeout=10).read().decode())

def ingest_top5(data):
    """Post Top 5 summary to Vantage signals."""
    try:
        summary = f"Top Degens: {', '.join([t['symbol'] for t in data.get('top_5_must_buy',[])][:5])}"
        payload = json.dumps(dict(
            symbol="TOP5", source="alpha_aggregator", type="top5_degen",
            direction="BUY", conviction=0.85,
            detail=json.dumps(data, default=str)[:500]
        )).encode()
        urllib.request.urlopen(urllib.request.Request(
            "http://localhost:8001/api/trading/signals/ingest",
            data=payload,
            headers={"Content-Type":"application/json","X-Agent-Key":VANTAGE_KEY}
        ), timeout=5)
    except: pass

# ════════════════════════════════════════════════════════════════
# 1. GRADUATED TOKENS — Raydium pools with pump.fun history
# ════════════════════════════════════════════════════════════════
def get_graduated_tokens():
    """Find tokens that graduated from pump.fun to Raydium."""
    try:
        d = fetch("https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1", {"accept":"application/json"})
        pools = d.get("data",[])
        graduated = []
        for p in pools[:30]:
            attrs = p.get("attributes",{})
            name = attrs.get("name","")
            addr = p.get("id","").split("_")[-1] if "_" in p.get("id","") else ""
            vol = attrs.get("volume_usd",{})
            vol_24h = float(str(vol.get("h24",0))) if isinstance(vol,dict) else 0
            pc = attrs.get("price_change_percentage",{})
            pc_24h = float(str(pc.get("h24",0))) if isinstance(pc,dict) else 0
            txns = attrs.get("transactions",{})
            buys_24h = txns.get("h24",{}).get("buys",0) if isinstance(txns.get("h24"),dict) else 0
            sells_24h = txns.get("h24",{}).get("sells",0) if isinstance(txns.get("h24"),dict) else 0
            symbol = name.split(" / ")[0][:12] if " / " in name else name[:12]
            
            # Graduated = pool exists on Raydium (not pure pump.fun bonding curve)
            is_graduated = "pump" in addr.lower() or "raydium" in attrs.get("dex","").lower()
            score = 0
            if vol_24h > 50000: score += 30
            if vol_24h > 100000: score += 20
            if pc_24h > 5: score += 20
            if buys_24h > sells_24h: score += 15  # Buy pressure
            if is_graduated: score += 15  # Graduated bonus
            
            graduated.append({
                "symbol": symbol, "name": name, "address": addr,
                "volume_24h": vol_24h, "price_change_24h": pc_24h,
                "buys_24h": buys_24h, "sells_24h": sells_24h,
                "graduated": is_graduated, "score": score,
                "reason": f"{'Graduated ✓ ' if is_graduated else ''}Vol=${vol_24h:,.0f} Buy/Sell={buys_24h}/{sells_24h}"
            })
        return sorted(graduated, key=lambda x: -x["score"])
    except:
        return []

# ════════════════════════════════════════════════════════════════
# 2. SMART MONEY WALLETS — Top traders from our watchlist
# ════════════════════════════════════════════════════════════════
def get_smart_money_wallets():
    """Get most active tracked wallets with high edge counts."""
    db = sqlite3.connect(DB)
    rows = db.execute("""
        SELECT w.address, w.label, w.chain,
               (SELECT COUNT(*) FROM wallet_edges we WHERE we.address_a=w.address OR we.address_b=w.address) as edges,
               (SELECT MAX(we.last_seen) FROM wallet_edges we WHERE we.address_a=w.address OR we.address_b=w.address) as last_seen
        FROM tracked_wallets w WHERE w.chain='solana'
        ORDER BY edges DESC LIMIT 10
    """).fetchall()
    db.close()
    return [{"wallet": r[0][:20]+"...", "label": r[1] or "unknown", "edges": r[2], "last_seen": str(r[3])[:19] if r[3] else "never"} for r in rows]

# ════════════════════════════════════════════════════════════════
# 3. DEX ADS / PROMOTIONS — Sponsored tokens with volume spikes
# ════════════════════════════════════════════════════════════════
def get_dex_ads():
    """Detect tokens with sudden volume without price movement (possible paid promo)."""
    try:
        d = fetch("https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1", {"accept":"application/json"})
        pools = d.get("data",[])
        ads = []
        for p in pools[:20]:
            attrs = p.get("attributes",{})
            name = attrs.get("name","")
            vol = attrs.get("volume_usd",{})
            vol_5m = float(str(vol.get("m5",0))) if isinstance(vol,dict) else 0
            vol_1h = float(str(vol.get("h1",0))) if isinstance(vol,dict) else 0
            pc = attrs.get("price_change_percentage",{})
            pc_1h = float(str(pc.get("h1",0))) if isinstance(pc,dict) else 0
            symbol = name.split(" / ")[0][:12] if " / " in name else name[:12]
            
            # Ad-like: high 5min volume but low price change
            if vol_5m > 5000 and abs(pc_1h) < 3:
                ads.append({
                    "symbol": symbol, "name": name,
                    "volume_5m": vol_5m, "volume_1h": vol_1h,
                    "price_change_1h": pc_1h,
                    "signal": "SUSPICIOUS VOLUME — possible paid promotion"
                })
        return ads
    except:
        return []

# ════════════════════════════════════════════════════════════════
# 4. NARRATIVE THEMES — Cluster tokens by name patterns
# ════════════════════════════════════════════════════════════════
THEMES = {
    "AI": ["ai", "agent", "gpt", "llm", "neural", "brain", "agi"],
    "Meme": ["pepe", "doge", "shib", "wojak", "chad", "meme", "bonk", "wif"],
    "Gaming": ["game", "play", "rpg", "pixel", "quest"],
    "DeFi": ["swap", "dex", "yield", "farm", "lend", "borrow"],
    "Degen": ["degen", "moon", "100x", "gem", "alpha", "based"],
}

def detect_themes(tokens):
    """Detect narrative themes from token names."""
    theme_counts = defaultdict(lambda: {"count": 0, "tokens": []})
    for t in tokens[:20]:
        name_lower = (t.get("name","") + t.get("symbol","")).lower()
        for theme, keywords in THEMES.items():
            if any(kw in name_lower for kw in keywords):
                theme_counts[theme]["count"] += 1
                theme_counts[theme]["tokens"].append(t.get("symbol",""))
    return sorted(theme_counts.values(), key=lambda x: -x["count"])[:5]

# ════════════════════════════════════════════════════════════════
# 5. INFLUENCER ALPHA — Tokens with social buzz
# ════════════════════════════════════════════════════════════════
def get_influencer_alpha(tokens):
    """Identify tokens showing influencer-pattern accumulation."""
    alpha = []
    for t in tokens[:20]:
        buys = t.get("buys_24h", 0)
        sells = t.get("sells_24h", 0)
        vol = t.get("volume_24h", 0)
        # Large buy volume relative to total = possible coordinated buy
        if buys > sells * 2 and vol > 50000:
            alpha.append({
                "symbol": t.get("symbol", ""),
                "name": t.get("name", ""),
                "buy_ratio": round(buys / max(1, sells), 1),
                "volume_24h": vol,
                "signal": f"Whale/influencer accumulation — {buys} buys vs {sells} sells"
            })
    return alpha[:5]

# ════════════════════════════════════════════════════════════════
# MAIN — Generate Top 5 Degen Play
# ════════════════════════════════════════════════════════════════
def run():
    print("═══ solana_alpha_aggregator v1 ═══")
    print("  Top 5 Degen Plays: Graduated + Smart Money + DEX Ads + Themes")

    while True:
        try:
            print(f"\n  [{time.strftime('%H:%M:%S')}] Scanning...")

            # 1. Graduated tokens
            graduated = get_graduated_tokens()
            print(f"  Graduated: {len(graduated)} tokens")

            # 2. Smart money wallets
            smart_wallets = get_smart_money_wallets()
            print(f"  Smart wallets: {len(smart_wallets)}")

            # 3. DEX ads
            ads = get_dex_ads()
            print(f"  DEX ads/volume anomalies: {len(ads)}")

            # 4. Themes
            themes = detect_themes(graduated)
            print(f"  Themes: {len(themes)}")

            # 5. Influencer alpha
            influencer = get_influencer_alpha(graduated)
            print(f"  Influencer signals: {len(influencer)}")

            # Compile Top 5
            top5 = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "top_5_must_buy_degen": [
                    {"symbol": t["symbol"], "score": t["score"], "reason": t["reason"][:100]}
                    for t in graduated[:5]
                ],
                "top_5_must_sell_rotations": [
                    {"symbol": t["symbol"], "score": t["score"], "reason": f"Vol=${t['volume_24h']:,.0f} Sell/Buy ratio high"}
                    for t in graduated[-5:] if t.get("sells_24h", 0) > t.get("buys_24h", 0)
                ],
                "top_5_themes": themes,
                "top_5_smart_money": smart_wallets[:5],
                "top_5_influencers": influencer,
                "dex_ads": ads[:5],
                "total_scanned": len(graduated),
            }

            # Print summary
            for i, t in enumerate(graduated[:5]):
                print(f"  #{i+1} {t['symbol']:12s} score={t['score']} {'🎓' if t['graduated'] else ''} ${t['volume_24h']:,.0f}")

            # Ingest into Vantage
            ingest_top5(top5)

            time.sleep(300)  # Every 5 minutes

        except Exception as e:
            print(f"  ⚠️ Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    run()
