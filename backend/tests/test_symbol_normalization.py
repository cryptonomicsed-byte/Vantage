"""Symbol normalization for price lookup.

PYTH_IDS and CG_IDS are keyed by bare tickers ("SOL"), but the trading path
deals in pairs ("SOL-USDC", "SOL/USDC") and sometimes raw Solana mints. An
unnormalised pair missed Pyth, reached CoinGecko as the id "sol-usdc", 404'd,
and resolve_price returned None.

That is not a cosmetic miss. _enforce_risk_limits fails closed when it cannot
compute a notional, so a symbol it could not price became
"cannot verify order notional against risk limit (no price/quote available)"
-- an error naming neither the symbol nor the real cause, on every order using
pair notation.
"""
import pytest

from backend import market_sources as ms


@pytest.mark.parametrize("symbol, expected", [
    ("SOL-USDC", "SOL"),
    ("SOL/USDC", "SOL"),
    ("SOL_USDC", "SOL"),
    ("BTC-USD", "BTC"),
    ("ETH/USDT", "ETH"),
    ("sol-usdc", "SOL"),          # case-insensitive
    ("  SOL-USDC  ", "SOL"),      # padded
    ("SOLUSDC", "SOL"),           # concatenated
    ("BTCUSDT", "BTC"),
])
def test_pairs_reduce_to_their_base_asset(symbol, expected):
    assert ms.normalize_symbol(symbol) == expected


@pytest.mark.parametrize("symbol", ["SOL", "BTC", "ETH", "BONK", "WIF"])
def test_bare_tickers_are_unchanged(symbol):
    assert ms.normalize_symbol(symbol) == symbol


def test_a_quote_currency_is_not_stripped_to_nothing():
    """USDC is itself priceable; the concatenated-pair rule must not eat it."""
    assert ms.normalize_symbol("USDC") == "USDC"
    assert ms.normalize_symbol("USDT") == "USDT"


def test_a_hyphenated_ticker_that_is_not_a_pair_survives():
    """Only a known quote currency on the right makes it a pair. Otherwise the
    whole name is the ticker, and truncating it would price the wrong asset."""
    assert ms.normalize_symbol("WEN-1") == "WEN-1"
    assert ms.normalize_symbol("FOO-BAR") == "FOO-BAR"


def test_a_solana_mint_resolves_to_its_ticker():
    """The trading path passes a mint for anything without a ticker; that is
    what test_quick_trade_rejects_ethereum_wallet sends."""
    assert ms.normalize_symbol("So11111111111111111111111111111111111111112") == "SOL"


def test_an_unknown_mint_is_left_alone_rather_than_guessed():
    unknown = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"
    assert ms.normalize_symbol(unknown) == unknown.upper()


@pytest.mark.parametrize("junk", ["", None])
def test_empty_input_is_not_an_error(junk):
    assert ms.normalize_symbol(junk) == ""


def test_normalized_pairs_are_actually_resolvable():
    """The point of the whole exercise: what comes out is a key the price maps
    hold. Asserting the mapping alone would pass even if it produced a ticker
    no source knows."""
    for pair in ("SOL-USDC", "BTC/USD", "ETH-USDT"):
        base = ms.normalize_symbol(pair)
        assert base in ms.PYTH_IDS or base in ms.CG_IDS, f"{pair} -> {base} is unpriceable"


def test_the_mint_table_has_not_drifted_from_the_execution_engine():
    """market_sources keeps its own small mint map rather than importing the
    execution engine -- a pure pricing module should not pull in crypto and DB
    deps. This is the cost of that choice: catch the two copies disagreeing."""
    from backend.execution_engine import SOLANA_TOKENS

    for ticker, mint in SOLANA_TOKENS.items():
        assert ms._MINT_SYMBOLS.get(mint) is not None, (
            f"{ticker} ({mint}) is in execution_engine.SOLANA_TOKENS but not in "
            "market_sources._MINT_SYMBOLS -- an order on it would fail to price"
        )
