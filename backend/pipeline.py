"""The trading pipeline, declared once, probed live.

Fourteen repositories were on this box with no stated relationship to each
other: seven wired to services, seven with no unit, no caller and no way to tell
"quiet" from "dead". This module is the single description of what each one is
for, which way data flows through it, and whether that flow is alive right now.

Two rules, learned the hard way in this ecosystem:

  * A node's status is *probed*, never asserted. A service that answers HTTP
    with any 4xx is alive (auth is a separate fact from liveness); a port that
    refuses is down; a repo with no process and no caller is dormant, not
    "connected".
  * Every hop that matters writes a row to pipeline_events. A pipeline you
    cannot read is indistinguishable from one that is not running.

Environment overrides (used by tests and by any future re-hosting):
  VANTAGE_PIPELINE_*  — not needed; probes read the same ports the services do.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# The registry. `kind` is what the thing IS; `layer` is where it sits in the
# flow; `status` is the *static* truth from inspection (the probe can only
# downgrade it — never upgrade a blocked node to live by accident).
#
#   live       wired to a running service/library, probed every call
#   dormant    code present, nothing runs it, nothing calls it
#   blocked    cannot run yet for a stated reason (missing input/credential)
#   reference  not a runtime component; a schema or spec others conform to
#   archived   deliberately out of the path; kept for history
# ---------------------------------------------------------------------------

# P1-02: the TradingOS SignalGenome schema is vendored into this repo, so
# validate-signal and the registry probe work without a TradingOS checkout.
_VENDORED_SIGNAL_PROTO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "protocols", "tradingos", "signal.proto")
_TRADINGOS_SIGNAL_PROTO = "/opt/ares/TradingOS/protos/signal.proto"


def signal_proto_path() -> str:
    """Vendored copy first; the TradingOS checkout only as a fallback."""
    if os.path.isfile(_VENDORED_SIGNAL_PROTO):
        return _VENDORED_SIGNAL_PROTO
    return _TRADINGOS_SIGNAL_PROTO


NODES: List[Dict[str, Any]] = [
    # ---- core -------------------------------------------------------------
    {
        "name": "Vantage", "layer": "core", "kind": "service", "repo": "/opt/ares/Vantage",
        "role": "system of record — signals, wallets, strategies, orders, Brier ledger, audit",
        "flow": "hub: every other node reads or writes it",
        "probe": {"type": "http", "url": "http://127.0.0.1:8001/metrics"},
    },
    # ---- signal production -------------------------------------------------
    {
        "name": "fomopulse-convergence", "layer": "signal", "kind": "service",
        "repo": "/opt/ares/fomopulse-convergence",
        "role": "convergence + anomaly scoring over the fomopulse tape",
        "flow": "fpconv_daemon (in the Vantage repo) -> POST /api/intel/signals/ingest",
        "probe": {"type": "unit", "unit": "ares-fpconv"},
    },
    {
        "name": "Loom", "layer": "signal", "kind": "service", "repo": "/opt/ares/Loom",
        "role": "whale intelligence fabric — wallet dossiers, contagion, trenches",
        "flow": "vantage_puller reads Vantage; vantage_watchlist/predictor push back",
        "probe": {"type": "http", "url": "http://127.0.0.1:8889/"},
    },
    {
        "name": "strategy-lab", "layer": "signal", "kind": "service", "repo": "/opt/ares/strategy-lab",
        "role": "lane ensemble — convergence surface that produces one early-entry score",
        "flow": "hub reads signals, writes scores back to Vantage; hub runs from strategy-lab-hub-deploy",
        # P1-07: probe /health (the hub 404s on "/"), at the same base the
        # enricher calls.
        "probe": {"type": "http", "url": os.environ.get("STRATEGY_LAB_BASE", "http://127.0.0.1:8012").rstrip("/") + "/health"},
    },
    {
        "name": "grokbot-mycelium", "layer": "signal", "kind": "bridge", "repo": "/opt/ares/grokbot-mycelium",
        "role": "grokbot tape -> Mycelium traces -> calibration -> measured finding",
        "flow": "tools/bridge_daemon.py reads a JSONL tape and emits traces; also ingests to Vantage intel",
        "probe": {"type": "path", "path": "/opt/ares/grokbot-pumpfun/logs/trades.jsonl"},
        "blocked_reason": "tape producer is now on disk (/opt/ares/grokbot-pumpfun, dry-run "
                          "capable) but cannot start without GROKBOT_GROK_API_KEY (xAI) and a "
                          "data-provider key; with no tape at "
                          "/opt/ares/grokbot-pumpfun/logs/trades.jsonl the bridge has nothing to "
                          "read. Unit grokbot-mycelium/deploy/ares-grokbot-paper.service is ready "
                          "and paper-only.",
    },
    {
        "name": "TradingOS", "layer": "signal", "kind": "reference", "repo": "/opt/ares/TradingOS",
        "role": "signal schema of record — protos/signal.proto (SignalGenome: conviction, dna, fitness)",
        "flow": "reference only: POST /api/pipeline/validate-signal checks Vantage signals against it",
        "probe": {"type": "path", "path": signal_proto_path()},
    },
    # ---- probability / truth ----------------------------------------------
    {
        "name": "Portent", "layer": "probability", "kind": "service", "repo": "/opt/ares/Portent",
        "role": "prediction resolution relay — settles claims against a named outcome",
        "flow": "relay :8899 -> Vantage daemons/portent_signal_bridge.py (120s poll) -> resolutions",
        "probe": {"type": "http", "url": "http://127.0.0.1:8899/"},
    },
    {
        "name": "Crucible", "layer": "probability", "kind": "library", "repo": "/opt/ares/Crucible",
        "role": "oracle authority seam — accepts a verdict only from a key named in policy",
        "flow": "would consume OracleVerdict (kind 47010) -> resolve.rs -> Vantage Brier ledger",
        "probe": {"type": "path", "path": "/opt/ares/Crucible/Cargo.toml"},
        "blocked_reason": "no binary built, no unit, and policy.oracle_authorities is None: "
                          "the authority key is an RS-2 decision that is still pending (approver null).",
    },
    # ---- execution ---------------------------------------------------------
    {
        "name": "ares-control", "layer": "execution", "kind": "service", "repo": "/opt/ares/ares-control",
        "role": "fleet control plane — the only component allowed to restart the others",
        "flow": "service/systemd_control.py + service/daemons.py (category gates, paper vs execution)",
        "probe": {"type": "http", "url": "http://127.0.0.1:8090/"},
    },
    # ---- substrate ---------------------------------------------------------
    {
        "name": "mycelium", "layer": "substrate", "kind": "service", "repo": "/opt/ares/mycelium",
        "role": "trace substrate — every decision and outcome as an append-only trace",
        "flow": "Vantage backend/mycelium_bridge.py -> POST :8811/api/trace (decision + outcome)",
        "probe": {"type": "http", "url": "http://127.0.0.1:8811/"},
    },
    {
        "name": "mycelium-tools", "layer": "substrate", "kind": "library", "repo": "/opt/ares/mycelium-tools",
        "role": "the same substrate as an installable package (storage, a2a, miners, findings)",
        "flow": "imported by Vantage for substrate counts/findings; collector/cycle units not installed",
        "probe": {"type": "import", "module": "mycelium_substrate",
                  "path": "/opt/ares/mycelium-tools/packages/mycelium-substrate"},
    },
    {
        "name": "minipae", "layer": "substrate", "kind": "library", "repo": "/opt/ares/minipae",
        "role": "portable agent memory over Nostr (NIP-AE kind:30174) — the reasoning journal",
        "flow": "would write signed engrams to a relay; none configured",
        "probe": {"type": "minipae"},
    },
    # ---- reputation --------------------------------------------------------
    {
        "name": "Proof", "layer": "reputation", "kind": "library", "repo": "/opt/ares/Proof",
        "role": "agent reputation economy — attested work (kind 30009) -> tier + score",
        "flow": "Vantage backend/proof_bridge.py -> ProofRelay.store_event -> reputations",
        "probe": {"type": "proof"},
    },
    # ---- archived / out of path -------------------------------------------
    {
        "name": "kanban", "layer": "archive", "kind": "archived", "repo": "/opt/ares/kanban",
        "role": "Trading OS work surface (Next.js app, profiles/backtests/deployments)",
        "flow": "out of path: needs its own docker stack (postgres/redis) and duplicates Vantage surfaces",
        "probe": {"type": "path", "path": "/opt/ares/kanban/README.md"},
    },
    {
        "name": "project-12-Cassandra-Oracle", "layer": "archive", "kind": "archived",
        "repo": "/opt/ares/project-12-Cassandra-Oracle",
        "role": "paradigm forecasting / autonomous architect experiment",
        "flow": "out of path: no trading interface, no caller, dockerised only",
        "probe": {"type": "path", "path": "/opt/ares/project-12-Cassandra-Oracle/README.md"},
    },
]

# Datasets that prove the flow is carrying traffic (not just that ports are up).
DATASETS = [
    ("signal_pool", "signals in the pool"),
    ("trading_orders", "orders ever created"),
    ("trading_strategies", "strategies defined"),
    ("trading_wallets", "wallets on file"),
    ("brier_predictions", "predictions scored"),
    ("trading_mode_audit", "mode/arm audit rows"),
    ("live_mode", "master switch rows"),
    ("pipeline_events", "pipeline hops recorded"),
]

_PROBE_CACHE: Dict[str, Tuple[float, Dict[str, Any], float]] = {}
_PROBE_TTL = 8.0


def _http_alive(url: str, timeout: float = 1.2) -> Tuple[bool, str]:
    """Any HTTP answer at all means the listener is up. 4xx is alive."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return True, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ConnectionRefusedError):
            return False, "connection refused (not listening)"
        return False, f"unreachable: {reason}"
    except Exception as e:  # noqa: BLE001 - a probe must never raise
        return False, f"probe error: {type(e).__name__}"


def _unit_active(unit: str) -> Tuple[bool, str]:
    try:
        import subprocess

        out = subprocess.run(["systemctl", "is-active", unit], capture_output=True,
                             text=True, timeout=3).stdout.strip()
        return out == "active", f"unit {out or 'unknown'}"
    except Exception as e:  # noqa: BLE001
        return False, f"unit check failed: {type(e).__name__}"


def _path_exists(path: str) -> Tuple[bool, str]:
    return os.path.exists(path), ("present" if os.path.exists(path) else "missing")


def _import_ok(module: str, path: Optional[str]) -> Tuple[bool, str]:
    import importlib
    import sys

    added = False
    if path and path not in sys.path:
        sys.path.insert(0, path)
        added = True
    try:
        importlib.import_module(module)
        return True, f"importable ({module})"
    except Exception as e:  # noqa: BLE001
        return False, f"import failed: {type(e).__name__}"
    finally:
        if added and path:
            try:
                sys.path.remove(path)
            except ValueError:
                pass


def _minipae_probe() -> Tuple[bool, str]:
    """Live when a signing key exists AND the configured relay accepts TCP."""
    from .minipae_bridge import NSEC_FILE, RELAY, configured

    if not configured():
        return False, f"no signing key at {NSEC_FILE}"
    host, port = "127.0.0.1", 7788
    try:
        rest = RELAY.split("://", 1)[-1]
        hostport = rest.split("/")[0]
        if ":" in hostport:
            host, port = hostport.rsplit(":", 1)
            port = int(port)
        with socket.create_connection((host, port), timeout=1.5):
            return True, f"key present, relay {RELAY} reachable"
    except Exception as e:  # noqa: BLE001
        return False, f"key present but relay {RELAY} unreachable: {type(e).__name__}"


def _proof_probe() -> Tuple[bool, str]:
    """Live when the Proof store exists and holds attestations."""
    import sqlite3 as _sq

    from .proof_bridge import PROOF_DB

    if not os.path.exists(PROOF_DB):
        return False, f"no store at {PROOF_DB} — nothing attested yet"
    try:
        con = _sq.connect(f"file:{PROOF_DB}?mode=ro", uri=True, timeout=2.0)
        try:
            n = con.execute("SELECT COUNT(*) FROM attestations").fetchone()[0]
            reps = con.execute("SELECT COUNT(*) FROM reputations").fetchone()[0]
        finally:
            con.close()
        return n > 0, f"{n} attestations, {reps} reputations in {PROOF_DB}"
    except Exception as e:  # noqa: BLE001
        return False, f"store unreadable: {type(e).__name__}"


def _run_probe(probe: Dict[str, Any]) -> Tuple[bool, str]:
    kind = probe.get("type")
    if kind == "minipae":
        return _minipae_probe()
    if kind == "proof":
        return _proof_probe()
    if kind == "http":
        return _http_alive(probe["url"])
    if kind == "unit":
        return _unit_active(probe["unit"])
    if kind == "path":
        return _path_exists(probe["path"])
    if kind == "import":
        return _import_ok(probe["module"], probe.get("path"))
    return False, f"unknown probe type {kind!r}"


def probe_node(node: Dict[str, Any], use_cache: bool = True) -> Dict[str, Any]:
    """Probe one node, applying the static/observed status rules."""
    name = node["name"]
    now = time.monotonic()
    if use_cache and name in _PROBE_CACHE:
        ts, cached, ttl = _PROBE_CACHE[name]
        if now - ts < ttl:
            return cached

    ok, detail = _run_probe(node["probe"])
    static_kind = node["kind"]

    if node.get("blocked_reason"):
        status = "blocked"
        detail = node["blocked_reason"]
    elif static_kind == "archived":
        status = "archived"
    elif static_kind == "reference":
        status = "reference" if ok else "missing"
    elif ok and static_kind in ("service", "bridge", "library"):
        status = "live"
    elif ok:
        status = "present"
    else:
        status = "dormant"

    result = {
        "name": name, "layer": node["layer"], "kind": static_kind,
        "status": status, "detail": detail, "role": node["role"],
        "flow": node["flow"], "repo": node.get("repo"),
        "repo_present": os.path.isdir(node["repo"]) if node.get("repo") else None,
    }
    # Cache a healthy probed node for a while; re-check a failing one quickly, so
    # a service that comes back (or one that was restarting when first probed)
    # is not reported dead for the rest of the window.
    _PROBE_CACHE[name] = (now, result, _PROBE_TTL if ok else 1.0)
    return result


# ── signal flow ───────────────────────────────────────────────────────────────
# The node probes above answer "is the producer running". They cannot answer
# "is it producing", and a feed that is up but silent looks identical to a feed
# that is working. These queries read the actual rows: per source, how many
# signals landed, how old the newest one is, and whether anything downstream
# ever turned them into an order and a marked outcome.

_SIGNAL_FRESH_S = 3600          # < 1h  : fresh
_SIGNAL_WARM_S = 6 * 3600       # < 6h  : warm
_SIGNAL_STALE_S = 48 * 3600     # < 48h : stale, beyond: dead


def _freshness(age_s: float | None) -> str:
    if age_s is None:
        return "none"
    if age_s < _SIGNAL_FRESH_S:
        return "fresh"
    if age_s < _SIGNAL_WARM_S:
        return "warm"
    if age_s < _SIGNAL_STALE_S:
        return "stale"
    return "dead"



# ── signal promotion ─────────────────────────────────────────────────────────
# 500 signals land in signal_pool per week and (until this existed) none of them
# carried a route into an order: the funnel read 0 attributed orders while every
# source was "fresh". Promotion is the consumer side, and it is deliberately
# explicit -- conviction floor, age ceiling, an owner-supplied size, and it never
# runs unless the caller asks for apply=true.

_BUY_WORDS = ("BUY", "LONG", "BULLISH")
_SELL_WORDS = ("SELL", "SHORT", "BEARISH")
_PROMOTE_SCAN_CAP = 5000  # rows read per call; the window is minutes, not weeks


def promotable_signals(min_conviction: float = 0.75, max_age_min: int = 60,
                       limit: int = 25, symbols: Optional[List[str]] = None,
                       sides: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Recent unconsumed pooled signals, one entry per distinct call, ranked.

    A "call" is (source, symbol, direction, mint): a producer repeating itself
    every 90s is one call, and two tokens that share a ticker are two calls.

    The limit applies to calls, after grouping, and only to calls on an
    actionable side. Limiting raw rows first let one producer's repeats fill
    every slot: four repeats of a 1.0-conviction SOL SELL hid ogun_degen's BUYs
    entirely, and a SELL that paper mode must skip still consumed the slot.
    """
    import sqlite3
    import time as _time

    from .db import DB_PATH

    if not os.path.exists(DB_PATH):
        return []
    now = _time.time()
    since = int(now) - max(1, max_age_min) * 60
    want_sides = {x.lower() for x in (sides or ["buy", "sell"])}
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    try:
        has_promotions = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='signal_promotions'"
        ).fetchone()
        dedupe = ("AND id NOT IN (SELECT signal_id FROM signal_promotions)"
                  if has_promotions else "")
        wanted = [x.strip().upper() for x in (symbols or []) if x.strip()]
        sym_sql = ("AND UPPER(symbol) IN (" + ",".join("?" * len(wanted)) + ")") if wanted else ""
        rows = con.execute(
            f"""SELECT id, symbol, source, type, conviction, direction,
                       COALESCE(mint, '') AS mint, ts
                FROM signal_pool
                WHERE ts >= ? AND conviction >= ?
                  AND direction IS NOT NULL AND direction != ''
                  {sym_sql} {dedupe}
                ORDER BY ts DESC LIMIT ?""",
            (since, min_conviction, *wanted, _PROMOTE_SCAN_CAP)).fetchall()

        calls: Dict[tuple, Dict[str, Any]] = {}
        for row in rows:
            r = dict(row)
            d = (r["direction"] or "").upper()
            side = "buy" if d in _BUY_WORDS else ("sell" if d in _SELL_WORDS else None)
            if side is None or side not in want_sides:
                continue
            key = (r["source"], (r["symbol"] or "").upper(), d, r["mint"])
            c = calls.get(key)
            if c is None:   # rows are newest-first: the first one seen is the latest
                r.update(side=side, repeats=1, group_ids=[r["id"]],
                         age_min=round((now - r["ts"]) / 60.0, 1))
                calls[key] = r
            else:
                c["repeats"] += 1
                c["group_ids"].append(r["id"])
                c["conviction"] = max(c["conviction"], r["conviction"])
        ranked = sorted(calls.values(), key=lambda c: (-c["conviction"], -c["ts"]))
        return ranked[:max(0, limit)]
    finally:
        con.close()


def signal_flow(days: int = 7) -> Dict[str, Any]:
    """Signal -> order -> outcome funnel, per source, from the real rows."""
    import sqlite3
    import time as _time

    from .db import DB_PATH

    out: Dict[str, Any] = {"available": False, "sources": [], "totals": {}}
    if not os.path.exists(DB_PATH):
        out["reason"] = f"no database at {DB_PATH}"
        return out

    now = int(_time.time())
    cutoff = now - days * 86400
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    try:
        signals = {r["source"]: dict(r) for r in con.execute(
            """SELECT source, COUNT(*) AS n_signals, MAX(ts) AS last_ts,
                      SUM(CASE WHEN ts >= ? THEN 1 ELSE 0 END) AS n_window
               FROM signal_pool GROUP BY source""", (cutoff,))}
        # Orders attribute to a source two ways: the explicit signal_id join, and
        # the trigger_reason the producer stamped on the order.
        orders = {r["source"]: dict(r) for r in con.execute(
            """SELECT s.source AS source,
                      COUNT(*) AS n_orders,
                      SUM(CASE WHEN o.status IN ('filled','confirmed') THEN 1 ELSE 0 END) AS n_filled,
                      SUM(CASE WHEN o.status='failed' THEN 1 ELSE 0 END) AS n_failed
               FROM trading_orders o JOIN signal_pool s ON s.id = o.signal_id
               GROUP BY s.source""")}
        by_reason = {r["source"]: dict(r) for r in con.execute(
            """SELECT lower(substr(trigger_reason, 1, instr(trigger_reason || ' ', ' ') - 1)) AS source,
                      COUNT(*) AS n_orders
               FROM trading_orders
               WHERE trigger_reason IS NOT NULL AND trigger_reason != ''
               GROUP BY source""")}
        perf = {(r["source"], r["window"]): dict(r) for r in con.execute(
            "SELECT source, window, n_trades, wins, avg_pnl_pct, n_suspect FROM source_performance"
        )} if con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_performance'"
        ).fetchone() else {}

        names = sorted(set(signals) | set(orders), key=lambda n: -(signals.get(n, {}).get("n_signals") or 0))
        rows = []
        for name in names:
            sig = signals.get(name, {})
            age = (now - sig["last_ts"]) if sig.get("last_ts") else None
            orders_row = orders.get(name, {})
            perf_row = perf.get((name, "24h")) or perf.get((name, "1h")) or {}
            rows.append({
                "source": name,
                "signals_total": sig.get("n_signals", 0),
                "signals_window": sig.get("n_window", 0),
                "last_signal_age_s": age,
                "freshness": _freshness(age),
                "orders_from_signal_id": orders_row.get("n_orders", 0),
                "orders_from_trigger": (by_reason.get(name, {}) or {}).get("n_orders", 0),
                "filled": orders_row.get("n_filled", 0),
                "failed": orders_row.get("n_failed", 0),
                "marked_trades": perf_row.get("n_trades", 0),
                "avg_pnl_pct": perf_row.get("avg_pnl_pct"),
            })
        window_rows = [r for r in rows if r["signals_window"]]
        out.update({
            "available": True,
            "window_days": days,
            "sources": rows,
            "totals": {
                "sources": len(rows),
                "fresh": sum(1 for r in rows if r["freshness"] == "fresh"),
                "dead": sum(1 for r in rows if r["freshness"] == "dead"),
                "signals_window": sum(r["signals_window"] for r in rows),
                "sources_in_window": len(window_rows),
                "orders_attributed": sum(r["orders_from_signal_id"] for r in rows),
            },
            "note": "freshness is read from signal_pool.ts, not from the producer's health",
        })
        return out
    finally:
        con.close()

def status() -> Dict[str, Any]:
    nodes = [probe_node(n) for n in NODES]
    by_layer: Dict[str, List[str]] = {}
    for n in nodes:
        by_layer.setdefault(n["layer"], []).append(f"{n['name']}:{n['status']}")
    counts = {s: sum(1 for n in nodes if n["status"] == s)
              for s in ("live", "available", "present", "dormant", "blocked", "reference", "archived", "missing")}
    return {
        "nodes": nodes,
        "by_layer": by_layer,
        "counts": {k: v for k, v in counts.items() if v},
        "datasets": _dataset_counts(),
        "signals": signal_flow(),
    }


def _dataset_counts() -> List[Dict[str, Any]]:
    from .db import DB_PATH

    out = []
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2.0)
    except Exception:
        return out
    try:
        for table, label in DATASETS:
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                out.append({"table": table, "label": label, "rows": n})
            except Exception:
                out.append({"table": table, "label": label, "rows": None})
    finally:
        con.close()
    return out


# ---------------------------------------------------------------------------
# The event log — every hop that matters
# ---------------------------------------------------------------------------

async def record_event(db, node: str, kind: str, ref: str = "", detail: Any = None) -> None:
    """Append one hop. Never raises: telemetry must not break a trade."""
    try:
        payload = json.dumps(detail, default=str)[:2000] if detail is not None else ""
        await db.execute(
            "INSERT INTO pipeline_events (node, kind, ref, detail) VALUES (?,?,?,?)",
            (node, kind[:60], (ref or "")[:200], payload),
        )
    except Exception:
        return None


async def recent_events(db, limit: int = 50) -> List[Dict[str, Any]]:
    cur = await db.execute(
        "SELECT id, ts, node, kind, ref, detail FROM pipeline_events ORDER BY id DESC LIMIT ?",
        (limit,))
    rows = await cur.fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["detail"] = json.loads(d["detail"]) if d.get("detail") else None
        except Exception:
            pass
        out.append(d)
    return out


async def events_by_node(db) -> Dict[str, Dict[str, Any]]:
    """Per-node last hop + count, for the status page."""
    cur = await db.execute(
        """SELECT node, COUNT(*) AS n, MAX(ts) AS last_ts
           FROM pipeline_events GROUP BY node""")
    rows = await cur.fetchall()
    return {r["node"]: {"events": r["n"], "last_ts": r["last_ts"]} for r in rows}

def stale_processes(root: str = "/opt/ares") -> Dict[str, Any]:
    """Running Python processes executing code older than what is on disk.

    A fix can be committed, merged and deployed while a long-lived daemon keeps
    the old module in memory: the trade-outcome learner ran for a day on its
    pre-fix code and kept rewriting the scoreboard with probe rows that the
    on-disk version excluded. Restarting the API does not touch it.

    A process is stale when its own script, or a backend module it imports,
    was modified after the process started. Imports are read statically from
    the script (and followed one level into backend/), so a module pulled in
    only transitively deeper than that is not seen: "not stale" means "nothing
    it visibly imports changed", not a proof.
    """
    out: Dict[str, Any] = {"available": False, "stale": [], "checked": 0}
    try:
        btime = next(int(l.split()[1]) for l in open("/proc/stat") if l.startswith("btime"))
        hz = os.sysconf("SC_CLK_TCK")
    except Exception as e:  # not Linux /proc
        out["reason"] = f"no /proc: {e}"
        return out
    vantage = os.path.join(root, "Vantage")
    backend_dir = os.path.join(vantage, "backend")
    import re as _re
    _imp = _re.compile(r"^\s*(?:from\s+(?:backend)?\.*([\w.]+)\s+import|import\s+backend\.([\w.]+))", _re.M)

    def _backend_imports(path: str, depth: int = 2, seen=None) -> set:
        seen = set() if seen is None else seen
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            return seen
        for a, b in _imp.findall(src):
            mod = (a or b).split(".")
            cand = os.path.join(backend_dir, *mod) + ".py"
            if not os.path.exists(cand):
                cand = os.path.join(backend_dir, *mod, "__init__.py")
            if os.path.exists(cand) and cand not in seen:
                seen.add(cand)
                if depth > 1:
                    _backend_imports(cand, depth - 1, seen)
        return seen
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            argv = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
            argv = [a.decode(errors="replace") for a in argv if a]
            if not argv or "python" not in os.path.basename(argv[0]):
                continue
            cwd = os.readlink(f"/proc/{pid}/cwd")
            script = next((a for a in argv[1:] if a.endswith(".py")), None)
            module = argv[argv.index("-m") + 1] if "-m" in argv[:-1] else None
            if script and not os.path.isabs(script):
                script = os.path.join(cwd, script)
            home = script or cwd
            if not home.startswith(root):
                continue
            stat = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
            started = btime + int(stat[19]) / hz
        except (OSError, ValueError, IndexError):
            continue
        out["checked"] += 1
        newest, why = 0.0, ""
        files = []
        if script and os.path.exists(script):
            files.append(script)
            if script.startswith(vantage):
                files += sorted(_backend_imports(script))
        elif module and home.startswith(vantage):
            mod_path = os.path.join(vantage, *module.split(".")) + ".py"
            if os.path.exists(mod_path):
                files.append(mod_path)
                files += sorted(_backend_imports(mod_path))
        changed = [f for f in files if os.path.getmtime(f) > started + 1]
        if changed:
            newest = max(os.path.getmtime(f) for f in changed)
            why = ", ".join(os.path.relpath(f, vantage if f.startswith(vantage) else root)
                            for f in changed[:4]) + (" …" if len(changed) > 4 else "")
        if newest > started + 1:
            out["stale"].append({
                "pid": int(pid),
                "process": script and os.path.relpath(script, root) or f"-m {module}",
                "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
                "code_changed": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(newest)),
                "changed": why,
                "behind_s": int(newest - started),
            })
    out["stale"].sort(key=lambda x: -x["behind_s"])
    out["available"] = True
    return out
