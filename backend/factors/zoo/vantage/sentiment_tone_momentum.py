"""Sentiment-Tone Momentum.

Formula: delta(close, 5) * (1 + zscore(news_tone).fillna(0))

Price momentum, amplified or dampened by GDELT-derived news tone
(worldmonitor_finance in signal_pool). A symbol with rising price AND
positive news tone gets amplified; rising price with negative tone gets
dampened (momentum without narrative support is treated as weaker) — the
same intuition as event-driven factor research, just fed from Vantage's own
ingested tone rather than a bundled news dataset.

Thin coverage (0 articles / tone 0.0) is common and is a real zero, not
missing data — zscore(0) contributes no amplification, which is the
intended behavior for a quiet news window.

Native to Vantage: none of the bundled zoos (alpha101/qlib158/gtja191/
academic) have a news-tone input at all.
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import delta, zscore
from backend.factors.signal_panel import align_to_price_index

ALPHA_ID = "vantage_sentiment_tone_momentum"

__alpha_meta__ = {
    'id': 'vantage_sentiment_tone_momentum',
    'nickname': 'Sentiment-Tone Momentum',
    'theme': ['sentiment', 'momentum'],
    'formula_latex': '\\Delta(close, 5) \\times (1 + zscore(news\\_tone))',
    'columns_required': ['close'],
    'extras_required': ['news_tone'],
    'requires_sector': False,
    'universe': ['crypto'],
    'frequency': ['live'],
    'decay_horizon': 5,
    'min_warmup_bars': 6,
    'notes': (
        'news_tone from worldmonitor_finance is event-driven and sparse — '
        'aligned onto the (daily) price timeline by calendar day before use, '
        'see signal_panel.align_to_price_index.'
    ),
}


def compute(panel: dict) -> pd.DataFrame:
    close = panel["close"]
    tone = align_to_price_index(panel["news_tone"], close.index).fillna(0.0)

    momentum = delta(close, 5)
    # A quiet news window (every symbol at 0 tone, or otherwise identical)
    # has no cross-sectional variance -> zscore is NaN by base.py's own
    # "never silent zero" contract (same failure mode as degen_pump_density's
    # buy_count). fillna(0) so "no differentiating tone signal" contributes
    # no amplification instead of nuking a momentum value that's perfectly
    # valid on its own.
    return momentum * (1.0 + zscore(tone).fillna(0.0))
