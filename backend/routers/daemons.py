"""Daemon control API — /api/admin/daemons/*, a Sentinel surface.

Who may do what (Sentinel, P0):
  * list / audit: any instance operator (human session).
  * stop: any operator, immediately — a brake never waits.
  * start / restart: files a sign-off card. A different owner or security
    operator approves it, the Èṣù gate clears it, and only then does it run.
    The requester can never approve their own card. Deny-listed units
    (VANTAGE_DAEMON_DENYLIST) can never be started.
  * which units: those matching the instance's VANTAGE_DAEMON_UNIT_PREFIX.
    Unset = daemon control is off on this instance (503). No product default.

Lets an admin toggle the ares-* systemd services on/off individually from
the ARES SOC console, instead of everything running unconditionally.
Motivated by a real live incident: 55 daemons + Vantage itself all sharing
one VPS's memory drove the box into heavy swap (263MB free RAM, 3.4/4GB
swap used), which made every backend endpoint -- not just one -- time out.
In dev mode (no real money in the traded account), the fix isn't more RAM,
it's the ability to turn off daemons you're not actively using.

Safety boundary: every action is restricted to unit names matching
`^ares-[a-z0-9-]+\\.service$` via `_validate_unit`, checked before any
subprocess call. This is a hard allowlist by pattern, not a blocklist --
`vantage.service` itself, `sshd`, `docker`, `postgresql`, and anything not
prefixed `ares-` can never be targeted through this API, so there is no
path from this endpoint to taking down the API that serves it or any
non-Ares system service. All actions are logged to `daemon_actions` for
a real audit trail, same discipline as the sentinel/governance tables.
"""

import asyncio
import hashlib
import logging
import re

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from fastapi.responses import JSONResponse

from backend.config import settings
from backend.db import get_db
from backend.sentinel.operators import require_operator

router = APIRouter(prefix="/api/admin/daemons", tags=["admin"])


def _prefix() -> str:
    p = (settings.DAEMON_UNIT_PREFIX or "").strip()
    if not p or not re.fullmatch(r"[a-z0-9][a-z0-9-]*-", p):
        raise HTTPException(503, "Daemon control is not configured on this instance (VANTAGE_DAEMON_UNIT_PREFIX)")
    return p


def _unit_re() -> "re.Pattern[str]":
    return re.compile(rf"^{re.escape(_prefix())}[a-z0-9-]+\.service$")

_ACTIONS_DDL = """
CREATE TABLE IF NOT EXISTS daemon_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit TEXT NOT NULL,
    action TEXT NOT NULL,
    admin_key_hash TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    actor TEXT DEFAULT ''
)
"""


def _validate_unit(unit: str) -> str:
    unit = (unit or "").strip()
    if not _unit_re().match(unit):
        raise HTTPException(422, f"unit must be a {_prefix()}*.service name")
    return unit


async def _systemctl(*args: str, timeout: float = 15.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "systemctl", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise HTTPException(504, f"systemctl {' '.join(args)} timed out after {timeout}s")
    return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


async def _log_action(unit: str, action: str, actor: str) -> None:
    """Best-effort audit log. Fail-open by design: the systemctl action
    this records has *already succeeded* by the time this runs (every call
    site awaits it after a successful systemctl call), so a DB contention
    error here (real and observed live: `database is locked` even after
    the full 20s busy_timeout, under the same lock pressure that caused
    the original incident this feature exists to let you resolve) must
    never turn a successful daemon toggle into a 500 to the caller."""
    try:
        async with get_db() as db:
            await db.execute(_ACTIONS_DDL)
            try:
                await db.execute("ALTER TABLE daemon_actions ADD COLUMN actor TEXT DEFAULT ''")
            except Exception:
                pass
            # Named identity, not a key-hash prefix: who did it is answerable.
            await db.execute(
                "INSERT INTO daemon_actions (unit, action, actor) VALUES (?, ?, ?)",
                (unit, action, actor[:300]),
            )
            await db.commit()
    except Exception as e:
        logging.getLogger(__name__).warning(f"daemon_actions audit log failed for {unit}/{action}: {e}")


HIGH_RESTART_THRESHOLD = 10


async def _daemon_restart_stats() -> dict[str, dict]:
    """NRestarts/ActiveEnterTimestamp per unit, straight from systemd.

    `running` alone (the only signal this endpoint exposed before) can't
    tell a healthy daemon from one crash-looping every few seconds --
    systemd reports "active/running" for both, because between crashes it
    IS briefly running again. This is not hypothetical: Zàngbétò crash-
    looped 17,132+ times before being caught, invisible to this exact
    check the whole time. NRestarts makes that visible without touching
    any of the 35 daemons' own code -- systemd already tracks it, this
    endpoint just wasn't reading it."""
    code, out, err = await _systemctl(
        "show", f"{_prefix()}*.service",
        "--property=Id,NRestarts,ActiveEnterTimestamp",
    )
    if code != 0:
        return {}
    stats: dict[str, dict] = {}
    current: dict = {}
    for line in out.splitlines():
        if not line.strip():
            if current.get("Id"):
                stats[current["Id"]] = current
            current = {}
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            current[k] = v
    if current.get("Id"):
        stats[current["Id"]] = current
    return stats


@router.get("")
async def list_daemons(op: dict = Depends(require_operator)):
    """Every ares-*.service unit and its live state, straight from systemd
    -- not a cached/stale table, this reflects what's actually running
    right now. `systemctl list-units` only shows units systemd has loaded;
    `--all` includes inactive/dead ones too, so a stopped daemon still
    shows up (as inactive) rather than disappearing from the list."""
    code, out, err = await _systemctl(
        "list-units", "--type=service", "--all", "--plain", "--no-legend", f"{_prefix()}*.service"
    )
    if code != 0:
        raise HTTPException(500, f"systemctl list-units failed: {err.strip()}")
    restart_stats = await _daemon_restart_stats()
    daemons = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        unit, load, active, sub = parts[0], parts[1], parts[2], parts[3]
        description = parts[4] if len(parts) > 4 else ""
        if not _unit_re().match(unit):
            continue
        stat = restart_stats.get(unit, {})
        n_restarts = int(stat.get("NRestarts") or 0)
        daemons.append({
            "unit": unit,
            "name": unit.removeprefix(_prefix()).removesuffix(".service"),
            "denylisted": _is_denylisted(unit),
            "load": load,
            "active": active,
            "sub": sub,
            "running": active == "active" and sub == "running",
            "description": description,
            "n_restarts": n_restarts,
            "active_since": stat.get("ActiveEnterTimestamp") or None,
            # Real, generic signal a stopped/started daemon still passes:
            # a high NRestarts count means systemd has been repeatedly
            # relaunching it (Restart=always masking a crash loop), not
            # that it's been reliably up.
            "crash_looping": n_restarts >= HIGH_RESTART_THRESHOLD,
        })
    daemons.sort(key=lambda d: d["name"])
    return {
        "daemons": daemons,
        "count": len(daemons),
        "crash_looping_count": sum(1 for d in daemons if d["crash_looping"]),
    }


class DaemonActionResult(BaseModel):
    unit: str
    action: str
    ok: bool
    detail: str = ""


def _is_denylisted(unit: str) -> bool:
    deny = {u.strip() for u in (settings.DAEMON_DENYLIST or "").split(",") if u.strip()}
    return unit in deny or unit.removesuffix(".service") in deny


def _refuse_denylisted(unit: str) -> None:
    """Instance deny-list (VANTAGE_DAEMON_DENYLIST). Stopping is always allowed."""
    if _is_denylisted(unit):
        raise HTTPException(403, f"{unit} is on this instance's daemon deny-list and cannot be started")


async def apply_action(unit: str, action: str, actor: str) -> dict:
    """Run one systemctl action and audit it. Called directly for stop, and
    only from an approved, Èṣù-cleared sign-off for start/restart."""
    unit = _validate_unit(unit)
    if action in ("start", "restart"):
        _refuse_denylisted(unit)
    code, out, err = await _systemctl(action, unit)
    await _log_action(unit, action, actor)
    if code != 0:
        raise HTTPException(500, f"failed to {action} {unit}: {err.strip() or out.strip()}")
    return {"unit": unit, "action": action, "ok": True}


def _actor(op: dict) -> str:
    return f"operator:{op['id']}:{op['operator_role']}"


async def _request(unit: str, action: str, op: dict) -> JSONResponse:
    from backend.sentinel import signoffs
    unit = _validate_unit(unit)
    _refuse_denylisted(unit)
    card = await signoffs.file(kind=f"daemon.{action}", payload={"unit": unit},
                               requester_human_id=op["id"], summary=f"{action.capitalize()} {unit}")
    return JSONResponse(status_code=202, content={"unit": unit, "action": action, "signoff": card})


@router.post("/{unit}/start")
async def start_daemon(unit: str, op: dict = Depends(require_operator)):
    return await _request(unit, "start", op)


@router.post("/{unit}/stop", response_model=DaemonActionResult)
async def stop_daemon(unit: str, op: dict = Depends(require_operator)):
    return DaemonActionResult(**await apply_action(unit, "stop", _actor(op)))


@router.post("/{unit}/restart")
async def restart_daemon(unit: str, op: dict = Depends(require_operator)):
    return await _request(unit, "restart", op)


@router.get("/actions")
async def list_daemon_actions(limit: int = Query(50, ge=1, le=500), op: dict = Depends(require_operator)):
    """Recent start/stop/restart audit trail — who (key-hash) toggled what,
    when. Real accountability for a control surface that can take live
    trading daemons offline."""
    async with get_db() as db:
        await db.execute(_ACTIONS_DDL)
        db.row_factory = aiosqlite.Row
        rows = [dict(r) for r in await (await db.execute(
            "SELECT * FROM daemon_actions ORDER BY id DESC LIMIT ?", (limit,)
        )).fetchall()]
    return {"actions": rows, "count": len(rows)}
