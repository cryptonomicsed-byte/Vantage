"""Relay admin operations against the relay's own postgres.

Why this exists: the inbound listener used `_docker_exec("add-member", ...)`,
which shells into a docker container to run the `buzz-admin` CLI. On this host
there is no docker, `buzz-admin` was never built (no cargo), and the relay
exposes no HTTP admin API — so membership could never be established, and the
relay closed every subscription the listener opened. That showed up as a
reconnect storm every ~2 seconds.

`psql` is present and the relay's own database is the source of truth, so the
same rows the CLI would write are written here directly. All channel ids and
pubkeys are validated against strict patterns before interpolation.
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Optional, Tuple

PSQL = "/usr/bin/psql"
DSN = os.environ.get(
    "BUZZ_DATABASE_URL", "postgres://buzz:buzz_dev@localhost:5432/buzz"
)
TIMEOUT = 8.0

_UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class RelayAdminError(RuntimeError):
    pass


def _uuid(v) -> str:
    s = str(v)
    if not _UUID_RE.match(s):
        raise RelayAdminError(f"not a uuid: {s[:40]}")
    return s


def _pubkey(v) -> str:
    s = str(v).lower()
    if not _HEX64_RE.match(s):
        raise RelayAdminError(f"not a 32-byte xonly pubkey: {s[:40]}")
    return s


async def _run(sql: str) -> list[str]:
    env = dict(os.environ)
    env.setdefault("PGCONNECT_TIMEOUT", "5")
    proc = await asyncio.create_subprocess_exec(
        PSQL, "-d", DSN, "-tA", "-F", "|", "-c", sql,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise RelayAdminError("psql timed out")
    if proc.returncode != 0:
        raise RelayAdminError((err.decode() or "psql failed").strip()[:200])
    return [line for line in out.decode().splitlines() if line.strip()]


async def relay_has_channel(channel_id) -> bool:
    """Does this channel exist in the relay at all?

    If it does not, subscribing is pointless: the relay accepts the frame and
    closes the subscription, which is what produced the reconnect storm.
    """
    cid = _uuid(channel_id)
    return bool(await _run(f"select 1 from channels where id = '{cid}'"))


async def community_of(channel_id) -> Optional[str]:
    cid = _uuid(channel_id)
    rows = await _run(f"select community_id from channels where id = '{cid}'")
    return rows[0] if rows else None


async def is_member(channel_id, pubkey_hex) -> bool:
    cid, pk = _uuid(channel_id), _pubkey(pubkey_hex)
    rows = await _run(
        "select 1 from channel_members "
        f"where channel_id = '{cid}' and pubkey = decode('{pk}','hex') and removed_at is null"
    )
    return bool(rows)


async def ensure_member(channel_id, pubkey_hex, role: str = "member") -> Tuple[bool, str]:
    """Idempotent: add the key as a member, or revive a soft-removed one."""
    cid, pk = _uuid(channel_id), _pubkey(pubkey_hex)
    if role not in ("member", "moderator", "owner"):
        raise RelayAdminError(f"bad role: {role}")
    if await is_member(cid, pk):
        return True, "already a member"
    community = await community_of(cid)
    if not community:
        return False, "channel does not exist"
    await _run(
        "insert into channel_members (community_id, channel_id, pubkey, role) "
        f"values ('{community}', '{cid}', decode('{pk}','hex'), '{role}') "
        "on conflict (community_id, channel_id, pubkey) "
        "do update set removed_at = null, removed_by = null, role = excluded.role"
    )
    ok = await is_member(cid, pk)
    return ok, ("added" if ok else "insert did not take")


async def list_members(channel_id) -> list[str]:
    cid = _uuid(channel_id)
    return await _run(
        "select encode(pubkey,'hex') || '|' || role || '|' || "
        "case when removed_at is null then 'active' else 'removed' end "
        f"from channel_members where channel_id = '{cid}'"
    )
