"""Build factor-zoo "extras" panels from Vantage's own signal_pool.

signal_pool (backend/routers/intel.py) is a durable, capped (500-row) log of
every ingested alt-data signal: pump.fun momentum (ogun_degen), Vantage's own
multi-timeframe technical-vote predictor (vantage-predictor), and GDELT-style
news tone (worldmonitor_finance/worldmonitor_geo). Because the table is
capped, this is a *live/recent* window, not deep history — factors built on
these panels are snapshot/ranking scores for "right now," not multi-year
backtests. That's an honest constraint of the data, not a shortcut.

Produces wide DataFrames (index = bucket timestamp, columns = symbol) that
slot into the same panel dict a factor's compute(panel) receives, using the
extras_required contract every AlphaMeta already declares (backend/factors/
registry.py) but that no zoo factor had used before this.
"""
from __future__ import annotations

import re
import time
from typing import Sequence

import aiosqlite
import pandas as pd

from ..db import get_db

_DIRECTION_SIGN = {"BUY": 1.0, "SELL": -1.0, "NEUTRAL": 0.0, "LONG": 1.0, "SHORT": -1.0}
_PUMP_PCT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")


def _direction_sign(direction: str) -> float:
    return _DIRECTION_SIGN.get(str(direction or "").upper(), 0.0)


async def _fetch_signal_rows(source: str, lookback_seconds: int) -> list[dict]:
    cutoff = int(time.time()) - lookback_seconds
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT symbol, source, type, conviction, direction, detail, ts "
            "FROM signal_pool WHERE source=? AND ts >= ? ORDER BY ts ASC",
            (source, cutoff),
        )
        return [dict(r) for r in await cur.fetchall()]


def _bucket_index(ts: int, bucket_seconds: int) -> pd.Timestamp:
    return pd.Timestamp(ts - (ts % bucket_seconds), unit="s")


async def build_predictor_panel(
    symbols: Sequence[str], lookback_seconds: int = 3600, bucket_seconds: int = 900,
) -> dict[str, pd.DataFrame]:
    """predictor_conviction / predictor_direction from the vantage-predictor
    source — Vantage's own multi-timeframe technical-vote consensus, one row
    per symbol per scan. direction is signed (+1 BUY / -1 SELL / 0 NEUTRAL)
    and multiplied into conviction so a single signed column also exists."""
    rows = await _fetch_signal_rows("vantage-predictor", lookback_seconds)
    wanted = {s.upper() for s in symbols}

    conviction: dict[pd.Timestamp, dict[str, float]] = {}
    direction: dict[pd.Timestamp, dict[str, float]] = {}
    for r in rows:
        sym = (r["symbol"] or "").upper()
        if sym not in wanted:
            continue
        bucket = _bucket_index(r["ts"], bucket_seconds)
        sign = _direction_sign(r["direction"])
        conviction.setdefault(bucket, {})[sym] = float(r["conviction"] or 0.0)
        direction.setdefault(bucket, {})[sym] = sign

    return {
        "predictor_conviction": pd.DataFrame(conviction).T.sort_index() if conviction else pd.DataFrame(),
        "predictor_direction": pd.DataFrame(direction).T.sort_index() if direction else pd.DataFrame(),
    }


async def build_sentiment_panel(
    symbols: Sequence[str], lookback_seconds: int = 3600, bucket_seconds: int = 900,
) -> dict[str, pd.DataFrame]:
    """news_tone from worldmonitor_finance — GDELT-derived article tone,
    signed by direction (NEUTRAL rows contribute 0). Thin coverage is normal;
    this source is often 0 articles/tone 0.0 in quiet windows — that's a real
    zero, not missing data, and factors should treat it as "no news signal"
    rather than NaN."""
    rows = await _fetch_signal_rows("worldmonitor_finance", lookback_seconds)
    wanted = {s.upper() for s in symbols}

    tone: dict[pd.Timestamp, dict[str, float]] = {}
    for r in rows:
        sym = (r["symbol"] or "").upper()
        if sym not in wanted:
            continue
        bucket = _bucket_index(r["ts"], bucket_seconds)
        sign = _direction_sign(r["direction"])
        tone.setdefault(bucket, {})[sym] = sign * float(r["conviction"] or 0.0)

    return {"news_tone": pd.DataFrame(tone).T.sort_index() if tone else pd.DataFrame()}


async def build_degen_panel(
    lookback_seconds: int = 3600, bucket_seconds: int = 900,
) -> dict[str, pd.DataFrame]:
    """degen_buy_count / degen_pump_pct from ogun_degen — pump.fun momentum
    scans on micro-cap tokens that have no reliable OHLCV via market_sources
    (they're not on major exchanges), so this panel has no "close" column at
    all. Symbols are whatever ogun_degen names them (ticker text, not a
    resolvable mint) — this is a signal-native universe, not a price one.
    """
    rows = await _fetch_signal_rows("ogun_degen", lookback_seconds)

    buy_count: dict[pd.Timestamp, dict[str, float]] = {}
    pump_pct: dict[pd.Timestamp, dict[str, float]] = {}
    for r in rows:
        sym = (r["symbol"] or "").upper()
        if not sym:
            continue
        bucket = _bucket_index(r["ts"], bucket_seconds)
        buy_count.setdefault(bucket, {})[sym] = buy_count.get(bucket, {}).get(sym, 0.0) + 1.0
        m = _PUMP_PCT_RE.search(r["detail"] or "")
        if m:
            pct = float(m.group(1))
            existing = pump_pct.setdefault(bucket, {}).get(sym)
            pump_pct[bucket][sym] = max(existing, pct) if existing is not None else pct

    return {
        "degen_buy_count": pd.DataFrame(buy_count).T.sort_index() if buy_count else pd.DataFrame(),
        "degen_pump_pct": pd.DataFrame(pump_pct).T.sort_index() if pump_pct else pd.DataFrame(),
    }
