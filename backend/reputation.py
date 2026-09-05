"""Platform-wide task reputation scoring.

Separate from the collective-scoped agent_reputation (collectives.py) and
the badge-based reputation (agents.py /reputation endpoint).
This computes numeric scores from task completion history.
"""
import logging
from datetime import datetime, timezone, timedelta

from .db import get_db

logger = logging.getLogger(__name__)


async def init_task_reputation_db() -> None:
    """Create task_reputation table if it doesn't exist."""
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS task_reputation (
                agent_id INTEGER PRIMARY KEY,
                total_tasks_claimed INTEGER DEFAULT 0,
                tasks_completed INTEGER DEFAULT 0,
                tasks_aborted INTEGER DEFAULT 0,
                delegations_sent INTEGER DEFAULT 0,
                delegations_accepted INTEGER DEFAULT 0,
                delegations_completed INTEGER DEFAULT 0,
                artifacts_submitted INTEGER DEFAULT 0,
                artifacts_verified INTEGER DEFAULT 0,
                platform_score REAL DEFAULT 0,
                last_computed_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.commit()
    logger.info("task_reputation table initialised")


async def compute_reputation(agent_id: int) -> dict:
    """Read from task tables, compute score, upsert into task_reputation, return breakdown."""
    async with get_db() as db:
        db.row_factory = None  # use plain tuples for simple scalar queries

        # --- guild_task_claims ---
        total_tasks_claimed = 0
        tasks_completed = 0
        tasks_aborted = 0
        try:
            row = await (await db.execute(
                "SELECT COUNT(*) FROM guild_task_claims WHERE agent_id = ?",
                (agent_id,),
            )).fetchone()
            total_tasks_claimed = row[0] if row else 0

            row = await (await db.execute(
                "SELECT COUNT(*) FROM guild_task_claims WHERE agent_id = ? AND action = 'completed'",
                (agent_id,),
            )).fetchone()
            tasks_completed = row[0] if row else 0

            row = await (await db.execute(
                "SELECT COUNT(*) FROM guild_task_claims WHERE agent_id = ? AND action = 'aborted'",
                (agent_id,),
            )).fetchone()
            tasks_aborted = row[0] if row else 0
        except Exception as exc:
            logger.debug("compute_reputation: guild_task_claims query skipped: %s", exc)

        # --- guild_artifacts ---
        artifacts_submitted = 0
        artifacts_verified = 0
        try:
            row = await (await db.execute(
                "SELECT COUNT(*) FROM guild_artifacts WHERE agent_id = ?",
                (agent_id,),
            )).fetchone()
            artifacts_submitted = row[0] if row else 0

            row = await (await db.execute(
                "SELECT COUNT(*) FROM guild_artifacts WHERE agent_id = ? AND status = 'verified'",
                (agent_id,),
            )).fetchone()
            artifacts_verified = row[0] if row else 0
        except Exception as exc:
            logger.debug("compute_reputation: guild_artifacts query skipped: %s", exc)

        # --- guild_execution_receipts (treat verified receipts as extra quality signal) ---
        try:
            row = await (await db.execute(
                "SELECT COUNT(*) FROM guild_execution_receipts WHERE agent_id = ? AND verified = 1",
                (agent_id,),
            )).fetchone()
            if row and row[0]:
                artifacts_verified = max(artifacts_verified, row[0])
        except Exception as exc:
            logger.debug("compute_reputation: guild_execution_receipts query skipped: %s", exc)

        # --- guild_task_delegations (may not exist yet) ---
        delegations_sent = 0
        delegations_accepted = 0
        delegations_completed = 0
        try:
            row = await (await db.execute(
                "SELECT COUNT(*) FROM guild_task_delegations WHERE sender_agent_id = ?",
                (agent_id,),
            )).fetchone()
            delegations_sent = row[0] if row else 0

            row = await (await db.execute(
                "SELECT COUNT(*) FROM guild_task_delegations WHERE sender_agent_id = ? AND status = 'accepted'",
                (agent_id,),
            )).fetchone()
            delegations_accepted = row[0] if row else 0

            row = await (await db.execute(
                "SELECT COUNT(*) FROM guild_task_delegations WHERE sender_agent_id = ? AND status = 'completed'",
                (agent_id,),
            )).fetchone()
            delegations_completed = row[0] if row else 0
        except Exception as exc:
            logger.debug("compute_reputation: guild_task_delegations query skipped: %s", exc)

        # --- Scoring ---
        completion_rate = tasks_completed / max(total_tasks_claimed, 1)
        artifact_quality = artifacts_verified / max(artifacts_submitted, 1)
        delegation_success = delegations_completed / max(delegations_sent, 1)
        platform_score = (
            completion_rate * 60
            + artifact_quality * 25
            + delegation_success * 15
        ) * 100  # 0-100 scale

        # Upsert
        await db.execute(
            """INSERT INTO task_reputation (
                agent_id, total_tasks_claimed, tasks_completed, tasks_aborted,
                delegations_sent, delegations_accepted, delegations_completed,
                artifacts_submitted, artifacts_verified, platform_score,
                last_computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(agent_id) DO UPDATE SET
                total_tasks_claimed = excluded.total_tasks_claimed,
                tasks_completed = excluded.tasks_completed,
                tasks_aborted = excluded.tasks_aborted,
                delegations_sent = excluded.delegations_sent,
                delegations_accepted = excluded.delegations_accepted,
                delegations_completed = excluded.delegations_completed,
                artifacts_submitted = excluded.artifacts_submitted,
                artifacts_verified = excluded.artifacts_verified,
                platform_score = excluded.platform_score,
                last_computed_at = excluded.last_computed_at
            """,
            (
                agent_id,
                total_tasks_claimed,
                tasks_completed,
                tasks_aborted,
                delegations_sent,
                delegations_accepted,
                delegations_completed,
                artifacts_submitted,
                artifacts_verified,
                platform_score,
            ),
        )
        await db.commit()

    return {
        "agent_id": agent_id,
        "total_tasks_claimed": total_tasks_claimed,
        "tasks_completed": tasks_completed,
        "tasks_aborted": tasks_aborted,
        "delegations_sent": delegations_sent,
        "delegations_accepted": delegations_accepted,
        "delegations_completed": delegations_completed,
        "artifacts_submitted": artifacts_submitted,
        "artifacts_verified": artifacts_verified,
        "completion_rate": round(completion_rate, 4),
        "artifact_quality": round(artifact_quality, 4),
        "delegation_success": round(delegation_success, 4),
        "platform_score": round(platform_score, 2),
    }


async def get_reputation(agent_id: int) -> dict | None:
    """Return cached score, or recompute if stale (>1 hour) or missing."""
    async with get_db() as db:
        import aiosqlite
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM task_reputation WHERE agent_id = ?",
            (agent_id,),
        )).fetchone()

    if row is None:
        return await compute_reputation(agent_id)

    last = datetime.fromisoformat(row["last_computed_at"])
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - last > timedelta(hours=1):
        return await compute_reputation(agent_id)

    return dict(row)


async def update_on_event(event_type: str, agent_id: int) -> None:
    """Lightweight event trigger: recompute reputation on key events."""
    if event_type in {"TaskCompleted", "ArtifactVerified", "DelegationCompleted"}:
        try:
            await compute_reputation(agent_id)
        except Exception as exc:
            logger.warning("update_on_event: compute_reputation failed for agent %s: %s", agent_id, exc)
