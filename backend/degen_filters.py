"""Shared filters for the degen/platform-leaders/aggregate-score feature:
exclude major/blue-chip/stablecoin tokens, and exclude obvious dust.

Both are real bugs found by live testing (2026-08-28): the Aggregate
Winner banner scored USDC #1 (huge real volume/liquidity/conviction --
mathematically correct, but useless for a "degen play" feature), the
CoinGecko platform-leader slot showed BTC (CoinGecko's /markets endpoint
is ALWAYS market-cap-ranked, so "top by market cap" can structurally
never be anything but a major -- the wrong data source for this feature,
not just a missing filter), the Vantage Conviction slot showed a token
labeled "penny" that was actually USDC's real mint (a separate, serious
upstream data-quality bug: token_wallet_roles has dozens of unrelated
symbols -- STEVE, TRUMP, PUMP, penny, etc -- all mapped to USDC's one
real address; some wallet-role daemon is confusing quote/base tokens.
That's outside this repo to fix at the source, but address-based
exclusion catches it correctly regardless of which garbage symbol shows
up), and pump.fun's slot showed a ~$10-mcap dead token (no floor at all).

## Major/stablecoin exclusion (two layers, deliberately redundant)

1. MAJOR_ADDRESSES -- a small, high-confidence Solana mint-address list.
   Address matching is exact and cannot be fooled by bad symbol data (see
   the USDC/"penny" bug above) -- the more robust of the two layers
   whenever a real address is available. Verified via direct evidence
   this session: USDC confirmed as the real corrupted-data mint itself;
   USDT confirmed live via DexScreener; wrapped SOL confirmed already in
   use elsewhere in this exact codebase (market_sources.py,
   execution_engine.py). Deliberately small and curated rather than
   trying to enumerate every wrapped-BTC/ETH variant on Solana (multiple
   bridge-specific mints exist and drift) -- the symbol layer below is
   the intentional catch-all for those.

2. MAJOR_SYMBOLS -- case-insensitive symbol match, the layer that catches
   non-Solana-native majors with no Solana CA at all (BTC itself has no
   Solana mint -- CoinGecko's global /markets data is exactly this case)
   and any wrapped-asset variant the address list doesn't enumerate.
   Real top-25-ish global blue chips + stables by market cap, not an
   exhaustive list -- the point is catching "obviously not a degen play"
   tokens, not building a full CoinGecko top-100 mirror.

A market-cap-RANK-threshold approach (e.g. "exclude CoinGecko top 100")
was considered instead of a curated list, per the task's suggestion, but
rejected: it requires a live CoinGecko call per candidate to get each
candidate's rank (most candidates are Solana degen tokens CoinGecko
doesn't even list), adding latency and a new failure mode for zero real
benefit over a list of ~30 well-known symbols that essentially never
changes.

## Dust floor

MIN_MARKET_CAP_USD reuses routers/alpha.py's own RUG_MCAP_FLOOR ($7,000,
already owner-approved production value from tonight's Money Flow work,
"fell back under this = dead") rather than inventing a new number --
same real threshold, same meaning ("this token is dead/dust"), applied
consistently across both features. MIN_LIQUIDITY_USD_FALLBACK is a
secondary check for candidates where market_cap couldn't be determined
(pre-migration pump.fun tokens with no DexScreener listing yet) but real
liquidity proves they're not empty/dead.
"""
from typing import Optional

MAJOR_ADDRESSES = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC (Solana native)
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT (Solana)
    "So11111111111111111111111111111111111111112",  # Wrapped SOL
}

MAJOR_SYMBOLS = {
    "BTC", "WBTC", "CBBTC", "TBTC",
    "ETH", "WETH", "STETH", "WSTETH",
    "SOL", "WSOL",
    "BNB", "WBNB", "XRP", "ADA", "DOGE", "TRX", "LINK", "MATIC", "POL",
    "DOT", "LTC", "BCH", "AVAX", "UNI", "ATOM", "XLM", "NEAR", "TON",
    "USDC", "USDT", "DAI", "USDS", "BUSD", "TUSD", "FDUSD", "USDE", "PYUSD",
}

# Reuses routers/alpha.py's RUG_MCAP_FLOOR exactly -- same real,
# owner-approved "dead token" threshold, not a new number invented here.
# Appropriate for anything DexScreener/GeckoTerminal has real liquidity
# data for (tokens that have already crossed a real DEX): below $7k there
# almost certainly means genuinely dead, not "just launched."
MIN_MARKET_CAP_USD = 7_000.0
MIN_LIQUIDITY_USD_FALLBACK = 2_000.0

# Deliberately lower, separate floor for pump.fun's own PRE-migration
# tokens specifically. Found live 2026-08-28: applying MIN_MARKET_CAP_USD
# ($7k) to pump.fun's own tier-scanner candidates emptied the slot
# entirely -- the real top-20 tokens by pump.fun's own activity score
# (real volume + trade diversity, wash-trading-penalized) all sat at
# $3,000-4,500 market cap, which is normal/expected there: pump.fun's
# real graduation threshold is ~$69k (see routers/alpha.py's
# MIGRATION_MCAP_FLOOR), so almost every legitimately active, not-yet-
# graduated token is naturally in the low thousands. $7k there doesn't
# mean "dead," it means "hasn't graduated yet" -- the WRONG signal to
# exclude on. This lower floor keeps excluding what the owner actually
# flagged (a literal ~$10-mcap token -- genuine near-zero dust) without
# wiping out the entire legitimate pre-migration candidate pool.
PUMPFUN_MIN_MARKET_CAP_USD = 500.0


def is_major_or_stable(symbol: Optional[str], address: Optional[str]) -> bool:
    """True if this token is a known blue-chip/stablecoin that should
    never occupy a degen-play slot, checked by address first (robust
    against bad/corrupted symbol data -- see the USDC/"penny" bug in the
    module docstring) then by symbol."""
    if address and address in MAJOR_ADDRESSES:
        return True
    if symbol and symbol.strip().upper().lstrip("$") in MAJOR_SYMBOLS:
        return True
    return False


def passes_dust_floor(market_cap: Optional[float], liquidity_usd: Optional[float] = None,
                       min_market_cap: float = MIN_MARKET_CAP_USD) -> bool:
    """True if this token clears the minimum real-signal bar. Market cap
    is the primary check; when it's genuinely unknown (None, common for
    fresh pre-migration pump.fun tokens with no DexScreener listing yet),
    real liquidity is used as a fallback proof-of-life instead of
    treating "unknown" the same as "known to be dust." `min_market_cap`
    defaults to the general MIN_MARKET_CAP_USD but callers scoring
    pump.fun's own pre-migration tokens should pass
    PUMPFUN_MIN_MARKET_CAP_USD instead -- see that constant's docstring
    for why the general floor is the wrong signal there."""
    if market_cap is not None:
        return market_cap >= min_market_cap
    if liquidity_usd is not None:
        return liquidity_usd >= MIN_LIQUIDITY_USD_FALLBACK
    # Neither figure available -- can't prove it's not dust, so it
    # doesn't pass. Consistent with this module's whole purpose: no
    # signal means no slot, not benefit-of-the-doubt.
    return False


def passes_all_filters(symbol: Optional[str], address: Optional[str],
                        market_cap: Optional[float], liquidity_usd: Optional[float] = None,
                        min_market_cap: float = MIN_MARKET_CAP_USD) -> bool:
    """Combined check: not a major/stable AND clears the dust floor."""
    if is_major_or_stable(symbol, address):
        return False
    return passes_dust_floor(market_cap, liquidity_usd, min_market_cap)
