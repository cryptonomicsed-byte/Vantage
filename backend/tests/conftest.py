"""Shared test fixtures — real DB in tmp dir, FastAPI test client."""
import asyncio
import hashlib
import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ── Set env vars BEFORE any backend module is imported ───────────────────────
_TEST_DIR = Path(tempfile.mkdtemp(prefix="vantage_test_"))
os.environ.setdefault("VANTAGE_DATA_DIR", str(_TEST_DIR))
os.environ.setdefault("VANTAGE_MEDIA_DIR", str(_TEST_DIR / "media"))
os.environ.setdefault("VANTAGE_ADMIN_KEY", "a" * 32)

# Ensure repo root is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ── App (session-scoped, one DB per test run) ─────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    """Session-scoped event loop."""
    policy = asyncio.DefaultEventLoopPolicy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def app():
    from backend.main import app as _app
    return _app


@pytest_asyncio.fixture(scope="session")
async def client(app):
    # ASGITransport doesn't run app lifespan — init DB manually
    from backend.db import init_agents_db
    await init_agents_db()

    # Start batch writers that the lifespan normally starts
    from backend.utils import view_events_writer, activity_log_writer
    view_events_writer.start()
    activity_log_writer.start()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c

    view_events_writer.stop()
    activity_log_writer.stop()


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_api_key() -> tuple[str, str]:
    """Return (raw_key, sha256_hash) pair."""
    raw = f"vantage_{secrets.token_hex(16)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed


@pytest_asyncio.fixture(scope="session")
async def registered_agent(client):
    """Register a test agent once per session; return {name, api_key}."""
    resp = await client.post(
        "/api/agents/register",
        json={"name": "TestAgent", "bio": "test"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return {"name": data["name"], "api_key": data["api_key"]}


@pytest_asyncio.fixture
async def fresh_agent(client):
    """Factory: create an isolated agent by inserting directly into the DB —
    bypasses the 5/min rate limit on /api/agents/register so tests needing many
    isolated agents don't 429. Returns an async maker → {name, api_key}."""
    import aiosqlite
    from backend.db import DB_PATH

    async def _make():
        raw, hashed = make_api_key()
        name = f"FreshAgent_{hashed[:10]}"
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO agents (name, api_key, bio) VALUES (?,?,?)", (name, hashed, "test"))
            await db.commit()
        return {"name": name, "api_key": raw}

    return _make
