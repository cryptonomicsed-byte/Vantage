"""Ares Council API — read-only views into the council DB + Mycelium substrate.

Endpoints (all require X-Agent-Key):
  GET /api/council/overview     — daemon health, verdict count, pool stats
  GET /api/council/verdicts     — last 25 verdicts with member votes
  GET /api/council/calibration  — per-persona win rate + effective weights
  GET /api/council/substrate    — mycelium traces + council findings
"""
import json, os, sqlite3, urllib.request
from fastapi import APIRouter, Depends
from backend.deps import get_agent

router = APIRouter(prefix="/api/council", tags=["council"])

COUNCIL_DB = "/opt/ares/ares_council/council.db"
MYCELIUM = "http://127.0.0.1:8811"

PERSONAS = [
    ("analyst", "Macro & fundamentals", 0.20, False),
    ("technician", "Price action & TA", 0.20, False),
    ("degen", "Momentum & meme hunter", 0.15, False),
    ("contrarian", "Devil's advocate (dissent x2)", 0.20, False),
    ("risk_officer", "Quant — VETO authority", 0.15, True),
    ("historian", "Institutional memory", 0.10, False),
]


def q(sql, args=()):
    try:
        conn = sqlite3.connect(f"file:{COUNCIL_DB}?mode=ro", uri=True, timeout=3)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, args).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        return {"error": str(e)}


def my_get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


@router.get("/overview")
async def council_overview(agent: dict = Depends(get_agent)):
    out = {"daemon_pid": None, "daemon_running": False, "verdict_count": 0,
           "trace_buffer_pending": 0, "mycelium": None, "signal_pool": None}
    try:
        with open("/tmp/ares_council.pid") as f:
            pid = f.read().strip()
        out["daemon_pid"] = pid
        out["daemon_running"] = os.path.exists(f"/proc/{pid}")
    except Exception:
        pass
    c = q("SELECT COUNT(*) n FROM verdicts")
    out["verdict_count"] = c.get("n") if isinstance(c, dict) else c[0]["n"]
    b = q("SELECT COUNT(*) n FROM trace_buffer WHERE sent_at IS NULL")
    out["trace_buffer_pending"] = b.get("n") if isinstance(b, dict) else b[0]["n"]
    out["mycelium"] = my_get(f"{MYCELIUM}/api/status")
    return out


@router.get("/verdicts")
async def council_verdicts(agent: dict = Depends(get_agent), limit: int = 25):
    rows = q("""SELECT v.id, v.symbol, v.direction, v.conviction, v.entry_price,
                       v.outcome, v.paper, v.posted_at, d.cycle_ts
                FROM verdicts v JOIN debates d ON d.id = v.debate_id
                ORDER BY v.id DESC LIMIT ?""", (limit,))
    if isinstance(rows, dict):
        return rows
    votes = q("""SELECT debate_id, persona, direction, confidence, weight, rationale
                 FROM member_votes WHERE round=2 ORDER BY debate_id DESC, persona""")
    by = {}
    if isinstance(votes, list):
        for v in votes:
            by.setdefault(v["debate_id"], []).append(v)
    for r in rows:
        r["votes"] = by.get(r["id"], [])
    return rows


@router.get("/calibration")
async def council_calibration(agent: dict = Depends(get_agent)):
    c = q("SELECT persona, correct, total, updated_at FROM calibration")
    if isinstance(c, dict):
        return c
    lookup = {x["persona"]: x for x in c}
    out = []
    for name, role, base, veto in PERSONAS:
        row = lookup.get(name, {})
        correct = row.get("correct", 0); total = row.get("total", 0)
        rate = (correct / total) if total else None
        mult = max(0.2, min(2.0, rate / 0.5)) if rate is not None else 1.0
        out.append({"persona": name, "role": role, "base_weight": base, "veto": veto,
                    "correct": correct, "total": total,
                    "rate": round(rate * 100, 1) if rate is not None else None,
                    "multiplier": round(mult, 2),
                    "eff_weight": round(base * mult, 3),
                    "updated": row.get("updated_at", "")})
    return out


@router.get("/substrate")
async def council_substrate(agent: dict = Depends(get_agent)):
    my = my_get(f"{MYCELIUM}/api/status")
    traces = my_get(f"{MYCELIUM}/api/traces?agent=council&limit=100")
    findings = my_get(f"{MYCELIUM}/api/findings?limit=50")
    out = {"mycelium": my, "council_traces": [], "findings": []}
    if isinstance(traces, dict):
        t = traces.get("traces", traces.get("data", []))
        if isinstance(t, list):
            out["council_traces"] = t[:100]
    if isinstance(findings, dict):
        f = findings.get("findings", findings.get("data", []))
        if isinstance(f, list):
            out["findings"] = [x for x in f if "council" in str(x.get("payload", "")).lower()
                               or "council" in str(x.get("title", "")).lower()]
    return out
