"""Build a factor-zoo panel from Vantage's own live market data.

Bridges backend.market_sources.ohlc() (per-symbol candle lists, Kraken/Binance/
CoinGecko, 60s-cached) into the wide-DataFrame panel shape every factor in
backend.factors.zoo expects: panel["close"] is a DataFrame indexed by date,
one column per symbol — same for open/high/low/volume.
"""
from __future__ import annotations

import asyncio
from typing import Sequence

import pandas as pd

from backend import market_sources


async def build_live_panel(
    symbols: Sequence[str], interval: str = "1d", limit: int = 200
) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV for each symbol concurrently and assemble a wide panel.

    Symbols with no data (delisted, rate-limited, bad ticker) are silently
    dropped rather than failing the whole panel — factors tolerate partial
    universes; a bad ticker choking out every other symbol would not be a
    reasonable failure mode for a live-data endpoint.
    """
    results = await asyncio.gather(
        *(market_sources.ohlc(sym, interval, limit) for sym in symbols),
        return_exceptions=True,
    )

    per_symbol: dict[str, list[dict]] = {}
    for sym, candles in zip(symbols, results):
        if isinstance(candles, Exception) or not candles:
            continue
        per_symbol[sym.upper()] = candles

    if not per_symbol:
        return {}

    cols = ("open", "high", "low", "close", "volume")
    panel: dict[str, pd.DataFrame] = {}
    for col in cols:
        series = {}
        for sym, candles in per_symbol.items():
            idx = pd.to_datetime([c["time"] for c in candles], unit="s")
            series[sym] = pd.Series([c[col] for c in candles], index=idx)
        df = pd.DataFrame(series).sort_index()
        # Candle timestamps can differ by a few seconds across exchanges even
        # at the same nominal interval; align to the day/hour grid so columns
        # actually overlap instead of each symbol living on its own NaN row.
        df.index = df.index.floor("h" if interval in ("1h", "4h") else "D")
        df = df.groupby(df.index).last()
        panel[col] = df

    return panel
