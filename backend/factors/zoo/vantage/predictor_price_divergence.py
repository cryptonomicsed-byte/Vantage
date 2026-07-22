"""Predictor-Price Divergence.

Formula: rank(predictor_direction * predictor_conviction) - rank(delta(close, 5))

Vantage's own multi-timeframe technical-vote predictor (vantage-predictor in
signal_pool) publishes a signed conviction score per symbol independently of
price history. This factor asks: is the predictor's current call already
priced in, or is it leading? A large positive value means the predictor is
bullish while 5-bar price momentum hasn't caught up yet (a potential leading
signal); a large negative value means the predictor is bearish while price
is still rising (a potential reversal warning).

Native to Vantage: no equivalent exists in the alpha101/qlib158/gtja191/
academic zoos, because none of those have access to an internal directional
predictor to diverge against.
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import delta, rank
from backend.factors.signal_panel import align_to_price_index

ALPHA_ID = "vantage_predictor_price_divergence"

__alpha_meta__ = {
    'id': 'vantage_predictor_price_divergence',
    'nickname': 'Predictor-Price Divergence',
    'theme': ['sentiment', 'reversal'],
    'formula_latex': 'rank(predictor\\_direction \\times predictor\\_conviction) - rank(\\Delta(close, 5))',
    'columns_required': ['close'],
    'extras_required': ['predictor_conviction', 'predictor_direction'],
    'requires_sector': False,
    'universe': ['crypto'],
    'frequency': ['live'],
    'decay_horizon': 1,
    'min_warmup_bars': 6,
    'notes': (
        'signal_pool is capped at 500 rows (durable-pool retention limit), so '
        'the extras panels here are a live/recent window, not deep history — '
        'this factor is meant for current ranking, not long backtests.'
    ),
}


def compute(panel: dict) -> pd.DataFrame:
    close = panel["close"]
    predictor_signal = panel["predictor_direction"] * panel["predictor_conviction"]

    # Extras panels are sparse/live (event-driven, not one row per price bar)
    # and land at whatever time-of-day the signal arrived; align onto the
    # price panel's (daily, midnight-anchored) timeline by calendar day
    # rather than exact timestamp — see align_to_price_index's docstring.
    predictor_signal = align_to_price_index(predictor_signal, close.index)

    momentum = delta(close, 5)
    return rank(predictor_signal) - rank(momentum)
