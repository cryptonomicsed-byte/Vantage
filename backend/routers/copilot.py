"""Copilot — Natural language command router for Vantage.

Maps intents (show price, place trade, check PnL, set alert, navigate,
volatility, dex liquidity, whale watch, market sentiment) to existing
Vantage endpoints and Ares RPC (localhost:9861) for market data.
"""
import logging
import re
from typing import Optional

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from backend.db import DB_PATH
from backend.config import settings
from backend.deps import get_agent, _parse_body

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/copilot", tags=["copilot"])

ARES_RPC_URL = "http://localhost:9861"

# ── Intent patterns (ordered: specific first, generic last) ───────────
# Specific intents (set_alert, place_trade, check_pnl) are checked before
# generic ones (show_price, navigate) to avoid false matches.
INTENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("set_alert", re.compile(
        r"(?:set|create|add)\s+(?:a\s+|an\s+)?alert\s+(?:for|when|if)\s+(.+)",
        re.I,
    )),
    ("place_trade", re.compile(
        r"(?:place|open|execute|make)\s+(?:a\s+)?(?:trade|order)\s+(?:for\s+)?([A-Za-z]{2,10})",
        re.I,
    )),
    ("buy", re.compile(
        r"(?:buy|long)\s+([A-Za-z]{2,10})",
        re.I,
    )),
    ("sell", re.compile(
        r"(?:sell|short)\s+([A-Za-z]{2,10})",
        re.I,
    )),
    ("check_pnl", re.compile(
        r"\b(my\s+)?(?:pnl|profit|loss|performance|portfolio)\b",
        re.I,
    )),
    ("volatility", re.compile(
        r"(?:(?:how\s+volatile|check\s+volatility)\s+(?:\S+\s+)?([A-Za-z]{2,10})"
        r"|(?:what'?s|what is)\s+(?:the\s+)?volatility\s+(?:of\s+)?([A-Za-z]{2,10})"
        r"|(?:what'?s|what is)\s+([A-Za-z]{2,10})\s+volatility)",
        re.I,
    )),
    ("dex_liquidity", re.compile(
        r"(?:(?:show|get|check|what'?s)\s+)?(?:DEX\s+)?(?:liquidity|pools|dex\s+pools)\s+(?:\S+\s+)?([A-Za-z]{2,10})",
        re.I,
    )),
    ("whale_watch", re.compile(
        r"(?:show|get|check|recent|what'?s)\s+(?:whale\s+)?(?:activity|movement|transactions|large\s+transactions|whale\s+activity)",
        re.I,
    )),
    ("market_sentiment", re.compile(
        r"(?:what'?s\s+the\s+)?(?:market\s+)?(?:sentiment|fear\s+and\s+greed|fear\s+greed|market\s+sentiment)",
        re.I,
    )),
    ("show_price", re.compile(
        r"(?:show|get|check|what'?s|what is|price of)\s+(?:me\s+|the\s+|my\s+)?(?:price\s+of\s+)?([A-Za-z]{2,10})\b(?:\s+price)?",
        re.I,
    )),
    ("navigate", re.compile(
        r"(?:go to|open|navigate to|show me|take me to)\s+(.+)",
        re.I,
    )),
]

PAGE_MAP: dict[str, str] = {
    "feed": "/",
    "agents": "/agents",
    "trading": "/trading",
    "market": "/market",
    "swarm": "/swarm",
    "dashboard": "/dashboard",
    "settings": "/settings",
    "knowledge": "/knowledge",
    "collectives": "/collectives",
    "guilds": "/guilds",
    "vault": "/vault",
    "pipeline": "/pipeline",
    "create": "/create",
    "heatmap": "/heatmap",
}

# ── Symbol alias map (name → ticker) ─────────────────────────────────
SYMBOL_ALIASES: dict[str, str] = {
    "bitcoin": "BTC", "btc": "BTC",
    "ethereum": "ETH", "eth": "ETH",
    "solana": "SOL", "sol": "SOL",
    "cardano": "ADA", "ada": "ADA",
    "ripple": "XRP", "xrp": "XRP",
    "dogecoin": "DOGE", "doge": "DOGE",
    "polkadot": "DOT", "dot": "DOT",
    "avalanche": "AVAX", "avax": "AVAX",
    "polygon": "MATIC", "matic": "MATIC",
    "chainlink": "LINK", "link": "LINK",
    "uniswap": "UNI", "uni": "UNI",
    "pepe": "PEPE",
    "bonk": "BONK",
    "shiba inu": "SHIB", "shib": "SHIB",
}


def _resolve_symbol(raw: str | None) -> str:
    """Resolve a user-provided symbol or name to its uppercase ticker."""
    if not raw:
        return ""
    key = raw.strip().lower()
    return SYMBOL_ALIASES.get(key, key.upper())


# ── Table initialisation ─────────────────────────────────────────────────
async def init_copilot_db() -> None:
    """Create the copilot_alerts table if it does not exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS copilot_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                symbol TEXT DEFAULT '',
                condition_text TEXT NOT NULL DEFAULT '',
                target_price REAL,
                direction TEXT DEFAULT 'above',
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        await db.commit()


# ── Helpers ──────────────────────────────────────────────────────────────

async def _parse_intent(text: str) -> dict:
    """Map natural language text to an action intent.

    Patterns are checked in order — specific intents (set_alert, place_trade,
    check_pnl) take priority over generic ones (show_price, navigate).
    """
    for intent, pattern in INTENT_PATTERNS:
        m = pattern.search(text)
        if m:
            groups = m.groups()
            return {
                "action": intent,
                "raw": text,
                "groups": groups,
                "confidence": 0.85,
            }
    return {"action": "unknown", "raw": text, "groups": (), "confidence": 0.0}


async def _fetch_ares_price(symbol: str) -> Optional[dict]:
    """Call Ares RPC for live price data via POST to coingecko proxy."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{ARES_RPC_URL}/api/rpc/coingecko",
                json={"path": f"/api/v3/simple/price?ids={symbol.lower()}&vs_currencies=usd"},
            )
            resp.raise_for_status()
            data = resp.json()
            price = data.get(symbol.lower(), {}).get("usd")
            if price is not None:
                return {"symbol": symbol.upper(), "price": float(price)}
            return None
    except Exception as exc:
        logger.warning("Ares RPC price lookup failed for %s: %s", symbol, exc)
        return None


async def _fetch_ares_market_data(symbol: str) -> Optional[dict]:
    """Call Ares RPC for broader market data (24h change, volume) via POST."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{ARES_RPC_URL}/api/rpc/coingecko",
                json={
                    "path": (
                        f"/api/v3/simple/price?ids={symbol.lower()}"
                        f"&vs_currencies=usd"
                        f"&include_24hr_change=true"
                        f"&include_24hr_vol=true"
                    ),
                },
            )
            resp.raise_for_status()
            data = resp.json()
            item = data.get(symbol.lower(), {})
            return {
                "symbol": symbol.upper(),
                "price": item.get("usd"),
                "change_24h": item.get("usd_24h_change"),
                "volume_24h": item.get("usd_24h_vol"),
            }
    except Exception as exc:
        logger.warning("Ares RPC market data failed for %s: %s", symbol, exc)
        return None


async def _fetch_ares_liquidity(symbol: str) -> Optional[dict]:
    """Call Ares RPC for DEX liquidity data via dexscreener."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{ARES_RPC_URL}/api/rpc/dexscreener",
                json={"path": f"/api/v1/tokens/{symbol.lower()}"},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("Ares RPC liquidity lookup failed for %s: %s", symbol, exc)
        return None


async def _fetch_ares_volatility(symbol: str) -> Optional[dict]:
    """Estimate volatility from 7-day price history via coingecko."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{ARES_RPC_URL}/api/rpc/coingecko",
                json={
                    "path": f"/api/v3/coins/{symbol.lower()}/market_chart?vs_currency=usd&days=7",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            prices = data.get("prices", [])
            if len(prices) > 1:
                vals = [p[1] for p in prices]
                mean = sum(vals) / len(vals)
                variance = sum((v - mean) ** 2 for v in vals) / len(vals)
                std_dev = variance ** 0.5
                volatility_pct = round(std_dev / mean * 100, 2) if mean else 0
                return {
                    "symbol": symbol.upper(),
                    "volatility_7d_pct": volatility_pct,
                    "avg_price_7d": round(mean, 4),
                    "data_points": len(prices),
                }
            return {"symbol": symbol.upper(), "volatility_7d_pct": 0, "data_points": 0}
    except Exception as exc:
        logger.warning("Ares RPC volatility lookup failed for %s: %s", symbol, exc)
        return None


# ── Endpoints ────────────────────────────────────────────────────────────

@router.post("/chat")
async def copilot_chat(request: Request, agent: dict = Depends(get_agent)):
    """Parse natural language input and return an intent with associated data."""
    body = await _parse_body(request)
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text field is required")

    parsed = await _parse_intent(text)
    action = parsed["action"]
    groups = parsed["groups"]
    confidence = parsed["confidence"]

    result: dict = {"action": action, "target": "", "data": {}, "confidence": confidence}

    if action == "show_price":
        symbol = _resolve_symbol(groups[0]) if groups else ""
        result["target"] = symbol
        price_data = await _fetch_ares_price(symbol)
        result["data"] = price_data or {"symbol": symbol, "price": None}
        if price_data and price_data.get("price") is not None:
            mkt = await _fetch_ares_market_data(symbol)
            if mkt:
                result["data"].update(mkt)

    elif action in ("buy", "sell"):
        symbol = _resolve_symbol(groups[0]) if groups else ""
        side = "buy" if action == "buy" else "sell"
        result["action"] = "place_trade"
        result["target"] = symbol
        result["data"] = {
            "symbol": symbol,
            "side": side,
            "endpoint": "/api/trading/orders",
        }

    elif action == "place_trade":
        symbol = _resolve_symbol(groups[0]) if groups else ""
        result["target"] = symbol
        side = "buy" if "buy" in text.lower() else "sell"
        result["data"] = {
            "symbol": symbol,
            "side": side,
            "endpoint": "/api/trading/orders",
        }

    elif action == "check_pnl":
        result["target"] = "portfolio"
        result["data"] = {"endpoint": "/api/trading/performance"}

    elif action == "set_alert":
        cond = groups[0] if groups else text
        result["target"] = "alert"
        result["data"] = {"condition": cond, "endpoint": "/api/copilot/alerts"}

    elif action == "volatility":
        raw = next((g for g in groups if g), "")
        symbol = _resolve_symbol(raw) if raw else ""
        result["target"] = symbol
        vol_data = await _fetch_ares_volatility(symbol)
        result["data"] = vol_data or {"symbol": symbol, "volatility_7d_pct": None}

    elif action == "dex_liquidity":
        symbol = _resolve_symbol(groups[0]) if groups else ""
        result["target"] = symbol
        liq_data = await _fetch_ares_liquidity(symbol)
        result["data"] = liq_data or {"symbol": symbol, "liquidity": None}

    elif action == "whale_watch":
        result["target"] = "whale_activity"
        result["data"] = {"source": "whale_watch"}

    elif action == "market_sentiment":
        result["target"] = "market_sentiment"
        result["data"] = {"source": "coingecko", "indicator": "fear_greed"}

    elif action == "navigate":
        raw_target = groups[0].lower().strip() if groups else ""
        raw_target = raw_target.rstrip(".,!?")
        matched = False
        for key, path in PAGE_MAP.items():
            if key in raw_target or raw_target in key:
                result["target"] = key
                result["data"] = {"path": path}
                matched = True
                break
        if not matched:
            result["target"] = raw_target
            result["data"] = {"path": f"/{raw_target}"}

    return {"query": text, "intent": result}


@router.post("/execute")
async def copilot_execute(request: Request, agent: dict = Depends(get_agent)):
    """Execute a parsed intent — actually places trades, fetches PnL, etc."""
    body = await _parse_body(request)
    action = body.get("action", "")
    target = body.get("target", "")
    data = body.get("data", {})

    if action == "navigate":
        path = PAGE_MAP.get(target) or f"/{target}"
        return {"action": action, "target": target, "data": {"path": path}, "confidence": 1.0}

    if action == "show_price":
        price_data = await _fetch_ares_price(target) if target else None
        return {
            "action": action,
            "target": target,
            "data": price_data or {},
            "confidence": 0.9,
        }

    if action == "check_pnl":
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                agent_key = request.headers.get("X-Agent-Key", "")
                base = settings.PUBLIC_URL.rstrip("/")
                resp = await client.get(
                    f"{base}/api/trading/performance",
                    headers={"X-Agent-Key": agent_key},
                )
                resp.raise_for_status()
                perf = resp.json()
        except Exception as exc:
            logger.warning("PnL fetch failed: %s", exc)
            perf = {"error": "performance fetch failed"}
        return {
            "action": action,
            "target": "portfolio",
            "data": perf,
            "confidence": 1.0,
        }

    if action in ("place_trade", "buy", "sell"):
        side = data.get("side", body.get("side", "buy"))
        quantity = data.get("quantity", body.get("quantity", 0.001))
        chain = data.get("chain", body.get("chain", "solana"))
        order_type = data.get("order_type", body.get("order_type", "market"))
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                agent_key = request.headers.get("X-Agent-Key", "")
                base = settings.PUBLIC_URL.rstrip("/")
                resp = await client.post(
                    f"{base}/api/trading/orders",
                    json={
                        "symbol": target,
                        "side": side,
                        "quantity": quantity,
                        "chain": chain,
                        "order_type": order_type,
                    },
                    headers={"X-Agent-Key": agent_key},
                )
                resp.raise_for_status()
                order_result = resp.json()
        except Exception as exc:
            logger.warning("Trade execution failed: %s", exc)
            order_result = {"error": f"trade execution failed: {exc}"}
        return {
            "action": "place_trade",
            "target": target,
            "data": {
                "symbol": target,
                "side": side,
                "order_result": order_result,
                "endpoint": "/api/trading/orders",
            },
            "confidence": 0.9,
        }

    raise HTTPException(400, f"Unknown or unsupported action: {action}")


# ── Alerts CRUD ──────────────────────────────────────────────────────────

@router.get("/alerts")
async def list_alerts(agent: dict = Depends(get_agent)):
    """List all copilot alerts for the authenticated agent."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM copilot_alerts WHERE agent_id=? ORDER BY created_at DESC",
            (agent["id"],),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/alerts")
async def create_alert(request: Request, agent: dict = Depends(get_agent)):
    """Create a new copilot alert."""
    body = await _parse_body(request)
    symbol = body.get("symbol", "")
    condition_text = body.get("condition", body.get("condition_text", ""))
    target_price = body.get("target_price")
    direction = body.get("direction", "above")

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO copilot_alerts (agent_id, symbol, condition_text, target_price, direction)
               VALUES (?, ?, ?, ?, ?)""",
            (agent["id"], symbol, condition_text, target_price, direction),
        )
        await db.commit()
        alert_id = cur.lastrowid
    return {"id": alert_id, "status": "created"}


@router.delete("/alerts/{alert_id}")
async def delete_alert(alert_id: int, agent: dict = Depends(get_agent)):
    """Delete a copilot alert."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM copilot_alerts WHERE id=? AND agent_id=?",
            (alert_id, agent["id"]),
        )
        await db.commit()
    return {"status": "deleted"}
