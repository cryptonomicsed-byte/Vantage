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


def test_select_tools_is_uncapped(app):
    """select_tools is the session's execution scope, not what gets sent to
    Gemini -- a "*" allowlist should match everything the catalog has, not be
    silently narrowed to whichever names sort first alphabetically."""
    matched = voice_tools.select_tools(app, ["*"])
    assert len(matched) == len(voice_tools._route_tools(app)) + 1  # +1 for composio


def test_declarations_are_capped_so_the_live_api_accepts_them(app):
    """The cap belongs to to_gemini_declarations, not select_tools: it is a
    presentation limit on what goes to the model, not a reason to narrow what
    a session may execute."""
    matched = voice_tools.select_tools(app, ["*"])
    declared = voice_tools.to_gemini_declarations(matched)
    assert len(declared) <= voice_tools.DIRECT_DECLARE_LIMIT


def test_a_large_scope_declares_search_and_call_instead_of_everything(app):
    matched = voice_tools.select_tools(app, ["*"])
    declared = voice_tools.to_gemini_declarations(matched)
    names = {d["name"] for d in declared}
    assert names == {voice_tools.FIND_TOOLS_TOOL, voice_tools.CALL_TOOL_TOOL}


def test_a_small_scope_declares_every_tool_directly(app):
    """No indirection tax for a small, deliberate preset -- the common case."""
    matched = voice_tools.select_tools(app, ["tag:memory_vault"])
    assert 0 < len(matched) <= voice_tools.DIRECT_DECLARE_LIMIT
    declared = voice_tools.to_gemini_declarations(matched)
    assert {d["name"] for d in declared} == {t["name"] for t in matched}


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


# ── Search-based routing (vantage_find_tools / vantage_call_tool) ────────────
#
# What replaces the alphabetical 128-tool truncation: a "*" allowlist gives the
# dispatcher the FULL matched catalog (~690 tools), declared to Gemini as just
# these two meta-tools. These cover the mechanism the model actually uses to
# reach that catalog.

async def test_find_tools_searches_the_full_scope_not_a_declared_subset(app, client, fresh_agent):
    agent = await fresh_agent()
    dispatcher, _ = await _dispatcher(app, client, agent, ["*"])
    assert len(dispatcher._by_name) > voice_tools.DIRECT_DECLARE_LIMIT

    result = await dispatcher.execute(voice_tools.FIND_TOOLS_TOOL, {"query": "whoami"})
    assert result["status"] == "ok"
    names = [m["name"] for m in result["result"]["matches"]]
    assert any("whoami" in n for n in names)


async def test_find_tools_requires_a_query(app, client, fresh_agent):
    agent = await fresh_agent()
    dispatcher, _ = await _dispatcher(app, client, agent, ["*"])
    result = await dispatcher.execute(voice_tools.FIND_TOOLS_TOOL, {"query": ""})
    assert result["status"] == "error"


async def test_find_tools_returns_a_hint_when_nothing_matches(app, client, fresh_agent):
    agent = await fresh_agent()
    dispatcher, _ = await _dispatcher(app, client, agent, ["*"])
    result = await dispatcher.execute(
        voice_tools.FIND_TOOLS_TOOL, {"query": "zzz_no_such_capability_zzz"}
    )
    assert result["status"] == "ok"
    assert result["result"]["matches"] == []
    assert "hint" in result["result"]


async def test_call_tool_actually_runs_the_named_tool(app, client, fresh_agent):
    """The point of the whole mechanism: a tool never directly declared is
    still callable, with the same real result a direct call would give."""
    agent = await fresh_agent()
    dispatcher, _ = await _dispatcher(app, client, agent, ["*"])

    name = next(n for n in dispatcher._by_name if "whoami" in n)
    found = await dispatcher.execute(voice_tools.FIND_TOOLS_TOOL, {"query": "whoami"})
    assert name in [m["name"] for m in found["result"]["matches"]]

    result = await dispatcher.execute(voice_tools.CALL_TOOL_TOOL, {"name": name, "arguments": {}})
    assert result["status"] == "ok", result
    assert agent["name"] in str(result["result"])


async def test_call_tool_still_gates_destructive_tools(app, client, fresh_agent):
    """Indirection must not be a way around the confirmation gate -- the
    dangerous case is a model discovering a destructive tool via search and
    routing around the opt-in check by calling it through vantage_call_tool
    instead of directly."""
    agent = await fresh_agent()
    dispatcher, _ = await _dispatcher(app, client, agent, ["*"])
    delete_tool = next(n for n, t in dispatcher._by_name.items() if t["method"] == "DELETE")

    result = await dispatcher.execute(voice_tools.CALL_TOOL_TOOL, {"name": delete_tool, "arguments": {}})
    assert result["status"] == "confirmation_required"


async def test_call_tool_requires_a_name(app, client, fresh_agent):
    agent = await fresh_agent()
    dispatcher, _ = await _dispatcher(app, client, agent, ["*"])
    result = await dispatcher.execute(voice_tools.CALL_TOOL_TOOL, {"arguments": {}})
    assert result["status"] == "error"


async def test_call_tool_refuses_to_recurse_into_the_meta_tools(app, client, fresh_agent):
    """Not a privilege escalation -- there's nothing to escalate to -- but
    unguarded this would recurse into itself until Python's stack gives out,
    breaking execute()'s never-raises contract instead of just answering."""
    agent = await fresh_agent()
    dispatcher, _ = await _dispatcher(app, client, agent, ["*"])

    result = await dispatcher.execute(
        voice_tools.CALL_TOOL_TOOL,
        {"name": voice_tools.CALL_TOOL_TOOL, "arguments": {"name": "x", "arguments": {}}},
    )
    assert result["status"] == "error"
    assert "cannot be called via vantage_call_tool" in result["error"]


async def test_call_tool_reports_an_unknown_name_like_a_direct_call_would(app, client, fresh_agent):
    agent = await fresh_agent()
    dispatcher, _ = await _dispatcher(app, client, agent, ["*"])
    result = await dispatcher.execute(
        voice_tools.CALL_TOOL_TOOL, {"name": "vantage__not_a_real_tool", "arguments": {}}
    )
    assert result["status"] == "unknown_tool"


def test_search_tools_ranks_more_term_matches_higher():
    tools = [
        {"name": "vantage__a", "path": "/api/a", "description": "wallet lookup", "tags": []},
        {"name": "vantage__b", "path": "/api/b", "description": "get wallet SOL balance", "tags": ["wallets"]},
        {"name": "vantage__c", "path": "/api/c", "description": "unrelated thing", "tags": []},
    ]
    results = voice_tools.search_tools(tools, "wallet balance")
    assert [t["name"] for t in results] == ["vantage__b", "vantage__a"]


def test_search_tools_with_an_empty_query_returns_the_head_of_the_list():
    tools = [{"name": f"vantage__{i}", "path": "/api/x", "description": "", "tags": []} for i in range(5)]
    assert voice_tools.search_tools(tools, "", limit=3) == tools[:3]


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


# ── Default system instruction ────────────────────────────────────────────

def test_a_custom_persona_still_gets_the_mechanical_guidance():
    """A caller's own persona knows nothing about vantage_find_tools; the
    functional half must be appended, not skipped just because a persona
    was supplied."""
    instruction = voice_tools.system_instruction_for(
        "You are Ares, a trading copilot.", [{"name": "x"}] * 50, False
    )
    assert instruction.startswith("You are Ares, a trading copilot.")
    assert "vantage_find_tools" in instruction


def test_no_persona_gets_the_default_identity():
    instruction = voice_tools.system_instruction_for("", [], False)
    assert "Vantage's voice assistant" in instruction


def test_no_tools_is_stated_plainly_rather_than_left_implicit():
    instruction = voice_tools.system_instruction_for("", [], False)
    assert "no tools enabled" in instruction


def test_a_small_scope_is_described_as_direct_tools():
    tools = [{"name": f"vantage__{i}"} for i in range(5)]
    instruction = voice_tools.system_instruction_for("", tools, False)
    assert "5 Vantage tools declared directly" in instruction
    assert "vantage_find_tools" not in instruction


def test_a_large_scope_explains_search_then_call():
    tools = [{"name": f"vantage__{i}"} for i in range(voice_tools.DIRECT_DECLARE_LIMIT + 1)]
    instruction = voice_tools.system_instruction_for("", tools, False)
    assert "vantage_find_tools" in instruction
    assert "vantage_call_tool" in instruction


def test_destructive_off_tells_the_model_not_to_pretend_it_ran():
    instruction = voice_tools.system_instruction_for("", [], False)
    assert "NOT enabled" in instruction
    assert "confirmation_required" in instruction


def test_destructive_on_asks_for_a_spoken_confirmation_first():
    instruction = voice_tools.system_instruction_for("", [], True)
    assert "enabled for this session" in instruction
    assert "before anything irreversible" in instruction
