"""Jev decision engine: takes a prediction_signal -> OpenRouter LLM -> returns verdict + confidence.

Updated: delegates to trading_decision.jev_decide() after enriching via signal_fusion.
Preserves the original public signature for all existing callers.
"""
import os
import json
from typing import Optional

OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE", os.environ.get("OPENROUTER_BASE_URL", "http://127.0.0.1:8300/v1"))
JEV_MODEL = os.environ.get("JEV_MODEL", os.environ.get("OPENROUTER_MODEL", "free-reasoner"))
CONFIDENCE_THRESHOLD = float(os.environ.get("JEV_CONFIDENCE_THRESHOLD", "0.65"))


async def jev_decide(signal: dict) -> dict:
    """Pure function: signal dict -> {verdict, confidence, reasoning}. No side effects."""
    try:
        from .signal_fusion import enrich_from_loom, enrich_from_strategy_lab
        from .trading_decision import jev_decide as _trade_decide
    except ImportError:  # backend/ itself on sys.path
        from signal_fusion import enrich_from_loom, enrich_from_strategy_lab
        from trading_decision import jev_decide as _trade_decide

    signal = await enrich_from_loom(signal)
    signal = await enrich_from_strategy_lab(signal)
    return await _trade_decide(signal)
