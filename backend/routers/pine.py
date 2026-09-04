"""Pine Script indicators — agents author technical indicators that run in the
isolated `pine-runtime` sidecar (never in this process) and return numeric series
only. Scripts are governed by a Zàngbétò review before run/save/share, persisted
per-agent, and shareable into guilds.
"""
import os
import json
import logging

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Query

from backend.db import DB_PATH
from backend.deps import get_agent, _parse_body
from backend import market_sources as ms
from ..db import get_db
from backend.pine_validate import validate_pine, annotate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pine", tags=["pine"])

PINE_RUNTIME_URL = os.environ.get("PINE_RUNTIME_URL", "http://127.0.0.1:9871")
ZANGBETO_URL = os.environ.get("ZANGBETO_URL", "")  # optional governance service


async def init_pine_db():
    async with get_db() as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS pine_indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            script TEXT NOT NULL,
            description TEXT DEFAULT '',
            shared INTEGER DEFAULT 0,
            guild_slug TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (agent_id) REFERENCES agents(id))""")
        await db.commit()


async def _review(script: str, agent: dict) -> dict:
    """Best-effort Zàngbétò review. Fail-open if the service is unconfigured or
    unreachable (the sandbox itself is the hard boundary), but BLOCK on a critical
    verdict. Returns {block: bool, reason: str}."""
    if not ZANGBETO_URL:
        return {"block": False, "reason": "review service not configured"}
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.post(f"{ZANGBETO_URL.rstrip('/')}/review",
                             json={"agent_id": agent.get("name"), "tool": "pine_script",
                                   "detail": script[:2000]})
            if r.status_code == 200:
                d = r.json()
                return {"block": bool(d.get("block")), "reason": d.get("rationale", "")}
    except Exception as e:
        logger.debug("zangbeto review unavailable: %s", e)
    return {"block": False, "reason": "review unavailable (fail-open)"}


@router.post("/run")
async def run_pine(request: Request, agent: dict = Depends(get_agent)):
    """Review → fetch candles → execute in the sandbox → return plotted series."""
    body = await _parse_body(request)
    script = (body.get("script") or "").strip()
    symbol = (body.get("symbol") or "BTC").strip()
    interval = (body.get("interval") or "1d").strip()
    if not script:
        raise HTTPException(400, "script is required")
    if len(script) > 8000:
        raise HTTPException(400, "script too long (max 8000 chars)")

    compile_result = await validate_pine(script)
    if not compile_result["valid"]:
        annotated = annotate(script, compile_result["errors"])
        raise HTTPException(422, {"error": "Pine compile error", "errors": compile_result["errors"], "annotated_source": annotated})

    verdict = await _review(script, agent)
    if verdict["block"]:
        raise HTTPException(403, f"Script blocked by governance: {verdict['reason']}")

    candles = await ms.ohlc(symbol, interval, 200)
    if not candles:
        raise HTTPException(404, f"No candle data for {symbol.upper()} ({interval})")

    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.post(f"{PINE_RUNTIME_URL.rstrip('/')}/run",
                             json={"script": script, "candles": candles})
        if r.status_code == 200:
            return {"symbol": symbol.upper(), "interval": interval, **r.json()}
        detail = r.json().get("error", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise HTTPException(422, f"Pine error: {detail}")
    except httpx.HTTPError:
        raise HTTPException(503, "Pine sandbox is unavailable")


@router.post("/indicators")
async def save_indicator(request: Request, agent: dict = Depends(get_agent)):
    """Save a Pine indicator to the agent's library (after governance review)."""
    body = await _parse_body(request)
    name = (body.get("name") or "").strip()[:120]
    script = (body.get("script") or "").strip()
    description = (body.get("description") or "").strip()[:500]
    if not name or not script:
        raise HTTPException(400, "name and script are required")
    if len(script) > 8000:
        raise HTTPException(400, "script too long (max 8000 chars)")

    compile_result = await validate_pine(script)
    if not compile_result["valid"]:
        annotated = annotate(script, compile_result["errors"])
        raise HTTPException(422, {"error": "Pine compile error", "errors": compile_result["errors"], "annotated_source": annotated})

    verdict = await _review(script, agent)
    if verdict["block"]:
        raise HTTPException(403, f"Script blocked by governance: {verdict['reason']}")

    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO pine_indicators (agent_id, name, script, description) VALUES (?,?,?,?)",
            (agent["id"], name, script, description))
        await db.commit()
        return {"id": cur.lastrowid, "status": "saved", "name": name}


@router.get("/indicators")
async def list_indicators(agent: dict = Depends(get_agent)):
    """The agent's own indicators plus any shared into its guilds."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        own = await (await db.execute(
            "SELECT id, name, script, description, shared, guild_slug, created_at FROM pine_indicators WHERE agent_id=? ORDER BY created_at DESC",
            (agent["id"],))).fetchall()
        shared = await (await db.execute(
            "SELECT id, name, script, description, shared, guild_slug, created_at FROM pine_indicators WHERE shared=1 AND agent_id!=? ORDER BY created_at DESC LIMIT 100",
            (agent["id"],))).fetchall()
    rows = [dict(r) for r in own] + [dict(r) for r in shared]
    return rows


@router.post("/indicators/{indicator_id}/share")
async def share_indicator(indicator_id: int, request: Request, agent: dict = Depends(get_agent)):
    """Share one of the agent's own indicators (optionally tagged to a guild)."""
    body = await _parse_body(request)
    guild_slug = (body.get("guild_slug") or "").strip() or None
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT id FROM pine_indicators WHERE id=? AND agent_id=?", (indicator_id, agent["id"]))).fetchone()
        if not row:
            raise HTTPException(404, "Indicator not found")
        await db.execute("UPDATE pine_indicators SET shared=1, guild_slug=? WHERE id=?", (guild_slug, indicator_id))
        await db.commit()
    return {"status": "shared", "id": indicator_id, "guild_slug": guild_slug}


@router.delete("/indicators/{indicator_id}")
async def delete_indicator(indicator_id: int, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        await db.execute("DELETE FROM pine_indicators WHERE id=? AND agent_id=?", (indicator_id, agent["id"]))
        await db.commit()
    return {"status": "deleted"}


@router.post("/indicators/{indicator_id}/signal")
async def evaluate_pine_signal(indicator_id: int, request: Request, agent: dict = Depends(get_agent)):
    """Evaluate a saved Pine indicator against the latest real candle and,
    if it triggers, optionally place a real (paper-fill) order tagged
    trigger_reason='pine:<id>:<name>' -- so trade_outcome_learner.py's
    source_performance tracks this indicator's real trading performance
    over time, same as any other signal source. Wires 2026-08-29's
    previously-disconnected pine_indicators table into the actual
    paper-fill/learning loop instead of leaving Pine indicators as chart
    overlays with no downstream effect.

    Trigger source: plotshape() markers ONLY (see pine-engine.js) -- the
    one output shape that's genuinely boolean/discrete in this engine.
    Scripts with no markers (Bollinger Squeeze, MACD Custom, VWAP Custom,
    the default EMA-crossover template all only feed their crossover/
    squeeze booleans into bgcolor(), which is parse-validated but produces
    no numeric/boolean series) have nothing to act on -- returns
    triggered=false honestly rather than inventing a signal from a plot.

    Direction heuristic: a triggered marker whose name contains
    bear/sell/short/down is SELL, everything else is BUY (matches
    pine.py's own RSI Divergence template: "Bearish Div"/"Bullish Div").

    Hard safety boundary: this endpoint only ever creates a pending order
    and, for BUY, paper-fills it (simulated, never real settlement) --
    it never calls any live-execution path. A Pine script cannot move real
    funds through this endpoint under any input.
    """
    body = await _parse_body(request)
    symbol = (body.get("symbol") or "BTC").strip()
    interval = (body.get("interval") or "1d").strip()
    chain = (body.get("chain") or "solana").strip()
    quantity = float(body.get("quantity") or 0)
    execute = bool(body.get("execute", False))

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM pine_indicators WHERE id=? AND (agent_id=? OR shared=1)",
            (indicator_id, agent["id"]))).fetchone()
    if not row:
        raise HTTPException(404, "Indicator not found")
    indicator = dict(row)

    candles = await ms.ohlc(symbol, interval, 200)
    if not candles:
        raise HTTPException(404, f"No candle data for {symbol.upper()} ({interval})")

    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.post(f"{PINE_RUNTIME_URL.rstrip('/')}/run",
                             json={"script": indicator["script"], "candles": candles})
        if r.status_code != 200:
            detail = r.json().get("error", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
            raise HTTPException(422, f"Pine error: {detail}")
        result = r.json()
    except httpx.HTTPError:
        raise HTTPException(503, "Pine sandbox is unavailable")

    markers = result.get("markers") or {}
    if not markers:
        return {
            "triggered": False,
            "indicator_id": indicator_id,
            "reason": "script has no plotshape() markers to act on -- only plotshape() "
                      "output is a real boolean signal in this sandbox",
        }

    last_idx = len(candles) - 1
    triggered_names = [
        name for name, series in markers.items()
        if series and len(series) > last_idx and series[last_idx].get("value") is True
    ]
    if not triggered_names:
        return {"triggered": False, "indicator_id": indicator_id, "checked_markers": sorted(markers)}

    def _direction(name: str) -> str:
        low = name.lower()
        return "SELL" if any(w in low for w in ("bear", "sell", "short", "down")) else "BUY"

    side = _direction(triggered_names[0])
    source = f"pine:{indicator_id}:{indicator['name']}"
    response = {
        "triggered": True,
        "indicator_id": indicator_id,
        "indicator_name": indicator["name"],
        "matched_markers": triggered_names,
        "side": side,
        "symbol": symbol.upper(),
    }

    if not execute or quantity <= 0:
        response["order"] = None
        response["note"] = "preview only -- pass execute=true and a real quantity to place a paper order"
        return response

    # Reuse the real order-creation + paper-fill paths (risk limits, journal
    # entries, live-quote fill price) rather than duplicating that logic.
    from .trading import OrderCreate, create_order, paper_fill_order

    order_data = OrderCreate(symbol=symbol.upper(), side=side.lower(), chain=chain,
                              quantity=quantity, trigger_reason=source)
    created = await create_order(order_data, agent)
    response["order"] = created

    if side == "BUY":
        response["fill"] = await paper_fill_order(created["id"], agent)
    else:
        response["fill"] = None
        response["fill_note"] = "SELL orders are created pending, not auto-paper-filled (no automatic position-matching in this pass)"

    # Real observation trace into Mycelium's substrate (2026-08-30 audit --
    # a Pine-indicator-triggered order was previously invisible to Mycelium
    # entirely; trade_outcome_learner.py's own source_performance trace only
    # covers this source's AGGREGATE PnL over time, not the individual
    # trigger event itself). Every real order here has its own new
    # autoincrement id, so unlike the other emitters in this module this is
    # a genuine discrete event -- no value-based dedup needed, each call
    # that reaches this point already represents a real, new order.
    try:
        from backend.mycelium_bridge import post_observation
        post_observation(
            agent="pine_signal", session=f"pine-{indicator_id}",
            action="pine_signal_triggered", target=symbol.upper(),
            payload={
                "indicator_id": indicator_id, "indicator_name": indicator["name"],
                "matched_markers": triggered_names, "side": side,
                "order_id": created.get("id"), "source": source,
            },
        )
    except Exception as e:
        logger.debug("evaluate_pine_signal: mycelium trace emit failed: %s", e)

    return response


@router.post("/generate")
async def generate_pine(request: Request, agent: dict = Depends(get_agent)):
    """Natural-language → Pine Script generation.

    Sends the prompt + chart context to the agent's LLM (via OpenRouter or similar).
    Falls back to a template-based generator if no LLM key is configured.
    Returns the generated Pine Script v5 code for the user to review and run.
    """
    body = await _parse_body(request)
    prompt = (body.get("prompt") or "").strip()
    symbol = (body.get("symbol") or "BTC").strip().upper()
    interval = (body.get("interval") or "1d").strip()
    if not prompt:
        raise HTTPException(400, "prompt is required")

    # Try LLM generation if OpenRouter key is available
    openrouter_key = os.environ.get("VANTAGE_OPENROUTER_KEY") or getattr(
        __import__("backend.config", fromlist=["settings"]).settings, "OPENROUTER_KEY", ""
    )
    if openrouter_key:
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {openrouter_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": os.environ.get("PINE_LLM_MODEL", "google/gemini-flash-1.5"),
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "You are a Pine Script v5 expert. Generate ONLY valid Pine Script code. "
                                    "No explanations, no markdown fences — raw code only. "
                                    f"Current chart: {symbol} on {interval} interval. "
                                    "The script must include indicator() or strategy() declaration. "
                                    "Use ta.ema(), ta.sma(), ta.rsi(), ta.macd(), ta.atr(), ta.vwap(), ta.crossover(), ta.crossunder(), etc. "
                                    "Include plot(), hline(), bgcolor() or plotshape() as appropriate."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 2000,
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    generated = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    # Strip markdown fences if present
                    generated = generated.replace("```pinescript", "").replace("```pine", "").replace("```", "").strip()
                    if generated and len(generated) > 20:
                        return {"script": generated, "method": "llm", "model": "openrouter"}
        except Exception as e:
            logger.warning("LLM Pine generation failed, falling back to template: %s", e)

    # Template-based fallback
    lower = prompt.lower()
    script = ""

    if "rsi" in lower and ("diverg" in lower or "divergence" in lower):
        script = f"""//@version=5
indicator("RSI Divergence", overlay=true)
length = input.int(14, "RSI Length")
rsi = ta.rsi(close, length)
priceHH = close > close[1] and close[1] > close[2]
rsiLH = rsi < rsi[1] and rsi[1] < rsi[2]
bearishDiv = priceHH and rsiLH
priceLL = close < close[1] and close[1] < close[2]
rsiHL = rsi > rsi[1] and rsi[1] > rsi[2]
bullishDiv = priceLL and rsiHL
plotshape(bearishDiv, "Bearish Div", shape.triangledown, location.abovebar, color=color.red)
plotshape(bullishDiv, "Bullish Div", shape.triangleup, location.belowbar, color=color.green)
bgcolor(bearishDiv ? color.new(color.red, 90) : bullishDiv ? color.new(color.green, 90) : na)
hline(70, "Overbought", color=color.red)
hline(30, "Oversold", color=color.green)
plot(rsi, "RSI", color=color.purple)"""
    elif "bollinger" in lower or "squeeze" in lower:
        script = f"""//@version=5
indicator("Bollinger Squeeze", overlay=true)
length = input.int(20, "Length")
mult = input.float(2.0, "Std Dev")
basis = ta.sma(close, length)
dev = mult * ta.stdev(close, length)
upper = basis + dev
lower = basis - dev
plot(basis, "SMA", color=color.blue)
plot(upper, "Upper", color=color.red)
plot(lower, "Lower", color=color.green)
bandWidth = (upper - lower) / basis * 100
squeeze = bandWidth < ta.sma(bandWidth, 20)
bgcolor(squeeze ? color.new(color.yellow, 90) : na)"""
    elif "macd" in lower:
        script = f"""//@version=5
indicator("MACD Custom", overlay=false)
[macdLine, signalLine, hist] = ta.macd(close, 12, 26, 9)
plot(macdLine, "MACD", color=color.blue)
plot(signalLine, "Signal", color=color.orange)
plot(hist, "Histogram", color=hist > 0 ? color.green : color.red, style=plot.style_columns)
hline(0, "Zero", color=color.gray)"""
    elif "vwap" in lower or "volume weighted" in lower:
        script = f"""//@version=5
indicator("VWAP Custom", overlay=true)
v = ta.vwap(close)
plot(v, "VWAP", color=color.orange, linewidth=2)
volColor = close >= open ? color.green : color.red
plot(volume, "Volume", color=color.new(volColor, 70), style=plot.style_columns)"""
    elif "supertrend" in lower or "super trend" in lower:
        script = f"""//@version=5
indicator("SuperTrend", overlay=true)
factor = input.float(3.0, "Factor")
atrLen = input.int(10, "ATR Length")
[st, dir] = ta.supertrend(factor, atrLen)
plot(st, "SuperTrend", color=dir < 0 ? color.green : color.red, linewidth=2)
bullish = ta.crossover(dir, 0)
bearish = ta.crossunder(dir, 0)
plotshape(bullish, "Bullish Flip", shape.triangleup, location.belowbar, color=color.green)
plotshape(bearish, "Bearish Flip", shape.triangledown, location.abovebar, color=color.red)"""
    elif "squeeze" in lower and "momentum" in lower:
        script = f"""//@version=5
indicator("Squeeze Momentum", overlay=false)
length = input.int(20, "BB/KC Length")
bbMult = input.float(2.0, "BB Mult")
kcMult = input.float(1.5, "KC Mult")
[sqzOn, mom] = ta.squeeze(length, bbMult, kcMult)
plot(mom, "Momentum", color=mom > 0 ? color.green : color.red, style=plot.style_columns)
plotshape(sqzOn, "Squeeze On", shape.circle, location.bottom, color=color.yellow)
hline(0, "Zero", color=color.gray)"""
    else:
        # Default: EMA crossover
        script = f"""//@version=5
indicator("{prompt[:30].strip() or 'Custom Indicator'}", overlay=true)
fastLen = input.int(10, "Fast EMA")
slowLen = input.int(30, "Slow EMA")
fastEMA = ta.ema(close, fastLen)
slowEMA = ta.ema(close, slowLen)
plot(fastEMA, "Fast EMA", color=color.green)
plot(slowEMA, "Slow EMA", color=color.red)
bullish = ta.crossover(fastEMA, slowEMA)
bearish = ta.crossunder(fastEMA, slowEMA)
bgcolor(bullish ? color.new(color.green, 90) : bearish ? color.new(color.red, 90) : na)"""

    compile_result = await validate_pine(script)
    if not compile_result["valid"]:
        logger.warning("generate_pine: template produced invalid Pine: %s", compile_result.get("errors"))
        return {
            "script": script,
            "method": "template",
            "message": "Template generated (LLM not configured). Review before running.",
            "compile_warning": compile_result.get("errors"),
        }

    return {
        "script": script,
        "method": "template",
        "message": "Template generated (LLM not configured). Review before running.",
    }
