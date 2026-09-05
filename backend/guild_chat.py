"""Guild channels as live chat: mentions, agent dispatch, slash commands.

The forum layer (backend/coordination.py) gave channels a durable message
log. This is what makes one feel like a room: you write `@someone`, they are
actually addressed, and if they are an agent this instance hosts, they
actually answer.

Three things live here.

**Multi-mention.** `buzz_inbound.py` already dispatches an `@name` to that
agent's brain, but with `_MENTION_RE.search()` — the *first* mention only.
In a room where agents talk to each other, "@alice @bob what do you think"
has to reach both. This parses every mention and addresses each one.

**Loop safety.** Agents mentioning agents is the point, and it is also an
infinite loop waiting to happen: A answers by mentioning B, B answers by
mentioning A, forever, each turn a real relay event and a real model call.
Every dispatched reply carries a depth tag and stops being dispatched past
`MAX_DISPATCH_DEPTH`. A conversation can bounce a few times and then has to
be picked up by a person.

**Slash commands.** Vantage's whole API is already a skill registry
generated from the live route table. A slash command is just a skill invoked
from the composer, so the palette is derived from that registry rather than
hand-listed — a new router shows up in chat without anyone maintaining a
list.
"""
import logging
import re
from typing import Optional

import aiosqlite

from . import coordination as coord
from .db import get_db

logger = logging.getLogger(__name__)

# Deliberately the same shape buzz_inbound.py already uses, so a mention
# means one thing across the platform.
MENTION_RE = re.compile(r"@([A-Za-z0-9_.-]+)")

# How many times a mention may bounce between agents before a person has to
# join in. Three is enough for "ask, answer, follow-up" and short enough that
# a runaway costs a handful of calls rather than a bill.
MAX_DISPATCH_DEPTH = 3

# Mentions parsed per message. A message naming forty agents is not a
# conversation, it is a broadcast, and probably an attack on your model spend.
MAX_MENTIONS = 8


def parse_mentions(content: str) -> list[str]:
    """Every @name in a message, in order, de-duplicated.

    Order is kept because "@alice @bob" reads as addressing alice first, and
    the UI shows them in that order.
    """
    seen: list[str] = []
    for match in MENTION_RE.finditer(content or ""):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
        if len(seen) >= MAX_MENTIONS:
            break
    return seen


async def resolve_mentions(guild_id: int, names: list[str]) -> list[dict]:
    """Turn @names into principals, limited to members of this guild.

    Scoping to the guild is what stops a mention being a way to page any
    agent on the instance from a room they are not in.
    """
    if not names:
        return []

    resolved: list[dict] = []
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        for name in names:
            cur = await db.execute(
                """SELECT p.* FROM principals p
                     JOIN guild_memberships m ON m.principal_id = p.id
                    WHERE m.guild_id = ? AND m.banned_at IS NULL
                      AND LOWER(p.display_name) = LOWER(?)
                    LIMIT 1""",
                (guild_id, name),
            )
            row = await cur.fetchone()
            if row:
                resolved.append(dict(row))
    return resolved


def dispatch_depth(event_or_tags) -> int:
    """Read the loop-guard depth off a message's tags."""
    tags = event_or_tags.get("tags", []) if isinstance(event_or_tags, dict) else event_or_tags
    for tag in tags or []:
        if tag and len(tag) >= 2 and tag[0] == "vdepth":
            try:
                return int(tag[1])
            except (TypeError, ValueError):
                return 0
    return 0


async def dispatchable_agents(principals: list[dict]) -> list[dict]:
    """Of the mentioned principals, the ones this instance can answer for.

    Two exclusions, both correct rather than incidental:

      * **Self-custody principals.** This instance cannot sign for them, so
        it cannot post a reply as them. They still receive the mention (the
        `p` tag is on the event) and answer with their own key, which is
        exactly what holding your own key means.
      * **Humans.** A mention addressed to a person is a notification, not
        a prompt.
    """
    out = []
    for principal in principals:
        if principal.get("kind") != "agent" or not principal.get("agent_id"):
            continue
        if principal.get("key_custody") == "self":
            continue
        out.append(principal)
    return out


async def dispatch_to_mentioned(
    *, channel: dict, guild_slug: str, content: str, author_principal: dict,
    mentioned: list[dict], depth: int, root_event_id: Optional[str] = None,
) -> list[dict]:
    """Ask each mentioned agent to answer, and post its reply into the channel.

    Replies are published as the mentioned agent, signed with its own derived
    key, so the transcript shows the agent speaking rather than the platform
    speaking for it.

    Never raises: one agent failing to answer must not cost the human's
    message or the other agents' replies.
    """
    if depth >= MAX_DISPATCH_DEPTH:
        logger.info("guild_chat: dispatch depth %d reached, not answering further", depth)
        return []

    from .routers.copilot import _dispatch_chat

    replies = []
    for principal in await dispatchable_agents(mentioned):
        if principal["id"] == author_principal["id"]:
            continue  # an agent mentioning itself is not a question

        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM agents WHERE id=?", (principal["agent_id"],))
            row = await cur.fetchone()
        if not row:
            continue
        agent_row = dict(row)

        # Strip the mentions so the agent gets the question, not the routing.
        prompt = MENTION_RE.sub("", content).strip()
        if not prompt:
            continue

        try:
            result = await _dispatch_chat(agent_row, prompt)
        except Exception as exc:
            logger.warning("guild_chat: %s failed to answer: %s", agent_row.get("name"), exc)
            continue

        reply_text = (result or {}).get("reply") or (result or {}).get("text") or ""
        if not reply_text.strip():
            continue

        try:
            event = await coord.publish_message(
                channel=channel, guild_slug=guild_slug, principal=principal,
                content=reply_text, msg_type="say",
                root_event_id=root_event_id,
                addressed_to=author_principal.get("pubkey"),
                extra_tags=[["vdepth", str(depth + 1)]],
            )
            replies.append({
                "agent": principal["display_name"],
                "event_id": event["id"],
                "depth": depth + 1,
            })
        except coord.RelayUnavailable as exc:
            logger.warning("guild_chat: %s answered but the relay refused it: %s",
                           principal["display_name"], exc)
        except Exception as exc:
            logger.warning("guild_chat: could not publish %s's reply: %s",
                           principal["display_name"], exc)

    return replies


# ── slash commands ───────────────────────────────────────────────────────────

# Categories worth offering from a chat composer. The registry carries the
# whole API; most of it is not something you type mid-conversation.
CHAT_COMMAND_CATEGORIES = {
    "social", "swarm", "workspace", "guilds", "memory", "knowledge",
    "code", "intel", "trading", "publish", "market",
}

# Never offered in chat regardless of category: irreversible, spend-money, or
# identity-destroying operations deserve a deliberate surface, not a slash.
BLOCKED_COMMAND_PREFIXES = (
    "/api/identity/custody",      # destroys a sealed seed
    "/api/agents/me/wallet",      # moves funds
    "/api/trading/orders",        # places orders
    "/api/intel/exchange",        # shares your alpha
)


def build_command_palette(app) -> list[dict]:
    """Slash commands, derived from the live route registry.

    Generated rather than listed so a new router appears in chat on its own,
    and a removed one disappears instead of 404ing from a stale menu.
    """
    from .skills_registry import build_skills_registry

    registry = build_skills_registry(app)
    commands: list[dict] = []

    categories = registry.get("categories", registry) if isinstance(registry, dict) else {}
    for category, entries in (categories or {}).items():
        if category not in CHAT_COMMAND_CATEGORIES:
            continue
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path") or ""
            if any(path.startswith(prefix) for prefix in BLOCKED_COMMAND_PREFIXES):
                continue
            slug = (entry.get("id") or "").strip()
            if not slug:
                continue
            commands.append({
                "command": "/" + slug.replace("_", "-").lower()[:48],
                "label": entry.get("name") or slug,
                "category": category,
                "method": entry.get("method", "GET"),
                "path": path,
                "auth": entry.get("auth", ""),
                "summary": (entry.get("description") or "")[:200],
            })

    commands.sort(key=lambda c: (c["category"], c["command"]))
    return commands
