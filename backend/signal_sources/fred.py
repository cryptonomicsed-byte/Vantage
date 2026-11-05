"""FRED macro context — keyless.

The FRED *API* (api.stlouisfed.org) needs a free key: without one it answers
400 "Variable api_key is not set" (verified). The graph CSV endpoint does not:
`fredgraph.csv?id=CPIAUCSL` returns the whole series as two columns, no key, no
auth. Since this producer only ever needs the last two observations, it uses the
CSV path and avoids minting a credential just to read a public series.

What this emits: one signal per watched series, carrying the latest value, the
previous value, the delta and the observation date. `direction` is the read for
risk assets, and the rule is written out rather than hidden -- a hotter CPI or
a higher policy rate is read as bearish for risk, a rising unemployment rate the
same, while a series with no editorial read is `catalyst` (it moves markets on
release but does not say which way).
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from .base import as_float, signal

SOURCE = "fred"
SIGNAL_TYPE = "macro"
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

# id -> (label, risk read). "up" means a rising value is bearish for risk assets,
# "down" the opposite, "none" means no editorial read (release is a catalyst).
WATCHLIST: dict[str, tuple[str, str]] = {
    "CPIAUCSL": ("CPI (all urban consumers)", "up"),
    "CPILFESL": ("Core CPI", "up"),
    "UNRATE": ("Unemployment rate", "up"),
    "PAYEMS": ("Nonfarm payrolls", "down"),
    "FEDFUNDS": ("Effective fed funds rate", "up"),
    "DGS10": ("10-year Treasury yield", "up"),
    "GDPC1": ("Real GDP", "down"),
    "UMCSENT": ("Consumer sentiment", "down"),
}


def parse_csv(body: str, series_id: str, label: str, risk_read: str) -> Optional[dict[str, Any]]:
    """Last two usable observations -> one normalized signal, or None.

    FRED's CSV writes missing observations as "."; those rows are skipped rather
    than coerced to 0, which would invent a value for a month that has none.
    """
    rows: list[tuple[str, float]] = []
    for line in (body or "").splitlines()[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        value = as_float(parts[1])
        if value is None:
            continue
        rows.append((parts[0], value))
    if not rows:
        return None

    date, latest = rows[-1]
    previous = rows[-2][1] if len(rows) > 1 else None
    delta = None if previous is None else latest - previous

    if risk_read == "none" or delta is None or abs(delta) < 1e-9:
        direction = "catalyst" if risk_read == "none" else "neutral"
    elif (delta > 0) == (risk_read == "up"):
        direction = "bearish"
    else:
        direction = "bullish"

    # Official prints are the strongest evidence this system has; confidence
    # still drops when the delta is flat, because "no change" is not a signal.
    confidence = 0.9 if delta not in (None, 0.0) else 0.6

    return signal(
        source=SOURCE,
        market_id=series_id,
        signal_type=SIGNAL_TYPE,
        confidence=confidence,
        direction=direction,
        payload={
            "label": label,
            "value": latest,
            "previous": previous,
            "delta": delta,
            "observation_date": date,
            "series_url": f"https://fred.stlouisfed.org/series/{series_id}",
        },
    )


async def collect(series: Optional[list[str]] = None) -> list[dict[str, Any]]:
    """Fetch every watched series; a failing series drops out, not the batch."""
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for series_id in (series or list(WATCHLIST)):
            label, risk_read = WATCHLIST.get(series_id, (series_id, "none"))
            try:
                r = await client.get(CSV_URL.format(series=series_id))
                if r.status_code != 200:
                    continue
                item = parse_csv(r.text, series_id, label, risk_read)
                if item:
                    out.append(item)
            except Exception:
                continue
    return out
