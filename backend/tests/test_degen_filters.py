"""Tests for degen_filters.py -- the major/stablecoin exclusion + dust
floor fix for the real bugs found live: Aggregate Winner scoring USDC #1,
CoinGecko's leader slot showing BTC, Vantage Conviction showing USDC
under a corrupted "penny" symbol, pump.fun showing ~$10-mcap dust.
"""
from backend.degen_filters import (
    is_major_or_stable,
    passes_dust_floor,
    passes_all_filters,
    MAJOR_ADDRESSES,
    MIN_MARKET_CAP_USD,
)


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


def test_pumpfun_floor_accepts_legitimate_premigration_token():
    """Real finding 2026-08-28: the general $7k floor emptied pump.fun's
    entire platform-leader slot -- real top-scored pump.fun tokens
    legitimately sit at $3,000-4,500 pre-migration (graduation is ~$69k).
    The lower pump.fun-specific floor must accept these."""
    from backend.degen_filters import PUMPFUN_MIN_MARKET_CAP_USD
    assert passes_dust_floor(3_500.0, min_market_cap=PUMPFUN_MIN_MARKET_CAP_USD) is True
    # But the general floor would have rejected the same real value.
    assert passes_dust_floor(3_500.0) is False


def test_pumpfun_floor_still_rejects_real_dust():
    from backend.degen_filters import PUMPFUN_MIN_MARKET_CAP_USD
    # The owner's actual reported bug: a ~$10 mcap token.
    assert passes_dust_floor(10.0, min_market_cap=PUMPFUN_MIN_MARKET_CAP_USD) is False
