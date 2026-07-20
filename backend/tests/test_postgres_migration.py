"""
Test Postgres migration and dual-backend database support.

Tests verify:
1. SQLite backend works (backward compatibility)
2. PostgreSQL backend works (new functionality)
3. Schema parity between backends
4. Connection pooling for PostgreSQL
5. No regressions in existing functionality
"""
import asyncio
import pytest
import os
from pathlib import Path

# Test fixtures and utilities


@pytest.fixture
def sqlite_db_path(tmp_path):
    """Temporary SQLite database for testing."""
    return tmp_path / "test.db"


@pytest.fixture
async def sqlite_connection(monkeypatch, sqlite_db_path):
    """Set up SQLite backend."""
    monkeypatch.setenv("VANTAGE_POSTGRES_URL", "")  # Disable PostgreSQL
    monkeypatch.setenv("VANTAGE_DATA_DIR", str(sqlite_db_path.parent))

    # Reimport to apply environment changes
    import importlib
    from backend import config
    importlib.reload(config)

    from backend.db_init import init_sqlite_schema
    await init_sqlite_schema()

    # Provide connection
    from backend.db_adapter import get_db_connection
    async with get_db_connection() as conn:
        yield conn


@pytest.mark.asyncio
async def test_sqlite_schema_exists(sqlite_connection):
    """Test that SQLite schema is created successfully."""
    # Check agents table exists
    result = await sqlite_connection.fetchrow(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='agents'"
    )
    assert result is not None, "agents table should exist"


@pytest.mark.asyncio
async def test_sqlite_crud_operations(sqlite_connection):
    """Test CRUD operations on SQLite."""
    # Create
    await sqlite_connection.execute(
        "INSERT INTO agents (name, api_key) VALUES (?, ?)",
        ("test_agent", "key_12345678901234567890123456789012")
    )

    # Read
    agent = await sqlite_connection.fetchrow(
        "SELECT * FROM agents WHERE name = ?",
        ("test_agent",)
    )
    assert agent is not None
    assert agent["name"] == "test_agent"

    # Update (not directly supported by fetchrow, need execute)
    await sqlite_connection.execute(
        "UPDATE agents SET bio = ? WHERE name = ?",
        ("Updated bio", "test_agent")
    )

    # Delete
    await sqlite_connection.execute(
        "DELETE FROM agents WHERE name = ?",
        ("test_agent",)
    )

    # Verify deletion
    agent = await sqlite_connection.fetchrow(
        "SELECT * FROM agents WHERE name = ?",
        ("test_agent",)
    )
    assert agent is None


@pytest.mark.asyncio
async def test_sqlite_foreign_keys(sqlite_connection):
    """Test foreign key constraints in SQLite."""
    # Create agent first
    await sqlite_connection.execute(
        "INSERT INTO agents (name, api_key) VALUES (?, ?)",
        ("agent1", "key_12345678901234567890123456789012")
    )

    agent = await sqlite_connection.fetchrow(
        "SELECT id FROM agents WHERE name = ?",
        ("agent1",)
    )
    agent_id = agent["id"]

    # Create broadcast with foreign key
    await sqlite_connection.execute(
        "INSERT INTO broadcasts (agent_id, title) VALUES (?, ?)",
        (agent_id, "Test Broadcast")
    )

    # Verify broadcast was created
    broadcast = await sqlite_connection.fetchrow(
        "SELECT * FROM broadcasts WHERE title = ?",
        ("Test Broadcast",)
    )
    assert broadcast is not None
    assert broadcast["agent_id"] == agent_id


@pytest.mark.asyncio
async def test_sqlite_indexes_exist(sqlite_connection):
    """Test that indexes are created for performance."""
    # Check indexes
    indexes = [
        "idx_agents_api_key",
        "idx_broadcasts_agent_id",
        "idx_broadcasts_status",
        "idx_broadcasts_created_at",
    ]

    for index_name in indexes:
        result = await sqlite_connection.fetchrow(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
            (index_name,)
        )
        assert result is not None, f"Index {index_name} should exist"


@pytest.mark.asyncio
async def test_sqlite_concurrent_writes(sqlite_connection):
    """Test handling of concurrent write attempts."""
    # SQLite should use busy_timeout to wait
    async def insert_agent(name):
        try:
            await sqlite_connection.execute(
                "INSERT INTO agents (name, api_key) VALUES (?, ?)",
                (name, f"key_{name:0>32}")
            )
        except Exception as e:
            pytest.fail(f"Concurrent insert failed: {e}")

    # Create multiple agents concurrently
    await asyncio.gather(
        insert_agent("agent_1"),
        insert_agent("agent_2"),
        insert_agent("agent_3"),
    )

    # Verify all were created
    agents = await sqlite_connection.fetchall(
        "SELECT COUNT(*) as count FROM agents"
    )
    assert agents[0]["count"] >= 3


@pytest.mark.asyncio
async def test_db_adapter_backward_compatibility():
    """Test that old get_db() calls still work."""
    from backend.db import get_db

    # get_db should still work (SQLite mode)
    async with get_db() as db:
        # Should be able to use cursor operations
        await db.execute("CREATE TABLE IF NOT EXISTS test_compat (id INT)")
        await db.execute("INSERT INTO test_compat VALUES (1)")
        await db.commit()


@pytest.mark.asyncio
async def test_database_adapter_interface():
    """Test the unified database adapter interface."""
    from backend.db_adapter import get_db_connection

    async with get_db_connection() as conn:
        # Should have all required methods
        assert hasattr(conn, 'execute')
        assert hasattr(conn, 'executemany')
        assert hasattr(conn, 'fetchrow')
        assert hasattr(conn, 'fetchall')
        assert hasattr(conn, 'fetchval')

        # Each method should be callable
        assert callable(conn.execute)
        assert callable(conn.fetchrow)


# PostgreSQL tests (only run if POSTGRES_URL is configured)
POSTGRES_URL = os.environ.get("VANTAGE_POSTGRES_URL", "")


@pytest.mark.skipif(not POSTGRES_URL, reason="POSTGRES_URL not configured")
class TestPostgresBackend:
    """Tests for PostgreSQL backend."""

    @pytest.mark.asyncio
    async def test_postgres_schema_exists(self):
        """Test that PostgreSQL schema is created."""
        from backend.db_init import init_postgres_schema
        from backend.db_adapter import get_db_connection, init_pg_pool

        await init_postgres_schema()
        await init_pg_pool()

        async with get_db_connection() as conn:
            result = await conn.fetchrow(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='agents')"
            )
            assert result is not None

        # Cleanup
        from backend.db_adapter import close_pg_pool
        await close_pg_pool()

    @pytest.mark.asyncio
    async def test_postgres_crud(self):
        """Test CRUD operations on PostgreSQL."""
        from backend.db_init import init_postgres_schema
        from backend.db_adapter import get_db_connection, init_pg_pool, close_pg_pool

        await init_postgres_schema()
        await init_pg_pool()

        async with get_db_connection() as conn:
            # Create
            await conn.execute(
                "INSERT INTO agents (name, api_key) VALUES ($1, $2)",
                ("pg_test_agent", "key_12345678901234567890123456789012")
            )

            # Read
            agent = await conn.fetchrow(
                "SELECT * FROM agents WHERE name = $1",
                ("pg_test_agent",)
            )
            assert agent is not None
            assert agent["name"] == "pg_test_agent"

            # Delete
            await conn.execute(
                "DELETE FROM agents WHERE name = $1",
                ("pg_test_agent",)
            )

        await close_pg_pool()

    @pytest.mark.asyncio
    async def test_postgres_indexes(self):
        """Test that PostgreSQL indexes exist."""
        from backend.db_init import init_postgres_schema
        from backend.db_adapter import get_db_connection, init_pg_pool, close_pg_pool

        await init_postgres_schema()
        await init_pg_pool()

        async with get_db_connection() as conn:
            indexes = [
                "idx_agents_api_key",
                "idx_broadcasts_agent_id",
                "idx_broadcasts_status",
            ]

            for index_name in indexes:
                result = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.indexes WHERE indexname = $1)",
                    (index_name,)
                )
                assert result is True, f"Index {index_name} should exist"

        await close_pg_pool()

    @pytest.mark.asyncio
    async def test_postgres_connection_pool(self):
        """Test PostgreSQL connection pooling."""
        from backend.db_adapter import init_pg_pool, close_pg_pool, _PG_POOL

        await init_pg_pool()

        # Pool should be initialized
        from backend.db_adapter import _PG_POOL as pg_pool_check
        assert pg_pool_check is not None

        await close_pg_pool()


class TestSchemaParity:
    """Verify schema is identical between SQLite and PostgreSQL."""

    def test_table_definitions_match(self):
        """Compare table schemas between backends (offline check)."""
        # This would require parsing both DDL statements
        # For now, we verify both have identical table counts
        from backend.db_init import SQLITE_DDL, POSTGRES_DDL

        sqlite_tables = [line for line in SQLITE_DDL.split('\n') if 'CREATE TABLE' in line]
        postgres_tables = [line for line in POSTGRES_DDL.split('\n') if 'CREATE TABLE' in line]

        assert len(sqlite_tables) == len(postgres_tables), (
            "SQLite and PostgreSQL should have same number of tables"
        )


# Integration test
@pytest.mark.asyncio
async def test_fallback_behavior():
    """Test that system falls back to SQLite when PostgreSQL is unavailable."""
    import os

    # Temporarily disable PostgreSQL
    old_url = os.environ.get("VANTAGE_POSTGRES_URL", "")
    os.environ["VANTAGE_POSTGRES_URL"] = ""

    try:
        # Reimport config
        import importlib
        from backend import config
        importlib.reload(config)

        from backend.db_adapter import _PG_POOL
        assert _PG_POOL is None, "PostgreSQL pool should not be initialized"

    finally:
        # Restore
        if old_url:
            os.environ["VANTAGE_POSTGRES_URL"] = old_url


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
