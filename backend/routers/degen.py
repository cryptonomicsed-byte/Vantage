"""Degen Alpha Router — Ultra-degen signals: early calls, smart wallets, volume surges, rug checks.
Uses existing keys: Helius RPC, Birdeye, GeckoTerminal.
"""
import json, os, urllib.request, hashlib, sqlite3, time
from pathlib import Path
from fastapi import APIRouter, Query, HTTPException, Header
from backend.wallet_blacklist import sql_label_exclusions

router = APIRouter(prefix="/api/intel/degen", tags=["degen"])
DB = Path("/opt/ares/Vantage/data/vantage.db")
HELIUS = os.environ.get("HELIUS_API_KEY", "")
BIRDEYE = os.environ.get("BIRDEYE_KEY", "")

# ── Last-known-good cache ────────────────────────────────────────────────────
# GeckoTerminal is rate-limited and these endpoints all hit it independently
# with zero caching — a single 429/timeout used to mean the endpoint returned
# an EMPTY list with 200 OK, and the frontend (which only fetches once per
# mount, no retry) would show a section that HAD data a moment ago as
# suddenly empty. Fix: cache the last successful (non-empty) response per key
# and serve THAT through any failure window, instead of ever returning an
# empty result just because this one poll hit a rate limit. A genuinely
# empty upstream result is still cached and served as empty — this only
# protects against transient fetch failures, it doesn't fabricate data.
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 90  # seconds a good result is served fresh before re-fetching
_CACHE_STALE_MAX = 900  # how long a stale-but-good result is still preferred over a fresh empty/failed one

def _cache_get_fresh(key: str):
    e = _CACHE.get(key)
    if e and (time.time() - e[0]) < _CACHE_TTL:
        return e[1]
    return None

def _cache_get_stale(key: str):
    e = _CACHE.get(key)
    if e and (time.time() - e[0]) < _CACHE_STALE_MAX:
        return e[1]
    return None

def _cache_put(key: str, val: dict):
    _CACHE[key] = (time.time(), val)
    return val

def _fetch(url, headers=None, timeout=10):
    h = headers or {}; h['User-Agent'] = 'curl/8.0'
    req = urllib.request.Request(url, headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())

def get_agent(key):
    h = hashlib.sha256(key.encode()).hexdigest()
    db = sqlite3.connect(str(DB)); db.row_factory = lambda c,r: dict(zip([col[0] for col in c.description], r))
    r = db.execute("SELECT id, name FROM agents WHERE api_key=?", (h,)).fetchone(); db.close()
    return dict(r) if r else None

def _trending_pools_cached():
    """The one upstream call every endpoint here shares — fetch once, cache,
    reuse. Cuts GeckoTerminal call volume ~5x and is the actual fix for the
    rate-limit-triggered empty flashes."""
    fresh = _cache_get_fresh("trending_pools")
    if fresh is not None:
        return fresh, True
    try:
        d = _fetch("https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1", {"accept": "application/json"})
        pools = d.get("data", [])
        _cache_put("trending_pools", pools)
        return pools, False
    except Exception:
        stale = _cache_get_stale("trending_pools")
        if stale is not None:
            return stale, True
        return [], False

def _mint_from_pool(p: dict) -> str:
    """p['id'] is the POOL address, not the token mint — same bug found
    and fixed today in degen_alpha_fusion.py/ogun_multiscan.py/pumpfun.py.
    The real mint is relationships.base_token.data.id ('solana_<mint>').
    Without this, cards built from these endpoints have no CA, so
    EntityProfileCard can't show its trade panel at all."""
    base_token_id = p.get("relationships",{}).get("base_token",{}).get("data",{}).get("id","")
    return base_token_id.split("_",1)[-1] if "_" in base_token_id else ""

# ════════════════════════════════════════════════════════════════
# EARLY CALLS — tokens <1h old with rising volume + smart money
# ════════════════════════════════════════════════════════════════
@router.get("/early-calls")
async def early_calls(limit: int=20, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    pools, from_cache = _trending_pools_cached()
    try:
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
        resp = {"early_calls":results[:limit],"count":len(results),"source":"GeckoTerminal","cached":from_cache}
        return _cache_put("early_calls", resp) if results else (_cache_get_stale("early_calls") or resp)
    except Exception:
        return _cache_get_stale("early_calls") or {"early_calls":[],"count":0,"source":"GeckoTerminal:offline"}

# ════════════════════════════════════════════════════════════════
# SMART WALLETS — top performing degen traders + recent entries.
# Excludes known exchange wallets (address_type='exchange') — those are
# high-edge-count by nature of being deposit/withdrawal hubs, not because
# they're "smart money"; showing them here would drown out real signal.
# ════════════════════════════════════════════════════════════════
@router.get("/smart-wallets")
async def smart_wallets(limit: int=20, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    db = sqlite3.connect(str(DB)); db.row_factory = lambda c,r: dict(zip([col[0] for col in c.description], r))
    # address_type='exchange' is the authoritative tag, but daemons that add
    # wallets don't always set it correctly (found 15 mistagged Binance/
    # Coinbase/Alameda/etc wallets live — fixed in the DB, but new ones can
    # still slip in mistagged) — so also exclude by label pattern as a
    # runtime safety net, not just the tag. Shared list in
    # backend/wallet_blacklist.py — also used by /api/moneyflow.
    label_exclusions = sql_label_exclusions("w.label")
    rows = db.execute(f"""
        SELECT w.address, w.label, w.chain, w.address_type,
               (SELECT COUNT(*) FROM wallet_edges we WHERE we.address_a=w.address OR we.address_b=w.address) as edge_count,
               (SELECT MAX(we.last_seen) FROM wallet_edges we WHERE we.address_a=w.address OR we.address_b=w.address) as last_active
        FROM tracked_wallets w
        WHERE w.chain IN ('solana','pumpfun')
          AND w.address_type != 'exchange'
          AND {label_exclusions}
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
    resp = {"smart_wallets":results,"count":len(results)}
    return _cache_put("smart_wallets", resp) if results else (_cache_get_stale("smart_wallets") or resp)

# ════════════════════════════════════════════════════════════════
# VOLUME SURGE — 10x volume in minutes alerts
# ════════════════════════════════════════════════════════════════
@router.get("/volume-surge")
async def volume_surge(limit: int=20, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    pools, from_cache = _trending_pools_cached()
    try:
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
                results.append({"symbol":sym,"name":name,"address":_mint_from_pool(p),"volume_5m":vol_5m,"volume_1h":vol_1h,"surge_ratio":round(surge_ratio,1),"signal":"🔥 SURGE" if surge_ratio>10 else "⚡ SPIKE"})
        results.sort(key=lambda x:-x.get("surge_ratio",0))
        resp = {"volume_surges":results,"count":len(results),"source":"GeckoTerminal","cached":from_cache}
        return _cache_put("volume_surge", resp) if results else (_cache_get_stale("volume_surge") or resp)
    except Exception:
        return _cache_get_stale("volume_surge") or {"volume_surges":[],"count":0,"source":"offline"}

# ════════════════════════════════════════════════════════════════
# TOP 5 DEGEN PLAY — aggregated from alpha aggregator
# ════════════════════════════════════════════════════════════════
@router.get("/top5")
async def top5_degen(limit: int=5, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    pools, from_cache = _trending_pools_cached()
    try:
        graduated = []
        for p in pools[:25]:
            attrs = p.get("attributes",{})
            name = attrs.get("name","")
            addr = _mint_from_pool(p)
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
        resp = {"top_5":graduated[:limit],"total_scanned":len(pools),"source":"GeckoTerminal","cached":from_cache}
        return _cache_put("top5", resp) if graduated else (_cache_get_stale("top5") or resp)
    except Exception:
        return _cache_get_stale("top5") or {"top_5":[],"total_scanned":0,"source":"offline"}

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
    pools, from_cache = _trending_pools_cached()
    try:
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
        resp = {'rotations':rotations[:limit],'count':len(rotations[:limit]),'source':'GeckoTerminal','cached':from_cache}
        return _cache_put("sell_rotations", resp) if rotations else (_cache_get_stale("sell_rotations") or resp)
    except Exception:
        return _cache_get_stale("sell_rotations") or {'rotations':[],'count':0}

# ════════════════════════════════════════════════════════════════
# MUST-BUY-20 — the aggregated cross-source list. Merges EVERY signal
# source Ares gathers, not just one API:
#   - GeckoTerminal trending pools (volume/momentum, via the shared cache)
#   - trading_signals (persisted pump.fun/strategy scanner rows, BUY-leaning)
#   - social_signals (Twitter/Telegram sentiment, BULLISH-tagged)
#   - signal_pool (in-memory intel pool: TG/Twitter/predictor ingest)
# Scored by how many independent sources agree + their conviction, not just
# raw volume — a token mentioned bullishly on social AND trending AND with a
# real trading signal ranks above one that's only trending.
# ════════════════════════════════════════════════════════════════
@router.get("/must-buy-20")
async def must_buy_20(limit: int=20, hours: int=24, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    pools, _ = _trending_pools_cached()

    candidates: dict[str, dict] = {}  # key: symbol.upper()

    def bucket(symbol: str):
        sym = (symbol or "").upper().lstrip("$")
        if not sym:
            return None
        return candidates.setdefault(sym, {
            "symbol": sym, "sources": [], "score": 0.0,
            "volume_24h": 0, "price_change_24h": 0, "ca": "",
        })

    # 1. Trending pools (momentum baseline)
    for p in pools[:40]:
        attrs = p.get("attributes", {})
        name = attrs.get("name", "")
        sym = name.split(" / ")[0][:12] if " / " in name else name[:12]
        c = bucket(sym)
        if not c:
            continue
        vol = attrs.get("volume_usd", {})
        vol_24h = float(str(vol.get("h24", 0))) if isinstance(vol, dict) else 0
        pc = attrs.get("price_change_percentage", {})
        pc_24h = float(str(pc.get("h24", 0))) if isinstance(pc, dict) else 0
        c["volume_24h"] = max(c["volume_24h"], vol_24h)
        c["price_change_24h"] = pc_24h
        addr = p.get("id", "").split("_")[-1] if "_" in p.get("id", "") else ""
        if addr:
            c["ca"] = addr
        c["sources"].append("trending")
        c["score"] += min(30, vol_24h / 10000) + (10 if pc_24h > 10 else 0)

    db = sqlite3.connect(str(DB)); db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

    # 2. Persisted trading signals — direction-aware, conviction-weighted.
    try:
        for r in db.execute(
            "SELECT symbol, direction, conviction FROM trading_signals "
            "WHERE created_at >= datetime('now', ?) AND UPPER(direction) IN ('BUY','LONG','BULLISH') "
            "ORDER BY id DESC LIMIT 300", (f"-{int(hours)} hours",)
        ).fetchall():
            c = bucket(r["symbol"])
            if not c:
                continue
            c["sources"].append(f"signal:{r.get('direction','')}")
            c["score"] += 20 * float(r.get("conviction") or 0.5)
    except sqlite3.Error:
        pass

    # 3. Social sentiment — bullish mentions, weighted by confidence.
    try:
        for r in db.execute(
            "SELECT ticker, contract_address, sentiment, confidence FROM social_signals "
            "WHERE created_at >= datetime('now', ?) AND UPPER(sentiment) = 'BULLISH' "
            "ORDER BY id DESC LIMIT 300", (f"-{int(hours)} hours",)
        ).fetchall():
            c = bucket(r["ticker"])
            if not c:
                continue
            if r.get("contract_address"):
                c["ca"] = c["ca"] or r["contract_address"]
            c["sources"].append("social:BULLISH")
            c["score"] += 15 * float(r.get("confidence") or 0.5)
    except sqlite3.Error:
        pass

    db.close()

    # 4. In-memory signal pool (predictor/degen fusion/telegram alpha ingest).
    try:
        from backend.routers.intel import _signal_pool, _signal_lock
        with _signal_lock:
            pool = list(_signal_pool)
        for s in pool:
            direction = str(s.get("direction", "")).upper()
            if direction not in ("BUY", "LONG", "BULLISH"):
                continue
            c = bucket(s.get("symbol", ""))
            if not c:
                continue
            c["sources"].append(f"pool:{s.get('source','')}")
            c["score"] += 12 * float(s.get("conviction", 0.5) or 0.5)
    except Exception:
        pass

    ranked = sorted(candidates.values(), key=lambda c: -c["score"])
    out = []
    for c in ranked[:limit]:
        source_types = sorted(set(s.split(":")[0] for s in c["sources"]))
        c["source_count"] = len(source_types)
        c["source_types"] = source_types
        c["score"] = round(c["score"], 1)
        out.append(c)

    resp = {"must_buy": out, "count": len(out), "candidates_scanned": len(candidates), "window_hours": hours}
    return _cache_put("must_buy_20", resp) if out else (_cache_get_stale("must_buy_20") or resp)

# ════════════════════════════════════════════════════════════════
# FRESH DEPLOYERS — recently-discovered token deployers, from
# pumpfun_wallet_intel.py's token_wallet_roles (see /api/moneyflow for the
# graph view of the same data). Trenches-relevant: who deployed a token
# that's currently surfacing as alpha, and does that deployer have other
# launches on record (repeat deployer = pattern, good or bad).
# ════════════════════════════════════════════════════════════════
@router.get("/fresh-deployers")
async def fresh_deployers(limit: int=20, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    db = sqlite3.connect(str(DB)); db.row_factory = lambda c,r: dict(zip([col[0] for col in c.description], r))
    label_exclusions = sql_label_exclusions("tw.label")
    rows = db.execute(f"""
        SELECT r.mint, r.symbol, r.wallet_address, r.discovered_at,
               (SELECT COUNT(*) FROM token_wallet_roles r2 WHERE r2.wallet_address = r.wallet_address AND r2.role = 'deployer') as launch_count
        FROM token_wallet_roles r
        LEFT JOIN tracked_wallets tw ON tw.address = r.wallet_address
        WHERE r.role = 'deployer' AND (tw.address_type IS NULL OR tw.address_type != 'exchange') AND {label_exclusions}
        ORDER BY r.discovered_at DESC LIMIT ?
    """, (limit,)).fetchall()
    db.close()
    return {"deployers": rows, "count": len(rows)}

# ════════════════════════════════════════════════════════════════
# TOP WALLETS TO COPY — output of wallet_learner.py, which studies
# token_wallet_roles + social_wallet_links all day (see that file's
# docstring for exactly how it scores and how it attributes names —
# nothing here is guessed, a blank display_name means genuinely
# unattributed, not a bug).
# ════════════════════════════════════════════════════════════════
@router.get("/top-wallets-to-copy")
async def top_wallets_to_copy(limit: int=20, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    db = sqlite3.connect(str(DB)); db.row_factory = lambda c,r: dict(zip([col[0] for col in c.description], r))
    rows = db.execute("""
        SELECT * FROM wallet_reputation
        WHERE copy_trade_score > 0
        ORDER BY copy_trade_score DESC LIMIT ?
    """, (limit,)).fetchall()
    db.close()
    return {"wallets": rows, "count": len(rows)}

# ════════════════════════════════════════════════════════════════
# CONVICTION SCORE — "% of this token held/traded by known-good wallets."
# Pure join of token_wallet_roles (who's connected to this token) against
# wallet_reputation (who wallet_learner.py has scored as worth following) —
# zero new external dependency, uses only what's already in the DB. This is
# the "conviction score" from the alpha-discovery proposal, built the honest
# way: real overlap between two tables we already trust, not a new scraped
# leaderboard.
# ════════════════════════════════════════════════════════════════
def _token_conviction(db, mint: str) -> dict:
    rows = db.execute("""
        SELECT twr.role, twr.wallet_address, wr.copy_trade_score, wr.display_name
        FROM token_wallet_roles twr
        JOIN wallet_reputation wr ON wr.wallet_address = twr.wallet_address
        WHERE twr.mint = ? AND wr.copy_trade_score > 0
    """, (mint,)).fetchall()
    smart_wallets = {r["wallet_address"]: r for r in rows}  # dedupe — one wallet can hold multiple roles
    total_score = sum(r["copy_trade_score"] for r in smart_wallets.values())
    return {
        "smart_wallet_count": len(smart_wallets),
        "conviction_score": round(total_score, 1),
        "smart_wallets": [
            {"wallet": w, "display_name": r["display_name"], "copy_trade_score": r["copy_trade_score"]}
            for w, r in sorted(smart_wallets.items(), key=lambda kv: -kv[1]["copy_trade_score"])[:10]
        ],
    }

@router.get("/conviction/{mint}")
async def token_conviction(mint: str, x_agent_key: str=Header(...)):
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    db = sqlite3.connect(str(DB)); db.row_factory = lambda c,r: dict(zip([col[0] for col in c.description], r))
    result = _token_conviction(db, mint)
    db.close()
    return {"mint": mint, **result}

@router.get("/high-conviction")
async def high_conviction_tokens(limit: int=20, x_agent_key: str=Header(...)):
    """Every currently-tracked token ranked by smart-money overlap — the
    'finding plays before they run' view: which tokens have known-good
    wallets already positioned in them, right now."""
    get_agent(x_agent_key) or (_ for _ in ()).throw(HTTPException(401))
    db = sqlite3.connect(str(DB)); db.row_factory = lambda c,r: dict(zip([col[0] for col in c.description], r))
    mints = db.execute("""
        SELECT DISTINCT twr.mint, twr.symbol FROM token_wallet_roles twr
        JOIN wallet_reputation wr ON wr.wallet_address = twr.wallet_address
        WHERE wr.copy_trade_score > 0
    """).fetchall()
    ranked = []
    for m in mints:
        conv = _token_conviction(db, m["mint"])
        if conv["smart_wallet_count"] == 0:
            continue
        ranked.append({"mint": m["mint"], "symbol": m["symbol"], **conv})
    db.close()
    ranked.sort(key=lambda t: -t["conviction_score"])
    return {"tokens": ranked[:limit], "count": len(ranked[:limit])}
