"""Shared sync DB helper for standalone /opt/ares daemon scripts (not part
of the backend.* package). Same dispatch logic as backend/db.py's
get_sync_db(): plain sqlite3 by default, or the Postgres shim once
VANTAGE_POSTGRES_URL is set on this process's environment (systemd
Environment= line).

2026-08-29: added PRAGMA journal_mode=WAL to get_sync_db() (was missing --
the real root cause of the fleet-wide "database is locked" incident; see
memory sqlite-vs-postgres-ecosystem-decision.md). Without WAL, SQLite
takes an exclusive lock on every write, blocking every other reader/writer
on vantage.db -- this shim is already the shared connection point for 6
daemons (pumpfun_tier_scanner, pumpfun_launch_radar, social_tracker,
tracked_wallet_balance_updater, trade_outcome_learner, wallet_learner),
so this one change fixes lock contention for all of them at once, matching
the proven fix already live in Vantage's own backend/db.py (WAL +
busy_timeout=30000, task #9, zero issues since).

Also added connect_wal() -- a GENERIC version of the same real fix (WAL +
configurable busy_timeout) for any sqlite file path, not just vantage.db.
Added specifically to retrofit the ares_autotrade.py family (6 daemons,
~10 distinct db files: autotrade.db, SOCIAL_DB, GRAPH_DB, INTEL_DB,
WALLET_DB, FOLLOW_DB, ALPHA_DB, COPY_DB, SHIELD_DB, HIVE_DB, PNL_DB --
the worst real offender, 23,622 'database is locked' occurrences) onto
the same one shared, reusable pattern instead of each file re-implementing
its own ad-hoc (and inconsistent -- some set busy_timeout, none set WAL)
connection logic.
"""
import os
import sys

sys.path.insert(0, "/opt/ares/Vantage")

_DB_PATH = "/opt/ares/Vantage/data/vantage.db"


def get_sync_db():
    pg_url = os.environ.get("VANTAGE_POSTGRES_URL", "")
    if pg_url:
        from backend import pg_compat
        return pg_compat.get_sync_pg_db(pg_url)
    import sqlite3
    conn = sqlite3.connect(_DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout=20000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def connect_wal(db_path: str, timeout_ms: int = 30000):
    """Generic real fix for any sqlite file: WAL mode (readers don't block
    writers, writers don't block readers, only writer-vs-writer serializes)
    plus a real busy_timeout (wait up to timeout_ms for a lock instead of
    failing instantly with 'database is locked'). Purely a connection/
    pragma-layer change -- callers' actual query/transaction logic is
    unaffected, additive only, matching backend/db.py's already-proven
    production pattern. Use in place of a bare sqlite3.connect(path)."""
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=timeout_ms / 1000.0)
    conn.execute(f"PRAGMA busy_timeout={timeout_ms}")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
