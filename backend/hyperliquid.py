"""Hyperliquid feeds, read directly from Hyperliquid.

# Why this exists rather than a vendor client

The obvious shortcut is Moon Dev's data layer, which packages exactly these
derivations behind `api.moondev.com` and a `MOONDEV_API_KEY`. We do not take it.
Hyperliquid's own API is public and free — that repo's own client carries a
fallback straight to it — so what the key actually buys is caching, rate-limit
relief and the derived analytics below. For an ecosystem whose premise is that
agents hold their own keys and answer to no central operator, routing the signal
layer through one company's credential is a contradiction, not a convenience.

So we take the *inventory* — which derivations are worth computing — and compute
them here.

# The endpoint registry

Every upstream shape this module depends on is named in `ENDPOINTS` with the
date it was last confirmed. This is the `tv_selectors.py` discipline: when a
venue changes its payload, the fix is one table rather than a hunt through call
sites, and a shape we have *proven* dead stays listed with a note so nobody
restores it from memory.

Hyperliquid's `info` endpoint is a POST-with-a-type-body API rather than REST,
so every read here posts a small JSON document and the request type is the thing
that varies.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

INFO_URL = "https://api.hyperliquid.xyz/info"
TIMEOUT = 8.0

# ── Endpoint registry ─────────────────────────────────────────────────────────
# SHAPES_VERIFIED_ON: never — see the note below.
#
# HONESTY NOTE: these request shapes are transcribed from Hyperliquid's public
# API documentation and from Moon Dev's client, NOT confirmed against a live
# response. The environment this was written in blocks outbound traffic to
# api.hyperliquid.xyz (the proxy answers 403 to CONNECT), so no call here has
# ever received a real reply. Treat every parser below as unverified until
# someone runs `python -m backend.hyperliquid --probe` against the live API and
# updates this line with a date.
ENDPOINTS: dict[str, dict[str, Any]] = {
    # All mids: {"BTC": "64123.0", ...}
    "all_mids": {"type": "allMids"},
    # Per-coin metadata + market contexts: [meta, [ctx, ...]] where each ctx
    # carries funding, openInterest and markPx.
    "meta_and_ctxs": {"type": "metaAndAssetCtxs"},
    # One address's positions and margin summary.
    "clearinghouse": {"type": "clearinghouseState"},  # + {"user": address}
}

# Gravestones — shapes proven wrong. Kept so they are not reintroduced.
#
# (none yet; add entries as `"name": "why it died, and when"`)
DEAD_SHAPES: dict[str, str] = {}


async def _info(body: dict) -> Optional[Any]:
    """POST one `info` request. `None` on any failure — never raises.

    A signal source that throws takes the whole aggregator scan down with it,
    and a missing signal is always better than a dead daemon.
    """
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(INFO_URL, json=body, headers={"Content-Type": "application/json"})
        if r.status_code != 200:
            logger.debug("hyperliquid %s -> HTTP %s", body.get("type"), r.status_code)
            return None
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.debug("hyperliquid %s unavailable: %s", body.get("type"), type(e).__name__)
        return None


def _f(v: Any) -> Optional[float]:
    """Hyperliquid returns numbers as strings. Parse, or None."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── Derivations ───────────────────────────────────────────────────────────────

async def funding_and_oi() -> dict[str, dict]:
    """Per-coin funding rate and open interest.

    Funding is the cheapest crowding signal there is: persistently positive
    funding means longs are paying to stay long, which is a positioning fact
    rather than an opinion about price.
    """
    data = await _info(ENDPOINTS["meta_and_ctxs"])
    if not isinstance(data, list) or len(data) < 2:
        return {}
    meta, ctxs = data[0], data[1]
    universe = (meta or {}).get("universe") or []
    if not isinstance(ctxs, list):
        return {}

    out: dict[str, dict] = {}
    for coin, ctx in zip(universe, ctxs):
        name = (coin or {}).get("name")
        if not name or not isinstance(ctx, dict):
            continue
        out[name] = {
            "funding": _f(ctx.get("funding")),
            "open_interest": _f(ctx.get("openInterest")),
            "mark": _f(ctx.get("markPx")),
        }
    return out


def liquidation_distance(position: dict, mark: float) -> Optional[float]:
    """How close a position is to liquidation, as a fraction of mark price.

    `0.005` means half a percent away. This is the number the whole
    liquidation-cascade thesis rests on: price is drawn toward large resting
    liquidation levels because that is where forced flow lives.

    `None` when the position has no liquidation price — an unlevered position
    cannot be liquidated, and reporting `0.0` for it would rank it as maximally
    urgent, which is exactly backwards.
    """
    liq = _f(position.get("liquidationPx"))
    if liq is None or not mark:
        return None
    return abs(mark - liq) / mark


async def positions_near_liquidation(
    addresses: list[str], threshold: float = 0.05, min_usd: float = 100_000.0
) -> list[dict]:
    """Positions within `threshold` of liquidation and worth at least `min_usd`.

    Both bounds matter. Distance alone surfaces dust that moves nothing; size
    alone surfaces whales in no danger. The signal is a large position close to
    forced closure.
    """
    mids = await _info(ENDPOINTS["all_mids"]) or {}
    found: list[dict] = []

    for addr in addresses:
        state = await _info({**ENDPOINTS["clearinghouse"], "user": addr})
        if not isinstance(state, dict):
            continue
        for ap in state.get("assetPositions") or []:
            pos = (ap or {}).get("position") or {}
            coin = pos.get("coin")
            mark = _f(mids.get(coin)) if coin else None
            if not coin or mark is None:
                continue
            value = _f(pos.get("positionValue")) or 0.0
            if value < min_usd:
                continue
            dist = liquidation_distance(pos, mark)
            if dist is None or dist > threshold:
                continue
            szi = _f(pos.get("szi")) or 0.0
            found.append({
                "address": addr,
                "coin": coin,
                "side": "long" if szi > 0 else "short",
                "value_usd": value,
                "distance": dist,
                "mark": mark,
            })

    # Closest to liquidation first: that is the one price is nearest to.
    found.sort(key=lambda p: p["distance"])
    return found


def cascade_direction(near: list[dict]) -> Optional[str]:
    """Which way a liquidation cascade would push, or None if it is ambiguous.

    The closest large position names the fuel pile price is nearest to: longs
    liquidating sell into the market and drag price down, shorts liquidating buy
    it back and push price up.

    Returns `None` when the nearest long and nearest short are within a hair of
    each other, because then the pile is on both sides and the direction is not
    a fact — it is a guess dressed as one.
    """
    longs = [p for p in near if p["side"] == "long"]
    shorts = [p for p in near if p["side"] == "short"]
    if not longs and not shorts:
        return None
    if not shorts:
        return "down"
    if not longs:
        return "up"
    l, s = longs[0]["distance"], shorts[0]["distance"]
    if abs(l - s) < 0.001:
        return None
    return "down" if l < s else "up"


def funding_signal(funding: Optional[float], threshold: float = 0.0005) -> Optional[dict]:
    """Turn a funding rate into a crowding call, or nothing.

    Extreme positive funding means longs are crowded and paying for it — a
    squeeze risk against them. Returns `None` inside the band, because "funding
    is normal" is not a signal and emitting it as one would drown the pool.
    """
    if funding is None or abs(funding) < threshold:
        return None
    crowded = "long" if funding > 0 else "short"
    return {
        "direction": "down" if funding > 0 else "up",
        "crowded_side": crowded,
        "funding": funding,
        # Saturating at 4x the threshold: beyond that the rate is extreme but
        # not four times as informative, and an unbounded score would let one
        # outlier dominate the aggregate.
        "conviction": min(1.0, abs(funding) / (threshold * 4)),
    }
