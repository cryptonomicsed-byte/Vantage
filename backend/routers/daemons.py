"""Daemon control API — /api/admin/daemons/*.

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
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.db import get_db
from backend.deps import get_admin

router = APIRouter(prefix="/api/admin/daemons", tags=["admin"])

_UNIT_RE = re.compile(r"^ares-[a-z0-9-]+\.service$")

_ACTIONS_DDL = """
CREATE TABLE IF NOT EXISTS daemon_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit TEXT NOT NULL,
    action TEXT NOT NULL,
    admin_key_hash TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
"""


def _validate_unit(unit: str) -> str:
    unit = (unit or "").strip()
    if not _UNIT_RE.match(unit):
        raise HTTPException(
            422,
            "unit must be an ares-*.service name (e.g. 'ares-wallet-learner.service')",
        )
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


async def _log_action(unit: str, action: str, admin_key: str) -> None:
    """Best-effort audit log. Fail-open by design: the systemctl action
    this records has *already succeeded* by the time this runs (every call
    site awaits it after a successful systemctl call), so a DB contention
    error here (real and observed live: `database is locked` even after
    the full 20s busy_timeout, under the same lock pressure that caused
    the original incident this feature exists to let you resolve) must
    never turn a successful daemon toggle into a 500 to the caller."""
    key_hash = hashlib.sha256(admin_key.encode()).hexdigest()[:16]
    try:
        async with get_db() as db:
            await db.execute(_ACTIONS_DDL)
            await db.execute(
                "INSERT INTO daemon_actions (unit, action, admin_key_hash) VALUES (?, ?, ?)",
                (unit, action, key_hash),
            )
            await db.commit()
    except Exception as e:
        logging.getLogger(__name__).warning(f"daemon_actions audit log failed for {unit}/{action}: {e}")


@router.get("")
async def list_daemons(_: str = Depends(get_admin)):
    """Every ares-*.service unit and its live state, straight from systemd
    -- not a cached/stale table, this reflects what's actually running
    right now. `systemctl list-units` only shows units systemd has loaded;
    `--all` includes inactive/dead ones too, so a stopped daemon still
    shows up (as inactive) rather than disappearing from the list."""
    code, out, err = await _systemctl(
        "list-units", "--type=service", "--all", "--plain", "--no-legend", "ares-*.service"
    )
    if code != 0:
        raise HTTPException(500, f"systemctl list-units failed: {err.strip()}")
    daemons = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        unit, load, active, sub = parts[0], parts[1], parts[2], parts[3]
        description = parts[4] if len(parts) > 4 else ""
        if not _UNIT_RE.match(unit):
            continue
        daemons.append({
            "unit": unit,
            "name": unit.removeprefix("ares-").removesuffix(".service"),
            "load": load,
            "active": active,
            "sub": sub,
            "running": active == "active" and sub == "running",
            "description": description,
        })
    daemons.sort(key=lambda d: d["name"])
    return {"daemons": daemons, "count": len(daemons)}


class DaemonActionResult(BaseModel):
    unit: str
    action: str
    ok: bool
    detail: str = ""


@router.post("/{unit}/start", response_model=DaemonActionResult)
async def start_daemon(unit: str, admin_key: str = Depends(get_admin)):
    unit = _validate_unit(unit)
    code, out, err = await _systemctl("start", unit)
    await _log_action(unit, "start", admin_key)
    if code != 0:
        raise HTTPException(500, f"failed to start {unit}: {err.strip() or out.strip()}")
    return DaemonActionResult(unit=unit, action="start", ok=True)


@router.post("/{unit}/stop", response_model=DaemonActionResult)
async def stop_daemon(unit: str, admin_key: str = Depends(get_admin)):
    unit = _validate_unit(unit)
    code, out, err = await _systemctl("stop", unit)
    await _log_action(unit, "stop", admin_key)
    if code != 0:
        raise HTTPException(500, f"failed to stop {unit}: {err.strip() or out.strip()}")
    return DaemonActionResult(unit=unit, action="stop", ok=True)


@router.post("/{unit}/restart", response_model=DaemonActionResult)
async def restart_daemon(unit: str, admin_key: str = Depends(get_admin)):
    unit = _validate_unit(unit)
    code, out, err = await _systemctl("restart", unit)
    await _log_action(unit, "restart", admin_key)
    if code != 0:
        raise HTTPException(500, f"failed to restart {unit}: {err.strip() or out.strip()}")
    return DaemonActionResult(unit=unit, action="restart", ok=True)


@router.get("/actions")
async def list_daemon_actions(limit: int = 50, _: str = Depends(get_admin)):
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
