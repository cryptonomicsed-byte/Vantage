"""Prediction-market settlement — the ground truth a Crucible claim resolves against.

# What this is for

`market_resolved.wasm` (IfáScript's falsifier set) grants exactly one
observation, `market:resolution`, and refuses to guess when it is absent. This
module is what gathers it.

A resolving market is the strongest falsifier available to this ecosystem. A
backtest is a replayable simulation whose inputs the claimant controls; a market
settles at a stated time, adjudicated by a party the claimant does not control,
with other people's money on the other side.

# Venues

Limitless (Base) and Polymarket both expose read-only public endpoints that need
no credential, which is why they are here and a vendor-proxied feed is not.

# The normalization contract

The falsifier compares outcome strings byte-for-byte after ASCII-lowercasing.
That puts the burden here: every venue's settlement vocabulary must be mapped
onto one small set before it is handed over. We map to exactly:

    "yes" | "no" | "unresolved" | "void"

and *nothing else*. An unrecognized settlement string returns `None` rather than
being passed through, because the falsifier would then compare a claim against a
word it has no rule for and report `Fails` — turning our parsing gap into a
verdict against an agent.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

TIMEOUT = 8.0

# ── Endpoint registry ─────────────────────────────────────────────────────────
# SHAPES_VERIFIED_ON: never — the environment this was written in blocks
# outbound traffic to both venues (proxy answers 403 to CONNECT), so no parser
# below has seen a live response. Field names come from each venue's public
# documentation and from Moon Dev's Limitless client. Confirm against a real
# reply before trusting a verdict derived from this.
VENUES: dict[str, dict[str, str]] = {
    "limitless": {
        "market": "https://api.limitless.exchange/markets/{market_id}",
        # Settlement lives in `winningOutcomeIndex` (0 = yes, 1 = no) with
        # `status` naming the lifecycle stage.
    },
    "polymarket": {
        "market": "https://gamma-api.polymarket.com/markets?condition_ids={market_id}",
        # Settlement lives in `umaResolutionStatus` / `outcomePrices`.
    },
}

# Settlement states that mean "no answer", mapped onto the two the falsifier
# treats as Indeterminate. Anything not listed here is deliberately unmapped.
_UNRESOLVED = {"open", "active", "pending", "trading", "scheduled"}
_VOID = {"void", "cancelled", "canceled", "invalid", "refunded"}


async def _get_json(url: str) -> Optional[Any]:
    """GET → JSON, or None. Never raises: a venue outage is not our crash."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": "Vantage/1.0"}) as c:
            r = await c.get(url)
        if r.status_code != 200:
            logger.debug("prediction market GET %s -> HTTP %s", url, r.status_code)
            return None
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.debug("prediction market GET %s unavailable: %s", url, type(e).__name__)
        return None


def normalize_status(status: Any) -> Optional[str]:
    """Map a venue's lifecycle word onto our vocabulary, or `None` if unknown.

    `None` is the important return. It means "we do not know what this venue
    just said", and the caller must then gather nothing rather than pass an
    unmapped word to a falsifier that would read it as a mismatch.
    """
    if not isinstance(status, str):
        return None
    s = status.strip().lower()
    if s in _UNRESOLVED:
        return "unresolved"
    if s in _VOID:
        return "void"
    if s in ("resolved", "settled", "closed"):
        return "resolved"
    return None


def limitless_resolution(market: dict) -> Optional[str]:
    """Settlement of one Limitless market, in the falsifier's vocabulary.

    Returns `"yes"`, `"no"`, `"unresolved"`, `"void"`, or `None` when the payload
    is a shape we do not recognize.
    """
    if not isinstance(market, dict):
        return None

    stage = normalize_status(market.get("status"))
    if stage is None:
        return None
    if stage in ("unresolved", "void"):
        return stage

    idx = market.get("winningOutcomeIndex")
    # A market can be `resolved` while the winning index has not propagated yet.
    # That is still "no answer", not a coin flip.
    if idx == 0:
        return "yes"
    if idx == 1:
        return "no"
    return "unresolved"


def polymarket_resolution(market: dict) -> Optional[str]:
    """Settlement of one Polymarket market, in the falsifier's vocabulary.

    Polymarket settles to prices rather than an index: a resolved binary market
    carries outcome prices of exactly 1 and 0.
    """
    if not isinstance(market, dict):
        return None

    if market.get("umaResolutionStatus") in ("cancelled", "canceled"):
        return "void"
    if not market.get("closed", False):
        return "unresolved"

    prices = market.get("outcomePrices")
    if isinstance(prices, str):
        # Gamma returns this JSON-encoded inside a string more often than not.
        import json
        try:
            prices = json.loads(prices)
        except ValueError:
            return None
    if not isinstance(prices, list) or len(prices) < 2:
        return None

    try:
        yes = float(prices[0])
    except (TypeError, ValueError):
        return None

    # Only an unambiguous settlement counts. A market closed at 0.5, or anywhere
    # between, has not resolved to an outcome — reporting the nearer side would
    # invent a settlement the venue never made.
    if yes >= 0.99:
        return "yes"
    if yes <= 0.01:
        return "no"
    return "unresolved"


async def resolution(venue: str, market_id: str) -> Optional[str]:
    """Gather `market:resolution` for one market.

    `None` means we could not determine it — the caller must then omit the
    observation entirely rather than substituting a value. The falsifier answers
    `Indeterminate` for an ungathered observation, which is the correct outcome
    for our ignorance; handing it a guess would convert that ignorance into a
    verdict.
    """
    spec = VENUES.get(venue)
    if not spec:
        logger.debug("unknown prediction-market venue: %s", venue)
        return None

    data = await _get_json(spec["market"].format(market_id=market_id))
    if data is None:
        return None

    if venue == "limitless":
        return limitless_resolution(data)

    # Gamma answers a query with a list even for one condition id.
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return None
    return polymarket_resolution(data)


async def observation_for(venue: str, market_id: str) -> dict[str, str]:
    """Build the observation map to hand a Crucible probe.

    Empty when the resolution could not be gathered — which is what makes the
    falsifier abstain rather than guess.
    """
    r = await resolution(venue, market_id)
    return {"market:resolution": r} if r is not None else {}
