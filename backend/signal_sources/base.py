"""Normalized context signals from non-venue sources.

These are not markets. They are the evidence that should move a market decision:
a macro print, a weather forecast, a community probability on the same question.
The venue (Kalshi / Polymarket) stays the settlement source of record; these
feeds only ever inform.

Wire shape (kept identical to the integration sketch so the producers are
interchangeable and the store can be dumb):

    {
      "source":      "fred" | "open-meteo" | "manifold",
      "market_id":   stable id inside that source (series id, grid point, contract),
      "signal_type": "macro" | "weather" | "community",
      "timestamp":   ISO-8601 UTC,
      "confidence":  0.0-1.0,
      "direction":   "bullish" | "bearish" | "neutral" | "catalyst",
      "payload":     { ... raw fields worth keeping ... }
    }

Every parser is a pure function over a fetched body so the tests run offline.
Fetching is separate and fail-open: a source that is down returns [].
"""
from __future__ import annotations

import time
from typing import Any, Optional

DIRECTIONS = ("bullish", "bearish", "neutral", "catalyst")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def signal(
    *,
    source: str,
    market_id: str,
    signal_type: str,
    confidence: float,
    direction: str,
    payload: dict[str, Any],
    timestamp: Optional[str] = None,
) -> dict[str, Any]:
    """Build one normalized record, clamped and validated.

    Direction is restricted to the four values the decision layer understands;
    an unknown word would silently become a fifth category that nothing reads.
    """
    if direction not in DIRECTIONS:
        raise ValueError(f"unknown direction {direction!r}")
    return {
        "source": source,
        "market_id": str(market_id),
        "signal_type": signal_type,
        "timestamp": timestamp or now_iso(),
        "confidence": max(0.0, min(1.0, float(confidence))),
        "direction": direction,
        "payload": payload,
    }


def as_float(value: Any) -> Optional[float]:
    try:
        if value in (None, "", ".", "NA", "N/A"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
