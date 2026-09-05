"""Turn a JSONL tool-call trace into engram content.

# Where the format comes from

Moon Dev's `tool_log.py` writes one JSON line per tool call — timestamp, tool,
args, result, duration — so a session can be grepped and replayed. That is
almost exactly the shape an engram wants: already structured, already stamped,
one record per thing that happened.

This module is the bridge. It reads that trace and produces the payload half of
a `kind:30174` engram, leaving signing to whoever holds the agent's key
(IfáScript's `engram_with_slug`, or minipae's `sign_event`).

# The two things it refuses to do

**It does not invent a timestamp.** A record with no `ts` is dropped rather
than stamped with the read time. An engram's whole value is being a record of
when something happened; substituting now() would turn a gap in the trace into
a confident lie about it.

**It does not carry results verbatim.** A tool result can be megabytes and can
contain anything the tool touched — keys, balances, another agent's data.
Engrams are published, so results are reduced to a shape and a size. A caller
that genuinely needs the payload should reference the trace file, not inline it.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Iterator, Optional

logger = logging.getLogger(__name__)

# A result summary never exceeds this; longer ones are described, not included.
MAX_RESULT_CHARS = 200


def parse_trace(lines: Iterable[str]) -> Iterator[dict]:
    """Yield well-formed trace records, skipping the rest.

    A trace is append-only and often truncated mid-write, so a malformed final
    line is normal rather than exceptional. One bad line must not cost the
    whole session.
    """
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            logger.debug("trace line %d is not JSON, skipping", i + 1)
            continue
        if not isinstance(rec, dict):
            continue
        if not isinstance(rec.get("tool"), str) or not rec["tool"]:
            continue
        ts = rec.get("ts")
        if not isinstance(ts, (int, float)):
            # No timestamp, no record. See the module note.
            logger.debug("trace line %d has no usable ts, skipping", i + 1)
            continue
        yield rec


def summarize_result(result: Any) -> dict:
    """Describe a tool result without reproducing it.

    Returns its type, and a short rendering only when that rendering is small
    enough to be safe to publish.
    """
    if result is None:
        return {"type": "none"}
    if isinstance(result, bool):
        return {"type": "bool", "value": result}
    if isinstance(result, (int, float)):
        return {"type": "number", "value": result}
    if isinstance(result, dict):
        out: dict = {"type": "object", "keys": sorted(result.keys())[:20]}
        # A status field is the one thing worth lifting: it is what a reader
        # scanning a trace is almost always looking for.
        if isinstance(result.get("status"), str):
            out["status"] = result["status"]
        return out
    if isinstance(result, list):
        return {"type": "array", "length": len(result)}

    text = str(result)
    if len(text) <= MAX_RESULT_CHARS:
        return {"type": "text", "value": text}
    return {"type": "text", "length": len(text), "truncated": True}


def to_engram(records: Iterable[dict], *, agent: str, session: str) -> dict:
    """Build engram content from a run of trace records.

    The content is a summary plus per-call entries. Counts and total duration
    come from the records actually kept, so a trace with dropped lines reports
    what was read rather than what the file claimed to hold.
    """
    calls = []
    total_ms = 0.0
    tools: dict[str, int] = {}
    failures = 0

    for rec in records:
        dur = rec.get("duration_ms")
        dur = float(dur) if isinstance(dur, (int, float)) else 0.0
        total_ms += dur
        tool = rec["tool"]
        tools[tool] = tools.get(tool, 0) + 1

        summary = summarize_result(rec.get("result"))
        if summary.get("status") == "error":
            failures += 1

        calls.append({
            "ts": rec["ts"],
            "tool": tool,
            "duration_ms": round(dur, 2),
            # Argument *names* only. The values are the caller's business and
            # routinely carry things that must not be published.
            "arg_keys": sorted(rec["args"].keys()) if isinstance(rec.get("args"), dict) else [],
            "result": summary,
        })

    calls.sort(key=lambda c: c["ts"])
    return {
        "agent": agent,
        "session": session,
        "calls": calls,
        "summary": {
            "count": len(calls),
            "failures": failures,
            "total_ms": round(total_ms, 2),
            "tools": dict(sorted(tools.items(), key=lambda kv: (-kv[1], kv[0]))),
            "first_ts": calls[0]["ts"] if calls else None,
            "last_ts": calls[-1]["ts"] if calls else None,
        },
    }


def engram_slug(agent: str, session: str) -> Optional[str]:
    """Address for a session's trace engram, in minipae's grammar.

    `None` when nothing survives normalization, so a caller fails here rather
    than building an address that only breaks at the relay.
    """
    def fold(s: str) -> str:
        out = "".join(c if (c.isascii() and (c.isalnum() or c in "_-")) else "-" for c in s.lower())
        return "-".join(p for p in out.split("-") if p)[:64]

    a, s = fold(agent), fold(session)
    if not a or not s or not (a[0].isalnum() or a[0] == "_"):
        return None
    if not (s[0].isalnum() or s[0] == "_"):
        return None
    return f"mem/trace/{a}/{s}"


def from_file(path: str, *, agent: str, session: str) -> dict:
    """Read a JSONL trace file and build its engram content."""
    with open(path, encoding="utf-8") as f:
        return to_engram(parse_trace(f), agent=agent, session=session)
