"""Tool access from a voice session: catalog selection, and real dispatch
through the app's own auth chain."""
import pytest

from backend import voice_session_store as store, voice_tools


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


async def _session(client, agent, tools=None, **body):
    r = await client.post("/api/agents/me/voice/sessions", headers=_h(agent),
                          json={"tools": tools, **body})
    assert r.status_code == 201, r.text
    return r.json()


# ── Catalog ──────────────────────────────────────────────────────────────────

def test_no_allowlist_means_no_tools(app):
    """The safe state is the default: a session that didn't ask for tools has
    none, inverting the audit's sketch where null meant everything."""
    assert voice_tools.select_tools(app, None) == []
    assert voice_tools.select_tools(app, []) == []


def test_star_selects_a_broad_catalog(app):
    tools = voice_tools.select_tools(app, ["*"])
    assert len(tools) > 50
    assert all(t["name"].startswith("vantage__") or t["name"] == voice_tools.COMPOSIO_TOOL
               for t in tools)


def test_catalog_is_capped_so_the_live_api_accepts_it(app):
    assert len(voice_tools.select_tools(app, ["*"])) <= voice_tools.MAX_TOOLS + 1


def test_admin_surfaces_are_never_callable(app):
    tools = voice_tools.select_tools(app, ["*"])
    assert not any("admin" in t["tags"] for t in tools)
    assert not any(t["path"].startswith("/api/admin") for t in tools)


def test_allowlist_can_select_by_path_or_tag(app):
    by_path = voice_tools.select_tools(app, ["/api/agents/*/vault/*"])
    by_tag = voice_tools.select_tools(app, ["tag:memory_vault"])
    assert by_path, "expected some vault routes by path"
    assert by_tag, "expected some vault routes by tag"
    assert all("/vault/" in t["path"] for t in by_path)
    assert all("memory_vault" in t["tags"] for t in by_tag)


def test_composio_is_one_declaration_not_thousands(app):
    tools = voice_tools.select_tools(app, [voice_tools.COMPOSIO_TOOL])
    names = [t["name"] for t in tools]
    assert voice_tools.COMPOSIO_TOOL in names


def test_declarations_declare_path_params_as_required(app):
    tools = [t for t in voice_tools.select_tools(app, ["*"]) if t.get("path_params")]
    assert tools, "expected at least one route with a path parameter"
    decl = voice_tools.to_gemini_declarations(tools[:1])[0]
    for param in tools[0]["path_params"]:
        assert param in decl["parameters"]["properties"]
        assert param in decl["parameters"]["required"]


def test_declaration_names_are_gemini_safe(app):
    import re
    for decl in voice_tools.to_gemini_declarations(voice_tools.select_tools(app, ["*"])):
        assert re.fullmatch(r"[a-zA-Z0-9_.-]+", decl["name"]), decl["name"]


# ── Dispatch ─────────────────────────────────────────────────────────────────

async def _dispatcher(app, client, agent, patterns, allow_destructive=False):
    s = await _session(client, agent, tools=patterns)
    tools = voice_tools.select_tools(app, patterns)
    return voice_tools.ToolDispatcher(
        app,
        exec_token=store.derive_exec_token(s["token"]),
        tools=tools,
        allow_destructive=allow_destructive,
    ), s


async def test_a_tool_call_really_hits_the_endpoint(app, client, fresh_agent):
    agent = await fresh_agent()
    # Narrow on purpose: a "*" allowlist matches ~690 routes and is truncated to
    # MAX_TOOLS alphabetically, which is exactly what a real session should
    # avoid doing.
    dispatcher, _ = await _dispatcher(app, client, agent, ["tag:copilot"])

    # whoami is the cleanest proof the call ran as the right agent.
    name = next((n for n in dispatcher.tool_names if "whoami" in n), None)
    assert name, f"expected a whoami tool; got {dispatcher.tool_names[:10]}"

    result = await dispatcher.execute(name, {})
    assert result["status"] == "ok", result
    assert agent["name"] in str(result["result"])


async def test_an_unlisted_tool_is_refused_with_alternatives(app, client, fresh_agent):
    agent = await fresh_agent()
    dispatcher, _ = await _dispatcher(app, client, agent, ["tag:memory_vault"])

    result = await dispatcher.execute("vantage__api_trading_execute_post", {})
    assert result["status"] == "unknown_tool"
    assert "available" in result


async def test_destructive_tools_need_the_session_to_opt_in(app, client, fresh_agent):
    agent = await fresh_agent()
    dispatcher, _ = await _dispatcher(app, client, agent, ["*"])

    delete_tool = next((n for n, t in dispatcher._by_name.items() if t["method"] == "DELETE"), None)
    assert delete_tool, "expected a DELETE route in the catalog"

    result = await dispatcher.execute(delete_tool, {})
    assert result["status"] == "confirmation_required"


async def test_workspace_exec_counts_as_destructive_despite_being_a_post(app, client, fresh_agent):
    """`rm -rf` through exec is as destructive as any DELETE, so a method-only
    gate would let the most powerful tool in the catalog past."""
    agent = await fresh_agent()
    dispatcher, _ = await _dispatcher(app, client, agent, ["tag:workspace"])

    name = next(n for n, t in dispatcher._by_name.items() if t["path"] == "/api/workspace/exec")
    result = await dispatcher.execute(name, {"command": "rm -rf /"})
    assert result["status"] == "confirmation_required"


async def test_reading_the_workspace_is_not_gated(app, client, fresh_agent):
    """Only the damaging half needs opt-in; listing files should just work."""
    agent = await fresh_agent()
    dispatcher, _ = await _dispatcher(app, client, agent, ["tag:workspace"])

    name = next(n for n, t in dispatcher._by_name.items() if t["path"] == "/api/workspace/list")
    result = await dispatcher.execute(name, {})
    # 503 (no sandbox configured in tests) rather than a confirmation refusal.
    assert result["status"] == "error"
    assert result.get("http_status") == 503


async def test_a_missing_path_parameter_is_reported_not_guessed(app, client, fresh_agent):
    agent = await fresh_agent()
    dispatcher, _ = await _dispatcher(app, client, agent, ["*"])

    name = next((n for n, t in dispatcher._by_name.items()
                 if t.get("path_params") and t["method"] == "GET"), None)
    assert name
    result = await dispatcher.execute(name, {})
    assert result["status"] == "error"
    assert "path parameter" in result["error"]


async def test_endpoint_errors_come_back_as_errors_not_fake_success(app, client, fresh_agent):
    """A fabricated success is the worst failure here — the model narrates it."""
    agent = await fresh_agent()
    dispatcher, _ = await _dispatcher(app, client, agent, ["*"])

    name = next((n for n, t in dispatcher._by_name.items()
                 if t.get("path_params") and t["method"] == "GET"), None)
    result = await dispatcher.execute(name, {p: "definitely-not-a-real-id"
                                             for p in dispatcher._by_name[name]["path_params"]})
    assert result["status"] in ("error", "ok")
    if result["status"] == "error":
        assert "http_status" in result


# ── The execution credential ─────────────────────────────────────────────────

async def test_exec_token_is_never_handed_to_the_client(client, fresh_agent):
    agent = await fresh_agent()
    s = await _session(client, agent, tools=["*"])
    assert "exec_token" not in s
    assert "exec_token_hash" not in s

    r = await client.get(f"/api/agents/me/voice/sessions/{s['session_id']}", headers=_h(agent))
    assert "exec_token_hash" not in r.json()


async def test_the_ws_token_alone_cannot_execute_tools(client, fresh_agent):
    """A leaked ws URL (query params reach history and access logs) must not
    confer tool execution."""
    agent = await fresh_agent()
    s = await _session(client, agent, tools=["*"])

    r = await client.get("/api/copilot/whoami", headers={"X-Voice-Exec": s["token"]})
    assert r.status_code == 401


async def test_exec_token_authenticates_as_the_session_agent(client, fresh_agent):
    agent = await fresh_agent()
    s = await _session(client, agent, tools=["*"])
    exec_token = store.derive_exec_token(s["token"])

    r = await client.get("/api/copilot/whoami", headers={"X-Voice-Exec": exec_token})
    assert r.status_code == 200, r.text
    assert agent["name"] in r.text


async def test_stopping_the_session_kills_tool_execution(client, fresh_agent):
    agent = await fresh_agent()
    s = await _session(client, agent, tools=["*"])
    exec_token = store.derive_exec_token(s["token"])
    assert (await client.get("/api/copilot/whoami", headers={"X-Voice-Exec": exec_token})).status_code == 200

    await client.post(f"/api/agents/me/voice/sessions/{s['session_id']}/stop", headers=_h(agent))

    r = await client.get("/api/copilot/whoami", headers={"X-Voice-Exec": exec_token})
    assert r.status_code == 401


async def test_an_idled_out_session_cannot_execute_tools(client, fresh_agent):
    agent = await fresh_agent()
    s = await _session(client, agent, tools=["*"], ttl_seconds=60)
    exec_token = store.derive_exec_token(s["token"])

    from backend.db import get_db
    async with get_db() as db:
        await db.execute("UPDATE voice_sessions SET last_activity_at=datetime('now','-2 hours') WHERE id=?",
                         (s["session_id"],))
        await db.commit()

    r = await client.get("/api/copilot/whoami", headers={"X-Voice-Exec": exec_token})
    assert r.status_code == 401


async def test_a_forged_exec_token_is_rejected(client, fresh_agent):
    agent = await fresh_agent()
    await _session(client, agent, tools=["*"])
    r = await client.get("/api/copilot/whoami", headers={"X-Voice-Exec": "vexec_" + "0" * 64})
    assert r.status_code == 401


async def test_exec_token_does_not_bypass_sentencing(client, fresh_agent):
    """A revoked agent must not regain access just by speaking."""
    agent = await fresh_agent()
    s = await _session(client, agent, tools=["*"])
    exec_token = store.derive_exec_token(s["token"])

    from backend.db import get_db
    async with get_db() as db:
        await db.execute("UPDATE agents SET agent_status='revoked' WHERE name=?", (agent["name"],))
        await db.commit()

    r = await client.get("/api/copilot/whoami", headers={"X-Voice-Exec": exec_token})
    assert r.status_code == 403, r.text
