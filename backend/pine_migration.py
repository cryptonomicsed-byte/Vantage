"""Which saved Pine indicators can this engine actually run?

# Why this exists

Agents have been saving Pine into `pine_indicators` since before there was any
gate on it, and the sandbox implements a subset of Pine. So some unknown number
of stored scripts cannot run, and nobody finds out until an agent tries.

This walks the table, puts every script through the native validator, and
reports what is blocking each one.

# The output that matters

Not the per-script list — the **ranking by missing function**. A blocked script
tells you one script is broken; "`ta.percentrank` blocks 12 scripts" tells you
what to implement next. Migration stops being an open-ended chore and becomes a
finite, ordered queue.

Nothing here writes. It reads the table and reports, so it is safe to run
against production.
"""
from __future__ import annotations

import logging
import re
from collections import Counter

import aiosqlite

from backend import pine_validate as pv
from backend.db import get_db

logger = logging.getLogger(__name__)

# The engine's two "I don't have that" errors. Anything else is a genuine
# syntax or structural problem in the script rather than a coverage gap.
_MISSING = re.compile(r"Unknown (?:function|identifier): ([A-Za-z_][A-Za-z0-9_.]*)")


def missing_symbol(message: str) -> str | None:
    """The symbol the engine did not recognise, or `None`.

    `None` matters: it separates "we have not implemented this" from "this
    script is broken". Only the first is migration work; counting the second
    toward a coverage queue would send someone implementing a typo.
    """
    m = _MISSING.search(message or "")
    return m.group(1) if m else None


async def audit_saved_indicators(limit: int = 5000) -> dict:
    """Validate every stored indicator against the native engine.

    Returns a report with the blocking-function ranking first, because that is
    the part that decides what to do next.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT id, agent_id, name, script FROM pine_indicators "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )).fetchall()

    runnable: list[dict] = []
    blocked: list[dict] = []
    unknown_verdict: list[dict] = []
    missing_counts: Counter = Counter()
    missing_examples: dict[str, list[int]] = {}

    for row in rows:
        verdict = await pv.validate_native(row["script"] or "")

        if verdict.get("status") != "ok":
            # No verdict — the sidecar is down or the row is empty. Reporting
            # these as blocked would invent migration work out of an outage.
            unknown_verdict.append({
                "id": row["id"], "name": row["name"],
                "reason": verdict.get("reason", "no verdict"),
            })
            continue

        if verdict.get("valid"):
            runnable.append({"id": row["id"], "name": row["name"]})
            continue

        err = (verdict.get("errors") or [{}])[0]
        sym = missing_symbol(err.get("message", ""))
        blocked.append({
            "id": row["id"],
            "agent_id": row["agent_id"],
            "name": row["name"],
            "line": err.get("line"),
            "message": err.get("message"),
            "source": err.get("source"),
            "missing": sym,
        })
        if sym:
            missing_counts[sym] += 1
            missing_examples.setdefault(sym, []).append(row["id"])

    return {
        # Implement these, in this order, and the counts say what each unblocks.
        "blocking_functions": [
            {"symbol": sym, "blocks": n, "example_ids": missing_examples[sym][:5]}
            for sym, n in missing_counts.most_common()
        ],
        "summary": {
            "total": len(rows),
            "runnable": len(runnable),
            "blocked": len(blocked),
            "no_verdict": len(unknown_verdict),
            # Scripts blocked by something that is not a missing symbol: real
            # breakage in the script, not a coverage gap.
            "blocked_by_script_errors": sum(1 for b in blocked if not b["missing"]),
        },
        "blocked": blocked,
        "no_verdict": unknown_verdict,
    }


def format_report(report: dict) -> str:
    """Render the audit for a terminal."""
    s = report["summary"]
    out = [
        "Pine indicator migration audit",
        "=" * 46,
        f"  stored     {s['total']}",
        f"  runnable   {s['runnable']}",
        f"  blocked    {s['blocked']}",
        f"  no verdict {s['no_verdict']}",
        "",
    ]
    if report["blocking_functions"]:
        out.append("Missing functions, by how many scripts each unblocks:")
        for row in report["blocking_functions"]:
            out.append(f"  {row['blocks']:>4}  {row['symbol']}")
        out.append("")
    broken = s["blocked_by_script_errors"]
    if broken:
        out.append(f"{broken} script(s) blocked by errors in the script itself, not coverage:")
        for b in report["blocked"]:
            if not b["missing"]:
                out.append(f"  #{b['id']} {b['name']!r} line {b['line']}: {b['message']}")
        out.append("")
    if s["no_verdict"]:
        out.append(f"{s['no_verdict']} script(s) could not be checked "
                   f"(is the pine-runtime sidecar up?)")
    return "\n".join(out)


async def _main() -> int:
    report = await audit_saved_indicators()
    print(format_report(report))
    return 0


if __name__ == "__main__":
    import asyncio
    raise SystemExit(asyncio.run(_main()))
