"""Schema for sovereign agent task/artifact/memory tables."""
from .db import get_db


async def init_tasks_db() -> None:
    async with get_db() as db:
        # guild_tasks: guild-scoped task board (distinct from workspace_tasks which use workspace_id)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_tasks (
                id TEXT PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                guild_slug TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'proposed',
                priority INTEGER NOT NULL DEFAULT 50,
                created_by_id INTEGER NOT NULL,
                created_by_name TEXT NOT NULL,
                claimed_by_id INTEGER,
                claimed_by_name TEXT,
                kind_tag TEXT DEFAULT '',
                nostr_event_id TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_guild_tasks_status ON guild_tasks(guild_id, status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_guild_tasks_claimed ON guild_tasks(claimed_by_id, status)"
        )

        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_task_claims (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                agent_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                action TEXT NOT NULL,
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_guild_claims_task ON guild_task_claims(task_id)"
        )

        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_artifacts (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                guild_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'other',
                title TEXT NOT NULL,
                content_text TEXT DEFAULT '',
                content_hash TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'submitted',
                review_note TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_guild_artifacts_task ON guild_artifacts(task_id)"
        )

        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_execution_receipts (
                id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                agent_id INTEGER NOT NULL,
                omokoda_receipt_id TEXT UNIQUE,
                receipt_body TEXT NOT NULL,
                verified INTEGER NOT NULL DEFAULT 0,
                verify_error TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_memory (
                id TEXT PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                visibility TEXT NOT NULL DEFAULT 'agent',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(guild_id, agent_id, key)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_guild_memory ON guild_memory(guild_id, visibility)"
        )

        # Sui settlement columns (added post-initial schema; ALTER is idempotent via try/except)
        try:
            await db.execute(
                "ALTER TABLE guild_execution_receipts ADD COLUMN sui_tx_digest TEXT DEFAULT NULL"
            )
        except Exception:
            pass
        try:
            await db.execute(
                "ALTER TABLE guild_execution_receipts ADD COLUMN settled_at TEXT DEFAULT NULL"
            )
        except Exception:
            pass

        # A2A task delegation table (P3)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_task_delegations (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                guild_slug TEXT NOT NULL,
                from_agent_id INTEGER NOT NULL,
                from_agent_name TEXT NOT NULL,
                to_agent_id INTEGER NOT NULL,
                to_agent_name TEXT NOT NULL,
                instructions TEXT DEFAULT '',
                deadline TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                reject_reason TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                accepted_at TEXT,
                completed_at TEXT,
                FOREIGN KEY (task_id) REFERENCES guild_tasks(id)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_delegations_task ON guild_task_delegations(task_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_delegations_to ON guild_task_delegations(to_agent_id, status)"
        )

        await db.commit()
