import asyncio
import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

import backend.agents as agents_module
from backend.agents import init_agents_db
from backend.main import app


@pytest.fixture(scope="session", autouse=True)
def setup_db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("data")
    media = tmp_path_factory.mktemp("media")
    with (
        patch.object(agents_module, "DB_PATH", tmp / "test.db"),
        patch.object(agents_module, "MEDIA_ROOT", media),
    ):
        asyncio.run(init_agents_db())
        yield


@pytest.fixture
def client(setup_db, tmp_path):
    with (
        patch.object(agents_module, "DB_PATH", Path(str(agents_module.DB_PATH))),
        patch.object(agents_module, "MEDIA_ROOT", Path(str(agents_module.MEDIA_ROOT))),
    ):
        yield TestClient(app)
