"""Mycelium trace-substrate bridge — real observation-trace emitters for
every genuinely-learnable Vantage signal source, so Mycelium's pattern
miners have real, structured data to reason over instead of each source's
numbers only ever existing inside its own SQLite table / HTTP response.

2026-08-30 audit (owner-requested): checked every real API-driven signal
source already live in Vantage against what's actually emitting into
Mycelium (POST http://127.0.0.1:8811/api/trace, verified live). Found
real, wired already: this module's own source_performance traces (below),
narrative_detection.py's combo_flags (narrative_combo action), and Ares
Council's verdicts (a separate daemon, /opt/ares/ares_council, confirmed
live via GET /api/traces?agent=council -- real votes/conviction/direction
payloads, current timestamps, not stale). Found real and NOT wired, added
this pass: aggregate_score.py's whole-app "ultimate winner" (below),
routers/degen.py's 6-platform top-picks (below), narrative_detection.py's
per-theme heat scores (a genuinely separate signal from combo_flags --
"theme X just went hot" vs "this token combines two hot themes"; added
alongside the existing combo_flags emission in that same function), and
routers/pine.py's real Pine-indicator-triggered paper-fill orders
(emitted from that router directly, using post_observation() below).
Deliberately NOT wired: InsightX Labels/Scanner/DEX Metrics (they're
scoring INPUTS folded into aggregate_score's own disqualification/ranking
-- already visible in that trace's payload, a standalone pass-through of
raw API responses with no aggregation point of its own would be noise,
not a distinct learnable signal) and DEX-pair-liveness sorting (a per-query
utility for arbitrary user searches, no natural "top pick" to trace).

Real trace contract (verified against mycelium/core.py's own emit()
validation, and confirmed live against the real deployed gateway
2026-08-29):
  kind: "observation" (this is a computed summary, not a tool
    invocation or an agent decision -- same real convention wallet_intel's
    collector.py already uses for its own wallet_buy/wallet_sell traces:
    "senses" data into the substrate, doesn't act on it)
  action: source-specific (see each emit_* function below)
  target: the "what/who is this observation ABOUT" -- a source name, a
    token address, a theme key
  payload: source-specific structured data

Real gateway endpoint (confirmed live, same host as Vantage --
hostinger-vps runs both ares-mycelium-gateway.service and
vantage.service): POST http://127.0.0.1:8811/api/trace, JSON body
{agent, session, kind, action, target, outcome, payload}. No auth
required (MYCELIUM_GATEWAY_AUTH unset on the real deployed gateway,
confirmed via `systemctl show ares-mycelium-gateway.service`) -- same
unauthenticated same-host pattern wallet_intel/collector.py already uses
against MYCELIUM_URL.

Dedup: every emit_* function below only emits a trace for a given key
whose real value has genuinely changed since the last emission -- an
on-demand endpoint (or a periodic cycle with nothing new) hit repeatedly
would otherwise emit an identical trace every call, pure noise for the
miners. Each function dedups on the real VALUE tuple, never on an
"updated_at"-style timestamp alone -- confirmed live 2026-08-30 across two
real, 10-minutes-apart trade_outcome_learner cycles that
refresh_source_performance()'s own SQL unconditionally bumps updated_at on
every full recompute regardless of whether the aggregate actually
changed, so a timestamp-keyed dedup looked correct in isolated unit tests
but never actually deduped anything against the real table's own write
behavior. In-memory only (module-level dict, not persisted across a
process restart) -- acceptable: the worst case after a restart is one
redundant trace per key before dedup catches up again, not a correctness
issue.

Fail-soft: Mycelium being down/unconfigured/unreachable must NEVER break
or slow the real work any of these callers are doing (marking trade
outcomes, computing an aggregate score, gathering platform leaders,
mining narrative themes, firing a Pine-triggered order) -- every failure
here is caught and logged, never raised.
"""
import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

MYCELIUM_URL = os.environ.get("MYCELIUM_URL", "http://127.0.0.1:8811")
_TRACE_TIMEOUT_S = 10

# Shared dedup store across every emit_* function in this module. Each
# function's key tuple starts with its own discriminator (e.g.
# "aggregate_score", "platform_leader", "narrative_theme") so different
# functions can never collide on the same key, even though they share one
# dict. Values are whatever value-tuple that function considers "the real
# state" -- see module docstring's Dedup section for why this is never a
# bare timestamp.
_last_emitted: dict[tuple, object] = {}


def _post_trace(body: dict) -> bool:
    """One real HTTP POST to the Mycelium gateway. Returns True on a real
    201/200, False on any failure -- never raises."""
    try:
        req = urllib.request.Request(
            f"{MYCELIUM_URL}/api/trace",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_TRACE_TIMEOUT_S) as r:
            return r.status in (200, 201)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.debug("mycelium_bridge: trace POST failed: %s", e)
        return False


def post_observation(
    agent: str, session: str, action: str, target: str, payload: dict, outcome: str = "info",
) -> bool:
    """Generic, real, fail-soft observation-trace POST -- the shared
    low-level primitive every Vantage emitter in this module (and
    routers/pine.py, which imports this directly) uses rather than
    reimplementing this module's try/except boilerplate. `kind` is always
    "observation" here (see module docstring) -- every caller of this
    function is a computed summary/detection, never a tool invocation or
    an agent decision. Returns True on a real 200/201, False on any
    failure -- never raises."""
    return _post_trace({
        "agent": agent, "session": session, "kind": "observation",
        "action": action, "target": target, "outcome": outcome, "payload": payload,
    })


def emit_source_performance_traces(rows: list[dict]) -> int:
    """Emit one real observation trace per (source, window) row that has
    genuinely changed since the last emission (see module docstring's
    Dedup section). `rows` is source_performance's own real shape: dicts
    with source/window/n_trades/wins/avg_pnl_pct/updated_at -- the exact
    columns trade_outcome_learner.py's refresh_source_performance() writes
    and GET /api/trading/source-performance already reads, no
    reshaping/relabeling.

    Returns the real count of traces successfully emitted this call. Never
    raises -- a Mycelium outage means 0 emitted, not a broken caller."""
    emitted = 0
    for row in rows:
        source = row.get("source")
        window = row.get("window")
        if not source or not window:
            continue

        n_trades = row.get("n_trades") or 0
        wins = row.get("wins") or 0
        avg_pnl_pct = row.get("avg_pnl_pct")
        win_rate = (wins / n_trades) if n_trades else None

        key = (source, window)
        # Real bug found and fixed here (confirmed live 2026-08-30, two
        # consecutive real trade_outcome_learner cycles 10 minutes apart):
        # refresh_source_performance()'s SQL unconditionally sets
        # updated_at=datetime('now') on EVERY full recompute via its own
        # ON CONFLICT...DO UPDATE, even when n_trades/wins/avg_pnl_pct are
        # byte-for-byte identical to the previous cycle. Deduping on
        # updated_at therefore never actually deduped anything -- every
        # cycle looked "changed" regardless of whether the real numbers
        # moved. Dedup key is now the actual value tuple instead.
        value_key = (n_trades, wins, avg_pnl_pct)
        if _last_emitted.get(key) == value_key:
            continue  # unchanged since last emission -- real dedup, not a bug

        updated_at = row.get("updated_at")
        body = {
            "agent": "trade_outcome_learner",
            "session": "source-performance-cycle",
            "kind": "observation",
            "action": "source_performance",
            "target": source,
            "outcome": "success",
            "payload": {
                "window": window,
                "n_trades": n_trades,
                "wins": wins,
                "win_rate": win_rate,
                "avg_pnl_pct": avg_pnl_pct,
                "updated_at": updated_at,
            },
        }
        if _post_trace(body):
            _last_emitted[key] = value_key
            emitted += 1
    return emitted


def emit_aggregate_score_trace(ranked: list[dict], disqualified: list[dict]) -> bool:
    """Emit the real "ultimate winner" from one aggregate_score.py
    compute_aggregate_scores() call -- the single highest-synthesis score
    in the whole app (5 weighted components, InsightX/Nansen/wallet-
    conviction/social/whale signals all folded in, full methodology in
    that module's docstring), computed fresh on every /api/degen/
    aggregate-score request and otherwise never persisted anywhere. A
    disqualified-but-would-have-scored-highest token is deliberately NOT
    reported here -- ranked[0] IS the real winner after disqualification,
    not a raw pre-filter score; `disqualified` count is included as
    payload context, not as an alternate target.

    target = the winner's address (same "what is this observation ABOUT"
    role every other emitter in this module uses). Dedup keys on
    (address, total_score rounded to 3dp) -- a caller re-hitting this
    on-demand endpoint with no real change in the underlying signals
    (same winner, same score) doesn't re-emit; a genuinely different
    winner OR the same winner with a materially different score does.

    Returns True if a trace was emitted (or would have been but Mycelium
    was unreachable -- see return semantics note below), False if there
    was no real winner to report (empty ranked) or nothing changed."""
    if not ranked:
        return False
    winner = ranked[0]
    address = winner.get("address")
    if not address:
        return False
    total_score = round(float(winner.get("total_score") or 0.0), 3)

    key = ("aggregate_score", address)
    value_key = total_score
    if _last_emitted.get(key) == value_key:
        return False

    ok = post_observation(
        agent="aggregate_score",
        session="aggregate-score-cycle",
        action="aggregate_winner",
        target=address,
        payload={
            "symbol": winner.get("symbol"),
            "total_score": total_score,
            "components": winner.get("components"),
            "disqualified_count": len(disqualified),
        },
    )
    if ok:
        _last_emitted[key] = value_key
    return ok


def emit_platform_leader_traces(leaders: list[dict]) -> int:
    """Emit one real observation trace per platform's own #1 pick from one
    /api/degen/platform-leaders call (routers/degen.py's
    _gather_platform_leaders()) -- 6 independent platforms/sources, each
    with its own real native ranking metric. Skips platforms that returned
    `available: False` (a genuine miss, nothing real to report -- see
    that endpoint's own docstring). Dedup per (platform, address): a
    platform's pick changing is real signal (this platform just rotated
    to a new leader); the same platform reporting the same leader again
    on a later poll is not.

    target = the picked token's address. Returns the real count of traces
    successfully emitted this call."""
    emitted = 0
    for leader in leaders:
        if not leader.get("available"):
            continue
        platform = leader.get("platform")
        address = leader.get("address")
        if not platform or not address:
            continue

        key = ("platform_leader", platform)
        value_key = address
        if _last_emitted.get(key) == value_key:
            continue

        ok = post_observation(
            agent="platform_leaders",
            session="platform-leaders-cycle",
            action="platform_leader",
            target=address,
            payload={
                "platform": platform,
                "symbol": leader.get("symbol"),
                "metric_label": leader.get("metric_label"),
                "metric_value": leader.get("metric_value"),
                "market_cap": leader.get("market_cap"),
                "narrative_flag": leader.get("narrative_flag"),
            },
        )
        if ok:
            _last_emitted[key] = value_key
            emitted += 1
    return emitted


def emit_narrative_theme_trace(theme_key: str, label: str, heat_score: int, sample_mints: list[str]) -> bool:
    """Emit one real observation trace for a narrative theme's current
    heat, from narrative_detection.py's compute_narrative_heat() -- a
    genuinely separate signal from that same function's existing
    narrative_combo emission (mint-level: "this token combines two hot
    themes" vs theme-level: "theme X just went hot"). Dedup on
    (theme_key, heat_score): a theme's heat changing (more/fewer distinct
    tokens matching it) is real signal; an unchanged heat on a later scan
    is not.

    target = theme_key (the "what is this observation ABOUT"). Returns
    True if emitted, False if unchanged since last emission."""
    key = ("narrative_theme", theme_key)
    value_key = heat_score
    if _last_emitted.get(key) == value_key:
        return False

    ok = post_observation(
        agent="narrative_detection",
        session="narrative-heat-cycle",
        action="narrative_theme_heat",
        target=theme_key,
        payload={"label": label, "heat_score": heat_score, "sample_mints": sample_mints[:10]},
    )
    if ok:
        _last_emitted[key] = value_key
    return ok


# ── 2026-08-30, ecosystem-wide follow-up audit ────────────────────────────
# Extending coverage beyond the Vantage FastAPI process: /opt/ares/
# wallet_learner.py and /opt/ares/social_tracker.py are separate standalone
# daemons (systemd services ares-wallet-learner / ares-social-tracker), not
# routes in this backend -- but both already successfully import backend.*
# modules directly (wallet_blacklist, wallet_naming; sys.path.insert(0,
# "/opt/ares/Vantage") at their own top), so importing this module the same
# way and calling the functions below is the natural fit, not a new
# integration pattern.
#
# Real finding while auditing wallet_learner's own territory: /opt/ares/
# trade_outcome_learner.py (a live, currently-running standalone daemon,
# ares-trade-outcome-learner.service, active 10h+ at audit time) writes
# into the SAME trading_order_outcomes/source_performance tables this
# module's emit_source_performance_traces() already reads and emits from --
# it is NOT the "lost writer" an earlier session (incorrectly) concluded
# had disappeared when building backend/trade_outcome_learner.py. Confirmed
# no active double-write in the last 10h (both target different order
# lifecycle states -- 'submitted'+tx_hash for real on-chain fills here,
# 'filled'+avg_fill_price for paper-fills in the in-process module) and its
# real aggregate contributions already flow into Mycelium via the shared
# table + this module's existing emission, so no new wiring was needed for
# it specifically -- flagged in the owner-facing report as a real
# consistency risk worth a human decision, not silently left undocumented.


def emit_wallet_reputation_traces(rows: list[dict], top_n: int = 20) -> int:
    """Emit real observation traces for the top-N (by copy_trade_score)
    wallets from one wallet_learner.py score_wallets() cycle -- that
    script can score thousands of wallets per 30-min cycle, so this is
    deliberately bounded to the ones actually worth Mycelium's attention
    (same "bounded, not everything" discipline as emit_platform_leader_
    traces), not a per-wallet trace flood.

    `rows`: dicts with wallet_address/chain/display_name/name_source/
    name_confidence/copy_trade_score/first_buyer_count/top_trader_count/
    top_holder_count/currently_hot_tokens/reasoning -- wallet_reputation's
    own real columns, no reshaping.

    target = the wallet address. Dedup per (wallet_address, copy_trade_score
    rounded to 1dp, matching that table's own real rounding) -- a wallet's
    score changing between cycles is real signal; being re-selected into
    the top 20 with an unchanged score is not.

    Returns the real count of traces successfully emitted this call."""
    ranked = sorted(
        (r for r in rows if r.get("wallet_address") and r.get("copy_trade_score")),
        key=lambda r: r["copy_trade_score"], reverse=True,
    )[:top_n]

    emitted = 0
    for row in ranked:
        address = row["wallet_address"]
        score = round(float(row["copy_trade_score"]), 1)

        key = ("wallet_reputation", address)
        value_key = score
        if _last_emitted.get(key) == value_key:
            continue

        ok = post_observation(
            agent="wallet_learner",
            session="wallet-reputation-cycle",
            action="wallet_reputation",
            target=address,
            payload={
                "chain": row.get("chain"),
                "display_name": row.get("display_name"),
                "name_source": row.get("name_source"),
                "name_confidence": row.get("name_confidence"),
                "copy_trade_score": score,
                "first_buyer_count": row.get("first_buyer_count"),
                "top_trader_count": row.get("top_trader_count"),
                "top_holder_count": row.get("top_holder_count"),
                "currently_hot_tokens": row.get("currently_hot_tokens"),
                "reasoning": row.get("reasoning"),
            },
        )
        if ok:
            _last_emitted[key] = value_key
            emitted += 1
    return emitted


def emit_verified_call_trace(row: dict) -> bool:
    """Emit one real observation trace for a social_tracker.py verified
    call -- a REAL on-chain-verified trading-call outcome (the wallet's
    actual swap, actual entry price computed from the swap amounts, actual
    current price -- never a self-reported PnL screenshot; see that
    module's own docstring). This is a richer, ground-truth-outcome-linked
    sibling to mycelium/miners/signal_quality.py's heuristic text-based
    scoring: that domain scores whether a CALL was well-constructed;
    this reports whether a call was actually RIGHT, verified on-chain.

    `row`: dicts with platform/username/wallet_address/mint/symbol/
    entry_price_usd/entry_tx_signature/current_price_usd/pct_change --
    verified_calls' own real columns, no reshaping.

    target = the wallet address that made the real on-chain trade (same
    role wallet-intel's own traces use for "what/who is this ABOUT").
    No dedup: the caller only ever calls this once a NEW verified_calls
    row has genuinely been inserted (that table's own
    ON CONFLICT...DO NOTHING already guards against re-verifying the same
    platform+username+wallet+mint claim) -- every call here is a real,
    new, discrete fact by construction, same posture as pine.py's
    per-order trigger trace.

    Returns True if emitted, False only on a real Mycelium-gateway
    failure (fail-soft, never raises)."""
    wallet_address = row.get("wallet_address")
    if not wallet_address:
        return False
    return post_observation(
        agent="social_tracker",
        session="verified-calls",
        action="verified_social_call",
        target=wallet_address,
        payload={
            "platform": row.get("platform"),
            "username": row.get("username"),
            "mint": row.get("mint"),
            "symbol": row.get("symbol"),
            "entry_price_usd": row.get("entry_price_usd"),
            "entry_tx_signature": row.get("entry_tx_signature"),
            "current_price_usd": row.get("current_price_usd"),
            "pct_change": row.get("pct_change"),
        },
    )
