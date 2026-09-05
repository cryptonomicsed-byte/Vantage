"""Shadow Account — retrospective self-behavior mining + counterfactual attribution.

Adapted from HKUDS/Vibe-Trading's shadow_account module (MIT), identified in
the 2026-08-29 HKUDS pattern audit as a genuinely novel pattern distinct from
this repo's existing backtest/validation/factor-zoo extraction (0a4ad48) --
that extraction is prospective (simulate a strategy forward); this is
retrospective (mine what the agent's OWN winning trades already have in
common, then measure exactly how much its losing/undisciplined trades cost
relative to that pattern).

Deliberate methodology change from the source repo, and why: Vibe-Trading
backtests extracted rules against a FRESH multi-market liquid-symbol basket
to project forward performance -- that needs a whole rule-to-runnable-code
generator (their codegen.py) and cross-symbol OHLCV infra Vantage doesn't
have (crypto-only, no equity codegen). Building that is a separate, much
larger project. Instead: classify the agent's OWN real historical trades
(from vantage_adapter.load_trade_records, which already replays real
trading_orders fills) as rule-compliant or not, and attribute PnL between
the two groups directly. No assumption that a rule generalizes to a new
symbol -- it's a pure partition of trades that already happened. Simpler,
zero new dependencies (no sklearn -- clustering is holding-time tertiles,
not KMeans; see _extract_rules), and every number traces back to a real
filled order.

Design mirrors the source's discipline:
    * <MIN_PROFITABLE_TRADES profitable trades -> explicit error, never a
      fabricated rule from too little evidence.
    * Attribution is pure arithmetic on real TradeRecord fields -- no LLM,
      no re-simulation, fully auditable.
    * missed_signals_pnl is an explicit residual (shadow - real - explained),
      never silently absorbed into another bucket.
"""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .models import TradeRecord
from .vantage_adapter import load_trade_records

MIN_PROFITABLE_TRADES = 5
DEFAULT_MIN_SUPPORT = 3
DEFAULT_MAX_RULES = 3


@dataclass(frozen=True)
class ShadowRule:
    """One if-then rule distilled from a cluster of the agent's own
    profitable trades: an entry-hour band + a holding-duration band, both
    p10-p90 quantile bounds over the cluster (interpretable, tolerant of
    outliers -- same choice Vibe-Trading's extractor makes over a full
    decision tree for small samples)."""

    rule_id: str
    human_text: str
    entry_hour_range: tuple[int, int]
    holding_hours_range: tuple[float, float]
    support_count: int
    coverage_rate: float
    sample_trades: tuple[str, ...]


@dataclass(frozen=True)
class ShadowProfile:
    agent_id: int
    profitable_trades: int
    total_trades: int
    rules: tuple[ShadowRule, ...]
    typical_holding_hours: tuple[float, float]  # (median, p75)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rules"] = [asdict(r) for r in self.rules]
        return d


@dataclass(frozen=True)
class AttributionBreakdown:
    """Signed PnL attribution between the agent's real trade history and
    its own rule-compliant subset ("shadow"). Positive = shadow made (or
    would have kept) more."""

    shadow_pnl: float
    real_pnl: float
    delta_pnl: float
    noise_trades_pnl: float
    early_exit_pnl: float
    late_exit_pnl: float
    overtrading_pnl: float
    missed_signals_pnl: float
    counterfactual_trades: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _holding_hours(t: TradeRecord) -> float:
    delta = t.exit_time - t.entry_time
    return max(0.0, delta.total_seconds() / 3600.0)


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, max(0, round(q * (len(values) - 1))))
    return values[idx]


def extract_shadow_profile(
    trades: list[TradeRecord],
    *,
    agent_id: int,
    min_support: int = DEFAULT_MIN_SUPPORT,
    max_rules: int = DEFAULT_MAX_RULES,
) -> ShadowProfile:
    """Extract a ShadowProfile from an agent's real trade history.

    Raises:
        ValueError: fewer than MIN_PROFITABLE_TRADES profitable trades --
            never fabricates a rule from insufficient evidence.
    """
    profitable = [t for t in trades if t.pnl > 0]
    if len(profitable) < MIN_PROFITABLE_TRADES:
        raise ValueError(
            f"Insufficient profitable trades: {len(profitable)} "
            f"(need >= {MIN_PROFITABLE_TRADES})."
        )

    holds = [_holding_hours(t) for t in profitable]
    hold_median = statistics.median(holds)
    hold_p75 = _quantile(holds, 0.75)

    rules = _extract_rules(profitable, min_support=min_support, max_rules=max_rules)

    return ShadowProfile(
        agent_id=agent_id,
        profitable_trades=len(profitable),
        total_trades=len(trades),
        rules=tuple(rules),
        typical_holding_hours=(round(hold_median, 2), round(hold_p75, 2)),
    )


def _extract_rules(
    profitable: list[TradeRecord],
    *,
    min_support: int,
    max_rules: int,
) -> list[ShadowRule]:
    """Bucket profitable trades into up to `max_rules` clusters by
    holding-time tertile (no sklearn dependency -- a deterministic quantile
    split instead of Vibe-Trading's KMeans, honestly documented as a
    simplification, not a like-for-like port). Each bucket with enough
    support becomes one rule; buckets below `min_support` are dropped."""
    n = len(profitable)
    k = min(max_rules, 3) if n >= min_support * 2 else 1
    holds = sorted(profitable, key=_holding_hours)
    bucket_size = max(1, n // k)

    rules: list[ShadowRule] = []
    for i in range(k):
        start = i * bucket_size
        end = n if i == k - 1 else min(n, start + bucket_size)
        cluster = holds[start:end]
        if len(cluster) < min_support:
            continue
        cluster_holds = [_holding_hours(t) for t in cluster]
        cluster_hours = [t.entry_time.hour for t in cluster]
        hold_lo = round(_quantile(cluster_holds, 0.10), 2)
        hold_hi = round(_quantile(cluster_holds, 0.90), 2)
        hour_lo = int(_quantile([float(h) for h in cluster_hours], 0.10))
        hour_hi = int(_quantile([float(h) for h in cluster_hours], 0.90))
        samples = tuple(f"{t.symbol}@{t.entry_time.isoformat()}" for t in cluster[:3])
        rules.append(ShadowRule(
            rule_id=f"R{len(rules) + 1}",
            human_text=_translate_rule(hour_lo, hour_hi, hold_lo, hold_hi),
            entry_hour_range=(hour_lo, hour_hi),
            holding_hours_range=(hold_lo, hold_hi),
            support_count=len(cluster),
            coverage_rate=round(len(cluster) / max(n, 1), 3),
            sample_trades=samples,
        ))

    if not rules:
        # Degenerate fallback: one rule over everything, same as Vibe-Trading's
        # _heuristic_single_rule when clustering yields nothing usable.
        cluster_holds = [_holding_hours(t) for t in profitable]
        cluster_hours = [float(t.entry_time.hour) for t in profitable]
        hold_lo, hold_hi = round(_quantile(cluster_holds, 0.10), 2), round(_quantile(cluster_holds, 0.90), 2)
        hour_lo, hour_hi = int(_quantile(cluster_hours, 0.10)), int(_quantile(cluster_hours, 0.90))
        rules = [ShadowRule(
            rule_id="R1",
            human_text=_translate_rule(hour_lo, hour_hi, hold_lo, hold_hi),
            entry_hour_range=(hour_lo, hour_hi),
            holding_hours_range=(hold_lo, hold_hi),
            support_count=len(profitable),
            coverage_rate=1.0,
            sample_trades=tuple(f"{t.symbol}@{t.entry_time.isoformat()}" for t in profitable[:3]),
        )]
    return rules


def _translate_rule(hour_lo: int, hour_hi: int, hold_lo: float, hold_hi: float) -> str:
    hour_text = f"at {hour_lo}:00 UTC" if hour_lo == hour_hi else f"between {hour_lo}:00-{hour_hi}:00 UTC"
    hold_text = f"hold {hold_lo:.1f}h" if hold_lo == hold_hi else f"hold {hold_lo:.1f}-{hold_hi:.1f}h"
    return f"Enter {hour_text}, {hold_text}"


def _rule_matches(t: TradeRecord, rule: ShadowRule) -> bool:
    hour_lo, hour_hi = rule.entry_hour_range
    hold_lo, hold_hi = rule.holding_hours_range
    hour = t.entry_time.hour
    hold = _holding_hours(t)
    return hour_lo <= hour <= hour_hi and hold_lo <= hold <= hold_hi


def _aggregate_holding_range(profile: ShadowProfile) -> tuple[float, float]:
    if not profile.rules:
        return (0.0, 24.0)
    los = [r.holding_hours_range[0] for r in profile.rules]
    his = [r.holding_hours_range[1] for r in profile.rules]
    return (min(los), max(his))


def compute_attribution(
    trades: list[TradeRecord],
    profile: ShadowProfile,
) -> AttributionBreakdown:
    """Attribute the delta between the agent's real PnL and its own
    rule-compliant ("shadow") subset. All fields signed; positive means the
    shadow retains more."""
    rule_hold_lo, rule_hold_hi = _aggregate_holding_range(profile)

    real_pnl = 0.0
    shadow_pnl = 0.0
    noise = 0.0
    early = 0.0
    late = 0.0
    counterfactuals: list[dict[str, Any]] = []

    for t in trades:
        pnl = float(t.pnl)
        real_pnl += pnl
        hold = _holding_hours(t)
        compliant = any(_rule_matches(t, r) for r in profile.rules)
        if compliant:
            shadow_pnl += pnl

        within_hold = rule_hold_lo <= hold <= rule_hold_hi
        impact = 0.0
        reason = ""
        if not compliant:
            noise += -pnl
            impact += -pnl
            reason = "rule_violation"
        if pnl > 0 and hold < rule_hold_lo:
            shortfall = pnl * max(0.0, (rule_hold_lo - hold) / max(rule_hold_lo, 1e-6))
            early += shortfall
            impact += shortfall
            reason = reason or "early_exit"
        if pnl < 0 and hold > rule_hold_hi:
            excess = -pnl * max(0.0, (hold - rule_hold_hi) / max(rule_hold_hi, 1e-6))
            late += excess
            impact += excess
            reason = reason or "late_exit"
        if impact != 0.0:
            counterfactuals.append({
                "symbol": t.symbol,
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat(),
                "holding_hours": round(hold, 2),
                "pnl": round(pnl, 2),
                "impact": round(impact, 2),
                "reason": reason,
            })

    overtrading = _overtrading_pnl(trades, profile)
    explained = noise + early + late + overtrading
    delta = shadow_pnl - real_pnl
    missed = round(delta - explained, 2)

    counterfactuals.sort(key=lambda r: abs(r["impact"]), reverse=True)

    return AttributionBreakdown(
        shadow_pnl=round(shadow_pnl, 2),
        real_pnl=round(real_pnl, 2),
        delta_pnl=round(delta, 2),
        noise_trades_pnl=round(noise, 2),
        early_exit_pnl=round(early, 2),
        late_exit_pnl=round(late, 2),
        overtrading_pnl=round(overtrading, 2),
        missed_signals_pnl=missed,
        counterfactual_trades=tuple(counterfactuals[:5]),
    )


def _overtrading_pnl(trades: list[TradeRecord], profile: ShadowProfile) -> float:
    """Excess-frequency PnL: trades beyond an expected budget of roughly
    1 trade per 2x the median holding period, over the real span of trades.
    Same shape as Vibe-Trading's overtrading heuristic. Penalizes the
    lowest-|pnl| extras first (those look most like noise)."""
    if not trades:
        return 0.0
    median_hold, _ = profile.typical_holding_hours
    if median_hold <= 0:
        return 0.0
    ordered = sorted(trades, key=lambda t: t.entry_time)
    span_hours = (ordered[-1].exit_time - ordered[0].entry_time).total_seconds() / 3600.0
    expected = max(1.0, span_hours / max(2 * median_hold, 1e-6))
    actual = len(trades)
    if actual <= expected:
        return 0.0
    extras = sorted(trades, key=lambda t: abs(float(t.pnl)))
    extra_count = int(actual - expected)
    extra_pnl = sum(float(t.pnl) for t in extras[:extra_count])
    return -extra_pnl


async def build_shadow_report(
    agent_id: int,
    *,
    min_support: int = DEFAULT_MIN_SUPPORT,
    max_rules: int = DEFAULT_MAX_RULES,
) -> dict[str, Any]:
    """End-to-end: load this agent's real trade history, extract a shadow
    profile, compute attribution. Raises ValueError (propagated to the
    caller as a 400) when there isn't enough profitable history yet."""
    trades = await load_trade_records(agent_id)
    profile = extract_shadow_profile(trades, agent_id=agent_id, min_support=min_support, max_rules=max_rules)
    attribution = compute_attribution(trades, profile)
    return {
        "profile": profile.to_dict(),
        "attribution": attribution.to_dict(),
    }
