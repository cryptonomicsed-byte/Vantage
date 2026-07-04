import os
import tempfile

# Redirect all persistence to an isolated temp dir BEFORE importing the backend.
# Every module resolves its DB via `from .db import DB_PATH`, where
# `db.DB_PATH = settings.DATA_DIR / "vantage.db"` is computed at import time, and
# `settings.DATA_DIR` reads the `VANTAGE_DATA_DIR` env var (env_prefix="VANTAGE_").
# Setting it here — before the first `import backend.*` — makes the whole app,
# and the init_*_db functions below, share one throwaway database.
_TEST_ROOT = tempfile.mkdtemp(prefix="vantage-test-")
os.environ.setdefault("VANTAGE_DATA_DIR", os.path.join(_TEST_ROOT, "data"))
os.environ.setdefault("VANTAGE_MEDIA_DIR", os.path.join(_TEST_ROOT, "media"))
# Enable the Admin API so unauthenticated admin requests are rejected with 403
# (the intended behaviour the tests assert) rather than the 503 the app returns
# when no admin key is configured at all. Must be >=32 chars (settings validator).
os.environ.setdefault("VANTAGE_ADMIN_KEY", "test-admin-key-for-ci-0123456789abcdef")

import asyncio
import pytest
from fastapi.testclient import TestClient

from backend.agents import limiter as agents_limiter
from backend.main import app, limiter as main_limiter
from backend.routers.identity import _limiter as identity_limiter
from backend.routers.guilds import _limiter as guilds_limiter
from backend.routers.intel import _limiter as intel_limiter

# Every slowapi Limiter instance in the app. Endpoints are decorated with
# whichever one lives in their module, so all must be disabled for tests.
_ALL_LIMITERS = (agents_limiter, main_limiter, identity_limiter, guilds_limiter, intel_limiter)


async def _init_all_tables():
    """Create every table the API touches, in the shared temp DB.

    The app's lifespan is not run under TestClient here (it would also spawn
    background loops that make network calls), so we invoke the schema
    initialisers directly. All are idempotent (CREATE TABLE IF NOT EXISTS).
    """
    from backend.db import init_agents_db
    from backend.mesh_store import init_mesh_db
    from backend.manifesto_store import init_manifesto_db
    from backend.routers.copilot import init_copilot_db
    from backend.routers.pine import init_pine_db
    from backend.routers.video_studio import init_video_db
    from backend.routers.collectives import init_collectives_db

    await init_agents_db()
    await init_mesh_db()
    await init_manifesto_db()
    await init_copilot_db()
    await init_pine_db()
    await init_video_db()
    await init_collectives_db()


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    asyncio.run(_init_all_tables())
    yield


@pytest.fixture(autouse=True, scope="session")
def disable_rate_limits():
    """Turn off slowapi entirely for the suite. All requests come from the same
    'testclient' IP, and several tests legitimately register more agents than the
    5/min production limit allows — enforcement would produce spurious 429s. No
    test asserts rate-limit behaviour, so disabling is safe."""
    prev = [lim.enabled for lim in _ALL_LIMITERS]
    for lim in _ALL_LIMITERS:
        lim.enabled = False
    yield
    for lim, was in zip(_ALL_LIMITERS, prev):
        lim.enabled = was


@pytest.fixture
def client(setup_db):
    return TestClient(app)
