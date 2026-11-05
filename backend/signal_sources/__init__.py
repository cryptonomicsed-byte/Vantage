"""Context signal producers: non-venue evidence for prediction-market decisions.

Each module exposes `collect() -> list[normalized signal]` and a pure `parse()`
so the parsing can be tested without network access. The venue feeds (Kalshi,
Polymarket) remain the settlement source of record; nothing here settles
anything.

Verified reachable and keyless on 2026-11-05:
  fred        via fredgraph.csv (the JSON API needs a key; the CSV does not)
  open-meteo  public forecast API
  manifold    public v0 markets API

Deliberately NOT included yet, with the reason measured at the same time:
  GDELT       429 "limit requests to one every 5 seconds" -- needs pacing, not
              a key, and a naive poll burns the budget
  Metaculus   403, API now requires an account token
  NewsCatcher 401, Perigon 404 without key, Event Registry 401 -- all keyed
  Reddit      403 from this host without OAuth
"""
from __future__ import annotations

from . import fred, manifold, open_meteo

SOURCES = {
    "fred": fred,
    "open-meteo": open_meteo,
    "manifold": manifold,
}

# Per-source poll cadence, in seconds. A macro series changes monthly; a
# forecast changes hourly; a play-money crowd moves in minutes.
CADENCE = {
    "fred": 6 * 3600,
    "open-meteo": 30 * 60,
    "manifold": 15 * 60,
}

__all__ = ["SOURCES", "CADENCE", "fred", "open_meteo", "manifold"]
