"""asyncpg-backed, aiosqlite-interface-compatible database shim.

Lets the existing ~600 `async with get_db() as db: await db.execute(sql, params)`
call sites across the backend keep working unchanged against real Postgres,
instead of hand-rewriting every one of them. Three real translation jobs
happen here, none of them guesswork -- each was scoped by grepping the
actual call sites in this codebase first (see the Postgres-migration deep
dive):

  1. `?` positional placeholders -> Postgres `$1, $2, ...`.
  2. SQLite's `datetime('now', 'X hours')` -> Postgres `now() + interval 'X hours'`.
     Every observed offset string in this codebase ('-24 hours', '+1 hour',
     etc.) is already valid Postgres interval syntax verbatim -- SQLite and
     Postgres happen to agree on this format, so no unit-parsing is needed.
  3. `CREATE TABLE`/`CREATE INDEX`/`CREATE VIRTUAL TABLE`/`PRAGMA` statements
     are silently no-op'd against Postgres. Every real table this backend
     needs already exists (migrated via pgloader from the live vantage.db
     mirror); these calls exist only as idempotent "create if missing"
     bootstrapping that SQLite needed and Postgres doesn't.

What this shim deliberately does NOT auto-translate: `INSERT OR IGNORE`/
`INSERT OR REPLACE` (16 occurrences, hand-fixed at each call site -- each
needs the table's real conflict target, which can't be safely guessed) and
the 3 SQL-side `strftime(...)` calls (hand-fixed in agents.py/deps.py).
`ON CONFLICT ... DO UPDATE SET x=excluded.x` needs no translation at all --
SQLite intentionally copied Postgres's own UPSERT syntax.
"""

import logging
import re
from contextlib import asynccontextmanager

import asyncpg
import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

_DDL_NOOP_RE = re.compile(
    r"^\s*(CREATE\s+(TABLE|INDEX|VIRTUAL\s+TABLE|TRIGGER)|PRAGMA|ALTER\s+TABLE\s+\S+\s+ADD\s+COLUMN)\b",
    re.IGNORECASE,
)

# SQLite stores all its 'timestamp' columns as plain TEXT in
# datetime('now')'s own format (YYYY-MM-DD HH:MM:SS, no timezone) -- there
# is no real timestamp type in SQLite. Every WHERE-clause comparison and
# INSERT against those columns in this codebase assumes that TEXT shape, so
# datetime('now', ...) is translated to a to_char(...)-produced TEXT string
# in that exact format rather than a Postgres timestamptz -- a timestamptz
# would satisfy the connection but fail every `text_col >= datetime(...)`
# comparison with "operator does not exist: text >= timestamp with time
# zone". This must run BEFORE the datetime() rewrite below, since strftime's
# only format string in this codebase ('%Y-%m-%d %H', for hourly buckets)
# wraps a literal datetime('now', ...) call as its second argument.
_STRFTIME_HOUR_NOW_RE = re.compile(r"strftime\(\s*'%Y-%m-%d %H'\s*,\s*'now'\s*\)", re.IGNORECASE)
_STRFTIME_HOUR_DATETIME_RE = re.compile(
    r"strftime\(\s*'%Y-%m-%d %H'\s*,\s*datetime\(\s*'now'\s*,\s*'([^']*)'\s*\)\s*\)",
    re.IGNORECASE,
)

_DATETIME_NOW_PARAM_RE = re.compile(r"datetime\(\s*'now'\s*,\s*\?\s*\)", re.IGNORECASE)
_DATETIME_NOW_LITERAL_RE = re.compile(r"datetime\(\s*'now'\s*,\s*'([^']*)'\s*\)", re.IGNORECASE)
_DATETIME_NOW_BARE_RE = re.compile(r"datetime\(\s*'now'\s*\)", re.IGNORECASE)

_INSERT_TABLE_RE = re.compile(r"^\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)

_SQLITE_TS_FMT = "YYYY-MM-DD HH24:MI:SS"

# SQLite's julianday(A) - julianday(B) computes a day-count difference
# between two datetime expressions (often scaled by *1440 for minutes or
# *24 for hours at each call site). Postgres has no julianday() function;
# translate the whole subtraction into an equivalent day-count via
# EXTRACT(EPOCH FROM ...) / 86400.0. Runs on raw SQL (both args can be
# 'now' or a bare column name -- 'now'::timestamptz is valid Postgres).
_JULIANDAY_DIFF_RE = re.compile(
    r"julianday\(\s*([^()]+?)\s*\)\s*-\s*julianday\(\s*([^()]+?)\s*\)",
    re.IGNORECASE,
)


def _translate_datetime_and_ddl(sql: str):
    """Shared first stage for both the async (asyncpg, `$N`) and sync
    (psycopg2, `%s`) paths -- everything except the final placeholder
    numbering, which differs between the two drivers. Returns None if the
    statement should be skipped entirely (DDL/PRAGMA bootstrapping)."""
    if _DDL_NOOP_RE.match(sql):
        return None
    sql = _STRFTIME_HOUR_NOW_RE.sub("to_char(now(), 'YYYY-MM-DD HH24')", sql)
    sql = _STRFTIME_HOUR_DATETIME_RE.sub(
        lambda m: f"to_char(now() + interval '{m.group(1)}', 'YYYY-MM-DD HH24')", sql
    )
    sql = _DATETIME_NOW_PARAM_RE.sub(
        f"to_char(now() + (?)::interval, '{_SQLITE_TS_FMT}')", sql
    )
    sql = _DATETIME_NOW_LITERAL_RE.sub(
        lambda m: f"to_char(now() + interval '{m.group(1)}', '{_SQLITE_TS_FMT}')", sql
    )
    sql = _DATETIME_NOW_BARE_RE.sub(f"to_char(now(), '{_SQLITE_TS_FMT}')", sql)
    sql = _JULIANDAY_DIFF_RE.sub(
        lambda m: f"(EXTRACT(EPOCH FROM (({m.group(1)})::timestamptz - ({m.group(2)})::timestamptz)) / 86400.0)",
        sql,
    )
    return sql


def _translate_sql(sql: str):
    """asyncpg variant: `?` -> `$1, $2, ...`."""
    sql = _translate_datetime_and_ddl(sql)
    if sql is None:
        return None
    counter = iter(range(1, 10_000))
    return re.sub(r"\?", lambda _: f"${next(counter)}", sql)


def _translate_sql_sync(sql: str):
    """psycopg2 variant: `?` -> `%s` (psycopg2's pyformat positional style
    for a plain params tuple -- no numbering needed, it binds by position)."""
    sql = _translate_datetime_and_ddl(sql)
    if sql is None:
        return None
    return re.sub(r"\?", "%s", sql)


class PgRow(dict):
    """dict-like row that also supports SQLite's positional index access
    (`row[0]`) and tuple-unpacking (`a, b, c = row`) alongside the usual
    `row["col"]` / `dict(row)` -- all three patterns are used throughout
    the existing router/daemon code, matching sqlite3.Row's own behavior.
    Without the __iter__ override below, dict's default iteration (over
    KEYS, not values) silently breaks every `for a, b, c in rows:`
    call site -- it unpacks column NAMES instead of the row's values."""

    __slots__ = ("_values",)

    def __init__(self, record):
        super().__init__(record)
        self._values = list(record.values())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)

    def __iter__(self):
        return iter(self._values)


def _parse_rowcount(status: str) -> int:
    # asyncpg's execute() returns a command tag like "UPDATE 3", "DELETE 1",
    # "INSERT 0 1" (the middle number for INSERT is always 0 here -- OIDs
    # aren't used -- the row count is the last token).
    try:
        return int(status.rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return -1


class PgCursor:
    def __init__(self, records, status: str, lastrowid=None):
        self._records = records or []
        self._idx = 0
        self.rowcount = len(self._records) if records is not None else _parse_rowcount(status)
        self.lastrowid = lastrowid

    async def fetchone(self):
        if self._idx >= len(self._records):
            return None
        row = PgRow(self._records[self._idx])
        self._idx += 1
        return row

    async def fetchall(self):
        rows = [PgRow(r) for r in self._records[self._idx:]]
        self._idx = len(self._records)
        return rows

    def __aiter__(self):
        return self

    async def __anext__(self):
        row = await self.fetchone()
        if row is None:
            raise StopAsyncIteration
        return row

    async def close(self):
        pass


class _ExecuteAwaitable:
    """aiosqlite's db.execute() returns an object usable BOTH as
    await db.execute(...) and async with db.execute(...) as cur: --
    both patterns are used throughout this codebase. Mirrors that dual
    contract: the underlying coroutine only actually runs once, cached in
    _result, however it is first consumed."""

    __slots__ = ("_coro", "_result")

    def __init__(self, coro):
        self._coro = coro
        self._result = None

    def __await__(self):
        return self._run().__await__()

    async def _run(self):
        if self._result is None:
            self._result = await self._coro
        return self._result

    async def __aenter__(self):
        return await self._run()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class PgConnection:
    """Wraps a single asyncpg.Connection with an aiosqlite-shaped API."""

    def __init__(self, conn: asyncpg.Connection, table_has_id: set):
        self._conn = conn
        self._table_has_id = table_has_id
        # Accepted for API compatibility (every call site sets this to
        # aiosqlite.Row) -- rows are always dict-like here regardless, so
        # there's nothing to actually configure.
        self.row_factory = None

    def execute(self, sql: str, params=()):
        return _ExecuteAwaitable(self._execute(sql, params))

    async def _execute(self, sql: str, params=()):
        translated = _translate_sql(sql)
        if translated is None:
            return PgCursor([], "SKIP 0")

        params = tuple(params) if params else ()
        stripped = translated.strip()
        upper = stripped.upper()

        if upper.startswith("SELECT") or upper.startswith("WITH"):
            records = await self._conn.fetch(translated, *params)
            return PgCursor(records, f"SELECT {len(records)}")

        lastrowid = None
        if upper.startswith("INSERT") and "RETURNING" not in upper:
            m = _INSERT_TABLE_RE.match(stripped)
            table = m.group(1).lower() if m else None
            if table and table in self._table_has_id:
                try:
                    row_id = await self._conn.fetchval(translated + " RETURNING id", *params)
                    return PgCursor([], "INSERT 0 1", lastrowid=row_id)
                except asyncpg.exceptions.UndefinedColumnError:
                    # table_has_id was stale (schema changed since caching);
                    # fall through to a plain execute rather than fail the
                    # whole request over a lastrowid nicety.
                    pass

        status = await self._conn.execute(translated, *params)
        return PgCursor(None, status, lastrowid=lastrowid)

    async def executescript(self, script: str):
        # Every real occurrence in this codebase is DDL-only bootstrapping
        # (see module docstring) -- always a no-op against Postgres, same
        # reasoning as the single-statement DDL no-op above.
        return None

    async def commit(self):
        # asyncpg auto-commits each statement outside an explicit
        # transaction block, matching the common case every existing call
        # site relies on (open connection, run one or a few statements,
        # commit, close).
        pass

    async def rollback(self):
        pass

    async def close(self):
        pass


_TABLE_HAS_ID: set = None


async def _load_table_has_id(conn: asyncpg.Connection) -> set:
    global _TABLE_HAS_ID
    if _TABLE_HAS_ID is not None:
        return _TABLE_HAS_ID
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name = 'id'"
    )
    _TABLE_HAS_ID = {r["table_name"] for r in rows}
    return _TABLE_HAS_ID


_POOL: asyncpg.Pool = None


async def get_pool(dsn: str) -> asyncpg.Pool:
    global _POOL
    if _POOL is None:
        _POOL = await asyncpg.create_pool(dsn, min_size=2, max_size=20, command_timeout=30)
    return _POOL


@asynccontextmanager
async def get_pg_db(dsn: str):
    pool = await get_pool(dsn)
    async with pool.acquire() as conn:
        table_has_id = await _load_table_has_id(conn)
        yield PgConnection(conn, table_has_id)


# ─────────────────────────────────────────────────────────────────────────
# Synchronous counterpart -- for the handful of files that call
# `sqlite3.connect(DB_PATH)` directly from plain `def` (not `async def`)
# functions. Converting those functions to async would cascade through
# every caller across multiple routers; this gives them a same-shaped
# sqlite3.Connection-compatible object instead, backed by psycopg2, so the
# call sites themselves barely change (swap the connect call, keep the
# rest). Uses a plain psycopg2 connection per call (these are low-frequency
# admin/read paths, not the hot request path asyncpg's pool covers).
# ─────────────────────────────────────────────────────────────────────────


class SyncPgCursor:
    """Wraps a real psycopg2 cursor so `.fetchall()` returns dicts (matching
    every `db.row_factory = lambda c,r: dict(zip(...))` call site) while
    still supporting `row[0]`-style positional access via PgRow."""

    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=()):
        translated = _translate_sql_sync(sql)
        if translated is None:
            return self
        self._cursor.execute(translated, tuple(params) if params else None)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        return PgRow(row) if row is not None else None

    def fetchall(self):
        return [PgRow(r) for r in self._cursor.fetchall()]

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def close(self):
        self._cursor.close()


class SyncPgConnection:
    """sqlite3.Connection-shaped wrapper. `row_factory` is accepted but
    ignored -- rows are always plain dicts here (SyncPgCursor.fetchall/
    fetchone already do the dict conversion every existing
    `row_factory = lambda c,r: dict(zip(...))` call site was doing by
    hand), so `dict(row)` and `row["col"]` both keep working unchanged."""

    def __init__(self, conn):
        self._conn = conn
        self.row_factory = None

    def execute(self, sql, params=()):
        translated = _translate_sql_sync(sql)
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if translated is None:
            return SyncPgCursor(cur)
        cur.execute(translated, tuple(params) if params else None)
        return SyncPgCursor(cur)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


def get_sync_pg_db(dsn: str) -> SyncPgConnection:
    """Real, plain psycopg2 connection per call -- mirrors
    `sqlite3.connect(DB_PATH)`'s own cost profile (also a fresh connection
    per call at every existing call site), so this isn't a regression."""
    conn = psycopg2.connect(dsn)
    return SyncPgConnection(conn)
