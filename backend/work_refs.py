"""Typed work references: the join between the chat log and the task systems.

Phase 0-3 gave `claim` and `artifact` messages a `work_ref` string and had
`coordination_scoring.py` join a claim to an artifact on that string. It
worked in isolation and only in isolation: `"tro:123"` was not a foreign key
to anything, so claiming a task in a workspace never marked it claimed in the
marketplace, and shipping an artifact never closed the request that paid for
it. The leaderboard scored work the task market could not see.

This module makes the string mean something.

    work_ref := "<kind>:<id>"

Three tiers, and the difference between them is the honest part:

* **Verified** -- the kind names a row in this database. The resolver reads
  it, and a claim or artifact message drives a real state transition on it.
* **Bound** -- the kind names something outside this database (a commit, a
  pull request, an issue). It can be *attributed* to a channel's bound
  repository, so the link is recorded, but nothing here can confirm the
  object exists and no state transition follows. It is marked unverified and
  it stays that way.
* **Refused** -- anything else. The resolver returns None rather than
  inventing a link, because a reference that resolves to nothing is worse
  than no reference: it scores.

The authorisation rule that makes this more than bookkeeping: **only the
principal holding the claim may close it.** Otherwise posting
`artifact / tro:123` would be a way to bank someone else's work.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import aiosqlite

from .db import get_db

logger = logging.getLogger(__name__)

WORK_REF_RE = re.compile(r"^([a-z][a-z0-9_]{0,15}):([A-Za-z0-9._/-]{1,120})$")

#: Link types written into work_ref_links. Mirrors the message types that
#: carry a work reference.
LINK_CLAIM = "claim"
LINK_ARTIFACT = "artifact"
LINK_PROPOSAL = "propose"
LINK_TYPES = {LINK_CLAIM, LINK_ARTIFACT, LINK_PROPOSAL}


@dataclass(frozen=True)
class RefKind:
    """How one reference kind maps onto a table.

    `claim_sql` and `close_sql` take `(actor_agent_id, actor_name, ref_id)` in
    that order and are only run when the row is in a state that allows it --
    the WHERE clause carries that guard, so the transition is atomic and a
    second claimant changes nothing rather than stealing the row.
    """

    name: str
    table: str
    title_column: str
    status_column: str = "status"
    owner_column: Optional[str] = None
    claimant_column: Optional[str] = None
    claim_sql: Optional[str] = None
    close_sql: Optional[str] = None
    #: External objects: recorded, attributed, never verified.
    external: bool = False

    @property
    def claimable(self) -> bool:
        return self.claim_sql is not None

    @property
    def closeable(self) -> bool:
        return self.close_sql is not None


KINDS: dict[str, RefKind] = {
    # Guild-internal token economy.
    "tro": RefKind(
        name="tro", table="tro_requests", title_column="description",
        owner_column="agent_name", claimant_column="matched_agent",
        claim_sql="""UPDATE tro_requests
                        SET matched_agent=?2, status='matched', updated_at=datetime('now')
                      WHERE id=?3 AND status='open'""",
        close_sql="""UPDATE tro_requests
                        SET status='completed', updated_at=datetime('now')
                      WHERE id=?3 AND status IN ('open','matched')""",
    ),
    # USDC marketplace.
    "task": RefKind(
        name="task", table="task_listings", title_column="title",
        owner_column="poster_name", claimant_column="awarded_to",
        claim_sql="""UPDATE task_listings
                        SET awarded_to=?2, status='claimed'
                      WHERE id=?3 AND status='open'""",
        close_sql="""UPDATE task_listings SET status='completed'
                      WHERE id=?3 AND status IN ('open','claimed','awarded')""",
    ),
    # Media/creation pipeline, task granularity.
    "jobtask": RefKind(
        name="jobtask", table="job_tasks", title_column="title",
        claimant_column="claimed_by_name",
        claim_sql="""UPDATE job_tasks
                        SET claimed_by_id=?1, claimed_by_name=?2, status='claimed'
                      WHERE id=?3 AND status='open'""",
        close_sql="""UPDATE job_tasks SET status='completed'
                      WHERE id=?3 AND status IN ('open','claimed')""",
    ),
    # A creation job as a whole. Owned at creation, so it is closeable by its
    # owner but never claimable by a third party -- there is no such transfer.
    "job": RefKind(
        name="job", table="creation_jobs", title_column="prompt",
        owner_column=None,
        close_sql="""UPDATE creation_jobs
                        SET status='completed', updated_at=datetime('now')
                      WHERE id=?3 AND status NOT IN ('completed','failed')""",
    ),
    # Git objects. Attributable to a channel's bound repository, verifiable
    # nowhere in this process.
    "commit": RefKind(name="commit", table="", title_column="", external=True),
    "pr": RefKind(name="pr", table="", title_column="", external=True),
    "issue": RefKind(name="issue", table="", title_column="", external=True),
}


@dataclass(frozen=True)
class ResolvedRef:
    kind: str
    ref_id: str
    verified: bool
    title: str = ""
    status: str = ""
    owner: str = ""
    claimant: str = ""

    @property
    def work_ref(self) -> str:
        return f"{self.kind}:{self.ref_id}"


def parse_work_ref(raw: Optional[str]) -> Optional[tuple[str, str]]:
    """Split a reference into (kind, id), or None if it is not one.

    Deliberately strict. A free-text note in the work_ref field is not a
    reference and must not be treated as one."""
    if not raw:
        return None
    m = WORK_REF_RE.match(raw.strip())
    if not m:
        return None
    kind, ref_id = m.group(1), m.group(2)
    if kind not in KINDS:
        return None
    return kind, ref_id


# ── schema ───────────────────────────────────────────────────────────────────

async def init_work_ref_db() -> None:
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS work_ref_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                channel_id INTEGER,
                principal_id INTEGER REFERENCES principals(id),
                link_type TEXT NOT NULL,
                ref_kind TEXT NOT NULL,
                ref_id TEXT NOT NULL,
                verified INTEGER NOT NULL DEFAULT 0,
                transitioned INTEGER NOT NULL DEFAULT 0,
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE (event_id, ref_kind, ref_id, link_type)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_wrl_ref ON work_ref_links(ref_kind, ref_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_wrl_principal ON work_ref_links(principal_id, link_type)"
        )
        await db.commit()


# ── resolution ───────────────────────────────────────────────────────────────

async def resolve(raw: Optional[str]) -> Optional[ResolvedRef]:
    """Turn a work_ref string into the thing it points at, or None.

    Returning None for an unknown kind is the point of the whole module: a
    reference nobody can check must not become a reference everybody trusts.
    """
    parsed = parse_work_ref(raw)
    if parsed is None:
        return None
    kind_name, ref_id = parsed
    spec = KINDS[kind_name]

    if spec.external:
        # Shape is checkable, existence is not. Say so in the flag rather
        # than in a comment nobody reads at query time.
        return ResolvedRef(kind=kind_name, ref_id=ref_id, verified=False)

    if not ref_id.isdigit():
        return None  # every local kind is keyed on an integer id

    cols = ["id", f"{spec.title_column} AS _title", f"{spec.status_column} AS _status"]
    if spec.owner_column:
        cols.append(f"{spec.owner_column} AS _owner")
    if spec.claimant_column:
        cols.append(f"{spec.claimant_column} AS _claimant")

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        try:
            cur = await db.execute(
                f"SELECT {', '.join(cols)} FROM {spec.table} WHERE id=?", (int(ref_id),)
            )
            row = await cur.fetchone()
        except Exception as exc:
            # The table may not exist on an instance that never enabled that
            # subsystem. An unresolvable reference, not a crash.
            logger.debug("work_ref: %s table unavailable: %s", spec.table, exc)
            return None
    if row is None:
        return None
    row = dict(row)
    return ResolvedRef(
        kind=kind_name, ref_id=ref_id, verified=True,
        title=str(row.get("_title") or "")[:200],
        status=str(row.get("_status") or ""),
        owner=str(row.get("_owner") or ""),
        claimant=str(row.get("_claimant") or ""),
    )


async def claim_holder(kind_name: str, ref_id: str) -> Optional[int]:
    """The principal that first claimed this reference, if any.

    First, not last: a claim is a race the earliest publisher wins, and the
    relay's `created_at` ordering is what decides it."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT principal_id FROM work_ref_links
                WHERE ref_kind=? AND ref_id=? AND link_type=? AND principal_id IS NOT NULL
                ORDER BY id ASC LIMIT 1""",
            (kind_name, ref_id, LINK_CLAIM),
        )
        row = await cur.fetchone()
    return dict(row)["principal_id"] if row else None


# ── recording a link, and the transition it may drive ────────────────────────

async def record_link(
    *, event_id: str, channel_id: Optional[int], principal: Optional[dict],
    link_type: str, raw_work_ref: Optional[str],
) -> Optional[dict]:
    """Resolve a message's work_ref, record the link, and apply the state
    transition it earns. Idempotent on (event, ref, link type).

    Returns a summary dict, or None if there was nothing resolvable to record.
    Never raises on a bad reference: an unparseable work_ref is a message
    that simply carries no link, not a failed publish.
    """
    if link_type not in LINK_TYPES:
        return None
    resolved = await resolve(raw_work_ref)
    if resolved is None:
        return None

    principal_id = principal.get("id") if principal else None
    transitioned = False
    note = ""

    if resolved.verified and link_type in (LINK_CLAIM, LINK_ARTIFACT):
        transitioned, note = await _apply_transition(resolved, link_type, principal)
    elif not resolved.verified:
        note = "external reference: recorded, not verified"

    async with get_db() as db:
        await db.execute(
            """INSERT OR IGNORE INTO work_ref_links
                 (event_id, channel_id, principal_id, link_type, ref_kind, ref_id,
                  verified, transitioned, note)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (event_id, channel_id, principal_id, link_type, resolved.kind, resolved.ref_id,
             1 if resolved.verified else 0, 1 if transitioned else 0, note),
        )
        await db.commit()

    return {
        "work_ref": resolved.work_ref, "link_type": link_type,
        "verified": resolved.verified, "transitioned": transitioned,
        "title": resolved.title, "status": resolved.status, "note": note,
    }


async def _apply_transition(
    resolved: ResolvedRef, link_type: str, principal: Optional[dict]
) -> tuple[bool, str]:
    """Run the claim or close, if this principal is entitled to it.

    Every reason for declining is returned as text rather than swallowed,
    because "my artifact didn't close the task" is otherwise unanswerable.
    """
    spec = KINDS[resolved.kind]
    if principal is None:
        return False, "no principal: link recorded without a transition"

    agent_id = principal.get("agent_id")
    actor_name = principal.get("display_name") or ""

    if link_type == LINK_CLAIM:
        if not spec.claimable:
            return False, f"{resolved.kind} references are not claimable"
        if spec.claim_sql and "?1" in spec.claim_sql and agent_id is None:
            # job_tasks stores a real agent id. A human or an outside agent
            # holding its own key has none, so it can link but not claim --
            # which is a schema limit, not a permission decision, and saying
            # so is better than silently doing nothing.
            return False, "this task system stores an agent id; this principal has none"
        return await _run(spec.claim_sql, agent_id, actor_name, resolved.ref_id,
                          f"already {resolved.status}")

    # artifact -> close
    if not spec.closeable:
        return False, f"{resolved.kind} references cannot be closed from chat"
    holder = await claim_holder(resolved.kind, resolved.ref_id)
    if holder is not None and holder != principal.get("id"):
        # The invariant that makes claims worth making.
        return False, "this reference is claimed by another principal"
    return await _run(spec.close_sql, agent_id, actor_name, resolved.ref_id,
                      f"not closeable from status {resolved.status!r}")


_PLACEHOLDER_RE = re.compile(r"\?([123])")


def _bind(sql: str, values: tuple) -> tuple[str, tuple]:
    """Rewrite `?1/?2/?3` into plain `?` and build the matching argument
    tuple in the order the placeholders actually appear.

    The numbered form is there so each statement can use only the arguments
    it needs -- `tro_requests` has no agent-id column, `job_tasks` has both.
    Blindly replacing all three and passing all three would bind the wrong
    values to the wrong columns, silently on any statement whose arity
    happened to match.
    """
    order = [int(m.group(1)) for m in _PLACEHOLDER_RE.finditer(sql)]
    return _PLACEHOLDER_RE.sub("?", sql), tuple(values[i - 1] for i in order)


async def _run(sql: str, agent_id, actor_name: str, ref_id: str, on_noop: str) -> tuple[bool, str]:
    statement, args = _bind(sql, (agent_id, actor_name, int(ref_id)))
    async with get_db() as db:
        try:
            cur = await db.execute(statement, args)
            await db.commit()
        except Exception as exc:
            logger.warning("work_ref transition failed: %s", exc)
            return False, f"transition failed: {exc}"
    if cur.rowcount and cur.rowcount > 0:
        return True, ""
    return False, on_noop


# ── read side ────────────────────────────────────────────────────────────────

async def links_for_ref(kind_name: str, ref_id: str) -> list[dict]:
    """Everything the chat log has said about one task, in order."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT l.*, p.display_name, p.kind AS principal_kind
                 FROM work_ref_links l LEFT JOIN principals p ON p.id = l.principal_id
                WHERE l.ref_kind=? AND l.ref_id=? ORDER BY l.id ASC""",
            (kind_name, ref_id),
        )
        return [dict(r) for r in await cur.fetchall()]


async def verified_deliveries(principal_id: int) -> int:
    """Artifacts this principal shipped that actually closed a real task.

    This is the number the leaderboard should trust, as distinct from the
    count of artifact messages, which anyone can type."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT COUNT(DISTINCT ref_kind || ':' || ref_id) AS n
                 FROM work_ref_links
                WHERE principal_id=? AND link_type=? AND verified=1 AND transitioned=1""",
            (principal_id, LINK_ARTIFACT),
        )
        row = await cur.fetchone()
    return int(dict(row)["n"] or 0)
