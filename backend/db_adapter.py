"""
Database adapter: unified interface for SQLite and PostgreSQL backends.
Maintains backward compatibility while enabling production-grade PostgreSQL support.
"""
import logging
from contextlib import asynccontextmanager
from typing import Optional, Any
import asyncpg
import aiosqlite

from .config import settings

logger = logging.getLogger(__name__)

# Global connection pool (PostgreSQL only)
_PG_POOL: Optional[asyncpg.Pool] = None


async def init_pg_pool() -> None:
    """Initialize PostgreSQL connection pool at startup."""
    global _PG_POOL
    if not settings.POSTGRES_URL:
        logger.info("POSTGRES_URL not set; using SQLite backend")
        return

    try:
        _PG_POOL = await asyncpg.create_pool(
            settings.POSTGRES_URL,
            min_size=settings.POSTGRES_POOL_MIN,
            max_size=settings.POSTGRES_POOL_MAX,
            timeout=settings.POSTGRES_POOL_TIMEOUT,
            command_timeout=30,
        )
        logger.info("PostgreSQL connection pool initialized: %d-%d connections",
                   settings.POSTGRES_POOL_MIN, settings.POSTGRES_POOL_MAX)
    except Exception as e:
        logger.error("Failed to initialize PostgreSQL pool: %s", e)
        raise


async def close_pg_pool() -> None:
    """Close PostgreSQL connection pool at shutdown."""
    global _PG_POOL
    if _PG_POOL:
        await _PG_POOL.close()
        _PG_POOL = None
        logger.info("PostgreSQL connection pool closed")


@asynccontextmanager
async def get_db_connection():
    """
    Get a database connection (PostgreSQL or SQLite).

    Usage:
        async with get_db_connection() as conn:
            result = await conn.fetchrow("SELECT ...")
            await conn.execute("INSERT INTO ...")

    For SQLite: wraps aiosqlite cursor
    For PostgreSQL: returns asyncpg connection
    """
    if settings.POSTGRES_URL and _PG_POOL:
        # PostgreSQL: get from pool
        async with _PG_POOL.acquire() as conn:
            yield PostgresConnection(conn)
    else:
        # SQLite: create new connection
        from .db import DB_PATH
        async with aiosqlite.connect(DB_PATH) as sqlite_conn:
            await sqlite_conn.execute("PRAGMA busy_timeout=20000")
            yield SqliteConnection(sqlite_conn)


class DbConnection:
    """Base class for database connection adapters."""

    async def execute(self, query: str, params: tuple = ()) -> None:
        """Execute a query without returning results."""
        raise NotImplementedError

    async def executemany(self, query: str, params_list: list) -> None:
        """Execute multiple queries with different parameters."""
        raise NotImplementedError

    async def fetchrow(self, query: str, params: tuple = ()) -> Optional[dict]:
        """Fetch a single row as a dict."""
        raise NotImplementedError

    async def fetchall(self, query: str, params: tuple = ()) -> list[dict]:
        """Fetch all rows as a list of dicts."""
        raise NotImplementedError

    async def fetchval(self, query: str, params: tuple = ()) -> Any:
        """Fetch a single scalar value."""
        raise NotImplementedError


class PostgresConnection(DbConnection):
    """PostgreSQL connection adapter using asyncpg."""

    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def execute(self, query: str, params: tuple = ()) -> None:
        await self.conn.execute(query, *params)

    async def executemany(self, query: str, params_list: list) -> None:
        for params in params_list:
            await self.conn.execute(query, *params)

    async def fetchrow(self, query: str, params: tuple = ()) -> Optional[dict]:
        row = await self.conn.fetchrow(query, *params)
        return dict(row) if row else None

    async def fetchall(self, query: str, params: tuple = ()) -> list[dict]:
        rows = await self.conn.fetch(query, *params)
        return [dict(row) for row in rows]

    async def fetchval(self, query: str, params: tuple = ()) -> Any:
        return await self.conn.fetchval(query, *params)


class SqliteConnection(DbConnection):
    """SQLite connection adapter using aiosqlite."""

    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    async def execute(self, query: str, params: tuple = ()) -> None:
        await self.conn.execute(query, params)
        await self.conn.commit()

    async def executemany(self, query: str, params_list: list) -> None:
        for params in params_list:
            await self.conn.execute(query, params)
        await self.conn.commit()

    async def fetchrow(self, query: str, params: tuple = ()) -> Optional[dict]:
        cursor = await self.conn.execute(query, params)
        row = await cursor.fetchone()
        if not row:
            return None
        # Convert tuple to dict using cursor description
        return {col[0]: val for col, val in zip(cursor.description, row)}

    async def fetchall(self, query: str, params: tuple = ()) -> list[dict]:
        cursor = await self.conn.execute(query, params)
        rows = await cursor.fetchall()
        if not rows:
            return []
        # Convert tuples to dicts using cursor description
        return [{col[0]: val for col, val in zip(cursor.description, row)} for row in rows]

    async def fetchval(self, query: str, params: tuple = ()) -> Any:
        cursor = await self.conn.execute(query, params)
        row = await cursor.fetchone()
        return row[0] if row else None


# Backward compatibility: wrap existing get_db() calls
@asynccontextmanager
async def get_db():
    """
    Compatibility wrapper for existing code.
    For direct cursor operations, use get_db_connection() instead.
    """
    async with get_db_connection() as conn:
        # Provide raw connection interface for backward compatibility
        if isinstance(conn, PostgresConnection):
            yield conn.conn
        else:
            yield conn.conn
