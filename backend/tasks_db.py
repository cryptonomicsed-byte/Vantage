"""Schema for sovereign agent task/artifact/memory tables."""
from .db import get_db


async def init_tasks_db() -> None:
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS vantage_tasks (
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
            "CREATE INDEX IF NOT EXISTS idx_tasks_guild_status ON vantage_tasks(guild_id, status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_claimed ON vantage_tasks(claimed_by_id, status)"
        )

        await db.execute("""
            CREATE TABLE IF NOT EXISTS task_claims (
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
            "CREATE INDEX IF NOT EXISTS idx_claims_task ON task_claims(task_id)"
        )

        await db.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
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
            "CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id)"
        )

        await db.execute("""
            CREATE TABLE IF NOT EXISTS execution_receipts (
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

        await db.commit()
