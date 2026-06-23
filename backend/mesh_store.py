"""Block Mesh SQLite schema — 6 tables backing the /api/mesh/* router."""
import aiosqlite
from .db import DB_PATH


async def init_mesh_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        for stmt in [
            """CREATE TABLE IF NOT EXISTS mesh_agents (
                agent_id TEXT NOT NULL,
                block_id TEXT NOT NULL,
                vantage_name TEXT DEFAULT '',
                role TEXT DEFAULT 'home',
                capabilities_json TEXT DEFAULT '{}',
                trust_score REAL DEFAULT 50.0,
                commitments_made INTEGER DEFAULT 0,
                commitments_kept INTEGER DEFAULT 0,
                last_seen_at TEXT DEFAULT (datetime('now')),
                joined_at TEXT DEFAULT (datetime('now')),
                status TEXT DEFAULT 'active',
                PRIMARY KEY (agent_id, block_id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_mesh_agents_block ON mesh_agents(block_id)",
            "CREATE INDEX IF NOT EXISTS idx_mesh_agents_active ON mesh_agents(block_id, status)",
            """CREATE TABLE IF NOT EXISTS mesh_proposals (
                id TEXT PRIMARY KEY,
                block_id TEXT NOT NULL,
                proposer_id TEXT NOT NULL,
                respondent_id TEXT DEFAULT NULL,
                give_json TEXT NOT NULL DEFAULT '[]',
                take_json TEXT NOT NULL DEFAULT '[]',
                status TEXT DEFAULT 'open',
                ttl_ms INTEGER DEFAULT 300000,
                created_at TEXT DEFAULT (datetime('now')),
                resolved_at TEXT DEFAULT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_mesh_proposals_block ON mesh_proposals(block_id, status)",
            """CREATE TABLE IF NOT EXISTS mesh_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id TEXT NOT NULL,
                respondent_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                counter_json TEXT DEFAULT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            "CREATE INDEX IF NOT EXISTS idx_mesh_responses_proposal ON mesh_responses(proposal_id)",
            """CREATE TABLE IF NOT EXISTS mesh_commitments (
                id TEXT PRIMARY KEY,
                proposal_id TEXT NOT NULL,
                agent_a TEXT NOT NULL,
                agent_b TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'ServicePerform',
                terms_json TEXT NOT NULL DEFAULT '{}',
                fulfilled INTEGER DEFAULT 0,
                receipt_hash TEXT DEFAULT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            "CREATE INDEX IF NOT EXISTS idx_mesh_commitments_agents ON mesh_commitments(agent_a, agent_b)",
            """CREATE TABLE IF NOT EXISTS mesh_resources (
                id TEXT PRIMARY KEY,
                block_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                description TEXT DEFAULT '',
                capacity INTEGER DEFAULT 1,
                reserved_by TEXT DEFAULT NULL,
                reserved_until TEXT DEFAULT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            "CREATE INDEX IF NOT EXISTS idx_mesh_resources_block ON mesh_resources(block_id)",
            """CREATE TABLE IF NOT EXISTS mesh_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                block_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor_id TEXT DEFAULT NULL,
                payload_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            "CREATE INDEX IF NOT EXISTS idx_mesh_events_block ON mesh_events(block_id, created_at)",
        ]:
            try:
                await db.execute(stmt)
            except Exception:
                pass
        await db.commit()
