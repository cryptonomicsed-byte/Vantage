import io
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import backend.routers.code as code_module
from backend.config import settings


def _register(client, name="CodeAgent") -> str:
    r = client.post("/api/agents/register", data={"name": name, "bio": "test"})
    assert r.status_code == 200
    return r.json()["api_key"]


def _mock_completed(returncode=0, stdout=b"", stderr=b""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ── Auth is required on every mutating endpoint ────────────────────────────

def test_create_repo_requires_auth(client):
    r = client.post("/api/code/repo/create", json={"name": "x"})
    assert r.status_code == 401


def test_push_requires_auth(client):
    r = client.post("/api/code/repo/o/n/push", json={"path": "a.py", "content": "x"})
    assert r.status_code == 401


def test_scan_requires_auth(client):
    r = client.post("/api/code/repo/o/n/scan")
    assert r.status_code == 401


def test_pr_requires_auth(client):
    r = client.post("/api/code/repo/o/n/pr", json={"title": "t", "head": "b"})
    assert r.status_code == 401


# ── push_file shells out to git with the expected argv, non-blocking ──────

def test_push_file_calls_git(client, tmp_path):
    """subprocess.run is mocked (no real network clone/push), but SCAN_DIR points
    at a real tmp dir so push_file's actual os.makedirs/open(...).write() calls
    run for real and the response reflects a genuine successful write."""
    key = _register(client, "PushAgent")
    with patch.object(code_module, "SCAN_DIR", str(tmp_path)), \
         patch.object(subprocess, "run", return_value=_mock_completed()) as run_mock:
        r = client.post(
            "/api/code/repo/o/n/push",
            json={"path": "a.py", "content": "print(1)", "message": "m", "branch": "main"},
            headers={"X-Agent-Key": key},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "pushed"
    argvs = [c.args[0] for c in run_mock.call_args_list]
    assert any(a[:2] == ["git", "clone"] for a in argvs)
    assert any("push" in a for a in argvs)


# ── regex scan (default engine) still finds a known hardcoded secret ──────

def test_regex_scan_finds_hardcoded_secret(tmp_path):
    """git clone is mocked with a side effect that writes a vulnerable fixture
    file into the target dir, so the real (unmocked) os.walk + regex logic
    runs against real files on disk — no path-mocking needed."""

    def fake_clone(argv, **kwargs):
        if argv[:2] == ["git", "clone"]:
            target = Path(argv[-1])
            target.mkdir(parents=True, exist_ok=True)
            # Deliberately not a recognizable real-provider key format, so this
            # doesn't trip secret-scanning on push — it only needs to match the
            # HARDCODED_SECRET regex below.
            (target / "config.py").write_text('api_key = "not_a_real_value_but_long_enough_1234"\n')
        return _mock_completed()

    async def _run():
        with patch.object(code_module, "SCAN_DIR", str(tmp_path)), \
             patch.object(subprocess, "run", side_effect=fake_clone):
            return await code_module._regex_scan("o", "n", "TestAgent")

    import asyncio
    result = asyncio.run(_run())
    assert result["total_findings"] >= 1
    assert any(f["vuln_id"] == "HARDCODED_SECRET" for f in result["findings"])


# ── engine=strix: 503 when no runner configured, 202-shaped when it is ────

def test_strix_scan_without_runner_configured(client):
    key = _register(client, "StrixAgent1")
    with patch.object(settings, "STRIX_RUNNER_URL", ""):
        r = client.post("/api/code/repo/o/n/scan?engine=strix", headers={"X-Agent-Key": key})
    assert r.status_code == 503


def test_strix_scan_dispatches_and_returns_immediately(client):
    key = _register(client, "StrixAgent2")

    async def fake_post(self, url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"run_id": "abc123"})
        return resp

    with patch.object(settings, "STRIX_RUNNER_URL", "http://127.0.0.1:9877"), \
         patch("httpx.AsyncClient.post", new=fake_post):
        r = client.post("/api/code/repo/o/n/scan?engine=strix", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "running"
    assert data["engine"] == "strix"
    assert "scan_id" in data


# ── supermemory ingest endpoint: graceful no-op when unconfigured ─────────

def test_memory_ingest_noop_when_unconfigured(client):
    key = _register(client, "MemAgent")
    with patch.object(settings, "SUPERMEMORY_URL", ""):
        r = client.post(
            "/api/code/repo/o/n/memory",
            json={"content": "some summary"},
            headers={"X-Agent-Key": key},
        )
    assert r.status_code == 200
    assert r.json()["stored"] is False
