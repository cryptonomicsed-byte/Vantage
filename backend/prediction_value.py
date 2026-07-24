"""Prediction Value scoring -- ported from HKUDS/FutureShow's log-return
method for scoring a binary-market call against market consensus.

A correct call scores -log(p) where p is the market's own probability for
the outcome called; an incorrect call scores +log(p) (a penalty, since log
of a probability < 1 is negative, so the penalty for a wrong high-confidence
call is small in magnitude while a wrong low-confidence... the sign
convention below matches FutureShow's exactly). The result: correctly
calling a longshot (low p) scores far higher than correctly calling the
favorite (high p), and the reverse for wrong calls -- this is what makes it
a measure of alpha over the market's own consensus, not just win/loss.
"""
import math
from typing import Optional


def compute_prediction_value(call: str, market_prob: float, is_correct: bool) -> Optional[float]:
    """call: 'YES' or 'NO' (case-insensitive). market_prob: the market's own
    probability for THIS call (e.g. yes_prob if call=='YES'), in [0,1].
    Returns None for an unscoreable input (e.g. an abstain)."""
    if call is None or market_prob is None:
        return None
    call_upper = call.strip().upper()
    if call_upper == "ABSTAIN":
        return None

    # Clamp to avoid log(0)/log(1) blowing up -- same bounds FutureShow uses,
    # giving a theoretical value range of [-6.9, +6.9].
    p = max(0.001, min(0.999, float(market_prob)))

    return -math.log(p) if is_correct else math.log(p)


def market_prob_for_call(call: str, yes_prob: Optional[float], no_prob: Optional[float]) -> Optional[float]:
    """Resolve the market probability relevant to a specific YES/NO call,
    given the market's yes/no probabilities (one may be derived from the
    other if only one is known)."""
    call_upper = (call or "").strip().upper()
    if call_upper == "YES":
        if yes_prob is not None:
            return yes_prob
        if no_prob is not None:
            return 1 - no_prob
    elif call_upper == "NO":
        if no_prob is not None:
            return no_prob
        if yes_prob is not None:
            return 1 - yes_prob
    return None
