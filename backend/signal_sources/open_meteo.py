"""Open-Meteo weather context — keyless, no account.

Weather contracts ("highest temperature in NYC on the 14th", "will it rain in
London") settle against observations, so the forecast is the only leading
evidence there is. This emits one signal per watched city: the 7-day max/min
temperature and precipitation probability, plus an extreme flag that the
decision layer reads as a catalyst.

Open-Meteo asks for attribution and a non-commercial free tier; that is the tier
this runs on. No key, no rate-limit ceiling worth engineering around at six
cities per poll.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from .base import as_float, signal

SOURCE = "open-meteo"
SIGNAL_TYPE = "weather"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# city -> (lat, lon). Weather markets are overwhelmingly US, but the ones that
# are not (London, Tokyo) are exactly the ones with no other reader here.
CITIES: dict[str, tuple[float, float]] = {
    "new-york": (40.7128, -74.0060),
    "chicago": (41.8781, -87.6298),
    "los-angeles": (34.0522, -118.2437),
    "miami": (25.7617, -80.1918),
    "london": (51.5074, -0.1278),
    "tokyo": (35.6762, 139.6503),
}

# A forecast this hot, or this likely wet, is a contract mover rather than
# background. Thresholds are deliberately blunt and documented so they can be
# argued with instead of guessed at.
HEAT_C = 35.0
WET_PCT = 80.0


def parse(payload: dict[str, Any], city: str) -> Optional[dict[str, Any]]:
    """Open-Meteo daily block -> one normalized signal, or None if unusable."""
    daily = (payload or {}).get("daily") or {}
    dates = daily.get("time") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    precip = daily.get("precipitation_probability_max") or []
    if not dates or not highs:
        return None

    days: list[dict[str, Any]] = []
    for i, day in enumerate(dates):
        days.append({
            "date": day,
            "max_c": as_float(highs[i]) if i < len(highs) else None,
            "min_c": as_float(lows[i]) if i < len(lows) else None,
            "precip_prob": as_float(precip[i]) if i < len(precip) else None,
        })

    next3 = days[:3]
    peak = max((d["max_c"] for d in next3 if d["max_c"] is not None), default=None)
    wettest = max((d["precip_prob"] for d in next3 if d["precip_prob"] is not None), default=None)

    extreme = (peak is not None and peak >= HEAT_C) or (wettest is not None and wettest >= WET_PCT)

    return signal(
        source=SOURCE,
        market_id=city,
        signal_type=SIGNAL_TYPE,
        confidence=0.75,
        direction="catalyst" if extreme else "neutral",
        payload={
            "city": city,
            "lat": payload.get("latitude"),
            "lon": payload.get("longitude"),
            "peak_c_next_72h": peak,
            "max_precip_prob_next_72h": wettest,
            "thresholds": {"heat_c": HEAT_C, "wet_pct": WET_PCT},
            "days": days,
        },
    )


async def collect(cities: Optional[list[str]] = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=15) as client:
        for city in (cities or list(CITIES)):
            lat, lon = CITIES[city]
            try:
                r = await client.get(FORECAST_URL, params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "timezone": "UTC",
                    "forecast_days": 7,
                })
                if r.status_code != 200:
                    continue
                item = parse(r.json(), city)
                if item:
                    out.append(item)
            except Exception:
                continue
    return out
