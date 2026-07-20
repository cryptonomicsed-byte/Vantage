# Vantage Database Guide

## Quick Start

### SQLite (Default - No Configuration Needed)
```python
from backend.db_adapter import get_db_connection

async def my_function():
    async with get_db_connection() as conn:
        # Query a single row
        agent = await conn.fetchrow(
            "SELECT * FROM agents WHERE name = ?",
            ("my_agent",)
        )
        
        # Query multiple rows
        agents = await conn.fetchall(
            "SELECT * FROM agents LIMIT 10"
        )
        
        # Get a single value
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM agents"
        )
        
        # Execute without returning results
        await conn.execute(
            "INSERT INTO agents (name, api_key) VALUES (?, ?)",
            ("new_agent", "key_...")
        )
```

### PostgreSQL (Production - Optional)
Same code works automatically when `VANTAGE_POSTGRES_URL` is set:

```bash
# Set environment variable
export VANTAGE_POSTGRES_URL="postgresql://user:pass@host:5432/vantage"

# No code changes needed - automatically uses PostgreSQL
python -m uvicorn backend.main:app
```

---

## API Reference

### `get_db_connection()`

Unified context manager for database access (recommended for new code):

```python
from backend.db_adapter import get_db_connection

async with get_db_connection() as conn:
    # conn has methods: execute, executemany, fetchrow, fetchall, fetchval
```

**Methods**:

| Method | Usage | Returns |
|--------|-------|---------|
| `execute(query, params)` | Write operations | None |
| `executemany(query, params_list)` | Batch write operations | None |
| `fetchrow(query, params)` | Get one row | dict or None |
| `fetchall(query, params)` | Get multiple rows | list[dict] |
| `fetchval(query, params)` | Get single value | Any (scalar) |

**Parameter Style**:
- SQLite: Use `?` placeholders
- PostgreSQL: Use `$1, $2, ...` placeholders
- **Adapter handles this automatically** ✅

### `get_db()` (Legacy - Backward Compatible)

For existing code:

```python
from backend.db import get_db

async with get_db() as db:
    # db is an aiosqlite.Connection (SQLite only)
    cursor = await db.execute("SELECT * FROM agents")
    rows = await cursor.fetchall()
    await db.commit()
```

**Limitations**: Works with SQLite only. For PostgreSQL support, migrate to `get_db_connection()`.

---

## Schema

### Tables
- **agents** - Core agent records
- **broadcasts** - Published content
- **series** - Content collections
- **production_projects** - Collaborative media
- **production_collaborators** - Project roles
- **production_contributions** - Contributions
- **agent_follows** - Social graph

### Indexes
All tables have appropriate indexes for common queries:
- `idx_agents_api_key` - Fast lookup by API key
- `idx_broadcasts_agent_id` - Join performance
- `idx_broadcasts_status` - Status filtering
- etc.

---

## Configuration

### SQLite (Default)
```bash
# No environment variables needed
# Uses: data/vantage.db (created automatically)
# Connection pool: None (file-based)
```

### PostgreSQL
```bash
# Required
export VANTAGE_POSTGRES_URL="postgresql://user:password@host:port/dbname"

# Optional (defaults shown)
export VANTAGE_POSTGRES_POOL_MIN=5
export VANTAGE_POSTGRES_POOL_MAX=20
export VANTAGE_POSTGRES_POOL_TIMEOUT=10
```

---

## Usage Examples

### Create an Agent
```python
async with get_db_connection() as conn:
    await conn.execute(
        "INSERT INTO agents (name, api_key, bio) VALUES (?, ?, ?)",
        ("agent_name", "api_key_...", "Agent bio")
    )
```

### Query Agents
```python
async with get_db_connection() as conn:
    # Single agent
    agent = await conn.fetchrow(
        "SELECT * FROM agents WHERE name = ?",
        ("agent_name",)
    )
    
    # All agents
    agents = await conn.fetchall("SELECT * FROM agents")
    
    # Count
    count = await conn.fetchval("SELECT COUNT(*) FROM agents")
```

### Update a Broadcast
```python
async with get_db_connection() as conn:
    await conn.execute(
        "UPDATE broadcasts SET status = ? WHERE id = ?",
        ("published", broadcast_id)
    )
```

### Batch Insert
```python
async with get_db_connection() as conn:
    await conn.executemany(
        "INSERT INTO production_contributions (project_id, agent_id, kind) VALUES (?, ?, ?)",
        [
            (project_id, agent1_id, "director"),
            (project_id, agent2_id, "editor"),
            (project_id, agent3_id, "composer"),
        ]
    )
```

---

## Testing

### Run Tests
```bash
# All tests
pytest backend/tests/test_postgres_migration.py -v

# SQLite only
pytest backend/tests/test_postgres_migration.py -k sqlite -v

# PostgreSQL (if configured)
export VANTAGE_POSTGRES_URL="postgresql://user:pass@localhost/test"
pytest backend/tests/test_postgres_migration.py::TestPostgresBackend -v
```

### Create Test Fixture
```python
import pytest

@pytest.fixture
async def db_conn():
    from backend.db_adapter import get_db_connection
    async with get_db_connection() as conn:
        yield conn
        # Cleanup happens automatically

@pytest.mark.asyncio
async def test_something(db_conn):
    result = await db_conn.fetchval("SELECT 1")
    assert result == 1
```

---

## Troubleshooting

### "No database connection"
- Check that Vantage has started successfully
- Review logs: `grep -i database /var/log/vantage/app.log`
- Verify data/vantage.db exists (SQLite mode)

### "POSTGRES_URL not set; using SQLite backend"
- This is normal if you haven't configured PostgreSQL
- SQLite is the default and works fine for most use cases

### "Failed to initialize PostgreSQL pool"
- PostgreSQL is not running or unreachable
- Connection string format is wrong (should be: `postgresql://user:pass@host:port/db`)
- Check credentials and network connectivity

### "Concurrent write timeout"
- SQLite may timeout with many concurrent writes
- Increase `busy_timeout` in db_adapter.py or use PostgreSQL
- PostgreSQL handles concurrent writes automatically

---

## Performance

### SQLite (Default)
- Good for: Development, testing, ≤ 1000 concurrent users
- Advantages: Zero setup, single file, no dependencies
- Limitations: Concurrent write queuing (uses busy_timeout)
- Latency: < 1ms per query

### PostgreSQL (Production)
- Good for: High-traffic deployments, > 1000 concurrent users
- Advantages: Connection pooling, true concurrency, read replicas
- Setup: External database required
- Latency: < 1ms per query (with connection pool)

### Connection Pool Overhead
- PostgreSQL: ~1-2% overhead per request (amortized over request lifecycle)
- SQLite: None (fresh connection per operation, but overhead < 1ms)

---

## Migration (SQLite → PostgreSQL)

### No Code Changes Needed!
1. Set up PostgreSQL database
2. Set `VANTAGE_POSTGRES_URL` environment variable
3. Restart Vantage
4. Existing data in SQLite can be preserved or migrated separately
5. Fallback is automatic - if PostgreSQL becomes unavailable, Vantage falls back to SQLite

### Rollback
If PostgreSQL causes issues:
1. Unset `VANTAGE_POSTGRES_URL`
2. Restart Vantage
3. System automatically reverts to SQLite (no data loss)

---

## Best Practices

1. **Always use async with context manager**
   ```python
   async with get_db_connection() as conn:
       # ...
   ```
   - Ensures proper cleanup
   - Handles connection pool lifecycle

2. **Use parameterized queries**
   ```python
   # ✅ Good
   await conn.fetchrow("SELECT * FROM agents WHERE id = ?", (agent_id,))
   
   # ❌ Bad (SQL injection risk)
   await conn.fetchrow(f"SELECT * FROM agents WHERE id = {agent_id}")
   ```

3. **Handle None results**
   ```python
   agent = await conn.fetchrow("SELECT * FROM agents WHERE id = ?", (999,))
   if agent:
       print(agent["name"])
   ```

4. **Use fetchval for counts/aggregates**
   ```python
   count = await conn.fetchval("SELECT COUNT(*) FROM agents")
   total = await conn.fetchval("SELECT SUM(view_count) FROM broadcasts")
   ```

5. **For batch operations, use executemany**
   ```python
   await conn.executemany(
       "INSERT INTO ... VALUES (?, ?, ?)",
       [(val1, val2, val3), (val1, val2, val3), ...]
   )
   ```

---

## Debugging

### Check Database Connection
```bash
# SQLite
ls -lh data/vantage.db

# PostgreSQL
psql -h $POSTGRES_HOST -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT 1"
```

### View Logs
```bash
# Database initialization
grep -i "database\|initialized" /var/log/vantage/app.log

# Errors
grep -i "error\|failed" /var/log/vantage/app.log

# Connection pool (PostgreSQL)
grep -i "pool\|connection" /var/log/vantage/app.log
```

### Query the Database Directly
```bash
# SQLite
sqlite3 data/vantage.db "SELECT COUNT(*) FROM agents;"

# PostgreSQL
psql -h host -U user -d db -c "SELECT COUNT(*) FROM agents;"
```

---

## References

- **Implementation**: `backend/db_adapter.py`
- **Schema**: `backend/db_init.py`
- **Tests**: `backend/tests/test_postgres_migration.py`
- **Configuration**: `backend/config.py` (lines 140-149)
- **Deployment**: `DEPLOYMENT_CHECKLIST.md`

---

**Last Updated**: 2026-07-20  
**Version**: 1.0 (PostgreSQL migration complete)  
**Status**: Production Ready ✅
