"""Tests for moonshot_client.py.

curve_price_and_mcap is pure (no I/O) -- covers the documented formula
directly. moonshot_tokens/moonshot_top_token are tested against real
DNS-unreachable behavior (api.moonshot.cc currently NXDOMAIN, see the
module docstring) to confirm fail-soft, not a mock -- there is nothing to
mock; a real request against a real (currently unreachable) host IS the
real behavior this code needs to handle correctly.
"""
import pytest

from backend.moonshot_client import (
    curve_price_and_mcap,
    moonshot_tokens,
    moonshot_top_token,
    MOONSHOT_TOTAL_SUPPLY,
)


def test_curve_price_and_mcap_real_formula():
    # price = vSOL / vToken; mcap = price * 1B supply
    price, mcap = curve_price_and_mcap(v_sol=30.0, v_token=1_000_000_000.0)
    assert price == pytest.approx(30.0 / 1_000_000_000.0)
    assert mcap == pytest.approx(price * MOONSHOT_TOTAL_SUPPLY)


def test_curve_price_and_mcap_scales_with_reserves():
    # Doubling vSOL (more SOL bought in) doubles price and mcap.
    p1, m1 = curve_price_and_mcap(v_sol=10.0, v_token=500_000_000.0)
    p2, m2 = curve_price_and_mcap(v_sol=20.0, v_token=500_000_000.0)
    assert p2 == pytest.approx(p1 * 2)
    assert m2 == pytest.approx(m1 * 2)


def test_curve_price_and_mcap_zero_v_token_returns_none():
    price, mcap = curve_price_and_mcap(v_sol=10.0, v_token=0.0)
    assert price is None
    assert mcap is None


@pytest.mark.asyncio
async def test_moonshot_tokens_fails_soft_when_unreachable():
    # api.moonshot.cc currently doesn't resolve (see module docstring) --
    # this must return [] cleanly, never raise.
    result = await moonshot_tokens("top", "solana")
    assert result == []


@pytest.mark.asyncio
async def test_moonshot_top_token_fails_soft_when_unreachable():
    result = await moonshot_top_token("solana")
    assert result is None


@pytest.mark.asyncio
async def test_moonshot_tokens_invalid_view_id_falls_back_to_top():
    # Invalid view_id should not raise -- falls back to "top" and still
    # fails soft against the unreachable host.
    result = await moonshot_tokens("not_a_real_view", "solana")
    assert result == []
