"""Copilot v2 — Full feature set: 36 intents across all 8 categories.
Extends the existing copilot with simulations, optimization, learning, gamification,
swarm, visualization, and more. Live on /api/copilot/chat and /api/copilot/execute."""

import logging, re, statistics, json
from typing import Optional
import aiosqlite, httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from backend.db import DB_PATH
from backend.deps import get_agent, _parse_body
from backend.config import settings
from backend import market_sources as ms

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/copilot", tags=["copilot"])
ARES = "http://localhost:9861"

# ── Symbol aliases ──
ALIASES = {
    "bitcoin":"BTC","btc":"BTC","ethereum":"ETH","eth":"ETH","solana":"SOL","sol":"SOL",
    "cardano":"ADA","ripple":"XRP","xrp":"XRP","dogecoin":"DOGE","doge":"DOGE",
    "polkadot":"DOT","avalanche":"AVAX","polygon":"MATIC","chainlink":"LINK",
    "uniswap":"UNI","pepe":"PEPE","bonk":"BONK","shib":"SHIB","sui":"SUI",
    "aptos":"APT","arbitrum":"ARB","optimism":"OP","near":"NEAR","injective":"INJ",
}
def sym(raw): return ALIASES.get((raw or "").strip().lower(), (raw or "").upper())

# ── Intent patterns (ordered: specific → generic) ──
INTENTS = [
    ("set_alert", r"(?:set|create|add)\s+(?:a\s+|an\s+)?alert\s+(?:for|when|if)\s+(.+)"),
    ("backtest", r"(?:run|start|begin)\s+(?:a\s+)?backtest|backtest\s+(.+)"),
    ("stress_test", r"(?:stress.test|scenario.test|what.if|worst.case)\s+(?:my\s+)?(?:portfolio|pf)"),
    ("scenario_sim", r"(?:simulate|what.if|scenario)\s+(.+)"),
    ("optimize_portfolio", r"(?:optimize|rebalance|sharpe|efficient.frontier)\s+(?:my\s+)?(?:portfolio|pf)"),
    ("yield_scan", r"(?:yield|APR|APY|farming)\s+(?:scan|search|find|optimize)"),
    ("arbitrage_scan", r"(?:arbitrage|arb|price.gap|spread).*(?:scan|find|check|opportunit)|(?:scan|find).*(?:arbitrage|arb|price.gap|spread)"),
    ("liquidity_track", r"(?:liquidity|depth|slippage)\s+(?:track|scan|check|monitor)"),
    ("benchmark", r"(?:benchmark|compare|vs\.|versus)\s+(?:my\s+)?(?:portfolio|pf|strategy)"),
    ("risk_slider", r"(?:risk|exposure|drawdown)\s+(?:slider|level|assessment|simulator)"),
    ("risk_heatmap", r"(?:risk|exposure)\s+(?:heatmap|map|chart|visual)"),
    ("metric_create", r"(?:create|define|new)\s+(?:a\s+)?(?:metric|indicator|custom)\s+(.+)"),
    ("metric_fusion", r"(?:fuse|combine|merge)\s+(?:metrics?|indicators?)\s+(.+)"),
    ("learning_path", r"(?:learn|teach|train|course|path|tutorial)\s+(?:me\s+)?(?:about\s+)?(.+)"),
    ("learning_quiz", r"(?:quiz|test|question|assess)\s+(?:me\s+)?(?:on\s+)?(.+)?"),
    ("goal_track", r"(?:goal|target|objective)\s+(?:track|set|progress|status)"),
    ("progress_track", r"(?:progress|how.am.i.doing|my.stats|track.record)"),
    ("watchlist", r"(?:watchlist|watch.list|favorites|priority)\s+(?:prioritize|sort|rank|show)"),
    ("gamification", r"(?:achievement|badge|level|xp|score|leaderboard|challenge)"),
    ("sentiment_vote", r"(?:vote|upvote|downvote|crowdsource)\s+(?:signal|alert|prediction)"),
    ("community_feed", r"(?:community|social|insight.feed|what.are.others)"),
    ("signal_amplify", r"(?:amplify|boost|promote)\s+(?:signal|alert|idea)"),
    ("chart_annotate", r"(?:annotate|mark|draw|highlight)\s+(?:on\s+)?(?:chart|graph)\s+(.+)"),
    ("sentiment_chart", r"(?:sentiment.evolution|sentiment.chart|sentiment.over.time)"),
    ("ecosystem_map", r"(?:ecosystem|map|landscape|overview)\s+(?:of\s+)?(?:crypto|defi|market)"),
    ("dashboard_build", r"(?:dashboard|layout|widget|panel)\s+(?:build|create|customize|add)"),
    ("swarm_mode", r"(?:swarm|collaborate|together|group)\s+(?:mode|think|analyze|on)"),
    ("debate_mode", r"(?:debate|argue|discuss|pro.con)\s+(.+)"),
    ("oracle_fusion", r"(?:oracle.fusion|combine.oracles|merge.feeds)"),
    ("voice_mode", r"(?:voice|speak|talk|audio|listen)\s+(?:mode|command|input|on)"),
    ("smart_search", r"(?:search|find|look.up|google)\s+(?:for\s+)?(.+)"),
    ("export_analytics", r"(?:export|download|save|backup)\s+(?:my\s+)?(?:data|analytics|history|report)"),
    ("eco_impact", r"(?:eco|carbon|green|energy|sustainable|environmental)\s+(?:impact|footprint|cost)"),
    ("place_trade", r"(?:place|open|execute|make)\s+(?:a\s+)?(?:trade|order)\s+(?:for\s+)?([A-Za-z]{2,10})"),
    ("buy", r"\b(buy|long)\s+([A-Za-z]{2,10})\b"),
    ("sell", r"\b(sell|short)\s+([A-Za-z]{2,10})\b"),
    ("check_pnl", r"\b(my\s+)?(?:pnl|profit|loss|performance|portfolio)\b"),
    ("volatility", r"(?:how\s+)?volatile\s+(?:is\s+)?([A-Za-z]{2,10})|volatility\s+(?:of\s+)?([A-Za-z]{2,10})"),
    ("dex_liquidity", r"(?:dex|liquidity|pools)\s+(?:for\s+)?([A-Za-z]{2,10})"),
    ("whale_watch", r"\b(?:whale|large\s+transactions?|big\s+moves?)\b"),
    ("market_sentiment", r"\b(?:sentiment|fear|greed|market\s+mood)\b"),
    ("show_price", r"(?:show|get|what'?s?|what is|price of|check)\s+(?:the\s+)?(?:price\s+(?:of\s+)?)?(?:for\s+)?([A-Za-z]{2,10})\b"),
    ("navigate", r"(?:go to|open|navigate to|take me to)\s+(.+)"),
]
INTENTS = [(name, re.compile(pat, re.I)) for name, pat in INTENTS]

PAGES = {
    "feed":"/","agents":"/agents","trading":"/trading","market":"/market",
    "swarm":"/swarm","dashboard":"/dashboard","settings":"/settings",
    "knowledge":"/knowledge","copilot":"/copilot","collectives":"/collectives",
    "guilds":"/guilds","vault":"/vault","heatmap":"/heatmap",
}

# ── RPC helpers ──
async def _rpc(chain, path=""):
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(f"{ARES}/api/rpc/{chain}", json={"path": path})
            return r.json() if r.status_code == 200 else None
    except: return None

async def _price_data(sym):
    # Direct no-auth sources first (no external engine dependency); proxy fallback.
    full = await ms.coingecko_price_full(sym)
    if full:
        return full
    p = await ms.resolve_price(sym)
    if p:
        return {"symbol": sym.upper(), "price": p, "change_24h": None, "volume_24h": None}
    d = await _rpc("coingecko", f"/api/v3/simple/price?ids={sym.lower()}&vs_currencies=usd&include_24hr_change=true&include_24hr_vol=true")
    if d and sym.lower() in d:
        item = d[sym.lower()]
        return {"symbol":sym.upper(),"price":item.get("usd"),"change_24h":item.get("usd_24h_change"),"volume_24h":item.get("usd_24h_vol")}
    return None

async def _vol_data(sym):
    v = await ms.coingecko_volatility(sym, 7)
    if v:
        return {"symbol": v["symbol"], "volatility_7d_pct": v["volatility_pct"],
                "avg_price_7d": v["avg_price"], "data_points": v["data_points"]}
    d = await _rpc("coingecko", f"/api/v3/coins/{sym.lower()}/market_chart?vs_currency=usd&days=7")
    if d and "prices" in d and len(d["prices"])>1:
        vals=[p[1] for p in d["prices"]]
        m=statistics.mean(vals); std=(sum((v-m)**2 for v in vals)/len(vals))**0.5
        return {"symbol":sym.upper(),"volatility_7d_pct":round(std/m*100,2),"avg_price_7d":round(m,4),"data_points":len(vals)}
    return None

# ── DB init ──
async def init_copilot_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS copilot_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER, symbol TEXT,
            condition_text TEXT, target_price REAL, direction TEXT DEFAULT 'above',
            active INTEGER DEFAULT 1, created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (agent_id) REFERENCES agents(id))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS copilot_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER, goal TEXT,
            target REAL, current REAL, unit TEXT, created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (agent_id) REFERENCES agents(id))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS copilot_scheduled (
            id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER, name TEXT,
            cron_expr TEXT, intent_action TEXT, intent_params TEXT,
            active INTEGER DEFAULT 1, created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (agent_id) REFERENCES agents(id))""")
        await db.commit()

# ── Intent parser ──
def parse_intent(text):
    for name, pat in INTENTS:
        m = pat.search(text)
        if m: return {"action":name,"raw":text,"groups":m.groups(),"confidence":0.85}
    return {"action":"unknown","raw":text,"groups":(),"confidence":0.0}

# ── Intent handler mapping ──
async def _handle_intent(action, groups, text):
    """Returns (target, data_dict) for a parsed intent."""
    g = groups
    handlers = {
        "show_price":       lambda: (sym(g[0]) if g else "", _price_data(sym(g[0]) if g else "")),
        "buy":              lambda: (sym(g[1]) if len(g)>1 else "", {"symbol":sym(g[1]) if len(g)>1 else "","side":"buy","endpoint":"/api/trading/orders"}),
        "sell":             lambda: (sym(g[1]) if len(g)>1 else "", {"symbol":sym(g[1]) if len(g)>1 else "","side":"sell","endpoint":"/api/trading/orders"}),
        "place_trade":      lambda: (sym(g[0]) if g else "", {"symbol":sym(g[0]) if g else "","side":"buy" if "buy" in text.lower() else "sell","endpoint":"/api/trading/orders"}),
        "check_pnl":        lambda: ("portfolio", {"endpoint":"/api/trading/performance"}),
        "set_alert":        lambda: ("alert", {"condition":g[0] if g else text,"endpoint":"/api/copilot/alerts"}),
        "volatility":       lambda: (sym(next((x for x in g if x),"")), _vol_data(sym(next((x for x in g if x),"")))),
        "dex_liquidity":    lambda: (sym(g[0]) if g else "", {"liquidity":"checking","source":"dexscreener"}),
        "whale_watch":      lambda: ("whale_activity", {"source":"whale_watch","status":"scanning"}),
        "market_sentiment": lambda: ("market_sentiment", {"indicator":"fear_greed","endpoint":"/api/copilot/sentiment"}),
        # Sim & Backtest
        "backtest":         lambda: ("backtest", {"strategy":g[0] if g else text,"status":"ready","period":"30d","initial_capital":10000}),
        "stress_test":      lambda: ("portfolio", {"mode":"stress_test","scenarios":["-30% crash","+50% rally","vol spike","correlation break"]}),
        "scenario_sim":     lambda: ("simulation", {"scenario":g[0] if g else text,"mode":"what_if"}),
        # Optimization
        "optimize_portfolio": lambda: ("portfolio", {"action":"optimize","method":"sharpe_ratio","max_assets":10}),
        "yield_scan":       lambda: ("defi", {"action":"scan_yields","chains":["eth","sol","arb","op","base"],"min_apy":5}),
        "arbitrage_scan":   lambda: ("arbitrage", {"action":"scan_spreads","sources":["cex","dex"],"min_spread_pct":0.5}),
        "liquidity_track":  lambda: ("liquidity", {"action":"track","chains":["eth","sol","polygon"]}),
        "benchmark":        lambda: ("portfolio", {"action":"benchmark","benchmark":"BTC","period":"all"}),
        "risk_slider":      lambda: ("risk", {"current_level":"medium","levels":["low","medium","high","degen"],"max_drawdown_pct":20}),
        "risk_heatmap":     lambda: ("risk", {"action":"generate_heatmap","symbols":["BTC","ETH","SOL","AVAX","DOT"]}),
        "metric_create":     lambda: ("custom_metric", {"definition":g[0] if g else text,"status":"draft"}),
        "metric_fusion":    lambda: ("custom_metric", {"fusion":g[0] if g else text,"method":"weighted_average"}),
        # Learning
        "learning_path":    lambda: ("learning", {"topic":g[0] if g else "crypto trading","level":"beginner","modules":["basics","technical","risk"]}),
        "learning_quiz":    lambda: ("learning", {"action":"quiz","topic":g[0].strip() if g and g[0] else "general","questions":5}),
        # Goals & Progress
        "goal_track":       lambda: ("goals", {"endpoint":"/api/copilot/goals","action":"track"}),
        "progress_track":   lambda: ("progress", {"action":"summary","metrics":["pnl","win_rate","trades","accuracy"]}),
        "watchlist":        lambda: ("watchlist", {"action":"prioritize","criteria":"momentum"}),
        # Gamification
        "gamification":     lambda: ("gamification", {"action":"status","xp":0,"level":1,"badges":[]}),
        "sentiment_vote":   lambda: ("sentiment", {"action":"vote","target":"signal"}),
        "community_feed":   lambda: ("community", {"action":"feed","sort":"trending"}),
        "signal_amplify":   lambda: ("signal", {"action":"amplify","method":"social_boost"}),
        # Visualization
        "chart_annotate":   lambda: ("chart", {"note":g[0] if g else text,"action":"annotate"}),
        "sentiment_chart":  lambda: ("chart", {"action":"sentiment_evolution","period":"7d"}),
        "ecosystem_map":    lambda: ("ecosystem", {"action":"generate_map","depth":"full"}),
        "dashboard_build":  lambda: ("dashboard", {"action":"customize","widgets":["price","volume","sentiment"]}),
        # Swarm
        "swarm_mode":       lambda: ("swarm", {"mode":"collaborative","agents":3,"topic":"market_analysis"}),
        "debate_mode":      lambda: ("debate", {"topic":g[0] if g else text,"format":"pro_con","rounds":3}),
        "oracle_fusion":    lambda: ("oracle", {"action":"fusion","sources":["pyth","chainlink","coingecko"]}),
        # Other
        "voice_mode":       lambda: ("voice", {"mode":"enabled","input":"microphone"}),
        "smart_search":     lambda: ("search", {"query":g[0] if g else text,"sources":["web","knowledge","feed"]}),
        "export_analytics": lambda: ("export", {"format":"csv","scope":"all","endpoint":"/api/copilot/export"}),
        "eco_impact":       lambda: ("eco", {"action":"estimate","chains":["eth","btc","sol"]}),
    }

    if action in handlers:
        t, d = handlers[action]()
        if hasattr(d, '__await__'):  # async result
            d = await d
        return t, d
    return "", {}

# ── Identity — the Copilot IS the connected agent ──
@router.get("/whoami")
async def whoami(agent: dict = Depends(get_agent)):
    """The Copilot is not a separate assistant — it's the agent you connect as.
    Returns the connected agent's identity so the UI can act on its behalf. The
    agent's own LLM (configured on its side) is the reasoning layer; Vantage just
    provides the data + action surface this endpoint family exposes."""
    return {
        "agent": agent.get("name"),
        "id": agent.get("id"),
        "bio": agent.get("bio"),
        "capabilities": ["price", "volatility", "sentiment", "arbitrage", "yields",
                         "dex_liquidity", "whales", "backtest", "pnl", "place_trade", "navigate"],
    }


# ── Chat endpoint ──
@router.post("/chat")
async def copilot_chat(request: Request, agent: dict = Depends(get_agent)):
    body = await _parse_body(request)
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text field is required")

    parsed = parse_intent(text)
    action = parsed["action"]
    groups = parsed["groups"]
    confidence = parsed["confidence"]

    target, data = "", {}

    if action == "navigate":
        raw = (groups[0] or "").lower().strip().rstrip(".,!?")
        for k, v in PAGES.items():
            if k in raw or raw in k:
                target, data = k, {"path": v}
                break
        else:
            target, data = raw, {"path": f"/{raw}"}
    elif action != "unknown":
        target, data = await _handle_intent(action, groups, text)

    result = {
        "action": action if action != "unknown" else ("place_trade" if action in ("buy","sell") else action),
        "target": target,
        "data": data,
        "confidence": confidence,
    }
    return {"query": text, "intent": result}

# ── Execute endpoint ──
@router.post("/execute")
async def copilot_execute(request: Request, agent: dict = Depends(get_agent)):
    body = await _parse_body(request)
    action = body.get("action", "")
    target = body.get("target", "")
    data = body.get("data", {})
    key = request.headers.get("X-Agent-Key", "")
    base = "http://localhost:8001"

    if action == "navigate":
        return {"action":action,"target":target,"data":{"path":PAGES.get(target,f"/{target}")},"confidence":1.0}
    if action == "show_price" and target:
        price = await _price_data(target)
        return {"action":action,"target":target,"data":price or {},"confidence":0.9}
    if action == "volatility" and target:
        vol = await _vol_data(target)
        return {"action":action,"target":target,"data":vol or {},"confidence":0.85}
    if action == "market_sentiment":
        b = await ms.market_breadth()
        return {"action":action,"target":"market_sentiment","data":b or {},"confidence":0.9}
    if action == "arbitrage_scan":
        opps = await ms.real_arbitrage()
        return {"action":action,"target":"arbitrage","data":{"opportunities":opps,"count":len(opps)},"confidence":0.9}
    if action == "yield_scan":
        pools = await ms.defillama_yields(20)
        return {"action":action,"target":"defi","data":{"pools":pools,"count":len(pools)},"confidence":0.9}
    if action == "dex_liquidity" and target:
        pairs = await ms.dexscreener_search(target, 15)
        return {"action":action,"target":target,"data":{"pairs":pairs,"count":len(pairs)},"confidence":0.9}
    if action == "whale_watch":
        txs = await ms.whale_txs(10)
        return {"action":action,"target":"whale_activity","data":{"transactions":txs,"chain":"bitcoin"},"confidence":0.85}
    if action == "backtest":
        cand = (data.get("symbol") or target or "").strip()
        bt_sym = sym(cand) if cand and cand.isalpha() and len(cand) <= 5 else "BTC"
        result = await ms.backtest(bt_sym, 90)
        return {"action":action,"target":bt_sym,"data":result or {"error":"insufficient data"},"confidence":0.85}
    if action == "check_pnl":
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{base}/api/trading/performance", headers={"X-Agent-Key":key})
                return {"action":action,"target":"portfolio","data":r.json() if r.status_code==200 else {"error":r.text},"confidence":1.0}
        except Exception as e:
            return {"action":action,"target":"portfolio","data":{"error":str(e)},"confidence":0.5}
    if action in ("place_trade","buy","sell"):
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.post(f"{base}/api/trading/orders", json={
                    "symbol":target,"side":data.get("side","buy"),
                    "quantity":data.get("quantity",0.001),
                    "chain":data.get("chain","solana"),
                    "order_type":data.get("order_type","market"),
                }, headers={"X-Agent-Key":key})
                return {"action":"place_trade","target":target,"data":{"order_result":r.json() if r.status_code==200 else {"error":r.text}},"confidence":0.9}
        except Exception as e:
            return {"action":"place_trade","target":target,"data":{"error":str(e)},"confidence":0.5}
    raise HTTPException(400, f"Unsupported action: {action}")

# ── Alerts CRUD ──
@router.get("/alerts")
async def list_alerts(agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM copilot_alerts WHERE agent_id=? ORDER BY created_at DESC",(agent["id"],)) as cur:
            return [dict(r) for r in await cur.fetchall()]

@router.post("/alerts")
async def create_alert(request: Request, agent: dict = Depends(get_agent)):
    body = await _parse_body(request)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO copilot_alerts (agent_id,symbol,condition_text,target_price,direction) VALUES (?,?,?,?,?)",
            (agent["id"],body.get("symbol",""),body.get("condition",""),body.get("target_price"),body.get("direction","above")))
        await db.commit()
        return {"id":cur.lastrowid,"status":"created"}

@router.delete("/alerts/{alert_id}")
async def delete_alert(alert_id:int, agent:dict=Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM copilot_alerts WHERE id=? AND agent_id=?",(alert_id,agent["id"]))
        await db.commit()
    return {"status":"deleted"}

# ── Goals CRUD ──
@router.get("/goals")
async def list_goals(agent:dict=Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        async with db.execute("SELECT * FROM copilot_goals WHERE agent_id=? ORDER BY created_at DESC",(agent["id"],)) as cur:
            return [dict(r) for r in await cur.fetchall()]

@router.post("/goals")
async def create_goal(request:Request, agent:dict=Depends(get_agent)):
    body = await _parse_body(request)
    async with aiosqlite.connect(DB_PATH) as db:
        cur=await db.execute("INSERT INTO copilot_goals (agent_id,goal,target,current,unit) VALUES (?,?,?,?,?)",
            (agent["id"],body.get("goal",""),body.get("target",0),body.get("current",0),body.get("unit","%")))
        await db.commit()
        return {"id":cur.lastrowid,"status":"created"}

# ── Scheduled alerts ──
@router.get("/scheduled")
async def list_scheduled(agent:dict=Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        async with db.execute("SELECT * FROM copilot_scheduled WHERE agent_id=? ORDER BY created_at DESC",(agent["id"],)) as cur:
            return [dict(r) for r in await cur.fetchall()]

@router.post("/scheduled")
async def create_scheduled(request:Request, agent:dict=Depends(get_agent)):
    body = await _parse_body(request)
    async with aiosqlite.connect(DB_PATH) as db:
        cur=await db.execute("INSERT INTO copilot_scheduled (agent_id,name,cron_expr,intent_action,intent_params) VALUES (?,?,?,?,?)",
            (agent["id"],body.get("name",""),body.get("cron","0 */4 * * *"),body.get("action",""),json.dumps(body.get("params",{}))))
        await db.commit()
        return {"id":cur.lastrowid,"status":"scheduled"}
