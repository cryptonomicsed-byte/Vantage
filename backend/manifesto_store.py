"""Living Manifesto SQLite schema + consensus math.

A collective's manifesto is a set of Odù-backed clauses, each promoted
Individual → Swarm → Council → Canonical by accumulated vote weight. The
thresholds mirror IfáScript's `calabash::scaling` so the Vantage governance flow
agrees with the Rust engine that produces the divined Odù.
"""
import aiosqlite

from .db import DB_PATH

# Consensus thresholds (mirror ifascript::calabash::scaling).
SWARM_THRESHOLD = 2.0
COUNCIL_THRESHOLD = 5.0
CANONICAL_THRESHOLD = 10.0

# Levels at or above which a clause is part of the binding canon.
CANON_LEVELS = ("council", "canonical")


def vote_weight(voter_tier: int) -> float:
    """Weight a single vote carries, by tier. Lower tiers carry more individual
    weight; tier 0 is guarded against division by zero."""
    return 1.0 / max(int(voter_tier), 1)


def level_for_weight(weight: float) -> str:
    if weight >= CANONICAL_THRESHOLD:
        return "canonical"
    if weight >= COUNCIL_THRESHOLD:
        return "council"
    if weight >= SWARM_THRESHOLD:
        return "swarm"
    return "individual"


async def init_manifesto_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        for stmt in [
            """CREATE TABLE IF NOT EXISTS manifesto_clauses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collective TEXT NOT NULL,
                odu_id INTEGER DEFAULT 0,
                vessel TEXT DEFAULT '',
                odu_name TEXT DEFAULT '',
                principle TEXT NOT NULL,
                author TEXT DEFAULT '',
                level TEXT DEFAULT 'individual',
                weight REAL DEFAULT 0.0,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            "CREATE INDEX IF NOT EXISTS idx_manifesto_collective ON manifesto_clauses(collective)",
            "CREATE INDEX IF NOT EXISTS idx_manifesto_level ON manifesto_clauses(collective, level)",
        ]:
            try:
                await db.execute(stmt)
            except Exception:
                pass
        await db.commit()
