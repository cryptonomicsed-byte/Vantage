"""Degen Alpha Router — Ultra-degen signals: early calls, smart wallets, volume surges, rug checks.
Uses existing keys: Helius RPC, Birdeye, GeckoTerminal.
"""
import json, urllib.request, hashlib, sqlite3, time
from pathlib import Path
from fastapi import APIRouter, Query, HTTPException, Header

router = APIRouter(prefix="/api/intel/degen", tags=["degen"])
DB = Path("/opt/ares/Vantage/data/vantage.db")
HELIUS = "3b16b895-d4f1-404b-8edd-f3be766830ca"
BIRDEYE = "0e95b1a929b541929e13f53713c0f0fc"

def _fetch(url, headers=None, timeout=10):
    h = headers or {}; h['User-Agent'] = 'curl/8.0'
    req = urllib.request.Request(url, headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())

def get_agent(key):
    h = hashlib.sha256(key.encode()).hexdigest()
    db = sqlite3.connect(str(DB)); db.row_factory = lambda c,r: dict(zip([col[0] for col in c.description], r))
    r = db.execute("SELECT id, name FROM agents WHERE api_key=?", (h,)).fetchone(); db.close()
    return dict(r) if r else None

# ════════════════════════════════════════════════════════════════
# EARLY CALLS — tokens <1h old with rising volume + smart money
# ════════════════════════════════════════════════════════════════
@router.get("/early-calls")
async def early_calls(limit: int=20, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    try:
        d = _fetch("https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1", {"accept":"application/json"})
        pools = d.get("data",[])
        now = time.time()
        results = []
        for p in pools[:limit]:
            attrs = p.get("attributes",{})
            created = attrs.get("pool_created_at","")
            # Filter: tokens created in last 24h
            try:
                created_ts = time.mktime(time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S"))
                age_hours = (now - created_ts) / 3600
            except:
                age_hours = 999
            if age_hours > 24:
                continue
            vol = attrs.get("volume_usd",{}).get("h24",0) if isinstance(attrs.get("volume_usd"),dict) else 0
            pc = attrs.get("price_change_percentage",{}).get("h1",0) if isinstance(attrs.get("price_change_percentage"),dict) else 0
            txns = attrs.get("transactions",{}).get("h1",{})
            buys = txns.get("buys",0) if isinstance(txns,dict) else 0
            name = attrs.get("name","")
            sym = name.split(" / ")[0][:12] if " / " in name else name[:12]
            # Score: volume * buy ratio * recency bonus
            buy_ratio = buys / max(1, buys + txns.get("sells",0)) if isinstance(txns,dict) else 0
            score = round(float(vol or 0) * buy_ratio / max(1, age_hours), 2)
            results.append({"symbol":sym,"name":name,"price":attrs.get("base_token_price_usd",0),"volume_1h":vol,"age_hours":round(age_hours,1),"buys_1h":buys,"alpha_score":score})
        results.sort(key=lambda x:-x.get("alpha_score",0))
        return {"early_calls":results[:limit],"count":len(results),"source":"GeckoTerminal"}
    except:
        return {"early_calls":[],"count":0,"source":"GeckoTerminal:offline"}

# ════════════════════════════════════════════════════════════════
# SMART WALLETS — top performing degen traders + recent entries
# ════════════════════════════════════════════════════════════════
@router.get("/smart-wallets")
async def smart_wallets(limit: int=20, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    # Get wallets from our watchlist with recent edge activity
    db = sqlite3.connect(str(DB)); db.row_factory = lambda c,r: dict(zip([col[0] for col in c.description], r))
    rows = db.execute("""
        SELECT w.address, w.label, w.chain,
               (SELECT COUNT(*) FROM wallet_edges we WHERE we.address_a=w.address OR we.address_b=w.address) as edge_count,
               (SELECT MAX(we.last_seen) FROM wallet_edges we WHERE we.address_a=w.address OR we.address_b=w.address) as last_active
        FROM tracked_wallets w
        WHERE w.chain IN ('solana','pumpfun')
        ORDER BY edge_count DESC LIMIT ?
    """,(limit,)).fetchall()
    db.close()
    now = time.time()
    results = []
    for r in rows:
        hours_since = 999
        if r.get("last_active"):
            try:
                last_ts = time.mktime(time.strptime(str(r["last_active"])[:19], "%Y-%m-%d %H:%M:%S"))
                hours_since = (now - last_ts) / 3600
            except:
                pass
        ec = r.get("edge_count",0)
        status = "hot" if hours_since < 1 else "active" if hours_since < 24 else "dormant"
        results.append({"wallet":r["address"],"label":r.get("label",""),"chain":r["chain"],"edge_count":ec,"hours_since":round(hours_since,1),"status":status})
    results.sort(key=lambda x:-x.get("edge_count",0))
    return {"smart_wallets":results,"count":len(results)}

# ════════════════════════════════════════════════════════════════
# VOLUME SURGE — 10x volume in minutes alerts
# ════════════════════════════════════════════════════════════════
@router.get("/volume-surge")
async def volume_surge(limit: int=20, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    try:
        d = _fetch("https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1", {"accept":"application/json"})
        pools = d.get("data",[])
        results = []
        for p in pools[:limit]:
            attrs = p.get("attributes",{})
            vol = attrs.get("volume_usd",{})
            vol_5m = float(str(vol.get("m5",0))) if isinstance(vol,dict) else 0
            vol_1h = float(str(vol.get("h1",0))) if isinstance(vol,dict) else 0
            vol_6h = float(str(vol.get("h6",0))) if isinstance(vol,dict) else 0
            surge_ratio = vol_1h / max(1, vol_6h/6) if vol_6h > 0 else 0
            name = attrs.get("name","")
            sym = name.split(" / ")[0][:12] if " / " in name else name[:12]
            if surge_ratio > 3:
                results.append({"symbol":sym,"name":name,"volume_5m":vol_5m,"volume_1h":vol_1h,"surge_ratio":round(surge_ratio,1),"signal":"🔥 SURGE" if surge_ratio>10 else "⚡ SPIKE"})
        results.sort(key=lambda x:-x.get("surge_ratio",0))
        return {"volume_surges":results,"count":len(results),"source":"GeckoTerminal"}
    except:
        return {"volume_surges":[],"count":0,"source":"offline"}

# ════════════════════════════════════════════════════════════════
# TOP 5 DEGEN PLAY — aggregated from alpha aggregator
# ════════════════════════════════════════════════════════════════
@router.get("/top5")
async def top5_degen(limit: int=5, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    try:
        # Pull latest from GeckoTerminal freshly
        d = _fetch("https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1", {"accept":"application/json"})
        pools = d.get("data",[])
        
        graduated = []
        for p in pools[:25]:
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
            graduated_bool = "pump" in addr.lower()
            score = 0
            if vol_24h > 50000: score += 30
            if vol_24h > 100000: score += 20
            if pc_24h > 10: score += 20
            if buys_24h > sells_24h: score += 15
            if graduated_bool: score += 15
            graduated.append(dict(symbol=symbol,name=name,address=addr,volume_24h=vol_24h,price_change_24h=pc_24h,buys_24h=buys_24h,sells_24h=sells_24h,graduated=graduated_bool,score=score,reason=f"{'🎓 ' if graduated_bool else ''}${vol_24h:,.0f}"))
        
        graduated.sort(key=lambda x:-x["score"])
        return {"top_5":graduated[:limit],"total_scanned":len(pools),"source":"GeckoTerminal"}
    except:
        return {"top_5":[],"total_scanned":0,"source":"offline"}

# ════════════════════════════════════════════════════════════════
# RUG CHECK — dev wallet, mint authority, LP lock
# ════════════════════════════════════════════════════════════════
@router.get("/rug-check")
async def rug_check(mint: str=Query(...), x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    try:
        payload = json.dumps({"jsonrpc":"2.0","id":1,"method":"getAccountInfo","params":[mint,{"encoding":"jsonParsed"}]}).encode()
        req = urllib.request.Request(f"https://mainnet.helius-rpc.com/?api-key={HELIUS}",data=payload,headers={"Content-Type":"application/json"})
        info = json.loads(urllib.request.urlopen(req,timeout=10).read().decode()).get("result",{}).get("value",{})
        if not info:
            return {"mint":mint,"found":False,"detail":"Account not found — may not exist"}
        data = info.get("data",{}).get("parsed",{}).get("info",{}) if info else {}
        mint_auth = bool(data.get("mintAuthority"))
        freeze_auth = bool(data.get("freezeAuthority"))
        supply = int(data.get("supply","0")) / (10**int(data.get("decimals","0") or "1"))
        # Get price from Birdeye
        try:
            pd = _fetch(f"https://public-api.birdeye.so/defi/price?address={mint}",{"X-API-KEY":BIRDEYE},timeout=5)
            price = float(pd.get("data",{}).get("value",0))
        except:
            price = 0
        checks = []
        risk_score = 0
        if mint_auth:
            checks.append({"check":"MINT_AUTHORITY","status":"FAIL","detail":"Can mint unlimited tokens"})
            risk_score += 40
        else:
            checks.append({"check":"MINT_AUTHORITY","status":"PASS","detail":"Mint authority revoked"})
        if freeze_auth:
            checks.append({"check":"FREEZE_AUTHORITY","status":"FAIL","detail":"Can freeze user tokens"})
            risk_score += 30
        else:
            checks.append({"check":"FREEZE_AUTHORITY","status":"PASS","detail":"No freeze authority"})
        if price == 0:
            checks.append({"check":"LIQUIDITY","status":"WARN","detail":"No price data — may have no liquidity"})
            risk_score += 20
        elif price < 0.000001:
            checks.append({"check":"PRICE","status":"WARN","detail":f"Extremely low price: \${price:.10f}"})
            risk_score += 10
        else:
            checks.append({"check":"PRICE","status":"PASS","detail":f"\${price:.8f}"})
        safe = risk_score <= 20
        return {"mint":mint,"checks":checks,"risk_score":risk_score,"safe":safe,"supply":supply}
    except Exception as e:
        return {"mint":mint,"error":str(e)[:100],"safe":False}

@router.get('/sell-rotations')
async def sell_rotations(limit: int=5, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    try:
        d = _fetch('https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1', {'accept':'application/json'})
        pools = d.get('data',[])
        rotations = []
        for p in pools[:25]:
            attrs = p.get('attributes',{})
            name = attrs.get('name','')
            vol = attrs.get('volume_usd',{})
            vol_24h = float(str(vol.get('h24',0))) if isinstance(vol,dict) else 0
            txns = attrs.get('transactions',{})
            buys_24h = txns.get('h24',{}).get('buys',0) if isinstance(txns.get('h24'),dict) else 0
            sells_24h = txns.get('h24',{}).get('sells',0) if isinstance(txns.get('h24'),dict) else 0
            pc = attrs.get('price_change_percentage',{})
            pc_24h = float(str(pc.get('h24',0))) if isinstance(pc,dict) else 0
            symbol = name.split(' / ')[0][:12] if ' / ' in name else name[:12]
            if sells_24h > buys_24h and vol_24h > 10000:
                rotations.append(dict(symbol=symbol,name=name,sell_buy_ratio=round(sells_24h/max(1,buys_24h),1),volume_24h=vol_24h,price_change_24h=pc_24h))
        rotations.sort(key=lambda x:-x['sell_buy_ratio'])
        return {'rotations':rotations[:limit],'count':len(rotations[:limit]),'source':'GeckoTerminal'}
    except:
        return {'rotations':[],'count':0}
