"""Pump.fun Degen Trenches — Solana meme coin alpha.
Data: GeckoTerminal (real-time Solana pools), Birdeye (prices), Jupiter (quotes)
"""
import json, os, urllib.request, hashlib, sqlite3
from pathlib import Path
from fastapi import APIRouter, Query, HTTPException, Header

router = APIRouter(prefix="/api/intel/pumpfun", tags=["pumpfun"])
DB = Path("/opt/ares/Vantage/data/vantage.db")
BIRDEYE = os.environ.get("BIRDEYE_KEY", "")
HELIUS = os.environ.get("HELIUS_API_KEY", "")

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
def _mint_from_pool(p: dict) -> str:
    """p['id'] is the POOL address, not the token mint — same bug found and
    fixed today in degen_alpha_fusion.py/ogun_multiscan.py. The real mint
    is relationships.base_token.data.id ('solana_<mint>'). Without this,
    every card built from these endpoints has no CA, so EntityProfileCard
    can't show the trade panel at all — that's the actual root cause of
    "trending/new-launches show a different/limited card"."""
    base_token_id = p.get("relationships",{}).get("base_token",{}).get("data",{}).get("id","")
    return base_token_id.split("_",1)[-1] if "_" in base_token_id else ""

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
            r.append({"symbol":sym,"name":name,"address":_mint_from_pool(p),"price":attrs.get("base_token_price_usd",0),"volume_24h":vol,"price_change_24h":pc,"created_at":attrs.get("pool_created_at","")})
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
            r.append({"symbol":sym,"name":name,"address":_mint_from_pool(p),"price":attrs.get("base_token_price_usd",0),"volume_24h":vol,"price_change_24h":pc,"buys_24h":buys,"sells_24h":sells})
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
# GRADUATIONS — was querying trading_signals WHERE type='pumpfun': that
# table has no 'type' column and no 'timestamp' column either — this has
# 500'd on every single call since it was written, nothing has ever landed
# in the "Recently Graduated" section. Rewritten to real data: any pool
# GeckoTerminal indexes necessarily already has a live DEX liquidity pair,
# which for a pump.fun-origin token (mint ends in the program's "pump"
# vanity suffix) can only be true post-migration — GeckoTerminal doesn't
# see bonding-curve-only tokens at all. Recency + real liquidity is the
# actual "just graduated" signal here, not a DB flag nothing ever set.
# ════════════════════════════════════════════════════════════════
@router.get("/graduations")
async def graduations(limit: int=20, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    try:
        d = _fetch("https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1", {"accept":"application/json"})
        pools = d.get("data",[])
        r = []
        for p in pools:
            mint = _mint_from_pool(p)
            if not mint.endswith("pump"):
                continue  # not a pump.fun-origin token — not a "graduation" in the sense this section means
            attrs = p.get("attributes",{})
            name = attrs.get("name","")
            sym = name.split(" / ")[0][:12] if " / " in name else name[:12]
            vol = attrs.get("volume_usd",{}).get("h24",0) if isinstance(attrs.get("volume_usd"),dict) else 0
            liq = attrs.get("reserve_in_usd", 0)
            r.append({"symbol":sym,"name":name,"address":mint,"volume_24h":vol,"liquidity_usd":float(liq or 0),"pool_created_at":attrs.get("pool_created_at","")})
            if len(r) >= limit:
                break
        r.sort(key=lambda x: x.get("pool_created_at") or "", reverse=True)
        return {"graduations":r,"count":len(r),"source":"GeckoTerminal"}
    except Exception:
        return {"graduations":[],"count":0,"source":"GeckoTerminal:offline"}

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
        d = _fetch(f"https://frontend-api.pump.fun/coins/{mint}",{"accept":"application/json"})
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

# ════════════════════════════════════════════════════════════════
# TRACE BY TOKEN — the on-demand version of what pumpfun_wallet_intel.py's
# background daemon already does on its own 10-min cycle for whatever
# tokens surface in top5/must-buy-20/social mentions. This lets the Trace
# tab run the exact same deployer + top-holder + top-trader + first-buyer
# extraction immediately for ANY token, not just whatever the daemon
# happened to already reach. Same persistence (token_wallet_roles +
# tracked_wallets) — a wallet found here is a real graph node right away,
# same as the daemon's own output, not a separate/throwaway preview.
#
# Runs the daemon's actual enrich_token() via asyncio.to_thread — that
# function uses blocking urllib + time.sleep() internally (shared-quota
# throttling against Helius/Birdeye), which would deadlock this process's
# event loop if awaited directly inside an async def (the same class of
# bug found and fixed elsewhere this session, e.g. telegram_webhook.py).
# ════════════════════════════════════════════════════════════════
@router.post("/trace-token/{mint}")
async def trace_token(mint: str, symbol: str = Query(""), x_agent_key: str = Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    import asyncio, sys
    sys.path.insert(0, "/opt/ares")
    import pumpfun_wallet_intel as pwi
    try:
        result = await asyncio.to_thread(pwi.enrich_token, mint, symbol)
    except Exception as e:
        raise HTTPException(502, f"Enrichment failed: {e}")
    holders = result.get("holders", {}).get("holders", [])
    traders = result.get("traders", {}).get("traders", [])
    first_buyers = result.get("traders", {}).get("first_buyers", [])
    creator = result.get("creator", {}).get("creator", "")
    return {
        "mint": mint, "symbol": symbol,
        "deployer": creator,
        "top_holders": holders,
        "top_traders": traders,
        "first_buyers": first_buyers,
        "concentrated": result.get("holders", {}).get("concentrated", False),
        "wallets_tracked": len({w for w in [creator] + [h["wallet"] for h in holders] + [t["wallet"] for t in traders] + [b["wallet"] for b in first_buyers] if w}),
    }
