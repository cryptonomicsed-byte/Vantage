"""pumpfun_wallet_intel — Holder, creator, and trader enrichment daemon.
Monitors pumpfun watchlist tokens, enriches with wallet intelligence,
ingests signals when concentrated or insider activity detected.
"""
import json, sqlite3, os, sys, signal, time, urllib.request

DB = "/opt/ares/Vantage/data/vantage.db"
HELIUS_KEY = os.environ.get("HELIUS_API_KEY", "")
BIRDEYE_KEY = os.environ.get("BIRDEYE_KEY", "")
VANTAGE_URL = "http://localhost:8001/api/trading/signals/ingest"
VANTAGE_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()

def rpc(method, params):
    payload = json.dumps(dict(jsonrpc="2.0", id=1, method=method, params=params)).encode()
    req = urllib.request.Request(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}", data=payload, headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

def birdeye(path):
    req = urllib.request.Request(f"https://public-api.birdeye.so/{path}", headers={"X-API-KEY":BIRDEYE_KEY,"accept":"application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

def pumpfun_api(mint):
    req = urllib.request.Request(f"https://frontend-api.pump.fun/coins/{mint}", headers={"accept":"application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

def ingest(symbol, direction, conviction, sig_type, detail):
    try:
        payload = json.dumps(dict(symbol=symbol, source="pumpfun_wallet_intel", direction=direction, conviction=conviction, type=sig_type, detail=detail)).encode()
        req = urllib.request.Request(VANTAGE_URL, data=payload, headers={"Content-Type":"application/json","X-Agent-Key":VANTAGE_KEY})
        urllib.request.urlopen(req, timeout=5)
    except:
        pass

def add_wallet_to_watchlist(address, chain, label):
    try:
        payload = json.dumps(dict(address=address, chain=chain, label=label)).encode()
        req = urllib.request.Request("http://localhost:8001/api/intel/watchlist", data=payload, headers={"Content-Type":"application/json","X-Agent-Key":VANTAGE_KEY})
        urllib.request.urlopen(req, timeout=5)
    except:
        pass

# ── 1. TOP HOLDERS ──────────────────────────────────────────────────
def get_top_holders(mint, limit=10):
    """Get top token holders using Birdeye v3 holder endpoint."""
    try:
        d = birdeye(f"defi/v3/token/holder?address={mint}&limit={limit}")
        holders = d.get("data", {}).get("items", d.get("data", []))
        if not isinstance(holders, list): holders = []
        results = []
        total_pct = 0
        for h in holders[:limit]:
            pct = float(h.get("percentage", h.get("pct", 0)))
            total_pct += pct
            results.append({
                "wallet": h.get("owner", h.get("address", "")),
                "amount": float(h.get("ui_amount", h.get("amount", 0))),
                "pct": pct,
            })
        return {
            "holders": results,
            "top5_pct": round(total_pct, 2) if results else 0,
            "total_holders": len(holders),
            "concentrated": total_pct > 20,
        }
    except:
        return {"holders": [], "top5_pct": 0, "concentrated": False, "error": "Birdeye v3 unavailable"}

# ── 2. TOKEN CREATOR ─────────────────────────────────────────────────
def get_creator(mint):
    """Get token creator from Pump.fun frontend API."""
    try:
        d = pumpfun_api(mint)
        creator = d.get("creator", d.get("creatorAddress", ""))
        name = d.get("name", mint[:8])
        symbol = d.get("symbol", "")
        website = d.get("website", "")
        twitter = d.get("twitter", "")
        created_at = d.get("created_timestamp", "")
        
        if creator:
            # Add creator to wallet watchlist for monitoring
            add_wallet_to_watchlist(creator, "solana", f"Creator: {symbol or name}")
        
        return {
            "creator": creator,
            "has_creator": bool(creator),
            "name": name,
            "symbol": symbol,
            "socials": {"website": website, "twitter": twitter},
            "created_at": created_at,
        }
    except:
        return {"creator": "", "has_creator": False, "error": "Pump.fun API unavailable"}

# ── 3. TOP TRADERS ───────────────────────────────────────────────────
def get_top_traders(mint, limit=5):
    """Get top traders for a token using recent transaction signatures."""
    try:
        sigs = rpc("getSignaturesForAddress", [mint, {"limit": 50}])
        sig_list = sigs.get("result", [])
        
        trader_vol = {}
        for s in sig_list:
            txn = rpc("getTransaction", [s["signature"], {"encoding":"jsonParsed","maxSupportedTransactionVersion":0}])
            accts = txn.get("result", {}).get("transaction", {}).get("message", {}).get("accountKeys", [])
            if not accts:
                continue
            signer = accts[0].get("pubkey") if isinstance(accts[0], dict) else accts[0]
            trader_vol[signer] = trader_vol.get(signer, 0) + 1
        
        # Sort by transaction count
        top = sorted(trader_vol.items(), key=lambda x: -x[1])[:limit]
        
        return {
            "traders": [{"wallet": w, "txn_count": c} for w, c in top],
            "unique_traders": len(trader_vol),
            "total_txns": len(sig_list),
        }
    except:
        return {"traders": [], "unique_traders": 0, "error": "RPC unavailable"}

# ── ENRICHMENT FLOW ──────────────────────────────────────────────────
def enrich_token(mint):
    """Full enrichment pipeline for a pumpfun token."""
    print(f"\n  Enriching: {mint[:16]}...")
    
    # 1. Holders
    holders = get_top_holders(mint)
    print(f"    Holders: {len(holders['holders'])} found, top5={holders['top5_pct']}% {'⚠️ CONCENTRATED' if holders['concentrated'] else ''}")
    if holders["concentrated"]:
        ingest(mint[:12], "SELL", 0.7, "pumpfun", f"Top 5 holders own {holders['top5_pct']}% — concentration risk")
    
    # 2. Creator
    creator = get_creator(mint)
    print(f"    Creator: {creator['creator'][:20]}..." if creator['has_creator'] else "    Creator: not found")
    if creator.get("twitter"):
        print(f"    Twitter: {creator['twitter']}")
    
    # 3. Traders
    traders = get_top_traders(mint)
    print(f"    Traders: {traders['unique_traders']} unique, {len(traders['traders'])} top traders")
    
    # Return enrichment summary
    return {
        "mint": mint,
        "holders": holders,
        "creator": creator,
        "traders": traders,
        "alerts": [
            f"Holders concentrated: top5={holders['top5_pct']}%" if holders.get("concentrated") else None,
            f"Creator tracked: {creator['creator'][:12]}..." if creator.get("has_creator") else None,
        ],
    }

# ── MAIN LOOP ────────────────────────────────────────────────────────
def get_watchlist():
    db = sqlite3.connect(DB)
    rows = db.execute("SELECT address FROM tracked_wallets WHERE chain='pumpfun' ORDER BY created_at DESC LIMIT 20").fetchall()
    db.close()
    return [r[0] for r in rows]

def run():
    print("═══ pumpfun_wallet_intel v1 ═══")
    print("  Enriching pumpfun tokens with holder/creator/trader intel")
    
    processed = set()
    
    while True:
        try:
            watchlist = get_watchlist()
            if not watchlist:
                print(f"  [{time.strftime('%H:%M:%S')}] No pumpfun tokens in watchlist")
                time.sleep(60)
                continue
            
            print(f"\n  [{time.strftime('%H:%M:%S')}] {len(watchlist)} tokens on watchlist")
            
            for mint in watchlist[:10]:  # Process max 10 per cycle
                if mint in processed:
                    continue
                try:
                    result = enrich_token(mint)
                    processed.add(mint)
                except Exception as e:
                    print(f"    Enrichment failed: {e}")
            
            # Add creator wallets to watchlist for monitoring
            if len(processed) >= len(watchlist):
                processed.clear()  # Re-process periodically
                
            time.sleep(300)  # Every 5 minutes
            
        except Exception as e:
            print(f"  ⚠️ Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    if "--daemon" in sys.argv:
        pid = os.fork()
        if pid > 0: sys.exit(0)
        os.setsid()
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    run()
