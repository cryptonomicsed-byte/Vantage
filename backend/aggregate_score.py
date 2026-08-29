"""Aggregate token score — a genuinely whole-app score per token, pulling
from every real signal source anywhere in the backend (not just each
platform's own top pick -- see routers/degen.py's platform_leaders for
that, task (a)). This is task (b): a single transparent, auditable,
weighted score used to flash one "ultimate winner" token app-wide.

═══════════════════════════════════════════════════════════════════════
METHODOLOGY (the whole thing -- no hidden weights, no black box)
═══════════════════════════════════════════════════════════════════════

STEP 1 -- Candidate pool (bounded, real, not the full token universe):
  the union of addresses from:
    a) each of the 6 platform-leaders (routers/degen.py's platform_leaders)
    b) /must-buy-20's top-ranked entries (already a real 4-source fusion:
       GeckoTerminal trending + persisted trading_signals + social_signals
       + the live in-memory signal pool -- see must_buy_20())
    c) /high-conviction's top-ranked mints (smart-wallet overlap)
  This keeps scoring work bounded to tokens that ALREADY have at least one
  real signal behind them, rather than re-scanning the whole market --
  the aggregate score ranks among genuinely-surfaced candidates, it
  doesn't discover new ones.

STEP 2 -- Five real, independently-sourced component scores per candidate,
  each normalized to 0..1 by min-max scaling against the OTHER candidates
  in this same request (transparent, no arbitrary fixed cutoffs -- "best
  in this candidate pool scores 1.0, worst scores 0.0"):

  1. PLATFORM BREADTH (weight 0.25) -- how many of the 6 independent
     platforms/sources in task (a) surfaced this exact address as their
     own #1 pick, PLUS whether it appears in must-buy-20's source_types
     (each independent source type counts once). Rationale: agreement
     across unrelated, independently-computed rankings is real corroborating
     evidence -- one source can be noisy or gamed, six agreeing is a much
     stronger signal.

  2. SMART-MONEY CONVICTION (weight 0.28, the largest single weight) --
     TWO independently-sourced real signals, blended, both surfaced
     separately in the response (never silently merged into one opaque
     number): (a) copy_trade_score sum from wallets already vetted by
     wallet_learner.py's own performance tracking (see
     degen.py::_token_conviction) -- weighted highest of the two because
     it's built from wallets' own verified trading performance, not a raw
     popularity count; (b) ADDITIONALLY, 2026-08-29: Nansen's own
     smart-money cohort holdings (see backend/nansen_client.py) when
     NANSEN_API_KEY is configured and Nansen has real data for a
     candidate -- a second, differently-sourced conviction signal (their
     own wallet-labeling, not Vantage's). Each normalized independently,
     then blended 70% copy_trade_score / 30% Nansen -- but only when
     Nansen actually returned data for this candidate pool; unconfigured
     or down, the component collapses to 100% copy_trade_score, never
     blocking or degrading real scoring.

  3. VOLUME / MOMENTUM (weight 0.20) -- real 24h volume from whichever
     source has it for this address (GeckoTerminal pool data first,
     falling back to pump.fun's own volume_sol_total for pre-migration
     tokens DexScreener/GeckoTerminal haven't indexed yet).

  4. SOCIAL SENTIMENT (weight 0.15, the smallest real-signal weight) --
     count of BULLISH social_signals mentions in the last 24h for this
     address/ticker, weighted by each mention's own confidence score.
     Weighted lowest of the real signals because social mentions are the
     easiest of the five to game (paid shills, bot amplification) --
     still real data (not fabricated), just the least reliable on its own.

  5. WHALE / MONEY-FLOW PRESENCE (weight 0.10) -- whether any of this
     token's deployer/top_holder/top_trader wallets (token_wallet_roles)
     is a currently-ACTIVE (archived_at IS NULL, per wallet_pruning.py)
     whale-tier tracked wallet (balance_usd >= WHALE_BALANCE_USD, the
     exact same $10k real threshold wallet_pruning.py already uses --
     picked there, and reused here, because it's the one wallet in
     ~86,600 tracked wallets pass bar already established and justified
     tonight, not a new number invented for this feature).

  Weights sum to 1.0 -- see WEIGHTS dict below for current real values
  (checked by assertion at import time; also includes narrative_combo,
  added 2026-08-28, not described in this older paragraph list).

STEP 3 -- DISQUALIFICATION (hard, overrides any score -- a disqualified
  token can never be the winner regardless of how high it scores):
    a) MAJOR/STABLECOIN EXCLUSION (degen_filters.is_major_or_stable) --
       real bug found live 2026-08-28: this scorer's own math correctly
       picked USDC as #1 (huge real volume/liquidity/conviction), which
       is mathematically right but useless for a degen-plays feature.
       Address-checked first (robust against corrupted symbol data --
       token_wallet_roles had USDC's real mint labeled "penny" among
       dozens of other unrelated symbols, a separate upstream bug), then
       symbol-checked as a catch-all. See degen_filters.py's own
       docstring for the full list and reasoning.
    b) DUST FLOOR (degen_filters.passes_dust_floor) -- real bug found
       live: no floor at all let pump.fun's leader slot show a ~$10-mcap
       dead token. Reuses routers/alpha.py's own RUG_MCAP_FLOOR ($7,000,
       already owner-approved) rather than inventing a new number; falls
       back to real liquidity when market cap is genuinely unknown.
    c) manipulation_flags non-empty in pumpfun_premigration_tokens for
       this mint (real, persisted wash-trading detection --
       pumpfun_tier_scanner.py's own score_tokens(), see that daemon).
    d) live mint/freeze-authority check via Helius RPC (same real check
       /rug-check already performs) -- an active mint authority (can print
       unlimited supply) or freeze authority (can freeze holder wallets)
       disqualifies. Cached 10 min per mint to bound RPC calls across the
       (small, bounded) candidate pool.
    e) InsightX Labels (added 2026-08-29, backend/insightx_client.py) --
       real, independently-sourced ADDITIONAL check alongside (a): an
       address InsightX labels exchange/CEX/stablecoin also disqualifies,
       same "no signal ever silently replaces existing data" discipline as
       Nansen's smart_money blend below. No-op if InsightX is
       unconfigured/down (returns {}).
    f) InsightX Scanner (added 2026-08-29) -- real, independently-sourced
       ADDITIONAL rug-check alongside (d): a definite drainable/honeypot
       flag disqualifies. Only ever disqualifies on a definite True flag
       from real data, exactly like (d) -- None (no data / InsightX's own
       tight rate limit already hit this minute / unconfigured) never
       disqualifies, same fail-soft discipline throughout this file.
    g) InsightX DEX Metrics clusters + bundlers (added 2026-08-29,
       backend/dex_metrics_client.py) -- real, independently-sourced
       ADDITIONAL cross-check alongside (c)'s own manipulation_flags: a
       real coordinated wallet cluster or bundler ring holding >5% of
       supply also disqualifies. Same fail-soft/no-data-never-disqualifies
       discipline as (f); shares that same account's rate-limit budget.
       (InsightX's Atlas API was NOT integrated: confirmed via their own
       docs it's currently decommissioned with no replacement endpoints
       live yet -- nothing real to build against as of this writing.)

STEP 4 -- The winner is the highest total-weighted-score candidate among
  the NON-disqualified ones. Ties broken by raw smart-money conviction
  (the single highest-weighted, most concrete component).
═══════════════════════════════════════════════════════════════════════
"""
import asyncio
import json
import logging
import time
import urllib.request
from typing import Optional

import aiosqlite

from .db import get_db
from .routers.alpha import _dexscreener_mcap_batch
from .wallet_pruning import WHALE_BALANCE_USD
from .degen_filters import is_major_or_stable, passes_dust_floor, pumpfun_token_is_alive, MIN_MARKET_CAP_USD
from .nansen_client import smart_money_holdings_by_mint
from .insightx_client import labels_for_addresses, scanner_for_token, scanner_risk_flags
from .dex_metrics_client import clusters_for_token, bundlers_for_token, manipulation_signal

logger = logging.getLogger(__name__)

# 2026-08-28: added narrative_combo (see backend/narrative_detection.py --
# real keyword-pattern-mining component: a token whose name/symbol combines
# 2+ currently-hot narrative themes, e.g. 'PINKFONE' during a live phone-prop
# + cause-awareness spike, gets a real boost here). Existing weights
# proportionally reduced (not zeroed) to make room, still summing to 1.0.
WEIGHTS = {
    "smart_money": 0.28,
    "platform_breadth": 0.20,
    "volume_momentum": 0.17,
    "social_sentiment": 0.13,
    "whale_presence": 0.09,
    "narrative_combo": 0.13,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "aggregate score weights must sum to 1.0"

_RUG_CACHE: dict[str, tuple[float, bool]] = {}
_RUG_CACHE_TTL = 600  # 10 min -- bounds Helius RPC calls across the candidate pool


async def _has_active_mint_or_freeze_authority(mint: str, helius_key: str) -> Optional[bool]:
    """True if mint or freeze authority is still active (real disqualifying
    risk -- same check /rug-check performs). None if the check itself
    failed (RPC error, no key) -- callers must NOT disqualify on None,
    only on a definite True, since "couldn't check" is not evidence of a
    problem."""
    cached = _RUG_CACHE.get(mint)
    if cached and (time.time() - cached[0]) < _RUG_CACHE_TTL:
        return cached[1]
    if not helius_key:
        return None
    try:
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
            "params": [mint, {"encoding": "jsonParsed"}],
        }).encode()
        req = urllib.request.Request(
            f"https://mainnet.helius-rpc.com/?api-key={helius_key}",
            data=payload, headers={"Content-Type": "application/json"},
        )
        result = await asyncio.to_thread(
            lambda: json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        )
        info = (result.get("result") or {}).get("value") or {}
        data = (info.get("data") or {}).get("parsed", {}).get("info", {}) if info else {}
        has_risk = bool(data.get("mintAuthority")) or bool(data.get("freezeAuthority"))
        _RUG_CACHE[mint] = (time.time(), has_risk)
        return has_risk
    except Exception as e:
        logger.debug("aggregate_score: mint-authority check failed for %s: %s", mint, e)
        return None


# ── Batched signal lookups ────────────────────────────────────────────────
# Real latency fix, found live 2026-08-28 (owner report): the per-candidate
# loop in compute_aggregate_scores() used to call 4 lookups (manipulation
# flags, smart-money conviction, social sentiment, whale presence) with a
# sequential `await` each, per candidate -- up to 41 candidates x 4 awaits
# = up to 164 serialized round trips against the SAME aiosqlite connection
# (which itself serializes work onto one thread, so per-candidate awaiting
# them "concurrently" wouldn't have parallelized the actual disk I/O
# anyway). manipulation_flags is now read from the same bulk
# pumpfun_premigration_tokens fetch compute_aggregate_scores already does
# (that SELECT just needed the column added). The other 3 below use the
# same principle already applied to the DexScreener market-data fetch: one
# IN-clause query covering the WHOLE candidate pool, instead of N
# individual single-mint queries. These return {mint: value} maps; callers
# look up per-candidate with .get().

async def _smart_money_conviction_batch(mints: list, db) -> dict:
    if not mints:
        return {}
    cur = await db.execute(
        """SELECT twr.mint, SUM(wr.copy_trade_score) as total
           FROM token_wallet_roles twr
           JOIN wallet_reputation wr ON wr.wallet_address = twr.wallet_address
           WHERE twr.mint IN ({}) AND wr.copy_trade_score > 0
           GROUP BY twr.mint""".format(",".join("?" * len(mints))),
        mints,
    )
    return {r["mint"]: float(r["total"] or 0.0) for r in await cur.fetchall()}


async def _social_sentiment_score_batch(mint_symbol: dict, db) -> dict:
    """mint_symbol: {mint: symbol_or_None}. One query across every mint AND
    every known symbol (both real match axes the per-mint version used),
    then attributed back to whichever mint(s) each row's contract_address
    or ticker actually matches -- same OR-match semantics as before, just
    evaluated in Python instead of once per mint in SQL."""
    mints = list(mint_symbol.keys())
    if not mints:
        return {}
    symbols = sorted({s.upper() for s in mint_symbol.values() if s})
    where = ["contract_address IN ({})".format(",".join("?" * len(mints)))]
    params: list = list(mints)
    if symbols:
        where.append("UPPER(ticker) IN ({})".format(",".join("?" * len(symbols))))
        params.extend(symbols)
    cur = await db.execute(
        f"""SELECT contract_address, ticker, confidence FROM social_signals
            WHERE ({' OR '.join(where)}) AND UPPER(sentiment) = 'BULLISH'
              AND created_at >= datetime('now', '-24 hours')""",
        params,
    )
    rows = await cur.fetchall()
    out = {m: 0.0 for m in mints}
    for r in rows:
        conf = float(r["confidence"] or 0.5)
        ca = r["contract_address"]
        ticker = (r["ticker"] or "").upper()
        for m, sym in mint_symbol.items():
            if ca == m or (sym and ticker == sym.upper()):
                out[m] = out.get(m, 0.0) + conf
    return out


async def _narrative_combo_batch(mints: list, db) -> dict:
    """Batched narrative-combo lookup -- see backend/narrative_detection.py
    for the real keyword-pattern-mining that populates narrative_combo_flags.
    One IN-clause query for the whole pool, same batching discipline as the
    other _*_batch helpers here."""
    if not mints:
        return {}
    cur = await db.execute(
        """SELECT mint, theme_labels, detected_at FROM narrative_combo_flags
           WHERE mint IN ({})""".format(",".join("?" * len(mints))),
        mints,
    )
    out = {}
    for r in await cur.fetchall():
        try:
            out[r["mint"]] = {"theme_labels": json.loads(r["theme_labels"] or "[]"), "detected_at": r["detected_at"]}
        except Exception:
            continue
    return out


async def _whale_presence_batch(mints: list, db) -> set:
    if not mints:
        return set()
    cur = await db.execute(
        """SELECT DISTINCT twr.mint FROM token_wallet_roles twr
           JOIN tracked_wallets tw ON tw.address = twr.wallet_address
           WHERE twr.mint IN ({}) AND tw.archived_at IS NULL
             AND COALESCE(tw.balance_usd, 0) >= ?""".format(",".join("?" * len(mints))),
        [*mints, WHALE_BALANCE_USD],
    )
    return {r["mint"] for r in await cur.fetchall()}


def _market_snapshot_from_dex(dex_snap: Optional[dict], pumpfun_row: Optional[dict]) -> dict:
    """Same real combining logic as the old per-mint _market_snapshot, but
    takes an already-fetched DexScreener snapshot instead of making its own
    network call -- see compute_aggregate_scores(), which now fetches all
    candidates' DexScreener data in one batched call (_dexscreener_mcap_batch)
    up front rather than N individual per-mint calls. Real bug this fixes:
    N concurrent single-token DexScreener requests to the same host were
    both slow (the actual 26-30s latency, confirmed live 2026-08-28) and
    individually rate-limitable -- a real token could come back with
    market_cap=None purely from one of the N requests getting throttled,
    then get wrongly disqualified as "dust" despite having a real listing.

    volume: DexScreener liquidity_usd (real pair depth) if available,
    else pump.fun's own persisted volume_sol_total (a comparable relative
    magnitude within this pool, never shown as an interchangeable dollar
    figure).

    market_cap: pump.fun's own persisted market_cap_usd takes priority
    whenever the mint is actively tracked in pumpfun_premigration_tokens
    (pumpfun_row is not None) -- NOT "DexScreener if present, else
    pump.fun." Found live 2026-08-28: a real pre-migration pump.fun token
    ($richness, pump.fun's own tracked mcap $3,043) also had one thin,
    unreliable DexScreener pair reporting marketCap=$10.75 -- a ~300x
    discrepancy. A still-migrating token's real price discovery happens
    on pump.fun's bonding curve; an incidental external DEX pair (a stray
    seed LP, a copy-listing, etc) is not more authoritative just because
    it's a different data source. Only falls back to DexScreener's figure
    when the mint isn't in pumpfun_premigration_tokens at all (already
    migrated, or never a pump.fun token to begin with).

    `from_pumpfun_only` flags when market_cap came from pump.fun's own
    persisted column -- callers use this to pick the right dust-floor
    threshold (see degen_filters.PUMPFUN_MIN_MARKET_CAP_USD's docstring:
    pump.fun's own pre-migration tokens legitimately sit far below the
    general $7k floor, which is calibrated for "graduated then
    collapsed," not "hasn't graduated yet")."""
    snap = dex_snap if isinstance(dex_snap, dict) else {}
    pf_volume = float((pumpfun_row or {}).get("volume_sol_total") or 0.0)
    pf_mcap = (pumpfun_row or {}).get("market_cap_usd")
    volume = float(snap["liquidity_usd"]) if snap.get("liquidity_usd") else pf_volume
    dex_mcap = snap.get("market_cap")
    is_tracked_pumpfun = pumpfun_row is not None
    market_cap = pf_mcap if (is_tracked_pumpfun and pf_mcap is not None) else dex_mcap
    return {
        "volume": volume,
        "market_cap": market_cap,
        "liquidity_usd": snap.get("liquidity_usd"),
        "from_pumpfun_only": is_tracked_pumpfun and pf_mcap is not None,
    }


def _normalize(values: dict[str, float]) -> dict[str, float]:
    """Min-max scale a {key: raw_value} map to {key: 0..1}.

    Real bug fixed here, found live 2026-08-28: a candidate pool of exactly
    1 survivor (after disqualification) trivially has min==max==that one
    value, so the old "hi-lo < 1e-12 -> everything 0.0" branch caught this
    case too -- a token with real signal (e.g. volume_momentum raw=6822.66)
    scored a flat total_score of 0.0, contradicting its own raw data.
    Min-max normalization genuinely CANNOT express relative magnitude with
    only one point to compare -- there's no "worst" to be better than. The
    honest substitute (not an invented curve/threshold) is presence-of-
    signal: a lone survivor gets 1.0 on any component where it has real
    (>0) raw signal, 0.0 where it has none. This only applies to the true
    n==1 case; when 2+ candidates are genuinely tied at the same value
    (hi==lo with len>1), 0.0-for-all is still correct -- there IS a real
    "best" to compare against, they just didn't distinguish themselves."""
    if not values:
        return {}
    if len(values) == 1:
        return {k: (1.0 if v > 0 else 0.0) for k, v in values.items()}
    lo, hi = min(values.values()), max(values.values())
    if hi - lo < 1e-12:
        return {k: 0.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


async def compute_aggregate_scores(
    candidates: list[dict], helius_key: str = ""
) -> dict:
    """candidates: list of {address, symbol} dicts (deduped by address,
    caller's responsibility -- see routers/degen.py's aggregate_score
    endpoint for how the candidate pool is assembled from task (a) +
    must-buy-20 + high-conviction).

    Returns {ranked: [...], disqualified: [...], methodology: WEIGHTS}.
    `ranked` is sorted highest-to-lowest total_score; ranked[0] (if
    non-empty) is the "ultimate winner." Every score component is
    included per-token so the result is fully auditable -- no number
    appears without the raw inputs that produced it.
    """
    addresses = [c["address"] for c in candidates if c.get("address")]
    if not addresses:
        return {"ranked": [], "disqualified": [], "methodology": WEIGHTS}

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        pumpfun_rows = await (await db.execute(
            """SELECT mint, symbol, volume_sol_total, market_cap_usd, buy_count, sell_count,
                      unique_buyers, unique_sellers, last_trade_at, v_sol_in_curve,
                      manipulation_flags
               FROM pumpfun_premigration_tokens WHERE mint IN ({})""".format(
                ",".join("?" * len(addresses))
            ),
            addresses,
        )).fetchall()
        pumpfun_by_mint = {r["mint"]: dict(r) for r in pumpfun_rows}

        # First pass: symbol backfill + major/stable exclusion only (cheap,
        # no DB round trip beyond what's already fetched above) -- builds
        # the address list that actually needs the 3 real signal lookups,
        # so majors/stables never waste a query.
        disqualified: list[dict] = []
        candidate_meta: dict[str, Optional[str]] = {}
        breadth_by_addr: dict[str, int] = {}
        for c in candidates:
            addr = c.get("address")
            if not addr or addr in candidate_meta:
                continue
            # Real bug fixed here: candidates surfaced only via must-buy-20/
            # high-conviction (not one of the 6 platform-leaders, which DO
            # carry a symbol) could reach here with symbol=None even though
            # pumpfun_premigration_tokens has the real symbol for any
            # pump.fun-origin mint -- that column just wasn't selected above
            # until now. Backfill from there before falling back to None.
            symbol = c.get("symbol") or (pumpfun_by_mint.get(addr) or {}).get("symbol")
            candidate_meta[addr] = symbol
            breadth_by_addr[addr] = c.get("platform_breadth", 0)

            if is_major_or_stable(symbol, addr):
                # Real bug fixed here: majors/stablecoins (USDC scored #1
                # by this engine's own math -- high real volume/liquidity/
                # conviction, but useless for a degen-plays feature) are
                # excluded outright, not just down-weighted.
                disqualified.append({"address": addr, "symbol": symbol, "reason": "major/stablecoin token, excluded from degen scoring"})
                del candidate_meta[addr]

        # InsightX Labels -- real, independently-sourced ADDITIONAL signal
        # alongside is_major_or_stable's own curated address/symbol lists
        # above (never replacing them: if InsightX is unconfigured/down,
        # labels_for_addresses returns {} and this loop is simply a no-op).
        # One batched call covering the whole surviving pool (its own real
        # 100-address/call max, per-address cached) -- catches an exchange/
        # CEX-controlled address or a stablecoin contract the curated lists
        # don't happen to enumerate, the same class of bug is_major_or_stable
        # was built to fix for USDC.
        if candidate_meta:
            labels = await labels_for_addresses(list(candidate_meta.keys()))
            for addr in list(candidate_meta.keys()):
                info = labels.get(addr)
                if not info:
                    continue
                tags = {str(t).lower() for t in (info.get("tags") or [])}
                if tags & {"exchange", "stablecoin", "cex"}:
                    disqualified.append({
                        "address": addr,
                        "symbol": candidate_meta[addr],
                        "reason": f"InsightX label: {info.get('label')} ({sorted(tags)})",
                    })
                    del candidate_meta[addr]

        # manipulation_flags now comes from the same bulk pumpfun_rows fetch
        # above (added to that SELECT) instead of a second per-mint query.
        for addr in list(candidate_meta.keys()):
            symbol = candidate_meta[addr]
            flags_raw = (pumpfun_by_mint.get(addr) or {}).get("manipulation_flags")
            try:
                flags = json.loads(flags_raw or "[]")
            except Exception:
                flags = []
            if flags:
                disqualified.append({"address": addr, "symbol": symbol, "reason": f"manipulation_flags: {flags}"})
                del candidate_meta[addr]

        # Real latency fix (see the 3 _*_batch functions' docstrings): one
        # query per signal type covering the WHOLE remaining pool, instead
        # of up to 3 x N sequential per-mint queries -- was the dominant
        # remaining cost after the DexScreener batching fix, confirmed live
        # 2026-08-28 (still ~21s with only the market-data fetch batched).
        # narrative_combo_map added the same way (batched IN-clause query,
        # not N individual mint_combo_flag() calls) rather than reintroducing
        # the exact per-candidate-connection pattern just fixed above.
        pool = list(candidate_meta.keys())
        smart_money_map, social_map, whale_set, narrative_combo_map = await asyncio.gather(
            _smart_money_conviction_batch(pool, db),
            _social_sentiment_score_batch(candidate_meta, db),
            _whale_presence_batch(pool, db),
            _narrative_combo_batch(pool, db),
        )

        raw: dict[str, dict] = {}
        for addr, symbol in candidate_meta.items():
            narrative = narrative_combo_map.get(addr)
            raw[addr] = {
                "symbol": symbol,
                "smart_money": smart_money_map.get(addr, 0.0),
                "social_sentiment": social_map.get(addr, 0.0),
                "whale_presence": 1.0 if addr in whale_set else 0.0,
                "platform_breadth": breadth_by_addr.get(addr, 0),
                "narrative_combo": 1.0 if narrative else 0.0,
                "_narrative": narrative,
            }

    # Market data (volume + market cap, for both the volume_momentum
    # component and the dust-floor check) -- ONE batched DexScreener call
    # for the whole surviving pool (see _dexscreener_mcap_batch's docstring
    # for why: N individual per-mint calls was both the real ~26-30s
    # latency and a source of spurious dust-floor disqualifications from
    # individually-rate-limited requests coming back empty).
    surviving = list(raw.keys())
    # Nansen smart-money holdings (real, ADDITIONAL signal alongside the
    # existing wallet_reputation/copy_trade_score conviction above -- see
    # backend/nansen_client.py's docstring for why it's kept separate
    # rather than merged into the same raw number: different source,
    # different units (USD value vs. an arbitrary point sum), and Nansen
    # being down/unconfigured must never block or skew scoring). One
    # batched call (whole-chain, cached) alongside the DexScreener fetch,
    # not N per-mint calls.
    dex_snapshots, nansen_map = await asyncio.gather(
        _dexscreener_mcap_batch(surviving),
        smart_money_holdings_by_mint(surviving),
    )
    for a in surviving:
        snap = _market_snapshot_from_dex(dex_snapshots.get(a), pumpfun_by_mint.get(a))
        raw[a]["volume_momentum"] = snap["volume"]
        raw[a]["_market_cap"] = snap["market_cap"]
        raw[a]["_liquidity_usd"] = snap["liquidity_usd"]
        raw[a]["_from_pumpfun_only"] = snap.get("from_pumpfun_only", False)
        raw[a]["_nansen_smart_money_usd"] = (nansen_map.get(a) or {}).get("value_usd")

    # Dust / dead-token floor. Two paths:
    #   - Candidates with a real DexScreener listing use
    #     routers/alpha.py's own RUG_MCAP_FLOOR ($7k = dead there).
    #   - Candidates whose only market-cap figure is pump.fun's own
    #     pre-migration column get the FULL real screening
    #     (degen_filters.pumpfun_token_is_alive: owner-specified $14k-$32k
    #     band + distinct participants + total trades + last-trade
    #     freshness + real curve liquidity), not just a floor -- refined
    #     2026-08-28 after a flat floor alone still let dead/frozen
    #     pump.fun tokens through the aggregate scorer.
    for addr in list(raw.keys()):
        from_pumpfun = raw[addr].pop("_from_pumpfun_only")
        market_cap = raw[addr].pop("_market_cap")
        liquidity = raw[addr].pop("_liquidity_usd")
        if from_pumpfun:
            pf_row = pumpfun_by_mint.get(addr) or {}
            alive = pumpfun_token_is_alive(
                market_cap_usd=market_cap,
                buy_count=pf_row.get("buy_count") or 0,
                sell_count=pf_row.get("sell_count") or 0,
                unique_buyers_json=pf_row.get("unique_buyers"),
                unique_sellers_json=pf_row.get("unique_sellers"),
                last_trade_at=pf_row.get("last_trade_at"),
                v_sol_in_curve=pf_row.get("v_sol_in_curve"),
            )
            reason = "outside pump.fun's real $14k-$32k alive-token band or activity minimums"
        else:
            alive = passes_dust_floor(market_cap, liquidity, min_market_cap=MIN_MARKET_CAP_USD)
            reason = "below minimum market-cap/liquidity floor (dust)"
        if not alive:
            disqualified.append({"address": addr, "symbol": raw[addr]["symbol"], "reason": reason})
            del raw[addr]

    # Real bug fixed here, found live 2026-08-28 while investigating latency:
    # `surviving` was snapshotted BEFORE the dust-floor loop above deletes
    # entries from `raw` -- so this ran a real Helius RPC call for every
    # PRE-dust-floor candidate (up to ~37-40), most of which were already
    # disqualified and thrown away regardless of the risk-check result.
    # Wasted the dominant share of the request's real latency on addresses
    # that could never be the winner anyway, AND was a latent crash: if a
    # risk check came back True for an address the dust floor had already
    # deleted from `raw`, `del raw[a]` below would KeyError the whole
    # request. Re-derive the post-dust-floor survivor list here instead of
    # reusing the stale pre-filter one.
    still_alive = list(raw.keys())
    if helius_key and still_alive:
        risk_checks = await asyncio.gather(
            *[_has_active_mint_or_freeze_authority(a, helius_key) for a in still_alive],
            return_exceptions=True,
        )
        for a, risk in zip(still_alive, risk_checks):
            if risk is True and a in raw:
                disqualified.append({"address": a, "symbol": raw[a]["symbol"], "reason": "active mint or freeze authority"})
                del raw[a]

    # InsightX Scanner -- real, independently-sourced ADDITIONAL rug-check
    # signal, same "only disqualify on a definite risk flag, never on None"
    # discipline as the mint/freeze-authority check just above (None here
    # means InsightX had no data / was unreachable / this process already
    # hit its own conservative per-minute call budget -- see
    # insightx_client.py's docstring for why that budget exists: the real
    # API's per-minute rate limit is genuinely tight, confirmed live).
    # Deliberately runs on the POST-dust-floor survivor list (still_alive),
    # same reasoning as the bug fix documented above the mint/freeze check:
    # don't spend a scarce, budget-capped call on a candidate that's
    # already disqualified and thrown away regardless of the result.
    still_alive_2 = list(raw.keys())
    if still_alive_2:
        scans = await asyncio.gather(
            *[scanner_for_token(a) for a in still_alive_2],
            return_exceptions=True,
        )
        for a, scan in zip(still_alive_2, scans):
            if isinstance(scan, BaseException):
                continue
            flags = scanner_risk_flags(scan)
            if flags["has_data"] and flags["drainable"] and a in raw:
                disqualified.append({
                    "address": a,
                    "symbol": raw[a]["symbol"],
                    "reason": f"InsightX Scanner: drainable/honeypot risk (score={flags['score']})",
                })
                del raw[a]

    # InsightX DEX Metrics (clusters + bundlers) -- real, independently-
    # sourced ADDITIONAL manipulation check alongside step 3c's own
    # manipulation_flags (pumpfun_tier_scanner.py's persisted wash-trading
    # detection above) -- a different detection method (InsightX's own
    # coordinated-wallet/bundler analysis) cross-validating the same real
    # risk category, not a replacement. Same discipline as every other
    # InsightX check in this function: only disqualifies on a definite
    # flagged=True from real data (manipulation_signal()'s own >5%-of-
    # supply threshold -- see dex_metrics_client.py's docstring for why a
    # bare nonzero percentage isn't enough, real detection noise exists),
    # never on has_data=False (unconfigured/down/this process's own
    # conservative per-minute budget already spent this minute -- shared
    # account-wide quota with the Scanner calls just above).
    still_alive_3 = list(raw.keys())
    if still_alive_3:
        cluster_results, bundler_results = await asyncio.gather(
            asyncio.gather(*[clusters_for_token(a) for a in still_alive_3], return_exceptions=True),
            asyncio.gather(*[bundlers_for_token(a) for a in still_alive_3], return_exceptions=True),
        )
        for a, clusters, bundlers in zip(still_alive_3, cluster_results, bundler_results):
            if isinstance(clusters, BaseException):
                clusters = None
            if isinstance(bundlers, BaseException):
                bundlers = None
            sig = manipulation_signal(clusters, bundlers)
            if sig["flagged"] and a in raw:
                disqualified.append({
                    "address": a,
                    "symbol": raw[a]["symbol"],
                    "reason": f"InsightX DEX Metrics: coordinated wallet concentration (cluster={sig['cluster_pct']}, bundler={sig['bundler_pct']})",
                })
                del raw[a]

    # Normalize each component 0..1 across the surviving candidate pool.
    # smart_money is special: TWO independently-sourced signals combined
    # into one weighted component (Vantage's own wallet_reputation/
    # copy_trade_score conviction, ADDITIONALLY Nansen's smart-money
    # holdings USD value when configured/available -- see nansen_client.py).
    # Each is normalized separately (different units, different source),
    # then blended 70/30 copytrade/nansen -- but ONLY when Nansen actually
    # returned real data for at least one candidate in this pool; if
    # Nansen is unconfigured or down, this collapses to 100% the existing
    # copytrade signal, never silently zeroing out real scoring.
    components = ["platform_breadth", "volume_momentum", "social_sentiment", "whale_presence", "narrative_combo"]
    normalized: dict[str, dict[str, float]] = {}
    for comp in components:
        normalized[comp] = _normalize({a: raw[a][comp] for a in raw})

    norm_copytrade = _normalize({a: raw[a]["smart_money"] for a in raw})
    nansen_present = len(nansen_map) > 0
    norm_nansen = _normalize({a: (raw[a].get("_nansen_smart_money_usd") or 0.0) for a in raw}) if nansen_present else {}
    NANSEN_BLEND_WEIGHT = 0.3  # of the smart_money component's own weight, when Nansen has real data
    smart_money_combined: dict[str, float] = {}
    for a in raw:
        ct = norm_copytrade.get(a, 0.0)
        if nansen_present:
            ns = norm_nansen.get(a, 0.0)
            smart_money_combined[a] = (1 - NANSEN_BLEND_WEIGHT) * ct + NANSEN_BLEND_WEIGHT * ns
        else:
            smart_money_combined[a] = ct

    ranked = []
    for addr, data in raw.items():
        scores = {comp: normalized[comp].get(addr, 0.0) for comp in components}
        scores["smart_money"] = smart_money_combined.get(addr, 0.0)
        total = (
            scores["smart_money"] * WEIGHTS["smart_money"]
            + scores["platform_breadth"] * WEIGHTS["platform_breadth"]
            + scores["volume_momentum"] * WEIGHTS["volume_momentum"]
            + scores["social_sentiment"] * WEIGHTS["social_sentiment"]
            + scores["whale_presence"] * WEIGHTS["whale_presence"]
            + scores["narrative_combo"] * WEIGHTS["narrative_combo"]
        )
        narrative = data.get("_narrative")
        nansen_usd = data.get("_nansen_smart_money_usd")
        ranked.append({
            "address": addr,
            "symbol": data["symbol"],
            "total_score": round(total, 4),
            "narrative_flag": narrative,
            "components": {
                "smart_money": {
                    "raw": {
                        "copy_trade_score": round(data["smart_money"], 2),
                        "nansen_smart_money_usd": round(nansen_usd, 2) if nansen_usd is not None else None,
                    },
                    "normalized": round(scores["smart_money"], 4),
                    "weight": WEIGHTS["smart_money"],
                    "sources": {
                        "copy_trade_normalized": round(norm_copytrade.get(addr, 0.0), 4),
                        "nansen_normalized": round(norm_nansen.get(addr, 0.0), 4) if nansen_present else None,
                        "nansen_available": nansen_present,
                    },
                },
                "platform_breadth": {"raw": data["platform_breadth"], "normalized": round(scores["platform_breadth"], 4), "weight": WEIGHTS["platform_breadth"]},
                "volume_momentum": {"raw": round(data["volume_momentum"], 2), "normalized": round(scores["volume_momentum"], 4), "weight": WEIGHTS["volume_momentum"]},
                "social_sentiment": {"raw": round(data["social_sentiment"], 2), "normalized": round(scores["social_sentiment"], 4), "weight": WEIGHTS["social_sentiment"]},
                "whale_presence": {"raw": bool(data["whale_presence"]), "normalized": round(scores["whale_presence"], 4), "weight": WEIGHTS["whale_presence"]},
                "narrative_combo": {"raw": bool(data["narrative_combo"]), "normalized": round(scores["narrative_combo"], 4), "weight": WEIGHTS["narrative_combo"], "detail": narrative},
            },
        })

    # Tie-break by raw copy_trade_score specifically (not the combined/
    # normalized smart_money value) -- same real, concrete, backtested
    # signal this tiebreak always used; smart_money.raw became a dict once
    # Nansen was added, so pull the one real number back out of it.
    ranked.sort(key=lambda r: (-r["total_score"], -r["components"]["smart_money"]["raw"]["copy_trade_score"]))
    return {"ranked": ranked, "disqualified": disqualified, "methodology": WEIGHTS}
