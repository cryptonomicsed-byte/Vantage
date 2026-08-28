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
import json as _json
from datetime import datetime as _datetime, timezone as _timezone
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

# ═══════════════════════════════════════════════════════════════════════
# pump.fun-specific screening (owner-specified 2026-08-28, refined after
# live testing kept surfacing dead/frozen tokens even after the earlier
# PUMPFUN_MIN_MARKET_CAP_USD=$500 floor -- that floor alone wasn't
# enough: it let through tokens that technically had SOME market cap but
# had gone completely silent, or were still pure noise from the first
# minute of launch). Replaced with an explicit lifecycle-stage band PLUS
# real recent-activity criteria -- a token must clear BOTH to be alive.
# ═══════════════════════════════════════════════════════════════════════

# Owner-specified exact band: past the earliest launch noise (where almost
# every token is a $2-5k coin with exactly one trade -- confirmed live
# 2026-08-28, all 243 currently-tracked tokens had buy_count+sell_count=1),
# before it's graduated/moved on (pump.fun's real graduation threshold is
# ~$69k, see routers/alpha.py's MIGRATION_MCAP_FLOOR). This band picks
# tokens in the middle of their pre-migration life -- proven past pure
# noise, not yet done climbing.
PUMPFUN_MIN_MARKET_CAP_USD = 14_000.0
PUMPFUN_MAX_MARKET_CAP_USD = 32_000.0

# Real activity criteria, from fields pumpfun_tier_scanner.py already
# tracks (buy_count/sell_count, unique_buyers/unique_sellers JSON arrays,
# last_trade_at) -- confirms a token inside the mcap band is actually
# alive right now, not just sitting at a stale mcap from before it went
# silent (the literal bug reported: "traded once and went silent").

# Distinct wallets (union of buyers ∪ sellers) that have ever interacted
# with the token. 3 is a real, low bar appropriate for LOW-CAP plays
# specifically -- a token with 0-2 total unique participants is either
# pure noise (a handful of bot/self-trades) or genuinely dead; the point
# is filtering that out, not requiring the crowd size of an already-
# popular token (which would defeat "still degen/low-cap" from the task).
PUMPFUN_MIN_DISTINCT_PARTICIPANTS = 3

# Cumulative trades (buy_count + sell_count). Directly targets the
# reported bug: a token with exactly 1 total trade traded once and went
# silent -- 2 is the real minimum proof that trading actually continued
# past the very first transaction.
PUMPFUN_MIN_TOTAL_TRADES = 2

# A token whose last real trade (pumpfun_tier_scanner.py's own
# last_trade_at, updated on every real PumpPortal trade event) is older
# than this is not "alive" for a feature about surfacing what's moving
# RIGHT NOW -- distinct from the mcap band, which is about lifecycle
# stage/size, not recency. 60 minutes matches pump.fun's genuinely fast
# pace (tokens graduate or die within hours, not days) without being so
# tight that a token pauses between trade bursts and gets wrongly excluded.
PUMPFUN_MAX_STALENESS_MINUTES = 60

# Real bonding-curve SOL reserve (v_sol_in_curve) -- pump.fun's protocol
# starts every curve at ~30 SOL virtual reserve (confirmed live 2026-08-28:
# avg 30.76 SOL across all 243 currently-tracked tokens), so this is a low
# floor that only excludes a genuinely broken/near-empty curve state, not
# normal early tokens (which should already clear it from the protocol's
# own starting parameters).
PUMPFUN_MIN_CURVE_SOL_LIQUIDITY = 5.0


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


def _distinct_participant_count(unique_buyers_json: Optional[str], unique_sellers_json: Optional[str]) -> int:
    """Union of buyer/seller wallet addresses -- pumpfun_tier_scanner.py's
    real tracked participant lists (capped at
    MAX_TRACKED_WALLETS_PER_TOKEN=50 per token in that daemon), not an
    on-chain holder count (which this data model doesn't track), but a
    genuine, real proxy for "how many distinct wallets have actually
    interacted with this token." Malformed/empty JSON parses as no
    participants rather than raising -- a data problem here shouldn't
    crash the screening pass, it just correctly fails the minimum."""
    try:
        buyers = set(_json.loads(unique_buyers_json or "[]"))
    except Exception:
        buyers = set()
    try:
        sellers = set(_json.loads(unique_sellers_json or "[]"))
    except Exception:
        sellers = set()
    return len(buyers | sellers)


def _minutes_since(sqlite_timestamp: Optional[str]) -> Optional[float]:
    """Minutes since a `datetime('now')`-format SQLite timestamp
    ('YYYY-MM-DD HH:MM:SS', UTC). None if unparseable/missing -- caller
    treats unknown recency as failing the freshness check (same
    no-benefit-of-the-doubt principle as passes_dust_floor)."""
    if not sqlite_timestamp:
        return None
    try:
        ts = _datetime.strptime(sqlite_timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_timezone.utc)
        return (_datetime.now(_timezone.utc) - ts).total_seconds() / 60.0
    except Exception:
        return None


def pumpfun_token_is_alive(
    market_cap_usd: Optional[float],
    buy_count: int,
    sell_count: int,
    unique_buyers_json: Optional[str],
    unique_sellers_json: Optional[str],
    last_trade_at: Optional[str],
    v_sol_in_curve: Optional[float],
) -> bool:
    """Real screening for pump.fun's platform-leader pick + aggregate-score
    candidacy: a token must be in the owner-specified lifecycle-stage band
    AND show real, recent, multi-participant activity -- not just have
    technically-nonzero numbers. Every input is a field
    pumpfun_tier_scanner.py already tracks; nothing invented. See this
    module's own section header comment above for the reasoning behind
    each specific threshold.

    All five checks must pass:
      1. market_cap_usd in [PUMPFUN_MIN_MARKET_CAP_USD, PUMPFUN_MAX_MARKET_CAP_USD]
      2. distinct participants (buyers ∪ sellers) >= PUMPFUN_MIN_DISTINCT_PARTICIPANTS
      3. buy_count + sell_count >= PUMPFUN_MIN_TOTAL_TRADES
      4. minutes since last_trade_at <= PUMPFUN_MAX_STALENESS_MINUTES
      5. v_sol_in_curve >= PUMPFUN_MIN_CURVE_SOL_LIQUIDITY
    """
    if market_cap_usd is None:
        return False
    if not (PUMPFUN_MIN_MARKET_CAP_USD <= market_cap_usd <= PUMPFUN_MAX_MARKET_CAP_USD):
        return False

    if _distinct_participant_count(unique_buyers_json, unique_sellers_json) < PUMPFUN_MIN_DISTINCT_PARTICIPANTS:
        return False

    if (buy_count or 0) + (sell_count or 0) < PUMPFUN_MIN_TOTAL_TRADES:
        return False

    age_minutes = _minutes_since(last_trade_at)
    if age_minutes is None or age_minutes > PUMPFUN_MAX_STALENESS_MINUTES:
        return False

    if v_sol_in_curve is None or v_sol_in_curve < PUMPFUN_MIN_CURVE_SOL_LIQUIDITY:
        return False

    return True
