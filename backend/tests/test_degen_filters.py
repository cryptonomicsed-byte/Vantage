"""Tests for degen_filters.py -- the major/stablecoin exclusion + dust
floor fix for the real bugs found live: Aggregate Winner scoring USDC #1,
CoinGecko's leader slot showing BTC, Vantage Conviction showing USDC
under a corrupted "penny" symbol, pump.fun showing ~$10-mcap dust.
"""
import json
from datetime import datetime, timedelta, timezone

from backend.degen_filters import (
    is_major_or_stable,
    passes_dust_floor,
    passes_all_filters,
    pumpfun_token_is_alive,
    MAJOR_ADDRESSES,
    MIN_MARKET_CAP_USD,
    PUMPFUN_MIN_MARKET_CAP_USD,
    PUMPFUN_MAX_MARKET_CAP_USD,
    PUMPFUN_MIN_DISTINCT_PARTICIPANTS,
    PUMPFUN_MIN_TOTAL_TRADES,
    PUMPFUN_MAX_STALENESS_MINUTES,
    PUMPFUN_MIN_CURVE_SOL_LIQUIDITY,
)


def _fresh_ts(minutes_ago: float = 0) -> str:
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _real_alive_token(**overrides):
    """A token that clears every real pumpfun_token_is_alive criterion --
    the baseline for the regression tests below, overridden one field at
    a time to prove each check independently gates on its own real signal."""
    base = dict(
        market_cap_usd=(PUMPFUN_MIN_MARKET_CAP_USD + PUMPFUN_MAX_MARKET_CAP_USD) / 2,
        buy_count=3, sell_count=2,
        unique_buyers_json=json.dumps(["WalletA", "WalletB"]),
        unique_sellers_json=json.dumps(["WalletC"]),
        last_trade_at=_fresh_ts(5),
        v_sol_in_curve=30.0,
    )
    base.update(overrides)
    return base


def test_usdc_excluded_by_address_regardless_of_corrupted_symbol():
    # The real bug: token_wallet_roles had USDC's real address labeled
    # "penny" (and dozens of other unrelated symbols). Address-based
    # exclusion must catch it even when the symbol data is garbage.
    assert is_major_or_stable("penny", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v") is True
    assert is_major_or_stable("STEVE", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v") is True


def test_usdt_and_sol_excluded_by_address():
    assert is_major_or_stable(None, "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB") is True
    assert is_major_or_stable(None, "So11111111111111111111111111111111111111112") is True


def test_btc_excluded_by_symbol_when_no_solana_address():
    # CoinGecko's real bug: BTC has no Solana mint at all, so only the
    # symbol layer can catch it.
    assert is_major_or_stable("BTC", None) is True
    assert is_major_or_stable("btc", None) is True  # case-insensitive


def test_degen_token_not_excluded():
    assert is_major_or_stable("SOMERANDOMDEGEN", "RandomMint1111111111111111111111111111") is False


def test_dollar_prefixed_symbol_normalized():
    assert is_major_or_stable("$USDC", None) is True


def test_dust_floor_rejects_ten_dollar_mcap():
    # The real bug: pump.fun's slot showed a ~$10 market cap token.
    assert passes_dust_floor(10.0) is False


def test_dust_floor_accepts_above_threshold():
    assert passes_dust_floor(MIN_MARKET_CAP_USD + 1) is True


def test_dust_floor_boundary_is_inclusive():
    assert passes_dust_floor(MIN_MARKET_CAP_USD) is True


def test_dust_floor_falls_back_to_liquidity_when_mcap_unknown():
    assert passes_dust_floor(None, liquidity_usd=5000.0) is True
    assert passes_dust_floor(None, liquidity_usd=100.0) is False


def test_dust_floor_rejects_when_no_data_at_all():
    assert passes_dust_floor(None, None) is False


def test_passes_all_filters_rejects_major_even_with_good_mcap():
    assert passes_all_filters("USDC", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 50_000_000_000.0) is False


def test_passes_all_filters_rejects_dust_even_if_not_major():
    assert passes_all_filters("DEGEN", "RandomMint1111111111111111111111111111", 10.0) is False


def test_passes_all_filters_accepts_real_candidate():
    assert passes_all_filters("DEGEN", "RandomMint1111111111111111111111111111", 50_000.0) is True


def test_pumpfun_min_floor_uses_band_lower_bound():
    """PUMPFUN_MIN_MARKET_CAP_USD is now the lower bound of the owner-
    specified $14k-$32k lifecycle band (superseding the earlier flat $500
    floor -- see degen_filters.py's section docstring for why). Generic
    passes_dust_floor still accepts it as a plain min_market_cap override,
    real for any caller that only cares about the lower edge."""
    from backend.degen_filters import PUMPFUN_MIN_MARKET_CAP_USD
    assert PUMPFUN_MIN_MARKET_CAP_USD == 14_000.0
    assert passes_dust_floor(20_000.0, min_market_cap=PUMPFUN_MIN_MARKET_CAP_USD) is True
    assert passes_dust_floor(3_500.0, min_market_cap=PUMPFUN_MIN_MARKET_CAP_USD) is False


def test_pumpfun_floor_still_rejects_real_dust():
    from backend.degen_filters import PUMPFUN_MIN_MARKET_CAP_USD
    # The owner's actual reported bug: a ~$10 mcap token.
    assert passes_dust_floor(10.0, min_market_cap=PUMPFUN_MIN_MARKET_CAP_USD) is False


# ── pumpfun_token_is_alive: owner-specified band + real activity screen ──

def test_alive_token_passes_all_criteria():
    assert pumpfun_token_is_alive(**_real_alive_token()) is True


def test_below_band_lower_bound_rejected():
    assert pumpfun_token_is_alive(**_real_alive_token(market_cap_usd=PUMPFUN_MIN_MARKET_CAP_USD - 1)) is False


def test_above_band_upper_bound_rejected():
    assert pumpfun_token_is_alive(**_real_alive_token(market_cap_usd=PUMPFUN_MAX_MARKET_CAP_USD + 1)) is False


def test_band_boundaries_are_inclusive():
    assert pumpfun_token_is_alive(**_real_alive_token(market_cap_usd=PUMPFUN_MIN_MARKET_CAP_USD)) is True
    assert pumpfun_token_is_alive(**_real_alive_token(market_cap_usd=PUMPFUN_MAX_MARKET_CAP_USD)) is True


def test_none_market_cap_rejected():
    assert pumpfun_token_is_alive(**_real_alive_token(market_cap_usd=None)) is False


def test_real_bug_traded_once_and_went_silent_rejected():
    """The exact reported bug: buy_count+sell_count=1 (a single trade,
    nothing since) must fail PUMPFUN_MIN_TOTAL_TRADES."""
    assert pumpfun_token_is_alive(**_real_alive_token(
        buy_count=1, sell_count=0,
        unique_buyers_json=json.dumps(["OnlyWallet"]),
        unique_sellers_json=json.dumps([]),
    )) is False


def test_too_few_distinct_participants_rejected():
    assert pumpfun_token_is_alive(**_real_alive_token(
        unique_buyers_json=json.dumps(["WalletA"]),
        unique_sellers_json=json.dumps([]),
    )) is False


def test_distinct_participants_dedupes_buyer_seller_overlap():
    """The same wallet buying then selling is still only 1 distinct
    participant, not 2 -- a real wash-trading-adjacent case, must still
    fail the minimum if that's the only real interaction."""
    assert pumpfun_token_is_alive(**_real_alive_token(
        unique_buyers_json=json.dumps(["WalletA"]),
        unique_sellers_json=json.dumps(["WalletA"]),
    )) is False


def test_stale_last_trade_rejected():
    assert pumpfun_token_is_alive(**_real_alive_token(
        last_trade_at=_fresh_ts(PUMPFUN_MAX_STALENESS_MINUTES + 1),
    )) is False


def test_fresh_last_trade_at_boundary_accepted():
    assert pumpfun_token_is_alive(**_real_alive_token(
        last_trade_at=_fresh_ts(PUMPFUN_MAX_STALENESS_MINUTES - 0.01),
    )) is True


def test_missing_last_trade_at_rejected():
    assert pumpfun_token_is_alive(**_real_alive_token(last_trade_at=None)) is False


def test_low_curve_liquidity_rejected():
    assert pumpfun_token_is_alive(**_real_alive_token(
        v_sol_in_curve=PUMPFUN_MIN_CURVE_SOL_LIQUIDITY - 0.1,
    )) is False


def test_curve_liquidity_boundary_inclusive():
    assert pumpfun_token_is_alive(**_real_alive_token(
        v_sol_in_curve=PUMPFUN_MIN_CURVE_SOL_LIQUIDITY,
    )) is True


def test_malformed_participant_json_fails_closed_not_crashes():
    assert pumpfun_token_is_alive(**_real_alive_token(
        unique_buyers_json="not valid json{{{",
        unique_sellers_json=None,
    )) is False
