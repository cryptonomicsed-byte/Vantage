"""Degen Pump Density.

Formula: zscore(degen_buy_count).fillna(0) + zscore(degen_pump_pct).fillna(0)

A signal-native factor with no price input at all: ogun_degen scans
pump.fun micro-cap tokens that have no reliable OHLCV via
backend.market_sources (they aren't listed on major exchanges), so there is
no "close" column to compute momentum from — the signal itself, not price
history, is the only available input.

Ranks symbols by how many independent pump-momentum hits they got in the
lookback window, combined with the magnitude of the largest observed pump
percentage — attention density, not price action. This is the first zoo
factor with columns_required=[] (no OHLCV dependency whatsoever); the
registry's shape-validation against "close" is skipped automatically when
"close" isn't in the panel (backend/factors/registry.py _validate_output).

Native to Vantage: this universe (pump.fun micro-caps surfaced only through
Vantage's own degen-alpha wallet-hunting pipeline, backend/alpha_engine.py
and daemons/ogun_degen equivalents) doesn't exist in any bundled zoo.
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import zscore

ALPHA_ID = "vantage_degen_pump_density"

__alpha_meta__ = {
    'id': 'vantage_degen_pump_density',
    'nickname': 'Degen Pump Density',
    'theme': ['momentum', 'volume'],
    'formula_latex': 'zscore(degen\\_buy\\_count) + zscore(degen\\_pump\\_pct)',
    'columns_required': [],
    'extras_required': ['degen_buy_count', 'degen_pump_pct'],
    'requires_sector': False,
    'universe': ['crypto'],
    'frequency': ['live'],
    'decay_horizon': 1,
    'min_warmup_bars': 1,
    'notes': (
        'No OHLCV dependency — signal_pool (ogun_degen source) is the only '
        'input. Symbols are pump.fun ticker text, not resolvable mints; '
        'trading against this factor requires resolving a real mint first '
        '(see daemons/ares_pumpfun_trader.py), this factor only ranks '
        'attention density. Note: the ogun_degen ingester currently posts '
        'one row per token per scan cycle, so raw buy_count often carries '
        'no cross-sectional variance (every tracked token gets the same '
        'count per bucket) — zscore of a constant row is NaN by design '
        '(base.py: "never silent zero"). Each zscore component is fillna(0) '
        'before summing so a degenerate component contributes no signal '
        'instead of poisoning a genuinely informative one (pump_pct nearly '
        'always varies across symbols even when buy_count does not).'
    ),
}


def compute(panel: dict) -> pd.DataFrame:
    buy_count = panel["degen_buy_count"].fillna(0.0)
    pump_pct = panel["degen_pump_pct"].fillna(0.0)
    return zscore(buy_count).fillna(0.0) + zscore(pump_pct).fillna(0.0)
