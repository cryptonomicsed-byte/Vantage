"""Price sanity check on order creation.

The notional risk gate computes quantity * price from the *caller's own* limit
price when one is supplied. That makes it satisfiable by lying: name a
tiny enough price and any size passes the cap. This check is the other half --
a limit price has to bear some relation to the market.

It is also the backstop for a bad feed. If a quote source returns a wrong
number, an order priced against it should not sail through unremarked.
"""
import pytest

from backend.routers import trading


@pytest.fixture
def quote(monkeypatch):
    """Pin the live quote so these tests never touch the network."""
    def _set(value):
        async def fake_fetch_quote(symbol):
            return value
        monkeypatch.setattr(trading, "_fetch_quote", fake_fetch_quote)
    return _set


async def test_a_price_near_the_market_is_accepted(quote):
    quote(100.0)
    await trading._enforce_price_sanity("SOL", 105.0)
    await trading._enforce_price_sanity("SOL", 60.0)


async def test_a_price_far_below_the_market_is_rejected(quote):
    """The attack shape: a tiny limit price makes a huge position look small
    enough to clear max_position_size_usd."""
    quote(100.0)
    with pytest.raises(trading.RiskLimitExceeded) as exc:
        await trading._enforce_price_sanity("SOL", 0.01)
    assert "away from the live" in str(exc.value)


async def test_a_price_far_above_the_market_is_rejected(quote):
    quote(100.0)
    with pytest.raises(trading.RiskLimitExceeded):
        await trading._enforce_price_sanity("SOL", 10_000.0)


@pytest.mark.parametrize("price, ok", [
    (149.0, True),    # 49% away — under the limit
    (151.0, False),   # 51% away — over it
    (51.0, True),     # 49% below
    (49.0, False),    # 51% below
])
async def test_the_boundary_is_fifty_percent(quote, price, ok):
    quote(100.0)
    if ok:
        await trading._enforce_price_sanity("SOL", price)
    else:
        with pytest.raises(trading.RiskLimitExceeded):
            await trading._enforce_price_sanity("SOL", price)


async def test_a_market_order_with_no_limit_price_is_not_checked(quote):
    """There is no limit price to be wrong about; the notional gate handles it."""
    quote(100.0)
    await trading._enforce_price_sanity("SOL", None)


async def test_no_live_quote_fails_open_here(quote):
    """Deliberately open: the notional check right after this one already fails
    closed when no quote is available, and rejecting twice would only replace a
    precise error with a vaguer one."""
    quote(None)
    await trading._enforce_price_sanity("SOL", 12345.0)


@pytest.mark.parametrize("bad", [0.0, -5.0])
async def test_a_nonpositive_price_is_left_to_quantity_validation(quote, bad):
    quote(100.0)
    await trading._enforce_price_sanity("SOL", bad)


async def test_a_zero_market_quote_does_not_divide_by_zero(quote):
    quote(0.0)
    await trading._enforce_price_sanity("SOL", 100.0)
