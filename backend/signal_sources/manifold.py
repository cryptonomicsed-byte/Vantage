"""Manifold Markets community context — keyless public read.

Manifold is play-money, which is exactly why it is useful: it reacts to the same
question faster than a real-money venue, with no capital at risk to distort it.
It is a second opinion, never a settlement source.

This emits the top open binary markets by 24h volume. `direction` is the
community's lean on the question (bullish = the crowd says yes), and
`confidence` grows with distance from 0.5 -- a 50/50 crowd is not evidence and
should not read as a strong one.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from .base import as_float, signal

SOURCE = "manifold"
SIGNAL_TYPE = "community"
MARKETS_URL = "https://api.manifold.markets/v0/markets"

TOP_N = 25
# Below this the crowd is not saying anything a decision should weigh.
NEUTRAL_BAND = 0.55


def parse(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Open binary markets -> normalized signals, ranked by 24h volume."""
    out: list[dict[str, Any]] = []
    for m in markets or []:
        if not isinstance(m, dict):
            continue
        if m.get("outcomeType") != "BINARY" or m.get("isResolved"):
            continue
        prob = as_float(m.get("probability"))
        if prob is None:
            continue
        if prob >= NEUTRAL_BAND:
            direction = "bullish"
        elif prob <= (1.0 - NEUTRAL_BAND):
            direction = "bearish"
        else:
            direction = "neutral"
        # 0.5 -> 0.0 confidence, 1.0 -> 0.5 .. so the cap stays at 0.95 and a
        # coin flip never arrives looking like a call.
        confidence = min(0.95, abs(prob - 0.5))
        out.append(signal(
            source=SOURCE,
            market_id=str(m.get("id")),
            signal_type=SIGNAL_TYPE,
            confidence=confidence,
            direction=direction,
            payload={
                "question": m.get("question"),
                "probability": prob,
                "volume_24h": as_float(m.get("volume24hr")),
                "total_liquidity": as_float(m.get("totalLiquidity")),
                "close_time": m.get("closeTime"),
                "url": m.get("url"),
                "creator": m.get("creatorUsername"),
            },
        ))
    out.sort(key=lambda s: s["payload"].get("volume_24h") or 0, reverse=True)
    return out[:TOP_N]


async def collect(limit: int = 100) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(MARKETS_URL, params={"limit": limit})
        if r.status_code != 200:
            return []
        return parse(r.json())
    except Exception:
        return []
