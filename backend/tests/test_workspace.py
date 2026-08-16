"""Agent workspaces: repo cloning, command execution and file edits, proxied to
the code sandbox.

The sandbox itself is a container; these tests stub its HTTP surface and assert
what Vantage is responsible for — that nothing runs on the host, that agents are
isolated from one another, and that a missing sandbox fails closed.
"""
import pytest

from backend.routers import workspace


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


@pytest.fixture
def sandbox(monkeypatch):
    """Stand in for the container, recording what it was asked to do."""
    calls: list[tuple[str, dict]] = []
    replies: dict[str, dict] = {}

    async def fake(endpoint: str, payload: dict) -> dict:
        calls.append((endpoint, payload))
        return replies.get(endpoint, {"exit_code": 0, "stdout": "", "stderr": ""})

    monkeypatch.setattr(workspace, "CODE_SANDBOX_URL", "http://sandbox.test")
    monkeypatch.setattr(workspace, "_sandbox", fake)
    return {"calls": calls, "replies": replies}


# ── Fails closed ─────────────────────────────────────────────────────────────

async def test_without_a_sandbox_nothing_runs(client, fresh_agent, monkeypatch):
    """No sandbox must mean 503, never a fallback that runs code on the host."""
    monkeypatch.setattr(workspace, "CODE_SANDBOX_URL", "")
    agent = await fresh_agent()

    r = await client.post("/api/workspace/exec", headers=_h(agent), json={"command": "echo hi"})
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


async def test_status_reports_unavailable_rather_than_pretending(client, fresh_agent, monkeypatch):
    monkeypatch.setattr(workspace, "CODE_SANDBOX_URL", "")
    agent = await fresh_agent()

    body = (await client.get("/api/workspace/status", headers=_h(agent))).json()
    assert body["available"] is False
    assert body["reason"]


async def test_every_endpoint_requires_an_agent_key(client):
    for path, payload in [
        ("/api/workspace/exec", {"command": "ls"}),
        ("/api/workspace/clone", {"repo_url": "https://example.com/x.git"}),
        ("/api/workspace/read", {"path": "a"}),
        ("/api/workspace/write", {"path": "a", "content": ""}),
        ("/api/workspace/list", {}),
        ("/api/workspace/remove", {"path": "a"}),
    ]:
        assert (await client.post(path, json=payload)).status_code == 401, path


# ── Per-agent isolation ──────────────────────────────────────────────────────

async def test_commands_run_inside_the_agents_own_directory(client, fresh_agent, sandbox):
    agent = await fresh_agent()
    await client.post("/api/workspace/exec", headers=_h(agent),
                      json={"command": "pytest", "cwd": "myrepo"})

    _endpoint, payload = sandbox["calls"][-1]
    assert payload["cwd"].startswith(f"agent-{await _agent_id(agent)}-")
    assert payload["cwd"].endswith("/myrepo")


async def test_two_agents_get_separate_workspaces(client, fresh_agent, sandbox):
    a, b = await fresh_agent(), await fresh_agent()

    await client.post("/api/workspace/exec", headers=_h(a), json={"command": "ls"})
    a_cwd = sandbox["calls"][-1][1]["cwd"]
    await client.post("/api/workspace/exec", headers=_h(b), json={"command": "ls"})
    b_cwd = sandbox["calls"][-1][1]["cwd"]

    assert a_cwd != b_cwd


async def test_a_caller_cannot_escape_its_own_directory(client, fresh_agent, sandbox):
    """Vantage always rebases onto the agent prefix; the sandbox confines again."""
    agent = await fresh_agent()
    await client.post("/api/workspace/read", headers=_h(agent), json={"path": "/etc/passwd"})

    _endpoint, payload = sandbox["calls"][-1]
    assert payload["path"].startswith("agent-")
    assert not payload["path"].startswith("/")


async def test_responses_do_not_leak_the_isolation_prefix(client, fresh_agent, sandbox):
    agent = await fresh_agent()
    prefix = workspace._agent_dir({"id": await _agent_id(agent), "name": agent["name"]})
    sandbox["replies"]["/list"] = {
        "path": f"{prefix}/myrepo",
        "entries": [{"name": "README.md", "type": "file"}],
    }

    body = (await client.post("/api/workspace/list", headers=_h(agent), json={"path": "myrepo"})).json()
    assert body["path"] == "myrepo"
    assert "agent-" not in body["path"]


# ── Clone ────────────────────────────────────────────────────────────────────

async def test_clone_requires_a_url(client, fresh_agent, sandbox):
    agent = await fresh_agent()
    r = await client.post("/api/workspace/clone", headers=_h(agent), json={})
    assert r.status_code == 422


async def test_clone_defaults_the_directory_to_the_repo_name(client, fresh_agent, sandbox):
    agent = await fresh_agent()
    await client.post("/api/workspace/clone", headers=_h(agent),
                      json={"repo_url": "https://github.com/owner/my-project.git"})

    _endpoint, payload = sandbox["calls"][-1]
    assert payload["dir"].endswith("/my-project")
    assert payload["full_history"] is False


async def test_clone_can_ask_for_full_history(client, fresh_agent, sandbox):
    agent = await fresh_agent()
    await client.post("/api/workspace/clone", headers=_h(agent),
                      json={"repo_url": "https://x/y.git", "full_history": True})
    assert sandbox["calls"][-1][1]["full_history"] is True


# ── Exec ─────────────────────────────────────────────────────────────────────

async def test_exec_requires_a_command(client, fresh_agent, sandbox):
    agent = await fresh_agent()
    assert (await client.post("/api/workspace/exec", headers=_h(agent), json={})).status_code == 422


async def test_a_failing_command_is_reported_not_hidden(client, fresh_agent, sandbox):
    """A failing build is information the agent needs to act on."""
    agent = await fresh_agent()
    sandbox["replies"]["/exec"] = {
        "exit_code": 1, "stdout": "2 passed, 1 failed", "stderr": "AssertionError",
        "timed_out": False, "duration_ms": 812,
    }

    r = await client.post("/api/workspace/exec", headers=_h(agent), json={"command": "pytest"})
    assert r.status_code == 200
    body = r.json()
    assert body["exit_code"] == 1
    assert "AssertionError" in body["stderr"]


async def test_a_timed_out_command_says_so(client, fresh_agent, sandbox):
    agent = await fresh_agent()
    sandbox["replies"]["/exec"] = {"exit_code": 124, "stdout": "", "stderr": "",
                                   "timed_out": True, "duration_ms": 900000}

    body = (await client.post("/api/workspace/exec", headers=_h(agent),
                              json={"command": "sleep 999"})).json()
    assert body["timed_out"] is True
    assert body["exit_code"] == 124


# ── Remove ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [".", "/", ""])
async def test_remove_refuses_to_wipe_the_whole_workspace(client, fresh_agent, sandbox, path):
    agent = await fresh_agent()
    r = await client.post("/api/workspace/remove", headers=_h(agent), json={"path": path})
    assert r.status_code == 422


async def test_remove_passes_a_named_path_through(client, fresh_agent, sandbox):
    agent = await fresh_agent()
    r = await client.post("/api/workspace/remove", headers=_h(agent), json={"path": "myrepo/node_modules"})
    assert r.status_code == 200
    assert sandbox["calls"][-1][1]["path"].endswith("/myrepo/node_modules")


# ── Voice reachability ───────────────────────────────────────────────────────

def test_workspace_tools_are_selectable_by_voice(app):
    """Adding routes is all it takes: the voice catalog is derived from the
    route table, so these are callable under a tag:workspace allowlist."""
    from backend import voice_tools
    tools = voice_tools.select_tools(app, ["tag:workspace"])
    paths = {t["path"] for t in tools}
    assert "/api/workspace/exec" in paths
    assert "/api/workspace/clone" in paths


def test_workspace_tools_are_not_in_a_session_without_them(app):
    from backend import voice_tools
    tools = voice_tools.select_tools(app, ["tag:memory_vault"])
    assert not any(t["path"].startswith("/api/workspace") for t in tools)


async def _agent_id(agent) -> int:
    import aiosqlite
    from backend.db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT id FROM agents WHERE name=?", (agent["name"],))).fetchone()
    return row[0]
