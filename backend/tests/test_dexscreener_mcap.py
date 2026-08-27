"""Regression tests for the DexScreener market-cap pair selection.

Covers the two real bugs fixed in backend/routers/alpha.py:

  1. Naive highest-raw-liquidity pair selection. A stale/abandoned pair keeps
     a frozen (wrong) marketCap but can still report a larger historical
     liquidity than the live pair that actually trades. The old
     ``max(pairs, key=liquidity)`` picked the dead pair and surfaced the
     frozen number (e.g. $8.2M vs a real $350M, or BONK's $1.16T frozen
     Meteora pair vs its real ~$264M).

  2. Truthy/falsy ``marketCap or fdv`` fallthrough. A literal ``marketCap: 0``
     (fresh pre-bonding pump.fun mints) is falsy in Python, so the old code
     silently returned fdv instead of treating it as "no data yet".

These are pure-function tests — no network, deterministic.
"""
from backend.routers.alpha import (
    _pair_recent_volume,
    _select_best_pair,
    _market_cap_from_pair,
)


def _pair(mcap=None, fdv=None, liq=0.0, h24=0.0, h6=0.0, h1=0.0, m5=0.0,
          dex="raydium", addr=""):
    return {
        "pairAddress": addr or dex,
        "dexId": dex,
        "marketCap": mcap,
        "fdv": fdv,
        "liquidity": {"usd": liq},
        "volume": {"h24": h24, "h6": h6, "h1": h1, "m5": m5},
    }


# ── Bug 1: stale high-liquidity pair must lose to the live trading pair ──────

def test_selects_live_pair_over_stale_high_liquidity():
    """A dead pair with frozen mcap but huge stale liquidity must not win."""
    # Stale pre-pump pair: frozen tiny mcap, huge leftover liquidity, ~0 volume.
    stale = _pair(mcap=8_200_000, liq=50_000_000, h24=0.0, dex="raydium-stale")
    # Live pair that actually trades: correct big mcap, smaller liquidity.
    live = _pair(mcap=350_000_000, liq=1_000_000, h24=5_000_000, dex="raydium")
    best = _select_best_pair([stale, live])
    assert best is live
    assert _market_cap_from_pair(best) == 350_000_000


def test_selects_live_pair_even_when_stale_liquidity_dominates():
    """The exact BONK failure: $1.16T frozen mcap on a higher-liquidity stale
    pair must lose to the ~$264M pair that actually has recent volume."""
    stale = _pair(mcap=1_162_292_874_599, liq=1_435_316.68, h24=131_807, dex="meteora-stale")
    live = _pair(mcap=264_487_093, liq=7_230.18, h24=216_426, dex="meteora")
    best = _select_best_pair([stale, live])
    assert best is live
    assert _market_cap_from_pair(best) == 264_487_093


def test_liquidity_tiebreak_when_volume_equal():
    """When two pairs are equally active, liquidity still breaks the tie."""
    a = _pair(mcap=100, liq=10_000, h24=500, dex="a")
    b = _pair(mcap=200, liq=50_000, h24=500, dex="b")
    assert _select_best_pair([a, b]) is b


def test_recent_volume_uses_any_window():
    """A pair with only h1 volume (token younger than 24h) is still 'live'."""
    stale = _pair(mcap=1_000, liq=100_000, h24=0, h6=0, h1=0, m5=0)
    young = _pair(mcap=500_000, liq=5_000, h24=0, h6=0, h1=12_345, m5=0)
    assert _pair_recent_volume(young) == 12_345
    assert _select_best_pair([stale, young]) is young


def test_empty_pairs_returns_none():
    assert _select_best_pair([]) is None


# ── Bug 2: falsy-zero marketCap must NOT fall through to fdv ─────────────────

def test_market_cap_zero_returns_none_not_fdv():
    """marketCap:0 is 'no data yet' — must return None, not fdv."""
    assert _market_cap_from_pair(_pair(mcap=0, fdv=850_000_000)) is None


def test_market_cap_zero_float_returns_none():
    """0.0 (float) is equally falsy — same fix must hold."""
    assert _market_cap_from_pair(_pair(mcap=0.0, fdv=99_000_000)) is None


def test_market_cap_absent_falls_back_to_fdv():
    """When marketCap is genuinely None, fdv is the only cap signal — use it."""
    assert _market_cap_from_pair(_pair(mcap=None, fdv=123_456_789)) == 123_456_789


def test_market_cap_present_wins():
    assert _market_cap_from_pair(_pair(mcap=350_000_000, fdv=999_999_999)) == 350_000_000


def test_market_cap_and_fdv_both_absent():
    assert _market_cap_from_pair(_pair(mcap=None, fdv=None)) is None


# ── Combined: the full reported symptom ──────────────────────────────────────

def test_end_to_end_reported_symptom():
    """8.2M-frozen stale pair + 350M live pair → must report 350M, not 8.2M."""
    stale = _pair(mcap=8_200_000, fdv=8_200_000, liq=30_000_000, h24=0.0, dex="pumpfun")
    live = _pair(mcap=350_000_000, fdv=350_000_000, liq=2_000_000, h24=9_000_000, dex="raydium")
    best = _select_best_pair([stale, live])
    assert _market_cap_from_pair(best) == 350_000_000


# ── Sibling bug: intel.py's pair selection for priceUsd ──────────────────────

def test_intel_best_pair_prefers_live_over_stale():
    """The same naive-liquidity bug existed in intel.py's _dexscreener_token_info
    (picked the pair for priceUsd). Its fix must also prefer the live pair."""
    from backend.routers.intel import _dexscreener_best_pair
    stale = _pair(liq=50_000_000, h24=0.0, dex="stale")
    live = _pair(liq=500_000, h24=3_000_000, dex="live")
    assert _dexscreener_best_pair([stale, live]) is live
    assert _dexscreener_best_pair([]) is None
