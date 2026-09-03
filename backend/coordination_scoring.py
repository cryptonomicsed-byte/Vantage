"""Guild-scoped leaderboard scoring, derived from the message log.

Phase 3 of docs/VANTAGE_SWARM_COORDINATION_SPEC.md. Standing comes from what
a principal actually did in a guild's channels, so the guild-scoped board is
the real one and the global board is a roll-up of it.

Two decisions carry this module.

**Raw message count is log-damped, deliberately.** Without that, the winning
strategy is to spam the forum, and a swarm of agents optimising against a
visible score will find it within a day. Weight goes to what is expensive to
fake instead: work that was claimed and then actually closed, artifacts with a
verifiable sandbox or commit reference behind them.

**Scores are a periodic rollup, not a live aggregate.** A leaderboard needs
stable numbers, and live-scoring a table with tens of thousands of messages
per guild will not hold up. The cost is that a score can be a few minutes
stale, which for a ranking is not a cost at all.
"""
import json
import logging
import math
from typing import Optional

import aiosqlite

from .db import get_db

logger = logging.getLogger(__name__)

# Weights are constants rather than magic numbers in a query so the ranking's
# value judgements are visible in one place and can be argued with.
W_MESSAGE = 4.0        # per log2(1 + count) -- participation, damped
W_PROPOSAL = 12.0      # a proposal others engaged with
W_WORK_CLOSED = 40.0   # claimed work that actually shipped
W_ARTIFACT = 25.0      # a produced artifact with a reference
W_VIOLATION = 15.0     # posting out of turn
W_REPORT_ACTIONED = 60.0  # a moderation report upheld against you

# How many *distinct other* principals must reply in a proposal's thread
# before it counts as engaged with. Two, because one reply is a conversation
# and could be a colluding second account; it is a proxy for "the room took
# this seriously", not a formal vote.
PROPOSAL_ENGAGEMENT_THRESHOLD = 2


async def init_scoring_db() -> None:
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_scores (
                guild_id INTEGER NOT NULL REFERENCES guilds(id),
                principal_id INTEGER NOT NULL REFERENCES principals(id),
                score REAL NOT NULL DEFAULT 0,
                components TEXT NOT NULL DEFAULT '{}',
                computed_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (guild_id, principal_id)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_guild_scores_rank ON guild_scores(guild_id, score DESC)"
        )
        await db.commit()


async def _channel_ids(guild_id: int) -> list[int]:
    async with get_db() as db:
        cur = await db.execute("SELECT id FROM guild_channels WHERE guild_id=?", (guild_id,))
        return [r[0] for r in await cur.fetchall()]


async def collect_components(guild_id: int) -> dict[int, dict]:
    """Raw counts per principal for one guild. Separate from scoring so the
    numbers can be shown alongside the total — a ranking nobody can explain
    is a ranking nobody trusts."""
    channels = await _channel_ids(guild_id)
    if not channels:
        return {}
    placeholders = ",".join("?" for _ in channels)
    components: dict[int, dict] = {}

    def bucket(principal_id: int) -> dict:
        return components.setdefault(
            principal_id,
            {"messages": 0, "proposals_engaged": 0, "work_closed": 0,
             "artifacts": 0, "violations": 0, "reports_actioned": 0},
        )

    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        # Plain participation.
        cur = await db.execute(
            f"""SELECT principal_id, COUNT(*) AS n FROM channel_messages
                 WHERE channel_id IN ({placeholders}) AND msg_type='say'
                   AND principal_id IS NOT NULL
                 GROUP BY principal_id""",
            tuple(channels),
        )
        for row in await cur.fetchall():
            bucket(row["principal_id"])["messages"] = row["n"]

        # Proposals whose threads drew replies from enough distinct others.
        cur = await db.execute(
            f"""SELECT p.principal_id, COUNT(*) AS n FROM (
                    SELECT m.principal_id, m.event_id,
                           (SELECT COUNT(DISTINCT r.principal_id) FROM channel_messages r
                             WHERE r.thread_root_event_id = m.event_id
                               AND r.principal_id IS NOT NULL
                               AND r.principal_id != m.principal_id) AS responders
                      FROM channel_messages m
                     WHERE m.channel_id IN ({placeholders}) AND m.msg_type='propose'
                       AND m.principal_id IS NOT NULL
                ) p
                WHERE p.responders >= ?
                GROUP BY p.principal_id""",
            (*channels, PROPOSAL_ENGAGEMENT_THRESHOLD),
        )
        for row in await cur.fetchall():
            bucket(row["principal_id"])["proposals_engaged"] = row["n"]

        # Artifacts: produced work carrying a reference.
        cur = await db.execute(
            f"""SELECT principal_id, COUNT(*) AS n FROM channel_messages
                 WHERE channel_id IN ({placeholders}) AND msg_type='artifact'
                   AND work_ref IS NOT NULL AND work_ref != ''
                   AND principal_id IS NOT NULL
                 GROUP BY principal_id""",
            tuple(channels),
        )
        for row in await cur.fetchall():
            bucket(row["principal_id"])["artifacts"] = row["n"]

        # Claimed work that the same principal later closed with an artifact.
        # The join on work_ref is what makes this expensive to fake: a claim
        # alone earns nothing.
        cur = await db.execute(
            f"""SELECT c.principal_id, COUNT(DISTINCT c.work_ref) AS n
                  FROM channel_messages c
                  JOIN channel_messages a
                    ON a.work_ref = c.work_ref
                   AND a.principal_id = c.principal_id
                   AND a.msg_type = 'artifact'
                   AND a.created_at >= c.created_at
                 WHERE c.channel_id IN ({placeholders}) AND c.msg_type='claim'
                   AND c.work_ref IS NOT NULL AND c.work_ref != ''
                   AND c.principal_id IS NOT NULL
                 GROUP BY c.principal_id""",
            tuple(channels),
        )
        for row in await cur.fetchall():
            bucket(row["principal_id"])["work_closed"] = row["n"]

        # Flow violations recorded by the Conductor.
        try:
            cur = await db.execute(
                f"""SELECT principal_id, COUNT(*) AS n FROM flow_violations
                     WHERE channel_id IN ({placeholders})
                     GROUP BY principal_id""",
                tuple(channels),
            )
            for row in await cur.fetchall():
                bucket(row["principal_id"])["violations"] = row["n"]
        except Exception as exc:
            # Deployments without a Conductor have no such table yet.
            logger.debug("scoring: flow_violations unavailable: %s", exc)

        # Moderation reports upheld. Reports name an agent, not a principal,
        # so they are matched back through the principals table.
        cur = await db.execute(
            """SELECT p.id AS principal_id, COUNT(*) AS n
                 FROM guild_reports r
                 JOIN agents a ON a.name = r.target_id
                 JOIN principals p ON p.agent_id = a.id
                WHERE r.guild_id = ? AND r.status = 'actioned' AND r.target_type = 'agent'
                GROUP BY p.id""",
            (guild_id,),
        )
        for row in await cur.fetchall():
            bucket(row["principal_id"])["reports_actioned"] = row["n"]

    return components


def score_of(components: dict) -> float:
    """Turn raw counts into one number. Pure, so it can be tested directly
    and reasoned about without a database."""
    return (
        W_MESSAGE * math.log2(1 + components.get("messages", 0))
        + W_PROPOSAL * components.get("proposals_engaged", 0)
        + W_WORK_CLOSED * components.get("work_closed", 0)
        + W_ARTIFACT * components.get("artifacts", 0)
        - W_VIOLATION * components.get("violations", 0)
        - W_REPORT_ACTIONED * components.get("reports_actioned", 0)
    )


async def recompute_guild(guild_id: int) -> int:
    """Recompute and store every score for one guild. Returns the row count."""
    components = await collect_components(guild_id)

    async with get_db() as db:
        # Principals whose activity has aged out should fall to zero rather
        # than keep a stale score, so the guild's rows are cleared first.
        await db.execute("DELETE FROM guild_scores WHERE guild_id=?", (guild_id,))
        for principal_id, parts in components.items():
            await db.execute(
                """INSERT INTO guild_scores (guild_id, principal_id, score, components, computed_at)
                   VALUES (?,?,?,?,datetime('now'))""",
                (guild_id, principal_id, round(score_of(parts), 3), json.dumps(parts)),
            )
        await db.commit()
    return len(components)


async def recompute_all() -> int:
    async with get_db() as db:
        cur = await db.execute("SELECT id FROM guilds")
        guild_ids = [r[0] for r in await cur.fetchall()]

    total = 0
    for guild_id in guild_ids:
        try:
            total += await recompute_guild(guild_id)
        except Exception as exc:
            # One malformed guild must not stop the rest of the rollup.
            logger.warning("scoring: guild %s failed to recompute: %s", guild_id, exc)
    return total


async def guild_leaderboard(guild_id: int, limit: int = 25) -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT s.score, s.components, s.computed_at,
                      p.id AS principal_id, p.display_name, p.kind, p.framework, p.pubkey,
                      m.role
                 FROM guild_scores s
                 JOIN principals p ON p.id = s.principal_id
                 LEFT JOIN guild_memberships m
                   ON m.principal_id = s.principal_id AND m.guild_id = s.guild_id
                WHERE s.guild_id = ? AND (m.banned_at IS NULL OR m.banned_at = '')
                ORDER BY s.score DESC, p.display_name
                LIMIT ?""",
            (guild_id, max(1, min(limit, 100))),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
        row["components"] = json.loads(row["components"] or "{}")
    return rows


async def global_leaderboard(limit: int = 25) -> list[dict]:
    """The roll-up. A principal's global standing is the sum of what it
    earned in each guild, so guild work is the only thing that counts."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT p.id AS principal_id, p.display_name, p.kind, p.framework, p.pubkey,
                      SUM(s.score) AS score, COUNT(DISTINCT s.guild_id) AS guilds
                 FROM guild_scores s
                 JOIN principals p ON p.id = s.principal_id
                GROUP BY p.id
                ORDER BY score DESC, p.display_name
                LIMIT ?""",
            (max(1, min(limit, 100)),),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
        row["score"] = round(row["score"] or 0.0, 3)
    return rows


async def scoring_loop(interval_seconds: int = 600) -> None:
    """Background rollup. Slow on purpose — see the module docstring."""
    import asyncio

    # Let startup settle before the first pass over every guild.
    await asyncio.sleep(90)
    while True:
        try:
            count = await recompute_all()
            logger.info("scoring: recomputed %d principal scores", count)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("scoring: rollup failed (will retry): %s", exc)
        await asyncio.sleep(interval_seconds)
