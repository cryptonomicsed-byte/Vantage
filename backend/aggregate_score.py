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

  2. SMART-MONEY CONVICTION (weight 0.30, the largest single weight) --
     real copy_trade_score sum from wallets already vetted by
     wallet_learner.py's own performance tracking (see
     degen.py::_token_conviction). Weighted highest because
     copy_trade_score is itself already a REAL, backtested signal (built
     from wallets' own verified trading performance), not a raw popularity
     count -- it's the single most concrete number this pool has.

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

  Weights sum to 1.0: 0.25 + 0.30 + 0.20 + 0.15 + 0.10 = 1.00

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
from .routers.alpha import _dexscreener_mcap
from .wallet_pruning import WHALE_BALANCE_USD
from .degen_filters import is_major_or_stable, passes_dust_floor, pumpfun_token_is_alive, MIN_MARKET_CAP_USD
from .narrative_detection import mint_combo_flag

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


async def _pumpfun_manipulation_flags(mint: str, db) -> list[str]:
    cur = await db.execute(
        "SELECT manipulation_flags FROM pumpfun_premigration_tokens WHERE mint=?", (mint,)
    )
    row = await cur.fetchone()
    if not row:
        return []
    try:
        return json.loads(row["manipulation_flags"] or "[]")
    except Exception:
        return []


async def _smart_money_conviction(mint: str, db) -> float:
    cur = await db.execute(
        """SELECT SUM(wr.copy_trade_score) as total
           FROM token_wallet_roles twr
           JOIN wallet_reputation wr ON wr.wallet_address = twr.wallet_address
           WHERE twr.mint = ? AND wr.copy_trade_score > 0""",
        (mint,),
    )
    row = await cur.fetchone()
    return float(row["total"]) if row and row["total"] else 0.0


async def _social_sentiment_score(mint: str, symbol: Optional[str], db) -> float:
    where = ["contract_address = ?"]
    params: list = [mint]
    if symbol:
        where.append("UPPER(ticker) = ?")
        params.append(symbol.upper())
    cur = await db.execute(
        f"""SELECT confidence FROM social_signals
            WHERE ({' OR '.join(where)}) AND UPPER(sentiment) = 'BULLISH'
              AND created_at >= datetime('now', '-24 hours')""",
        params,
    )
    rows = await cur.fetchall()
    return sum(float(r["confidence"] or 0.5) for r in rows)


async def _whale_presence(mint: str, db) -> bool:
    cur = await db.execute(
        """SELECT 1 FROM token_wallet_roles twr
           JOIN tracked_wallets tw ON tw.address = twr.wallet_address
           WHERE twr.mint = ? AND tw.archived_at IS NULL
             AND COALESCE(tw.balance_usd, 0) >= ?
           LIMIT 1""",
        (mint, WHALE_BALANCE_USD),
    )
    return (await cur.fetchone()) is not None


async def _market_snapshot(mint: str, pumpfun_row: Optional[dict]) -> dict:
    """One real market-data fetch per candidate, reused for BOTH the
    volume_momentum component and the dust-floor check (previously two
    separate concerns risked drifting -- combined so there's exactly one
    source of truth per candidate per request).

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
    snap = await _dexscreener_mcap(mint)
    snap = snap if isinstance(snap, dict) else {}
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
    """Min-max scale a {key: raw_value} map to {key: 0..1}. All-equal or
    empty input maps everything to 0.0 (no signal to distinguish on)."""
    if not values:
        return {}
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
            """SELECT mint, volume_sol_total, market_cap_usd, buy_count, sell_count,
                      unique_buyers, unique_sellers, last_trade_at, v_sol_in_curve
               FROM pumpfun_premigration_tokens WHERE mint IN ({})""".format(
                ",".join("?" * len(addresses))
            ),
            addresses,
        )).fetchall()
        pumpfun_by_mint = {r["mint"]: dict(r) for r in pumpfun_rows}

        raw: dict[str, dict] = {}
        disqualified: list[dict] = []
        candidate_meta: dict[str, Optional[str]] = {}
        for c in candidates:
            addr = c.get("address")
            if not addr or addr in raw or addr in candidate_meta:
                continue
            symbol = c.get("symbol")
            candidate_meta[addr] = symbol

            if is_major_or_stable(symbol, addr):
                # Real bug fixed here: majors/stablecoins (USDC scored #1
                # by this engine's own math -- high real volume/liquidity/
                # conviction, but useless for a degen-plays feature) are
                # excluded outright, not just down-weighted.
                disqualified.append({"address": addr, "symbol": symbol, "reason": "major/stablecoin token, excluded from degen scoring"})
                continue

            flags = await _pumpfun_manipulation_flags(addr, db)
            if flags:
                disqualified.append({"address": addr, "symbol": symbol, "reason": f"manipulation_flags: {flags}"})
                continue

            smart_money = await _smart_money_conviction(addr, db)
            social = await _social_sentiment_score(addr, symbol, db)
            whale = await _whale_presence(addr, db)
            narrative = await mint_combo_flag(addr)
            raw[addr] = {
                "symbol": symbol,
                "smart_money": smart_money,
                "social_sentiment": social,
                "whale_presence": 1.0 if whale else 0.0,
                "platform_breadth": c.get("platform_breadth", 0),
                "narrative_combo": 1.0 if narrative else 0.0,
                "_narrative": narrative,
            }

    # Market data (volume + market cap, for both the volume_momentum
    # component and the dust-floor check) + rug check run outside the DB
    # connection (real network calls) -- bounded concurrency over the
    # (small) surviving candidate set.
    surviving = list(raw.keys())
    snapshots = await asyncio.gather(
        *[_market_snapshot(a, pumpfun_by_mint.get(a)) for a in surviving], return_exceptions=True
    )
    for a, snap in zip(surviving, snapshots):
        snap = snap if isinstance(snap, dict) else {"volume": 0.0, "market_cap": None, "liquidity_usd": None, "from_pumpfun_only": False}
        raw[a]["volume_momentum"] = snap["volume"]
        raw[a]["_market_cap"] = snap["market_cap"]
        raw[a]["_liquidity_usd"] = snap["liquidity_usd"]
        raw[a]["_from_pumpfun_only"] = snap.get("from_pumpfun_only", False)

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

    if helius_key:
        risk_checks = await asyncio.gather(
            *[_has_active_mint_or_freeze_authority(a, helius_key) for a in surviving],
            return_exceptions=True,
        )
        for a, risk in zip(surviving, risk_checks):
            if risk is True:
                disqualified.append({"address": a, "symbol": raw[a]["symbol"], "reason": "active mint or freeze authority"})
                del raw[a]

    # Normalize each component 0..1 across the surviving candidate pool.
    components = ["smart_money", "platform_breadth", "volume_momentum", "social_sentiment", "whale_presence", "narrative_combo"]
    normalized: dict[str, dict[str, float]] = {}
    for comp in components:
        normalized[comp] = _normalize({a: raw[a][comp] for a in raw})

    ranked = []
    for addr, data in raw.items():
        scores = {comp: normalized[comp].get(addr, 0.0) for comp in components}
        total = (
            scores["smart_money"] * WEIGHTS["smart_money"]
            + scores["platform_breadth"] * WEIGHTS["platform_breadth"]
            + scores["volume_momentum"] * WEIGHTS["volume_momentum"]
            + scores["social_sentiment"] * WEIGHTS["social_sentiment"]
            + scores["whale_presence"] * WEIGHTS["whale_presence"]
            + scores["narrative_combo"] * WEIGHTS["narrative_combo"]
        )
        narrative = data.get("_narrative")
        ranked.append({
            "address": addr,
            "symbol": data["symbol"],
            "total_score": round(total, 4),
            "narrative_flag": narrative,
            "components": {
                "smart_money": {"raw": round(data["smart_money"], 2), "normalized": round(scores["smart_money"], 4), "weight": WEIGHTS["smart_money"]},
                "platform_breadth": {"raw": data["platform_breadth"], "normalized": round(scores["platform_breadth"], 4), "weight": WEIGHTS["platform_breadth"]},
                "volume_momentum": {"raw": round(data["volume_momentum"], 2), "normalized": round(scores["volume_momentum"], 4), "weight": WEIGHTS["volume_momentum"]},
                "social_sentiment": {"raw": round(data["social_sentiment"], 2), "normalized": round(scores["social_sentiment"], 4), "weight": WEIGHTS["social_sentiment"]},
                "whale_presence": {"raw": bool(data["whale_presence"]), "normalized": round(scores["whale_presence"], 4), "weight": WEIGHTS["whale_presence"]},
                "narrative_combo": {"raw": bool(data["narrative_combo"]), "normalized": round(scores["narrative_combo"], 4), "weight": WEIGHTS["narrative_combo"], "detail": narrative},
            },
        })

    ranked.sort(key=lambda r: (-r["total_score"], -r["components"]["smart_money"]["raw"]))
    return {"ranked": ranked, "disqualified": disqualified, "methodology": WEIGHTS}
