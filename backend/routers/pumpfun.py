"""Pump.fun Degen Trenches — Solana meme coin alpha.
Data: GeckoTerminal (real-time Solana pools), Birdeye (prices), Jupiter (quotes)
"""
import json, urllib.request, hashlib, sqlite3
from pathlib import Path
from fastapi import APIRouter, Query, HTTPException, Header

router = APIRouter(prefix="/api/intel/pumpfun", tags=["pumpfun"])
DB = Path("/opt/ares/Vantage/data/vantage.db")
BIRDEYE = "0e95b1a929b541929e13f53713c0f0fc"
HELIUS = "3b16b895-d4f1-404b-8edd-f3be766830ca"

def get_agent(key):
    h = hashlib.sha256(key.encode()).hexdigest()
    db = sqlite3.connect(str(DB)); db.row_factory = lambda c,r: dict(zip([col[0] for col in c.description], r))
    r = db.execute("SELECT id, name FROM agents WHERE api_key=?", (h,)).fetchone(); db.close()
    return dict(r) if r else None

def _fetch(url, headers=None, timeout=10):
    h = headers or {}
    h['User-Agent'] = 'curl/8.0'
    req = urllib.request.Request(url, headers=h)
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read().decode())

# ════════════════════════════════════════════════════════════════
# NEW LAUNCHES — GeckoTerminal Solana new pools
# ════════════════════════════════════════════════════════════════
@router.get("/new-launches")
async def new_launches(limit: int=20, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    try:
        d = _fetch(f"https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1", {"accept":"application/json"})
        pools = d.get("data",[])
        r = []
        for p in pools[:limit]:
            attrs = p.get("attributes",{})
            name = attrs.get("name","")
            sym = name.split(" / ")[0][:12] if " / " in name else name[:12]
            vol = attrs.get("volume_usd",{}).get("h24",0) if isinstance(attrs.get("volume_usd"),dict) else 0
            pc = attrs.get("price_change_percentage",{}).get("h24",0) if isinstance(attrs.get("price_change_percentage"),dict) else 0
            r.append({"symbol":sym,"name":name,"price":attrs.get("base_token_price_usd",0),"volume_24h":vol,"price_change_24h":pc,"created_at":attrs.get("pool_created_at","")})
        return {"launches":r,"count":len(r),"source":"GeckoTerminal"}
    except:
        return {"launches":[],"count":0,"source":"GeckoTerminal:offline"}

# ════════════════════════════════════════════════════════════════
# TRENDING — GeckoTerminal Solana trending pools (real pump.fun data)
# ════════════════════════════════════════════════════════════════
@router.get("/trending")
async def trending(limit: int=20, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    try:
        d = _fetch("https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1", {"accept":"application/json"})
        pools = d.get("data",[])
        r = []
        for p in pools[:limit]:
            attrs = p.get("attributes",{})
            name = attrs.get("name","")
            sym = name.split(" / ")[0][:12] if " / " in name else name[:12]
            vol = attrs.get("volume_usd",{}).get("h24",0) if isinstance(attrs.get("volume_usd"),dict) else 0
            pc = attrs.get("price_change_percentage",{}).get("h24",0) if isinstance(attrs.get("price_change_percentage"),dict) else 0
            txns = attrs.get("transactions",{}).get("h24",{})
            buys = txns.get("buys",0) if isinstance(txns,dict) else 0
            sells = txns.get("sells",0) if isinstance(txns,dict) else 0
            r.append({"symbol":sym,"name":name,"price":attrs.get("base_token_price_usd",0),"volume_24h":vol,"price_change_24h":pc,"buys_24h":buys,"sells_24h":sells})
        return {"trending":r,"count":len(r),"source":"GeckoTerminal"}
    except:
        return {"trending":[],"count":0,"source":"GeckoTerminal:offline"}

# ════════════════════════════════════════════════════════════════
# BONDING CURVE — Birdeye price check
# ════════════════════════════════════════════════════════════════
@router.get("/bonding-curve")
async def bonding_curve(mint: str=Query(...), x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    try:
        d = _fetch(f"https://public-api.birdeye.so/defi/price?address={mint}", {"X-API-KEY": BIRDEYE, "accept":"application/json"})
        price = float(d.get("data",{}).get("value",0))
        curve_target = 69000
        progress = min(100, round((price * 1_000_000 / curve_target) * 100, 2)) if price else 0
        return {"mint":mint,"price":price,"curve_target_usd":curve_target,"progress_pct":progress,"graduated":progress>=100,"source":"Birdeye"}
    except:
        return {"mint":mint,"price":0,"curve_target_usd":69000,"progress_pct":0,"source":"Birdeye:offline"}

# ════════════════════════════════════════════════════════════════
# GRADUATIONS + WATCHLIST + SIGNALS — DB-backed
# ════════════════════════════════════════════════════════════════
@router.get("/graduations")
async def graduations(limit: int=20, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    db = sqlite3.connect(str(DB)); db.row_factory = lambda c,r: dict(zip([col[0] for col in c.description], r))
    rows = db.execute("SELECT * FROM trading_signals WHERE type='pumpfun' ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall(); db.close()
    return {"graduations":[dict(r) for r in rows],"count":len(rows)}

@router.get("/trades/{mint}")
async def trades(mint: str, limit: int=20, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    try:
        d = _fetch(f"https://quote-api.jup.ag/v6/quote?inputMint={mint}&outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v&amount=1000000&slippageBps=50")
        return {"mint":mint,"in_amount":d.get("inAmount",0),"out_amount":d.get("outAmount",0),"price_impact_pct":float(d.get("priceImpactPct",0)),"routes":len(d.get("routePlan",[])),"source":"Jupiter"}
    except:
        return {"mint":mint,"source":"Jupiter:offline"}

@router.get("/risk/{mint}")
async def risk(mint: str, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    try:
        d = _fetch(f"https://public-api.birdeye.so/defi/price?address={mint}", {"X-API-KEY": BIRDEYE})
        price = float(d.get("data",{}).get("value",0))
        risks = []
        if price < 0.000001: risks.append({"type":"MICRO_CAP","severity":"HIGH","detail":"Price < $0.000001"})
        if price == 0: risks.append({"type":"NO_PRICE","severity":"HIGH","detail":"No price data"})
        score = len([r for r in risks if r["severity"]=="HIGH"]) * 50
        return {"mint":mint,"price":price,"risks":risks,"risk_score":min(100,score),"safe":len(risks)==0}
    except:
        return {"mint":mint,"risk_score":50,"safe":False}

@router.get("/watchlist")
async def watchlist(x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    db = sqlite3.connect(str(DB)); db.row_factory = lambda c,r: dict(zip([col[0] for col in c.description], r))
    rows = db.execute("SELECT * FROM tracked_wallets WHERE chain='pumpfun' ORDER BY created_at DESC LIMIT 50").fetchall(); db.close()
    return {"watchlist":[dict(r) for r in rows],"count":len(rows)}

@router.post("/watchlist")
async def add_watchlist(mint: str=Query(...), label: str=Query(""), x_agent_key: str=Header(...)):
    agent = get_agent(x_agent_key)
    if not agent: raise HTTPException(401)
    db = sqlite3.connect(str(DB))
    db.execute("INSERT OR IGNORE INTO tracked_wallets (chain,address,label,added_by_agent_id) VALUES (?,?,?,?)", ("pumpfun", mint, label or f"Pumpfun-{mint[:8]}", agent["id"]))
    db.commit(); db.close()
    return {"status":"added","mint":mint}

@router.get("/signals")
async def signals(limit: int=20, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    db = sqlite3.connect(str(DB)); db.row_factory = lambda c,r: dict(zip([col[0] for col in c.description], r))
    rows = db.execute("SELECT * FROM trading_signals WHERE type='pumpfun' ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall(); db.close()
    return {"signals":[dict(r) for r in rows],"count":len(rows),"source":"pumpfun"}

@router.get("/detect")
async def detect(address: str = Query(...), x_agent_key: str = Header(...)):
    """Auto-detect: wallet vs token mint (CA) on Solana via Helius RPC."""
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    try:
        payload = json.dumps({"jsonrpc":"2.0","id":1,"method":"getAccountInfo","params":[address,{"encoding":"jsonParsed"}]}).encode()
        req = urllib.request.Request(f"https://mainnet.helius-rpc.com/?api-key={HELIUS}",data=payload,headers={"Content-Type":"application/json"})
        resp = json.loads(urllib.request.urlopen(req,timeout=10).read().decode())
        info = resp.get("result",{}).get("value",{})
        if not info: return {"address":address,"type":"not_found","label":"Account not found","action":"none"}
        owner = info.get("owner","")
        data = info.get("data",{}).get("parsed",{}).get("info",{}) if info else {}
        program = info.get("data",{}).get("program","")
        if program == "spl-token":
            return {"address":address,"type":"token_mint","label":"Token Mint (CA)","action":"add_to_pumpfun","supply":data.get("supply","0"),"decimals":data.get("decimals",0),"mint_authority":data.get("mintAuthority")}
        elif program == "spl-token-2022":
            return {"address":address,"type":"token_mint_2022","label":"Token Mint 2022 (CA)","action":"add_to_pumpfun","supply":data.get("supply","0")}
        elif owner == "11111111111111111111111111111111":
            return {"address":address,"type":"wallet","label":"Wallet (System)","action":"add_to_watchlist","sol_balance":info.get("lamports",0)/1e9}
        elif owner == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":
            return {"address":address,"type":"token_account","label":"Token Account","action":"add_to_watchlist"}
        elif owner == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P":
            return {"address":address,"type":"pumpfun_program","label":"Pump.fun Program","action":"none"}
        else:
            return {"address":address,"type":"unknown","label":"Unknown","action":"review","owner":owner[:20]}
    except Exception as e:
        return {"address":address,"error":str(e)[:100]}

@router.get("/token/holders")
async def token_holders(mint: str = Query(...), limit: int = Query(20), x_agent_key: str = Header(...)):
    """Top token holders via Birdeye."""
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    try:
        d = _fetch(f"https://public-api.birdeye.so/defi/v3/token/holder?address={mint}&limit={limit}", {"X-API-KEY":BIRDEYE,"accept":"application/json"})
        items = d.get("data",{}).get("items",d.get("data",[]))
        if not isinstance(items,list): items=[]
        holders = []
        total_pct = 0
        for h in items[:limit]:
            pct = float(h.get("percentage",h.get("pct",0)))
            total_pct += pct
            holders.append({"wallet":h.get("owner",h.get("address","")),"amount":float(h.get("ui_amount",h.get("amount",0))),"pct":pct})
        return {"mint":mint,"holders":holders,"count":len(holders),"top5_pct":round(total_pct,2),"concentrated":total_pct>20}
    except:
        return {"mint":mint,"holders":[],"count":0}

@router.get("/token/creator")
async def token_creator(mint: str = Query(...), x_agent_key: str = Header(...)):
    """Token creator from Pump.fun frontend API."""
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    try:
        d = {}
        # Fallback: use Helius token metadata
        try:
            from urllib.request import Request, urlopen
            payload = json.dumps({"jsonrpc":"2.0","id":1,"method":"getAsset","params":{"id":mint}}).encode()
            req = Request(f"https://mainnet.helius-rpc.com/?api-key={HELIUS}",data=payload,headers={"Content-Type":"application/json"})
            asset = json.loads(urlopen(req,timeout=10).read().decode()).get("result",{})
            d = {
                "creator": asset.get("creators",[{}])[0].get("address","") if asset.get("creators") else "",
                "name": asset.get("content",{}).get("metadata",{}).get("name",""),
                "symbol": asset.get("content",{}).get("metadata",{}).get("symbol","")[:8] if asset.get("content",{}).get("metadata",{}).get("symbol") else "",
                "description": (asset.get("content",{}).get("metadata",{}).get("description","") or ""),
            }
        except: pass
        return {"mint":mint,"creator":d.get("creator",d.get("creatorAddress","")),"name":d.get("name",""),"symbol":d.get("symbol",""),"description":d.get("description","")[:200],"twitter":d.get("twitter",""),"website":d.get("website",""),"created_at":d.get("created_timestamp","")}
    except:
        return {"mint":mint,"creator":"","error":"Pump.fun API unavailable"}

@router.get("/token/traders")
async def token_traders(mint: str = Query(...), x_agent_key: str = Header(...)):
    """Top traders for a token via Helius RPC."""
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    try:
        import urllib.request as ur
        payload = json.dumps({"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[mint,{"limit":30}]}).encode()
        req = ur.Request(f"https://mainnet.helius-rpc.com/?api-key={HELIUS}",data=payload,headers={"Content-Type":"application/json"})
        sigs = json.loads(ur.urlopen(req,timeout=10).read().decode()).get("result",[])
        trader_vol={}
        for s in sigs[:30]:
            txn_payload = json.dumps({"jsonrpc":"2.0","id":2,"method":"getTransaction","params":[s["signature"],{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]}).encode()
            txn_req = ur.Request(f"https://mainnet.helius-rpc.com/?api-key={HELIUS}",data=txn_payload,headers={"Content-Type":"application/json"})
            try:
                txn = json.loads(ur.urlopen(txn_req,timeout=5).read().decode()).get("result",{})
                accts = txn.get("transaction",{}).get("message",{}).get("accountKeys",[])
                signer = accts[0]["pubkey"] if isinstance(accts[0],dict) else accts[0]
                trader_vol[signer] = trader_vol.get(signer,0)+1
            except: pass
        top = sorted(trader_vol.items(),key=lambda x:-x[1])[:10]
        return {"mint":mint,"traders":[{"wallet":w,"txn_count":c} for w,c in top],"unique_traders":len(trader_vol)}
    except:
        return {"mint":mint,"traders":[],"error":"RPC unavailable"}
