"""P4-15 -- TradingOS challenger in shadow.

Every CHALLENGER_POLL_S (300 s), the live promotable calls (the exact list
the live path ranks: pipeline.promotable_signals()) are sent, once per call,
to TradingOS's intelligence orchestrator POST /challenge, where adversarial
challengers (liquidity, security, manipulation, ...) argue against them.

Shadow only: the answer is logged in tradingos_challenges next to the
call's live rank and is never read by ranking, the gate, sizing or
execution. "Would have vetoed" = any challenger reported severity high or
critical; the walk-forward report (P4-16) later scores those vetoes against
the resolver's outcomes.

Config: TRADINGOS_INTEL_URL (no default -- TradingOS is not running on the
box; unset means idle and says so), TRADINGOS_CHALLENGER_ENABLED (off),
TRADINGOS_CHALLENGE_TYPES (liquidity,security,manipulation).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

POLL_S = int(os.environ.get("CHALLENGER_POLL_S", "300"))
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
VETO_AT = 3

DDL = (
    """CREATE TABLE IF NOT EXISTS tradingos_challenges (
  call_key TEXT PRIMARY KEY, source TEXT, symbol TEXT, mint TEXT, direction TEXT,
  conviction REAL, live_rank INTEGER, call_ts INTEGER,
  status TEXT NOT NULL, max_severity TEXT, conviction_impact REAL, would_veto INTEGER,
  challengers INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0,
  summary TEXT, challenged_at INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_toc_ts ON tradingos_challenges(challenged_at)",
)


def enabled() -> bool:
    return os.environ.get("TRADINGOS_CHALLENGER_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def base_url() -> Optional[str]:
    u = os.environ.get("TRADINGOS_INTEL_URL", "").strip().rstrip("/")
    return u or None


def call_key(c: Dict[str, Any]) -> str:
    raw = f"{c.get('source')}|{c.get('symbol')}|{c.get('direction')}|{c.get('mint') or ''}|{c.get('ts')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _walk(obj: Any, out: List[dict]) -> None:
    if isinstance(obj, dict):
        if "severity" in obj and ("conviction_impact" in obj or "arguments" in obj):
            out.append(obj)
        for v in obj.values():
            _walk(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, out)
    elif isinstance(obj, str) and '"severity"' in obj:
        try:
            _walk(json.loads(obj), out)
        except ValueError:
            pass


def summarize(resp: Dict[str, Any]) -> Dict[str, Any]:
    """Pull every submitted challenge (severity, conviction_impact, arguments)
    out of the orchestrator's free-form agent results."""
    found: List[dict] = []
    for ch in resp.get("challenges") or []:
        _walk(ch, found)
    sev = [str(f.get("severity", "")).lower() for f in found if str(f.get("severity", "")).lower() in SEVERITY_RANK]
    impacts = [float(f["conviction_impact"]) for f in found if isinstance(f.get("conviction_impact"), (int, float))]
    top = max(sev, key=lambda s: SEVERITY_RANK[s]) if sev else None
    return {"challengers": len(resp.get("challenges") or []), "errors": len(resp.get("errors") or []),
            "findings": len(found), "max_severity": top,
            "conviction_impact": round(sum(impacts), 4) if impacts else None,
            "would_veto": bool(top and SEVERITY_RANK[top] >= VETO_AT),
            "arguments": [a for f in found for a in (f.get("arguments") or [])][:12]}


def _post(url: str, body: dict, timeout: float = 120.0) -> Dict[str, Any]:
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 -- operator-configured URL
        return json.loads(r.read() or b"{}")


async def challenge_once(calls: Optional[List[Dict[str, Any]]] = None,
                         post: Callable[[str, dict], Dict[str, Any]] = _post,
                         now: Optional[int] = None) -> Dict[str, Any]:
    """One shadow pass. Never raises; never writes anything the live path reads."""
    stats: Dict[str, Any] = {"challenged": 0, "already": 0, "failed": 0, "status": "ok"}
    url = base_url()
    if not url:
        stats["status"] = "not_configured"
        return stats
    if calls is None:
        from backend.pipeline import promotable_signals
        calls = await asyncio.to_thread(promotable_signals)
    now = int(now or time.time())
    types = [t.strip() for t in os.environ.get("TRADINGOS_CHALLENGE_TYPES",
                                               "liquidity,security,manipulation").split(",") if t.strip()]
    from backend.db import get_db
    try:
        async with get_db() as db:
            for d in DDL:
                await db.execute(d)
            for rank, c in enumerate(calls, 1):
                key = call_key(c)
                if await (await db.execute("SELECT 1 FROM tradingos_challenges WHERE call_key=?", (key,))).fetchone():
                    stats["already"] += 1
                    continue
                signal = {"asset_symbol": c.get("symbol"), "mint": c.get("mint"), "direction": c.get("direction"),
                          "conviction": c.get("conviction"), "source": c.get("source"),
                          "detail": c.get("detail"), "ts": c.get("ts")}
                try:
                    resp = await asyncio.to_thread(post, f"{url}/challenge",
                                                   {"signal_id": key, "signal_data": signal, "challenge_types": types})
                    s = summarize(resp)
                    status = "challenged" if s["challengers"] else "empty"
                    stats["challenged"] += 1
                except Exception as e:  # noqa: BLE001 -- recorded, retried never (one try per call)
                    s = {"challengers": 0, "errors": 1, "max_severity": None, "conviction_impact": None,
                         "would_veto": None, "arguments": [], "error": str(e)[:200]}
                    status = "failed"
                    stats["failed"] += 1
                await db.execute(
                    "INSERT OR IGNORE INTO tradingos_challenges (call_key, source, symbol, mint, direction, "
                    "conviction, live_rank, call_ts, status, max_severity, conviction_impact, would_veto, "
                    "challengers, errors, summary, challenged_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, c.get("source"), c.get("symbol"), c.get("mint"), c.get("direction"), c.get("conviction"),
                     rank, c.get("ts"), status, s.get("max_severity"), s.get("conviction_impact"),
                     None if s.get("would_veto") is None else int(bool(s["would_veto"])), s["challengers"],
                     s["errors"], json.dumps(s), now))
                await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("tradingos challenger pass failed (fail-open): %s", e)
        stats["status"] = "error"
        stats["error"] = str(e)[:200]
    return stats


async def challenger_loop() -> None:
    while True:
        await asyncio.sleep(POLL_S)
        if not enabled():
            continue
        out = await challenge_once()
        if out["challenged"] or out["failed"]:
            logger.info("tradingos challenger (shadow): %s", out)


async def recent(limit: int = 50) -> Dict[str, Any]:
    from backend.db import get_db
    async with get_db() as db:
        for d in DDL:
            await db.execute(d)
        cols = ["call_key", "source", "symbol", "mint", "direction", "conviction", "live_rank", "status",
                "max_severity", "conviction_impact", "would_veto", "challengers", "challenged_at"]
        rows = [dict(zip(cols, r)) for r in await (await db.execute(
            f"SELECT {', '.join(cols)} FROM tradingos_challenges ORDER BY challenged_at DESC LIMIT ?",
            (limit,))).fetchall()]
        tot = await (await db.execute(
            "SELECT COUNT(*), SUM(would_veto=1), SUM(status='failed') FROM tradingos_challenges")).fetchone()
    return {"enabled": enabled(), "configured": base_url() is not None, "shadow_only": True,
            "total": tot[0] or 0, "would_veto": tot[1] or 0, "failed": tot[2] or 0, "rows": rows}
