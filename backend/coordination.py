"""Coordination layer: principals, guild channels, and the relay-backed
message log.

This is Phase 0 of docs/VANTAGE_SWARM_COORDINATION_SPEC.md — the data model
that turns guilds into forums (humans and agents in the same membership
table, sub-guilds as channels) and workspaces into channels that happen to
have a sandbox attached.

The invariant everything else rests on: **the relay is the log, this database
is the index**. A message row is only ever written after the relay has
accepted the signed event, and the Nostr event id is its identity. That makes
the index disposable — it can be dropped and replayed from the relay — and it
means an external agent publishing straight to the relay (which is the whole
point of the keypair join boundary) produces exactly the same rows as one
posting through this backend.

Consequently there is no local-only fallback when the relay is down. Posting
returns 503, the same stance routers/workspace.py takes for the sandbox: a
fallback that silently wrote unsigned messages nobody else could see would
defeat the entire design.
"""
import json
import logging
from typing import Optional

import aiosqlite

from .buzz_client import BuzzSession
from .buzz_identity import (
    derive_buzz_keypair,
    derive_human_buzz_keypair,
    public_key_xonly_hex,
)
from .buzz_registration import RELAY_WS_URL
from .db import get_db

logger = logging.getLogger(__name__)

# Kinds confirmed against the existing modules that already use them --
# 9 in buzz_ops_channel.py / buzz_trading_channel.py, 9007 in buzz_rooms.py.
KIND_MESSAGE = 9
KIND_CREATE_CHANNEL = 9007

# The orchestration vocabulary. `system` is reserved for the Conductor and is
# rejected from every other publisher (see publish_message).
MSG_TYPES = {"say", "propose", "claim", "handoff", "artifact", "system"}

CHANNEL_KINDS = {"forum", "workspace"}
FLOW_MODES = {"open", "round_robin", "moderated"}
VISIBILITIES = {"public", "members", "private"}
GUILD_ROLES = {"founder", "admin", "moderator", "member"}

MAX_CONTENT_CHARS = 8000


# ── schema ───────────────────────────────────────────────────────────────────

async def init_coordination_db() -> None:
    """Create the coordination tables and migrate existing guild_members.

    Kept out of db.py's init_agents_db() deliberately: that function is
    already enormous, and this schema is self-contained enough to own its
    own migration (including the guild_members -> principals backfill,
    which must run after both tables exist).
    """
    async with get_db() as db:
        # Everything that can speak. Agents and humans get their keypair
        # derived by Vantage; an external agent brings its own and we only
        # ever see the public half.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS principals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,                  -- agent | human | external_agent
                agent_id INTEGER REFERENCES agents(id),
                human_id INTEGER REFERENCES humans(id),
                pubkey TEXT NOT NULL UNIQUE,         -- x-only hex, 64 chars
                display_name TEXT NOT NULL,
                framework TEXT DEFAULT '',
                key_custody TEXT NOT NULL,           -- derived | self | nip46
                capabilities TEXT DEFAULT '[]',      -- self-declared, ADVISORY ONLY
                created_at TEXT DEFAULT (datetime('now')),
                last_seen_at TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_principals_pubkey ON principals(pubkey)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_principals_agent ON principals(agent_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_principals_human ON principals(human_id)")

        # A guild owns a tree of channels, exactly one level deep:
        # guild -> sub-guild -> threads, where threads are message chains
        # rather than more channels. That keeps the tree finite and the
        # relay mapping one channel per node.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL REFERENCES guilds(id),
                parent_channel_id INTEGER REFERENCES guild_channels(id),
                slug TEXT NOT NULL,
                name TEXT NOT NULL,
                topic TEXT DEFAULT '',
                channel_kind TEXT NOT NULL DEFAULT 'forum',
                flow_mode TEXT NOT NULL DEFAULT 'open',
                visibility TEXT NOT NULL DEFAULT 'members',
                buzz_channel_id TEXT,
                sandbox_bound INTEGER DEFAULT 0,
                created_by_principal_id INTEGER REFERENCES principals(id),
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(guild_id, slug)
            )
        """)
        # A workspace channel can be bound to a Gitea repo, which is what makes
        # it a place to collaborate on code rather than just another room.
        # Added as ALTERs so an existing deployment picks them up without a
        # migration step.
        for col, ddl in (
            ("repo_owner", "TEXT DEFAULT NULL"),
            ("repo_name", "TEXT DEFAULT NULL"),
            ("repo_branch", "TEXT DEFAULT 'main'"),
        ):
            try:
                await db.execute(f"ALTER TABLE guild_channels ADD COLUMN {col} {ddl}")
            except Exception:
                pass  # already present

        await db.execute("CREATE INDEX IF NOT EXISTS idx_gchannels_guild ON guild_channels(guild_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_gchannels_parent ON guild_channels(parent_channel_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_gchannels_buzz ON guild_channels(buzz_channel_id)")

        # Humans and agents in one membership table with one set of roles.
        # This is what "humans and agents in the same guild" costs: one table.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_memberships (
                guild_id INTEGER NOT NULL REFERENCES guilds(id),
                principal_id INTEGER NOT NULL REFERENCES principals(id),
                role TEXT NOT NULL DEFAULT 'member',
                joined_at TEXT DEFAULT (datetime('now')),
                banned_at TEXT,
                PRIMARY KEY (guild_id, principal_id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_gmemberships_principal ON guild_memberships(principal_id)")

        # The index. Never written unless the relay accepted the event first.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS channel_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                channel_id INTEGER NOT NULL REFERENCES guild_channels(id),
                buzz_channel_id TEXT NOT NULL,
                pubkey TEXT NOT NULL,
                principal_id INTEGER REFERENCES principals(id),
                thread_root_event_id TEXT,
                reply_to_event_id TEXT,
                msg_type TEXT NOT NULL DEFAULT 'say',
                work_ref TEXT,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                indexed_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_cm_channel_time ON channel_messages(channel_id, created_at DESC)"
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_cm_thread ON channel_messages(thread_root_event_id)")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_cm_principal ON channel_messages(principal_id, created_at DESC)"
        )
        await db.commit()

    await _migrate_guild_members()


async def _migrate_guild_members() -> None:
    """Backfill principals + guild_memberships from the agent-only
    guild_members table.

    Idempotent and additive: guild_members is left in place so the existing
    routers/guilds.py endpoints keep working unchanged during Phase 0. The
    two tables are kept in step by the membership helpers below, which write
    both. Dropping guild_members is a later cleanup, not part of this phase.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        try:
            cur = await db.execute("SELECT guild_id, agent_id, role, joined_at FROM guild_members")
            rows = [dict(r) for r in await cur.fetchall()]
        except Exception:
            return  # guild_members doesn't exist yet on a fresh database

    migrated = 0
    for row in rows:
        try:
            principal = await get_or_create_agent_principal(row["agent_id"])
        except Exception as exc:
            logger.warning("coordination migrate: agent %s has no derivable identity: %s",
                           row["agent_id"], exc)
            continue
        async with get_db() as db:
            await db.execute(
                """INSERT OR IGNORE INTO guild_memberships (guild_id, principal_id, role, joined_at)
                   VALUES (?,?,?,COALESCE(?, datetime('now')))""",
                (row["guild_id"], principal["id"], row.get("role") or "member", row.get("joined_at")),
            )
            await db.commit()
        migrated += 1
    if migrated:
        logger.info("coordination: migrated %d guild_members rows into principals/guild_memberships", migrated)


# ── principals ───────────────────────────────────────────────────────────────

async def _get_principal_by_pubkey(pubkey: str) -> Optional[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM principals WHERE pubkey=?", (pubkey,))
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_principal(principal_id: int) -> Optional[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM principals WHERE id=?", (principal_id,))
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_or_create_agent_principal(agent_id: int) -> dict:
    """The principal for a Vantage-native agent. Keypair is derived, so this
    is stable across calls and matches the pubkey the agent already uses on
    the relay for every other buzz_* feature."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM principals WHERE kind='agent' AND agent_id=?", (agent_id,))
        row = await cur.fetchone()
        if row:
            return dict(row)
        cur = await db.execute("SELECT name FROM agents WHERE id=?", (agent_id,))
        agent_row = await cur.fetchone()
    if not agent_row:
        raise ValueError(f"no such agent: {agent_id}")

    pk = await derive_buzz_keypair(agent_id)
    pubkey = public_key_xonly_hex(pk)
    return await _insert_principal(
        kind="agent", pubkey=pubkey, display_name=dict(agent_row)["name"],
        framework="vantage", key_custody="derived", agent_id=agent_id,
    )


async def get_or_create_human_principal(human_id: int) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM principals WHERE kind='human' AND human_id=?", (human_id,))
        row = await cur.fetchone()
        if row:
            return dict(row)
        cur = await db.execute("SELECT display_name, email FROM humans WHERE id=?", (human_id,))
        human_row = await cur.fetchone()
    if not human_row:
        raise ValueError(f"no such human: {human_id}")
    human_row = dict(human_row)

    pk = await derive_human_buzz_keypair(human_id)
    pubkey = public_key_xonly_hex(pk)
    # Prefer the chosen display name; fall back to the local part of the
    # email rather than the whole address, which shouldn't be on screen.
    name = human_row.get("display_name") or (human_row.get("email") or "").split("@")[0] or f"human-{human_id}"
    return await _insert_principal(
        kind="human", pubkey=pubkey, display_name=name,
        framework="human", key_custody="derived", human_id=human_id,
    )


async def get_or_create_external_principal(
    *, pubkey: str, display_name: str, framework: str = "", capabilities: Optional[list] = None,
) -> dict:
    """The principal for an outside agent framework.

    key_custody is 'self' and stays that way: Vantage holds the public half
    only, which is why signing_key_for_principal returns None for these and
    they publish to the relay themselves.

    A pubkey that comes back already known is returned as-is rather than
    overwritten — re-joining a second guild must not let a later request
    silently rename or re-badge an identity that is already speaking
    somewhere else.
    """
    pubkey = (pubkey or "").strip().lower()
    existing = await _get_principal_by_pubkey(pubkey)
    if existing:
        return existing
    return await _insert_principal(
        kind="external_agent", pubkey=pubkey, display_name=display_name,
        framework=framework or "external", key_custody="self",
        capabilities=capabilities or [],
    )


async def _insert_principal(
    *, kind: str, pubkey: str, display_name: str, framework: str, key_custody: str,
    agent_id: Optional[int] = None, human_id: Optional[int] = None,
    capabilities: Optional[list] = None,
) -> dict:
    async with get_db() as db:
        await db.execute(
            """INSERT OR IGNORE INTO principals
                 (kind, agent_id, human_id, pubkey, display_name, framework, key_custody, capabilities)
               VALUES (?,?,?,?,?,?,?,?)""",
            (kind, agent_id, human_id, pubkey, display_name, framework, key_custody,
             json.dumps(capabilities or [])),
        )
        await db.commit()
    principal = await _get_principal_by_pubkey(pubkey)
    if not principal:
        raise RuntimeError(f"failed to create principal for pubkey {pubkey[:8]}")
    return principal


async def principal_for_agent_dict(agent: dict) -> dict:
    """Bridge from the existing get_agent dependency to a principal."""
    return await get_or_create_agent_principal(agent["id"])


async def signing_key_for_principal(principal: dict):
    """Return the coincurve PrivateKey Vantage may sign with on this
    principal's behalf, or None.

    Keyed on *custody*, not on principal kind. An external agent has always
    held its own key, but a native agent or human can migrate to self-custody
    too (see backend/sovereignty.py), and after that this instance genuinely
    cannot sign for them. Reading `kind` instead of `key_custody` would have
    kept signing for migrated accounts with a key they no longer control.

    Callers must handle None rather than assume a key exists.
    """
    if principal.get("key_custody") == "self":
        return None
    if principal["kind"] == "agent" and principal["agent_id"]:
        return await derive_buzz_keypair(principal["agent_id"])
    if principal["kind"] == "human" and principal["human_id"]:
        return await derive_human_buzz_keypair(principal["human_id"])
    return None


# ── membership ───────────────────────────────────────────────────────────────

async def get_membership(guild_id: int, principal_id: int) -> Optional[dict]:
    """Membership for a principal, reconciling the legacy table on the way.

    routers/guilds.py still writes only guild_members, and it keeps doing so
    throughout Phase 0 — including for guilds created after this module's
    startup migration ran. So a lookup that misses falls back to the old
    table for agent principals and backfills what it finds, which makes the
    two views converge through ordinary use instead of needing a flag day.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM guild_memberships WHERE guild_id=? AND principal_id=?",
            (guild_id, principal_id),
        )
        row = await cur.fetchone()
    if row:
        return dict(row)

    principal = await get_principal(principal_id)
    if not principal or principal["kind"] != "agent" or not principal["agent_id"]:
        return None

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT role, joined_at FROM guild_members WHERE guild_id=? AND agent_id=?",
            (guild_id, principal["agent_id"]),
        )
        legacy = await cur.fetchone()
    if not legacy:
        return None
    legacy = dict(legacy)

    async with get_db() as db:
        await db.execute(
            """INSERT OR IGNORE INTO guild_memberships (guild_id, principal_id, role, joined_at)
               VALUES (?,?,?,COALESCE(?, datetime('now')))""",
            (guild_id, principal_id, legacy.get("role") or "member", legacy.get("joined_at")),
        )
        await db.commit()
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM guild_memberships WHERE guild_id=? AND principal_id=?",
            (guild_id, principal_id),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def is_active_member(guild_id: int, principal_id: int) -> bool:
    m = await get_membership(guild_id, principal_id)
    return bool(m and not m.get("banned_at"))


async def add_membership(guild_id: int, principal: dict, role: str = "member") -> None:
    """Add a principal to a guild, keeping the legacy guild_members table in
    step for agent principals so routers/guilds.py stays correct."""
    if role not in GUILD_ROLES:
        raise ValueError(f"invalid role: {role}")
    async with get_db() as db:
        await db.execute(
            """INSERT INTO guild_memberships (guild_id, principal_id, role)
               VALUES (?,?,?)
               ON CONFLICT(guild_id, principal_id)
               DO UPDATE SET role=excluded.role, banned_at=NULL""",
            (guild_id, principal["id"], role),
        )
        if principal["kind"] == "agent" and principal["agent_id"]:
            await db.execute(
                """INSERT OR IGNORE INTO guild_members (guild_id, agent_id, agent_name, role)
                   VALUES (?,?,?,?)""",
                (guild_id, principal["agent_id"], principal["display_name"], role),
            )
        await db.commit()


async def remove_membership(guild_id: int, principal: dict) -> None:
    async with get_db() as db:
        await db.execute(
            "DELETE FROM guild_memberships WHERE guild_id=? AND principal_id=?",
            (guild_id, principal["id"]),
        )
        if principal["kind"] == "agent" and principal["agent_id"]:
            await db.execute(
                "DELETE FROM guild_members WHERE guild_id=? AND agent_id=?",
                (guild_id, principal["agent_id"]),
            )
        await db.commit()


# ── channels ─────────────────────────────────────────────────────────────────

async def get_channel(guild_id: int, slug: str) -> Optional[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM guild_channels WHERE guild_id=? AND slug=?", (guild_id, slug)
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_channel_by_id(channel_id: int) -> Optional[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM guild_channels WHERE id=?", (channel_id,))
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_channel_by_buzz_id(buzz_channel_id: str) -> Optional[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM guild_channels WHERE buzz_channel_id=?", (buzz_channel_id,)
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def provision_channel_on_relay(channel: dict, principal: dict, guild_slug: str) -> Optional[str]:
    """Create the real relay channel backing a guild channel (kind 9007).

    Follows buzz_rooms.create_room_channel: never raises, because a failed
    mirror shouldn't lose the channel row. The consequence is explicit
    rather than silent — a channel with a NULL buzz_channel_id cannot be
    posted to (post_message returns 503) until provisioning is retried.
    """
    import uuid

    pk = await signing_key_for_principal(principal)
    if pk is None:
        logger.warning("coordination: cannot provision channel %s — principal %s holds its own key",
                       channel["slug"], principal["id"])
        return None

    buzz_channel_id = str(uuid.uuid4())
    sess = None
    try:
        sess = BuzzSession(RELAY_WS_URL, pk)
        await sess.connect()
        await sess.authenticate()
        await sess.publish(
            KIND_CREATE_CHANNEL, "",
            tags=[
                ["h", buzz_channel_id],
                ["name", f"{guild_slug}-{channel['slug']}"],
                ["visibility", "private" if channel["visibility"] != "public" else "public"],
                ["about", channel.get("topic") or channel["name"]],
            ],
        )
    except Exception as exc:
        logger.warning("coordination: relay channel provisioning failed for %s: %s", channel["slug"], exc)
        return None
    finally:
        if sess is not None:
            try:
                await sess.close()
            except Exception as exc:
                logger.debug("silenced relay session close: %s", exc)

    async with get_db() as db:
        await db.execute(
            "UPDATE guild_channels SET buzz_channel_id=? WHERE id=?",
            (buzz_channel_id, channel["id"]),
        )
        await db.commit()
    return buzz_channel_id


async def can_read_channel(channel: dict, principal: Optional[dict]) -> bool:
    """Read gating lives here, in Python, deliberately.

    The relay's own per-channel subscription ACL for `visibility: private`
    is documented as UNVERIFIED in the spec (§9.1) — create_room_channel
    publishes the tag but nothing in this tree proves the relay refuses a
    non-member's subscription. Until that is confirmed, treat the relay tag
    as advisory and enforce reads here, where we can actually check.
    """
    if channel["visibility"] == "public":
        return True
    if principal is None:
        return False
    return await is_active_member(channel["guild_id"], principal["id"])


# ── messages ─────────────────────────────────────────────────────────────────

def build_message_tags(
    *, buzz_channel_id: str, guild_slug: str, channel_slug: str,
    msg_type: str = "say", root_event_id: Optional[str] = None,
    reply_to_event_id: Optional[str] = None, addressed_to=None,
    work_ref: Optional[str] = None, extra_tags: Optional[list] = None,
) -> list:
    """The wire format from spec §6.

    Structure rides in tags, never in the content body, so a plain Nostr
    client shows a normal readable message and any framework that
    understands nothing but kind 9 can still take part.
    """
    tags = [["h", buzz_channel_id]]
    if root_event_id:
        tags.append(["e", root_event_id, "", "root"])
    if reply_to_event_id:
        tags.append(["e", reply_to_event_id, "", "reply"])
    # One `p` tag per addressee. A room where "@alice @bob" only reaches
    # alice is not a room, so this takes a list as readily as a string.
    if addressed_to:
        for pubkey in ([addressed_to] if isinstance(addressed_to, str) else addressed_to):
            if pubkey:
                tags.append(["p", pubkey])
    tags.append(["vg", guild_slug, channel_slug])
    tags.append(["vt", msg_type])
    if work_ref:
        tags.append(["vw", work_ref])
    for tag in extra_tags or []:
        tags.append(tag)
    return tags


def _tags_named(event: dict, name: str) -> list:
    return [t for t in event.get("tags", []) if t and len(t) >= 2 and t[0] == name]


def parse_message_event(event: dict) -> dict:
    """Pull Vantage structure back out of a kind 9 event.

    Written to survive events from clients that know nothing about the `vg`
    / `vt` / `vw` tags: everything Vantage-specific has a sane default, so a
    bare kind 9 with only an `h` tag indexes correctly as a top-level `say`.
    """
    e_tags = _tags_named(event, "e")
    root = next((t[1] for t in e_tags if len(t) > 3 and t[3] == "root"), None)
    reply = next((t[1] for t in e_tags if len(t) > 3 and t[3] == "reply"), None)
    # A single untyped `e` tag is a reply to that event in NIP-10's older
    # positional form; treat it as such rather than dropping the threading.
    if root is None and reply is None and e_tags:
        reply = e_tags[0][1]

    vt = _tags_named(event, "vt")
    msg_type = vt[0][1] if vt else "say"
    if msg_type not in MSG_TYPES:
        msg_type = "say"

    vw = _tags_named(event, "vw")
    h = _tags_named(event, "h")

    return {
        "event_id": event.get("id", ""),
        "pubkey": event.get("pubkey", ""),
        "buzz_channel_id": h[0][1] if h else "",
        "thread_root_event_id": root or reply,
        "reply_to_event_id": reply,
        "msg_type": msg_type,
        "work_ref": vw[0][1] if vw else None,
        "content": event.get("content", ""),
        "created_at": int(event.get("created_at") or 0),
    }


async def index_event(event: dict, channel: Optional[dict] = None) -> Optional[int]:
    """Write one relay event into the index. Idempotent on event_id.

    Shared by the publish path and the background indexer, so a message
    posted through this backend and one published straight to the relay by
    an external agent produce identical rows. Returns the row id, or None if
    the event was skipped (unknown channel, banned author, duplicate).
    """
    parsed = parse_message_event(event)
    if not parsed["event_id"] or not parsed["buzz_channel_id"]:
        return None

    if channel is None:
        channel = await get_channel_by_buzz_id(parsed["buzz_channel_id"])
    if channel is None:
        return None  # an event for a channel this instance doesn't own

    principal = await _get_principal_by_pubkey(parsed["pubkey"])
    principal_id = principal["id"] if principal else None

    # Risk §9.2: relay roles are deployment-wide, so a principal banned from
    # a guild is still a relay member and its events still arrive here. The
    # ban is enforced at index time because it cannot be enforced upstream.
    if principal_id is not None:
        membership = await get_membership(channel["guild_id"], principal_id)
        if membership and membership.get("banned_at"):
            logger.info("coordination: dropping event %s from banned principal %s",
                        parsed["event_id"][:8], principal_id)
            return None

    # `system` is the Conductor's alone. Accepting it from anyone else would
    # let any relay member forge floor grants into the transcript.
    if parsed["msg_type"] == "system" and not await _is_system_publisher(parsed["pubkey"]):
        logger.warning("coordination: rejecting forged system event %s from %s",
                       parsed["event_id"][:8], parsed["pubkey"][:8])
        return None

    async with get_db() as db:
        cur = await db.execute(
            """INSERT OR IGNORE INTO channel_messages
                 (event_id, channel_id, buzz_channel_id, pubkey, principal_id,
                  thread_root_event_id, reply_to_event_id, msg_type, work_ref,
                  content, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (parsed["event_id"], channel["id"], parsed["buzz_channel_id"], parsed["pubkey"],
             principal_id, parsed["thread_root_event_id"], parsed["reply_to_event_id"],
             parsed["msg_type"], parsed["work_ref"], parsed["content"][:MAX_CONTENT_CHARS],
             parsed["created_at"]),
        )
        await db.commit()
        row_id = cur.lastrowid

    if principal_id is not None:
        async with get_db() as db:
            await db.execute(
                "UPDATE principals SET last_seen_at=datetime('now') WHERE id=?", (principal_id,)
            )
            await db.commit()
    return row_id or None


async def _is_system_publisher(pubkey: str) -> bool:
    """Only the deployment's own instance identity may emit `vt=system`.

    In Phase 2 the Conductor signs with this same instance key, so this stays
    the single check for both.
    """
    try:
        from .buzz_identity import derive_instance_keypair
        instance_pk = await derive_instance_keypair()
        return public_key_xonly_hex(instance_pk) == pubkey
    except Exception as exc:
        logger.debug("silenced system-publisher check: %s", exc)
        return False


class RelayUnavailable(RuntimeError):
    """The relay refused or could not be reached. Callers surface this as a
    503 rather than writing an unsigned message nobody else would see."""


async def publish_message(
    *, channel: dict, guild_slug: str, principal: dict, content: str,
    msg_type: str = "say", root_event_id: Optional[str] = None,
    reply_to_event_id: Optional[str] = None, addressed_to=None,
    work_ref: Optional[str] = None, extra_tags: Optional[list] = None,
) -> dict:
    """Sign, publish to the relay, then index. In that order, always.

    Raises RelayUnavailable if the relay does not accept the event, and
    writes nothing — the index must never contain a message the log doesn't.
    """
    if msg_type not in MSG_TYPES or msg_type == "system":
        raise ValueError(f"invalid message type: {msg_type}")
    content = (content or "").strip()
    if not content:
        raise ValueError("message content is empty")
    if not channel.get("buzz_channel_id"):
        raise RelayUnavailable("channel is not provisioned on the relay yet")

    pk = await signing_key_for_principal(principal)
    if pk is None:
        raise ValueError(
            "this principal holds its own key — publish directly to the relay instead"
        )

    tags = build_message_tags(
        buzz_channel_id=channel["buzz_channel_id"], guild_slug=guild_slug,
        channel_slug=channel["slug"], msg_type=msg_type, root_event_id=root_event_id,
        reply_to_event_id=reply_to_event_id, addressed_to=addressed_to, work_ref=work_ref,
        extra_tags=extra_tags,
    )

    sess = None
    try:
        sess = BuzzSession(RELAY_WS_URL, pk)
        await sess.connect()
        await sess.authenticate()
        result = await sess.publish(KIND_MESSAGE, content[:MAX_CONTENT_CHARS], tags=tags)
    except Exception as exc:
        raise RelayUnavailable(f"relay publish failed: {exc}") from exc
    finally:
        if sess is not None:
            try:
                await sess.close()
            except Exception as exc:
                logger.debug("silenced relay session close: %s", exc)

    ack = result.get("ack") or []
    if not (len(ack) > 2 and ack[0] == "OK" and ack[2]):
        reason = ack[3] if len(ack) > 3 else "relay rejected the event"
        raise RelayUnavailable(f"relay rejected the message: {reason}")

    await index_event(result["event"], channel=channel)
    return result["event"]


async def publish_system_message(*, channel: dict, guild_slug: str, text: str) -> dict:
    """Publish a `vt=system` event signed with the deployment's instance key.

    Separate from publish_message because the rules are inverted: `system` is
    the one type ordinary principals may never send, and the one type this
    function may only send. index_event checks the signing pubkey against the
    instance identity, so a system event signed by anything else is dropped —
    which is what stops a relay member forging floor grants into a
    transcript.
    """
    from .buzz_identity import derive_instance_keypair

    if not channel.get("buzz_channel_id"):
        raise RelayUnavailable("channel is not provisioned on the relay yet")
    text = (text or "").strip()
    if not text:
        raise ValueError("system message text is empty")

    pk = await derive_instance_keypair()
    tags = build_message_tags(
        buzz_channel_id=channel["buzz_channel_id"], guild_slug=guild_slug,
        channel_slug=channel["slug"], msg_type="system",
    )

    sess = None
    try:
        sess = BuzzSession(RELAY_WS_URL, pk)
        await sess.connect()
        await sess.authenticate()
        result = await sess.publish(KIND_MESSAGE, text[:MAX_CONTENT_CHARS], tags=tags)
    except Exception as exc:
        raise RelayUnavailable(f"relay publish failed: {exc}") from exc
    finally:
        if sess is not None:
            try:
                await sess.close()
            except Exception as exc:
                logger.debug("silenced relay session close: %s", exc)

    ack = result.get("ack") or []
    if not (len(ack) > 2 and ack[0] == "OK" and ack[2]):
        reason = ack[3] if len(ack) > 3 else "relay rejected the event"
        raise RelayUnavailable(f"relay rejected the system message: {reason}")

    await index_event(result["event"], channel=channel)
    return result["event"]


async def list_messages(
    channel_id: int, *, limit: int = 50, before_id: Optional[int] = None,
    thread_root: Optional[str] = None,
) -> list[dict]:
    """Newest-first page of a channel, or of one thread within it."""
    limit = max(1, min(limit, 200))
    sql = [
        """SELECT m.*, p.display_name, p.kind AS principal_kind, p.framework
             FROM channel_messages m
             LEFT JOIN principals p ON p.id = m.principal_id
            WHERE m.channel_id = ?"""
    ]
    params: list = [channel_id]
    if thread_root is not None:
        sql.append("AND (m.thread_root_event_id = ? OR m.event_id = ?)")
        params += [thread_root, thread_root]
    else:
        sql.append("AND m.thread_root_event_id IS NULL")
    if before_id is not None:
        sql.append("AND m.id < ?")
        params.append(before_id)
    sql.append("ORDER BY m.created_at DESC, m.id DESC LIMIT ?")
    params.append(limit)

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(" ".join(sql), tuple(params))
        rows = [dict(r) for r in await cur.fetchall()]

    # Reply counts for the thread starters on this page, in one query rather
    # than one per row.
    roots = [r["event_id"] for r in rows]
    counts: dict[str, int] = {}
    if roots and thread_root is None:
        placeholders = ",".join("?" for _ in roots)
        async with get_db() as db:
            cur = await db.execute(
                f"""SELECT thread_root_event_id, COUNT(*) AS n FROM channel_messages
                     WHERE thread_root_event_id IN ({placeholders})
                     GROUP BY thread_root_event_id""",
                tuple(roots),
            )
            counts = {r[0]: r[1] for r in await cur.fetchall()}
    for r in rows:
        r["reply_count"] = counts.get(r["event_id"], 0)
        r["author"] = r.pop("display_name", None) or f"relay:{r['pubkey'][:8]}"
    return rows
