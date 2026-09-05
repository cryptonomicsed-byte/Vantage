"""Pump.fun Degen Trenches — Solana meme coin alpha.
Data: GeckoTerminal (real-time Solana pools), Birdeye (prices), Jupiter (quotes)
"""
import asyncio, json, os, urllib.request, hashlib
from pathlib import Path
from fastapi import APIRouter, Query, HTTPException, Header, UploadFile, File, Form
import aiosqlite
import httpx
from backend.db import get_db
from backend.routers.alpha import _dexscreener_mcap
from backend.crypto_utils import decrypt_key_for_agent
from contextlib import asynccontextmanager

router = APIRouter(prefix="/api/intel/pumpfun", tags=["pumpfun"])
DB = Path("/opt/ares/Vantage/data/vantage.db")
BIRDEYE = os.environ.get("BIRDEYE_KEY", "")
HELIUS = os.environ.get("HELIUS_API_KEY", "")
PINATA_JWT = os.environ.get("PINATA_JWT", "")


@asynccontextmanager
async def _db():
    """Pooled connection via get_db() -- bounded by the shared semaphore
    with busy_timeout=30000, and carrying the dict row_factory every
    endpoint here expects (downstream code calls .get() on rows, which
    sqlite3.Row/aiosqlite.Row doesn't support)."""
    async with get_db() as conn:
        conn.row_factory = lambda cur, row: dict(zip([c[0] for c in cur.description], row))
        yield conn


async def get_agent(key):
    h = hashlib.sha256(key.encode()).hexdigest()
    async with _db() as db:
        cur = await db.execute("SELECT id, name FROM agents WHERE api_key=?", (h,))
        r = await cur.fetchone()
        return dict(r) if r else None

def _fetch_sync(url, headers=None, timeout=10):
    h = headers or {}
    h['User-Agent'] = 'curl/8.0'
    req = urllib.request.Request(url, headers=h)
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read().decode())

async def _fetch(url, headers=None, timeout=10):
    """Off the event loop -- calling urlopen directly from an async handler
    used to freeze every other in-flight request (DB ops included) for the
    full duration of each upstream call."""
    return await asyncio.to_thread(_fetch_sync, url, headers, timeout)

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
    if not await get_agent(x_agent_key): raise HTTPException(401)
    try:
        d = await _fetch(f"https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1", {"accept":"application/json"})
        pools = d.get("data",[])
        r = []
        for p in pools[:limit]:
            attrs = p.get("attributes",{})
            name = attrs.get("name","")
            sym = name.split(" / ")[0][:12] if " / " in name else name[:12]
            vol = attrs.get("volume_usd",{}).get("h24",0) if isinstance(attrs.get("volume_usd"),dict) else 0
            pc = attrs.get("price_change_percentage",{}).get("h24",0) if isinstance(attrs.get("price_change_percentage"),dict) else 0
            r.append({"symbol":sym,"name":name,"address":_mint_from_pool(p),"price":attrs.get("base_token_price_usd",0),"volume_24h":vol,"price_change_24h":pc,"created_at":attrs.get("pool_created_at","")})

        # Real market cap + liquidity, bounded to just the `limit` rows
        # returned (never the full scanned pool) -- same labeled pattern as
        # degen.py's /top5. A raw 24h volume figure isn't market cap, and a
        # brand-new pool can show real volume on near-zero real liquidity.
        mcaps = await asyncio.gather(
            *[_dexscreener_mcap(row["address"]) if row["address"] else asyncio.sleep(0, result=None) for row in r],
            return_exceptions=True,
        )
        for row, snap in zip(r, mcaps):
            row["market_cap"] = snap.get("market_cap") if isinstance(snap, dict) else None
            row["liquidity_usd"] = snap.get("liquidity_usd") if isinstance(snap, dict) else None

        return {"launches":r,"count":len(r),"source":"GeckoTerminal"}
    except:
        return {"launches":[],"count":0,"source":"GeckoTerminal:offline"}

# ════════════════════════════════════════════════════════════════
# TRENDING — GeckoTerminal Solana trending pools (real pump.fun data)
# ════════════════════════════════════════════════════════════════
@router.get("/trending")
async def trending(limit: int=20, x_agent_key: str=Header(...)):
    if not await get_agent(x_agent_key): raise HTTPException(401)
    try:
        d = await _fetch("https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1", {"accept":"application/json"})
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

        # Real market cap + liquidity, bounded to just the `limit` rows
        # returned. Same pattern as /new-launches above.
        mcaps = await asyncio.gather(
            *[_dexscreener_mcap(row["address"]) if row["address"] else asyncio.sleep(0, result=None) for row in r],
            return_exceptions=True,
        )
        for row, snap in zip(r, mcaps):
            row["market_cap"] = snap.get("market_cap") if isinstance(snap, dict) else None
            row["liquidity_usd"] = snap.get("liquidity_usd") if isinstance(snap, dict) else None

        return {"trending":r,"count":len(r),"source":"GeckoTerminal"}
    except:
        return {"trending":[],"count":0,"source":"GeckoTerminal:offline"}

# ════════════════════════════════════════════════════════════════
# BONDING CURVE — Birdeye price check
# ════════════════════════════════════════════════════════════════
@router.get("/bonding-curve")
async def bonding_curve(mint: str=Query(...), x_agent_key: str=Header(...)):
    if not await get_agent(x_agent_key): raise HTTPException(401)
    try:
        d = await _fetch(f"https://public-api.birdeye.so/defi/price?address={mint}", {"X-API-KEY": BIRDEYE, "accept":"application/json"})
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
    if not await get_agent(x_agent_key): raise HTTPException(401)
    try:
        d = await _fetch("https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1", {"accept":"application/json"})
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

        # Real market cap, bounded to just the rows returned. liquidity_usd
        # here is already real (GeckoTerminal reserve_in_usd) -- market_cap
        # fills the other half of "is this graduation actually worth
        # anything" that volume/liquidity alone doesn't answer.
        mcaps = await asyncio.gather(
            *[_dexscreener_mcap(row["address"]) for row in r], return_exceptions=True
        )
        for row, snap in zip(r, mcaps):
            row["market_cap"] = snap.get("market_cap") if isinstance(snap, dict) else None

        return {"graduations":r,"count":len(r),"source":"GeckoTerminal"}
    except Exception:
        return {"graduations":[],"count":0,"source":"GeckoTerminal:offline"}

@router.get("/trades/{mint}")
async def trades(mint: str, limit: int=20, x_agent_key: str=Header(...)):
    if not await get_agent(x_agent_key): raise HTTPException(401)
    try:
        d = await _fetch(f"https://quote-api.jup.ag/v6/quote?inputMint={mint}&outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v&amount=1000000&slippageBps=50")
        return {"mint":mint,"in_amount":d.get("inAmount",0),"out_amount":d.get("outAmount",0),"price_impact_pct":float(d.get("priceImpactPct",0)),"routes":len(d.get("routePlan",[])),"source":"Jupiter"}
    except:
        return {"mint":mint,"source":"Jupiter:offline"}

@router.get("/risk/{mint}")
async def risk(mint: str, x_agent_key: str=Header(...)):
    if not await get_agent(x_agent_key): raise HTTPException(401)
    try:
        d = await _fetch(f"https://public-api.birdeye.so/defi/price?address={mint}", {"X-API-KEY": BIRDEYE})
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
    if not await get_agent(x_agent_key): raise HTTPException(401)
    async with _db() as db:
        cur = await db.execute("SELECT * FROM tracked_wallets WHERE chain='pumpfun' ORDER BY created_at DESC LIMIT 50")
        rows = await cur.fetchall()
    return {"watchlist":[dict(r) for r in rows],"count":len(rows)}

@router.post("/watchlist")
async def add_watchlist(mint: str=Query(...), label: str=Query(""), x_agent_key: str=Header(...)):
    # NOTE: this was `agent = get_agent(x_agent_key)` with get_agent still
    # synchronous at the time -- once get_agent became async (this pass),
    # that would've silently assigned a coroutine object instead of awaiting
    # it, made `if not agent` always False (coroutines are truthy), skipped
    # the 401 entirely, and then crashed on agent["id"]. Fixed by awaiting.
    agent = await get_agent(x_agent_key)
    if not agent: raise HTTPException(401)
    async with _db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO tracked_wallets (chain,address,label,added_by_agent_id) VALUES (?,?,?,?)",
            ("pumpfun", mint, label or f"Pumpfun-{mint[:8]}", agent["id"]),
        )
        await db.commit()
    return {"status":"added","mint":mint}

@router.get("/signals")
async def signals(limit: int=20, x_agent_key: str=Header(...)):
    # Was querying `WHERE type='pumpfun' ORDER BY timestamp` -- trading_signals
    # has neither column (real columns: source, created_at), so this has
    # been an unconditional 500 on every call, same class of bug already
    # found and fixed for /graduations in this file. There's no signal
    # source actually tagged "pumpfun" in the live data yet either (checked
    # live: only "vantage-predictor" rows exist today) -- LIKE match is
    # forward-compatible with whatever a future pumpfun-specific pipeline
    # tags itself, and correctly returns an honest empty list today rather
    # than fabricating rows.
    if not await get_agent(x_agent_key): raise HTTPException(401)
    async with _db() as db:
        cur = await db.execute(
            "SELECT * FROM trading_signals WHERE source LIKE '%pumpfun%' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
    return {"signals":[dict(r) for r in rows],"count":len(rows),"source":"pumpfun"}

@router.get("/detect")
async def detect(address: str = Query(...), x_agent_key: str = Header(...)):
    """Auto-detect: wallet vs token mint (CA) on Solana via Helius RPC."""
    if not await get_agent(x_agent_key): raise HTTPException(401)
    try:
        payload = json.dumps({"jsonrpc":"2.0","id":1,"method":"getAccountInfo","params":[address,{"encoding":"jsonParsed"}]}).encode()
        req = urllib.request.Request(f"https://mainnet.helius-rpc.com/?api-key={HELIUS}",data=payload,headers={"Content-Type":"application/json"})
        resp = await asyncio.to_thread(lambda: json.loads(urllib.request.urlopen(req,timeout=10).read().decode()))
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
    if not await get_agent(x_agent_key): raise HTTPException(401)
    try:
        d = await _fetch(f"https://public-api.birdeye.so/defi/v3/token/holder?address={mint}&limit={limit}", {"X-API-KEY":BIRDEYE,"accept":"application/json"})
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
    if not await get_agent(x_agent_key): raise HTTPException(401)
    try:
        d = await _fetch(f"https://frontend-api.pump.fun/coins/{mint}",{"accept":"application/json"})
        return {"mint":mint,"creator":d.get("creator",d.get("creatorAddress","")),"name":d.get("name",""),"symbol":d.get("symbol",""),"description":d.get("description","")[:200],"twitter":d.get("twitter",""),"website":d.get("website",""),"created_at":d.get("created_timestamp","")}
    except:
        return {"mint":mint,"creator":"","error":"Pump.fun API unavailable"}

def _token_traders_sync(mint: str, helius_key: str) -> dict:
    import urllib.request as ur
    payload = json.dumps({"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[mint,{"limit":30}]}).encode()
    req = ur.Request(f"https://mainnet.helius-rpc.com/?api-key={helius_key}",data=payload,headers={"Content-Type":"application/json"})
    sigs = json.loads(ur.urlopen(req,timeout=10).read().decode()).get("result",[])
    trader_vol={}
    for s in sigs[:30]:
        txn_payload = json.dumps({"jsonrpc":"2.0","id":2,"method":"getTransaction","params":[s["signature"],{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]}).encode()
        txn_req = ur.Request(f"https://mainnet.helius-rpc.com/?api-key={helius_key}",data=txn_payload,headers={"Content-Type":"application/json"})
        try:
            txn = json.loads(ur.urlopen(txn_req,timeout=5).read().decode()).get("result",{})
            accts = txn.get("transaction",{}).get("message",{}).get("accountKeys",[])
            signer = accts[0]["pubkey"] if isinstance(accts[0],dict) else accts[0]
            trader_vol[signer] = trader_vol.get(signer,0)+1
        except: pass
    return trader_vol

@router.get("/token/traders")
async def token_traders(mint: str = Query(...), x_agent_key: str = Header(...)):
    """Top traders for a token via Helius RPC. Up to 31 sequential RPC calls
    (1 signature lookup + up to 30 transaction fetches) -- was running all
    of them synchronously inside the async handler, freezing the entire
    event loop (every other in-flight request) for the whole chain, up to
    ~150s worst case. Runs off the event loop in one thread instead."""
    if not await get_agent(x_agent_key): raise HTTPException(401)
    try:
        trader_vol = await asyncio.to_thread(_token_traders_sync, mint, HELIUS)
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
    if not await get_agent(x_agent_key): raise HTTPException(401)
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

# ════════════════════════════════════════════════════════════════
# TOKEN CREATION — real deployment via PumpPortal's Lightning (hosted)
# Trading API (https://pumpportal.fun/api/trade, action=create), which
# signs+broadcasts server-side using the wallet's own PumpPortal-custodied
# key. Scoped to wallets created via POST /api/trading/wallets/generate
# {system:"pumpportal"} (trading.py's generate_wallet already mints and
# encrypts a real PumpPortal Lightning API key per-agent) -- NOT the
# generic local-signing path trading.py's execute_live_order uses for
# Jupiter swaps. Reason: pump.fun token creation needs a *second* keypair
# (the new mint account) to co-sign the create instruction alongside the
# payer; PumpPortal's Local Trading API supports that by returning an
# unsigned tx for the caller to sign with both keys, but that doubles the
# real key-handling surface for a first cut of this feature. Lightning
# mode avoids that entirely (PumpPortal generates+signs the mint keypair
# server-side, already inside the same custodial boundary this codebase
# accepted the moment it minted a PumpPortal API key for that wallet).
# Local-signing (self-custodied wallets) is a real gap, not silently
# pretended-away -- see the 422 below.
#
# Fee reality check (deliberately not hardcoded as a flat "0.02 SOL fee"
# anywhere in this code): PumpPortal's own docs say there's no separate
# platform fee for creation itself -- the real cost is (a) Solana rent for
# the new mint/metadata/bonding-curve accounts (~0.02 SOL, standard
# rent-exempt minimum for those account sizes, paid automatically by the
# transaction) plus (b) PumpPortal's standard trading fee applied to
# whatever `dev_buy_sol` amount is requested (0 is valid -- creates the
# token with no initial buy). The UI should show `dev_buy_sol` as the only
# amount actually controllable by the caller.
# ════════════════════════════════════════════════════════════════

PINATA_UPLOAD_URL = "https://uploads.pinata.cloud/v3/files"


def _require_pinata():
    if not PINATA_JWT:
        raise HTTPException(
            503,
            "Token creation is not configured on this deployment: PINATA_JWT is unset. "
            "Pump.fun no longer accepts direct metadata uploads -- an IPFS pinning "
            "credential (Pinata) is a real prerequisite, not optional, for this feature.",
        )


@router.get("/create/config")
async def create_config(x_agent_key: str = Header(...)):
    """Lets the frontend show real availability instead of a form that
    fails at submit time. `ready=False` here means the owner still needs
    to set PINATA_JWT (image/metadata IPFS pinning) before any launch can
    actually happen -- there is no working fallback for that step."""
    if not await get_agent(x_agent_key): raise HTTPException(401)
    return {
        "ipfs_ready": bool(PINATA_JWT),
        "requires_wallet_system": "pumpportal",
        "note": (
            "Real deployment requires a PumpPortal Lightning wallet "
            "(POST /api/trading/wallets/generate {system:'pumpportal'}), funded "
            "with at least the dev-buy amount plus ~0.02 SOL rent."
            if PINATA_JWT else
            "PINATA_JWT is not set on this server -- token creation is disabled "
            "until an IPFS pinning credential is configured."
        ),
    }


async def _pinata_upload_bytes(data: bytes, filename: str, content_type: str) -> str:
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            PINATA_UPLOAD_URL,
            headers={"Authorization": f"Bearer {PINATA_JWT}"},
            data={"network": "public"},
            files={"file": (filename, data, content_type)},
        )
        r.raise_for_status()
        cid = r.json().get("data", {}).get("cid", "")
        if not cid:
            raise HTTPException(502, f"Pinata upload returned no cid: {r.text[:200]}")
        return cid


@router.post("/create/upload-image")
async def upload_image(image: UploadFile = File(...), x_agent_key: str = Header(...)):
    """Step 1 of 2 for real deployment: pin the token image to IPFS. Returns
    a URL to pass back as `image_url` to POST /create. Separated from the
    final create call so a caller can inspect/confirm the pinned image
    before anything on-chain (or money-spending) happens."""
    if not await get_agent(x_agent_key): raise HTTPException(401)
    _require_pinata()
    body = await image.read()
    if len(body) > 5 * 1024 * 1024:
        raise HTTPException(413, "Image too large (5MB limit)")
    cid = await _pinata_upload_bytes(body, image.filename or "token.png", image.content_type or "image/png")
    return {"cid": cid, "url": f"https://ipfs.io/ipfs/{cid}"}


class _CreateTokenBody:
    """Plain holder, not a pydantic model -- kept as Form(...) params below
    so this endpoint accepts the same multipart-friendly shape as the image
    upload, letting a single frontend form submit both in one flow if it
    wants to (image_url from the prior step is just a string field here)."""


@router.post("/create")
async def create_token(
    wallet_id: int = Form(...),
    name: str = Form(...),
    symbol: str = Form(...),
    image_url: str = Form(...),
    description: str = Form(""),
    twitter: str = Form(""),
    telegram: str = Form(""),
    website: str = Form(""),
    dev_buy_sol: float = Form(0.0),
    slippage: float = Form(10.0),
    priority_fee: float = Form(0.0005),
    dry_run: bool = Form(True),
    x_agent_key: str = Header(...),
):
    """Real pump.fun token creation. `dry_run=True` (the default -- callers
    must explicitly opt into spending real SOL) pins the metadata JSON to
    IPFS and returns exactly what would be submitted, without ever calling
    PumpPortal's create action or touching the wallet's key. `dry_run=False`
    performs the actual on-chain creation and (if dev_buy_sol > 0) initial
    buy, spending real SOL from the wallet."""
    agent = await get_agent(x_agent_key)
    if not agent: raise HTTPException(401)
    _require_pinata()

    if len(symbol) > 10:
        raise HTTPException(422, f"Symbol '{symbol}' exceeds pump.fun's 10-character limit")
    if not name or not symbol:
        raise HTTPException(422, "name and symbol are required")

    async with _db() as db:
        cur = await db.execute(
            "SELECT * FROM trading_wallets WHERE id=? AND agent_id=?",
            (wallet_id, agent["id"]),
        )
        wallet = await cur.fetchone()
    if not wallet:
        raise HTTPException(404, "Wallet not found")
    if wallet.get("chain") != "solana":
        raise HTTPException(422, f"Wallet is chain='{wallet.get('chain')}' -- token creation is Solana-only")
    encrypted_pp_key = wallet.get("encrypted_api_key")
    if not encrypted_pp_key:
        raise HTTPException(
            422,
            "This wallet has no PumpPortal Lightning API key. Real deployment currently "
            "requires a wallet generated via POST /api/trading/wallets/generate "
            "{system:'pumpportal'} -- self-custodied wallets aren't wired up for token "
            "creation yet (would need local dual-keypair signing, not implemented).",
        )

    metadata = {
        "name": name, "symbol": symbol, "image": image_url,
        "description": description, "twitter": twitter,
        "telegram": telegram, "website": website,
    }
    metadata_cid = await _pinata_upload_bytes(
        json.dumps({k: v for k, v in metadata.items() if v}).encode(),
        "metadata.json", "application/json",
    )
    metadata_uri = f"https://ipfs.io/ipfs/{metadata_cid}"

    payload = {
        "action": "create",
        "tokenMetadata": {"name": name, "symbol": symbol, "uri": metadata_uri},
        "denominatedInSol": "true",
        "amount": dev_buy_sol,
        "slippage": slippage,
        "priorityFee": priority_fee,
        "pool": "pump",
    }

    if dry_run:
        return {
            "dry_run": True,
            "metadata_uri": metadata_uri,
            "payload_preview": payload,
            "estimated_cost_sol": round(0.02 + dev_buy_sol, 6),
            "note": (
                "Nothing was submitted on-chain and no key was touched. "
                "Re-call with dry_run=false to actually create the token and spend real SOL."
            ),
        }

    try:
        pp_api_key = decrypt_key_for_agent(encrypted_pp_key, {"id": agent["id"], "api_key": x_agent_key})
    except Exception:
        raise HTTPException(500, "Failed to decrypt this wallet's PumpPortal API key -- wrong agent key, or corrupted")
    if not pp_api_key:
        raise HTTPException(500, "Decrypted PumpPortal API key is empty")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"https://pumpportal.fun/api/trade?api-key={pp_api_key}",
                json=payload,
            )
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
    except Exception as e:
        raise HTTPException(502, f"PumpPortal create request failed: {e}")

    signature = body.get("signature")
    if not signature:
        # Real, surfaced failure -- not swallowed into a fake "success".
        raise HTTPException(502, f"PumpPortal did not return a signature: {body}")

    async with _db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO tracked_wallets (chain,address,label,added_by_agent_id) VALUES (?,?,?,?)",
            ("pumpfun", metadata_uri, f"{symbol}-launch", agent["id"]),
        )
        await db.commit()

    return {
        "dry_run": False,
        "signature": signature,
        "metadata_uri": metadata_uri,
        "explorer_url": f"https://solscan.io/tx/{signature}",
        "dev_buy_sol": dev_buy_sol,
    }
