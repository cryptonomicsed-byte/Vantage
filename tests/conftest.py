import asyncio
import pytest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

import backend.agents as agents_module
from backend.agents import init_agents_db, limiter as agents_limiter
from backend.main import app, limiter as main_limiter


@pytest.fixture(scope="session")
def _tmp_dirs(tmp_path_factory):
    return {
        "db": tmp_path_factory.mktemp("data") / "test.db",
        "media": tmp_path_factory.mktemp("media"),
    }


@pytest.fixture(scope="session", autouse=True)
def setup_db(_tmp_dirs):
    with (
        patch.object(agents_module, "DB_PATH", _tmp_dirs["db"]),
        patch.object(agents_module, "MEDIA_ROOT", _tmp_dirs["media"]),
    ):
        asyncio.run(init_agents_db())
        yield


@pytest.fixture(autouse=True)
def disable_rate_limits():
    """Reset slowapi storage before each test so per-minute counters don't
    bleed across tests (all requests share the same 'testclient' IP)."""
    agents_limiter._storage.reset()
    main_limiter._storage.reset()
    yield
    agents_limiter._storage.reset()
    main_limiter._storage.reset()


@pytest.fixture
def client(setup_db, _tmp_dirs):
    with (
        patch.object(agents_module, "DB_PATH", _tmp_dirs["db"]),
        patch.object(agents_module, "MEDIA_ROOT", _tmp_dirs["media"]),
    ):
        yield TestClient(app)
