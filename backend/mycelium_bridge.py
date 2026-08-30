"""Mycelium trace-substrate bridge — real per-source trading-performance
observations, emitted after every trade_outcome_learner.py cycle so
Mycelium's general pattern miners have real, structured signal-source
performance data to reason over (not just the raw PnL numbers Vantage's
own /api/trading/source-performance already exposes) -- cross-referencing
against whatever else is already in the substrate: wallet_intel's
observation traces (mycelium/miners/wallet.py) and signal_quality's
decision/observation traces (mycelium/miners/signal_quality.py) live in
the exact same substrate, so a shared source name showing up in a
signal_post trace AND a source_performance trace becomes something the
substrate can actually correlate over time, not two disconnected numbers
in two separate SQLite databases.

Real trace contract (verified against mycelium/core.py's own emit()
validation, and confirmed live against the real deployed gateway
2026-08-29):
  kind: "observation" (this is a computed summary, not a tool
    invocation or an agent decision -- same real convention wallet_intel's
    collector.py already uses for its own wallet_buy/wallet_sell traces:
    "senses" data into the substrate, doesn't act on it)
  action: "source_performance"
  target: the source name itself (e.g. "strategy:9", "pine:14:rsi_cross",
    "manual_ui") -- same role wallet_intel's target=<wallet address> plays,
    "what/who is this observation ABOUT"
  payload: {window, n_trades, wins, win_rate, avg_pnl_pct, updated_at}

Real gateway endpoint (confirmed live, same host as Vantage --
hostinger-vps runs both ares-mycelium-gateway.service and
vantage.service): POST http://127.0.0.1:8811/api/trace, JSON body
{agent, session, kind, action, target, outcome, payload}. No auth
required (MYCELIUM_GATEWAY_AUTH unset on the real deployed gateway,
confirmed via `systemctl show ares-mycelium-gateway.service`) -- same
unauthenticated same-host pattern wallet_intel/collector.py already uses
against MYCELIUM_URL.

Dedup: only emits a trace for a (source, window) pair whose real
updated_at has changed since the last successful emission -- otherwise a
10-minute recompute cycle with zero new outcome marks would emit an
identical trace every cycle forever, pure noise for the miners. In-memory
only (module-level dict, not persisted across a process restart) --
acceptable: the worst case after a restart is one redundant trace per
(source, window) before dedup catches up again, not a correctness issue,
and matches this module's own fail-soft, best-effort posture throughout.

Fail-soft: Mycelium being down/unconfigured/unreachable must NEVER break
or slow trade_outcome_learner.py's own real job (marking real trade
outcomes) -- every failure here is caught and logged, never raised.
"""
import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

MYCELIUM_URL = os.environ.get("MYCELIUM_URL", "http://127.0.0.1:8811")
_TRACE_TIMEOUT_S = 10

# {(source, window): last-emitted updated_at string} -- see module
# docstring's Dedup section.
_last_emitted: dict[tuple[str, str], str] = {}


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
        updated_at = row.get("updated_at")
        if not source or not window:
            continue
        key = (source, window)
        if updated_at and _last_emitted.get(key) == updated_at:
            continue  # unchanged since last emission -- real dedup, not a bug

        n_trades = row.get("n_trades") or 0
        wins = row.get("wins") or 0
        win_rate = (wins / n_trades) if n_trades else None

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
                "avg_pnl_pct": row.get("avg_pnl_pct"),
                "updated_at": updated_at,
            },
        }
        if _post_trace(body):
            _last_emitted[key] = updated_at
            emitted += 1
    return emitted
