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
        d = _fetch(f"https://api.jup.ag/swap/v1/quote?inputMint={mint}&outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v&amount=1000000&slippageBps=50")
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
# AUTOMATED SCAN → SIGNAL → ORDER PIPELINE
# ════════════════════════════════════════════════════════════════
# Background loop (started from main.py when settings.PUMPFUN_SCAN_ENABLED):
# every PUMPFUN_SCAN_INTERVAL seconds it polls GeckoTerminal trending pools,
# applies a safety filter, and for tokens that pass it records a pumpfun signal
# and — at conviction > 0.7 — a pending order for the configured agent's wallet.
# The execution engine picks those orders up. Disabled by default; requires
# PUMPFUN_SCAN_AGENT_ID so signals/orders attribute to a real agent + wallet.
import asyncio
import logging

logger = logging.getLogger(__name__)


def _passes_safety(pool_attrs: dict, min_volume: float, max_top5_pct: float) -> tuple[bool, str]:
    """Cheap, data-only safety gate over a GeckoTerminal pool row. Deeper
    on-chain checks (mint authority, holder concentration) run in the execution
    engine's adapter just before a live trade — this filter only decides whether
    a token is worth signaling at all."""
    vol = pool_attrs.get("volume_usd", {})
    vol_24h = float(vol.get("h24", 0)) if isinstance(vol, dict) else 0.0
    if vol_24h < min_volume:
        return False, f"volume ${vol_24h:.0f} < ${min_volume:.0f}"
    resv = pool_attrs.get("reserve_in_usd")
    if resv is not None and float(resv) < 500:
        return False, f"reserve ${float(resv):.0f} < $500 (illiquid)"
    txns = pool_attrs.get("transactions", {}).get("h24", {})
    if isinstance(txns, dict):
        buys, sells = txns.get("buys", 0), txns.get("sells", 0)
        if buys + sells < 20:
            return False, "too few 24h txns (<20)"
    return True, "ok"


def _conviction_from_pool(pool_attrs: dict) -> float:
    """Map trending momentum to a 0–1 conviction. Positive 24h move + healthy
    buy/sell ratio lifts conviction toward the auto-order threshold."""
    pc = pool_attrs.get("price_change_percentage", {})
    pc_24h = float(pc.get("h24", 0)) if isinstance(pc, dict) else 0.0
    txns = pool_attrs.get("transactions", {}).get("h24", {})
    buys = txns.get("buys", 0) if isinstance(txns, dict) else 0
    sells = txns.get("sells", 0) if isinstance(txns, dict) else 0
    ratio = buys / max(buys + sells, 1)
    momentum = max(0.0, min(pc_24h / 100.0, 1.0))  # +100% caps momentum term
    return round(min(0.5 + 0.3 * momentum + 0.2 * (ratio - 0.5) * 2, 0.99), 3)


def _ensure_signal_table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS trading_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT DEFAULT 'pumpfun',
        symbol TEXT, mint TEXT, direction TEXT, conviction REAL, source TEXT,
        reason TEXT, timestamp TEXT DEFAULT (datetime('now')))""")


def _record_pumpfun_signal(agent_id: int, symbol: str, mint: str, conviction: float,
                           reason: str, conviction_threshold: float,
                           max_sol: float) -> dict:
    """Persist a pumpfun signal and, above threshold, a pending order for the
    agent's Solana wallet. Mirrors /api/trading/signals/ingest's auto-order
    behavior but runs in-process (no self-HTTP, no system-tool key needed)."""
    from backend.config import settings
    db = sqlite3.connect(str(DB))
    _ensure_signal_table(db)
    db.execute("""INSERT INTO trading_signals (type, symbol, mint, direction, conviction, source, reason)
                  VALUES ('pumpfun', ?, ?, 'BUY', ?, 'pumpfun_scan', ?)""",
               (symbol, mint, conviction, reason))
    result = {"symbol": symbol, "mint": mint, "conviction": conviction, "signaled": True}
    if conviction > 0.7:
        wallet = db.execute(
            "SELECT id, address FROM trading_wallets WHERE agent_id=? AND chain='solana' "
            "ORDER BY created_at LIMIT 1", (agent_id,)).fetchone()
        if wallet:
            # Position size in SOL, capped by the per-order safety limit.
            qty = min(max_sol, settings.TRADING_MAX_SOL_PER_ORDER)
            cur = db.execute(
                """INSERT INTO trading_orders (agent_id, wallet_id, order_type, side, symbol,
                   chain, quantity, trigger_reason, status)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (agent_id, wallet[0], "market", "BUY", mint or symbol, "solana", qty,
                 f"pumpfun_scan_conviction_{conviction:.2f}", "pending"))
            result["order_created"] = cur.lastrowid
            result["quantity_sol"] = qty
        else:
            result["warning"] = f"no solana wallet for agent {agent_id}"
    db.commit()
    db.close()
    return result


async def pumpfun_scan_once() -> dict:
    """One scan pass. Returns a summary dict; safe to call manually for testing."""
    from backend.config import settings
    agent_id = settings.PUMPFUN_SCAN_AGENT_ID
    if not agent_id:
        return {"status": "skipped", "reason": "PUMPFUN_SCAN_AGENT_ID not set"}
    try:
        d = await asyncio.to_thread(
            _fetch, "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1",
            {"accept": "application/json"})
    except Exception as e:
        return {"status": "error", "reason": f"GeckoTerminal fetch failed: {e}"}

    pools = d.get("data", [])
    included = {i.get("id"): i for i in d.get("included", [])}
    signaled, skipped = [], 0
    for p in pools[:20]:
        attrs = p.get("attributes", {})
        name = attrs.get("name", "")
        sym = name.split(" / ")[0][:12] if " / " in name else name[:12]
        # Resolve the base token mint from the relationships/included section.
        mint = ""
        rel = p.get("relationships", {}).get("base_token", {}).get("data", {})
        tok = included.get(rel.get("id"))
        if tok:
            mint = tok.get("attributes", {}).get("address", "")
        ok, why = _passes_safety(attrs, settings.PUMPFUN_MIN_VOLUME_USD,
                                 settings.PUMPFUN_MAX_TOP5_HOLDER_PCT)
        if not ok:
            skipped += 1
            continue
        conviction = max(_conviction_from_pool(attrs), settings.PUMPFUN_SCAN_CONVICTION)
        res = await asyncio.to_thread(
            _record_pumpfun_signal, agent_id, sym, mint, conviction,
            f"trending: {why}", settings.PUMPFUN_SCAN_CONVICTION,
            settings.TRADING_MAX_SOL_PER_ORDER)
        signaled.append(res)
    return {"status": "ok", "signaled": len(signaled), "skipped": skipped,
            "orders": [s for s in signaled if s.get("order_created")]}


async def pumpfun_scan_loop():
    """Background scan loop. Started from main.py's lifespan when enabled."""
    from backend.config import settings
    interval = settings.PUMPFUN_SCAN_INTERVAL
    logger.info(f"Pump.fun scan loop started (interval={interval}s, agent={settings.PUMPFUN_SCAN_AGENT_ID})")
    while True:
        try:
            summary = await pumpfun_scan_once()
            if summary.get("signaled"):
                logger.info(f"[pumpfun-scan] {summary['signaled']} signals, "
                            f"{len(summary.get('orders', []))} orders, {summary.get('skipped')} filtered")
        except asyncio.CancelledError:
            logger.info("Pump.fun scan loop stopping")
            raise
        except Exception as e:
            logger.error(f"[pumpfun-scan] error: {e}")
        await asyncio.sleep(interval)
