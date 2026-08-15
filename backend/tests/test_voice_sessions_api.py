"""Voice session REST surface: lifecycle under the agent key, write-through
under the session's own scoped token."""
import pytest

from backend import voice_session_store as store


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def _open(client, agent, **body):
    r = await client.post("/api/agents/me/voice/sessions", headers=_h(agent), json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ── Lifecycle ────────────────────────────────────────────────────────────────

async def test_create_returns_token_and_ws_url(client, fresh_agent):
    agent = await fresh_agent()
    s = await _open(client, agent, engine="gemini_live", voice="Puck")

    assert s["session_id"].startswith("vsess_")
    assert s["token"].startswith("vvoice_")
    assert s["status"] == "active"
    assert s["session_id"] in s["ws_url"]


async def test_token_is_returned_once_and_never_read_back(client, fresh_agent):
    agent = await fresh_agent()
    s = await _open(client, agent)

    r = await client.get(f"/api/agents/me/voice/sessions/{s['session_id']}", headers=_h(agent))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "token" not in body
    # The stored hash must never be exposed either.
    assert "ws_token_hash" not in body


async def test_rejects_unknown_engine(client, fresh_agent):
    agent = await fresh_agent()
    r = await client.post("/api/agents/me/voice/sessions", headers=_h(agent),
                          json={"engine": "whisper_of_the_ancients"})
    assert r.status_code == 422, r.text


async def test_rejects_absurd_ttl(client, fresh_agent):
    agent = await fresh_agent()
    r = await client.post("/api/agents/me/voice/sessions", headers=_h(agent),
                          json={"ttl_seconds": 10_000_000})
    assert r.status_code == 422, r.text


async def test_requires_an_agent_key(client):
    r = await client.post("/api/agents/me/voice/sessions", json={})
    assert r.status_code == 401


async def test_another_agent_cannot_see_my_session(client, fresh_agent):
    mine = await fresh_agent()
    theirs = await fresh_agent()
    s = await _open(client, mine)

    r = await client.get(f"/api/agents/me/voice/sessions/{s['session_id']}", headers=_h(theirs))
    assert r.status_code == 404, r.text

    r = await client.get("/api/agents/me/voice/sessions", headers=_h(theirs))
    assert all(x["id"] != s["session_id"] for x in r.json()["sessions"])


async def test_stop_is_idempotent(client, fresh_agent):
    agent = await fresh_agent()
    s = await _open(client, agent)

    first = await client.post(f"/api/agents/me/voice/sessions/{s['session_id']}/stop", headers=_h(agent))
    assert first.status_code == 200 and first.json()["was_active"] is True

    second = await client.post(f"/api/agents/me/voice/sessions/{s['session_id']}/stop", headers=_h(agent))
    assert second.status_code == 200 and second.json()["was_active"] is False


# ── Write-through auth ───────────────────────────────────────────────────────

async def test_turns_are_written_and_ordered(client, fresh_agent):
    agent = await fresh_agent()
    s = await _open(client, agent)
    sid, tok = s["session_id"], s["token"]

    for role, text in [("user", "what's my balance"), ("assistant", "checking"), ("user", "thanks")]:
        r = await client.post(f"/api/agents/me/voice/sessions/{sid}/turns",
                              headers=_bearer(tok), json={"role": role, "content_text": text})
        assert r.status_code == 201, r.text

    r = await client.get(f"/api/agents/me/voice/sessions/{sid}/transcript", headers=_h(agent))
    turns = r.json()["turns"]
    assert [t["sequence_num"] for t in turns] == [1, 2, 3]
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]


async def test_write_through_needs_the_session_token_not_the_agent_key(client, fresh_agent):
    agent = await fresh_agent()
    s = await _open(client, agent)

    r = await client.post(f"/api/agents/me/voice/sessions/{s['session_id']}/turns",
                          headers=_h(agent), json={"role": "user", "content_text": "hi"})
    assert r.status_code == 401, r.text


async def test_one_sessions_token_cannot_write_into_another(client, fresh_agent):
    agent = await fresh_agent()
    a = await _open(client, agent)
    b = await _open(client, agent)

    r = await client.post(f"/api/agents/me/voice/sessions/{b['session_id']}/turns",
                          headers=_bearer(a["token"]), json={"role": "user", "content_text": "leak"})
    assert r.status_code == 403, r.text

    r = await client.get(f"/api/agents/me/voice/sessions/{b['session_id']}/transcript", headers=_h(agent))
    assert r.json()["turns"] == []


async def test_stopping_a_session_burns_its_token(client, fresh_agent):
    agent = await fresh_agent()
    s = await _open(client, agent)
    sid, tok = s["session_id"], s["token"]

    await client.post(f"/api/agents/me/voice/sessions/{sid}/stop", headers=_h(agent))

    r = await client.post(f"/api/agents/me/voice/sessions/{sid}/turns",
                          headers=_bearer(tok), json={"role": "user", "content_text": "after close"})
    assert r.status_code == 401, r.text


async def test_garbage_token_is_rejected(client, fresh_agent):
    agent = await fresh_agent()
    s = await _open(client, agent)

    r = await client.post(f"/api/agents/me/voice/sessions/{s['session_id']}/turns",
                          headers=_bearer("vvoice_" + "0" * 48), json={"role": "user", "content_text": "x"})
    assert r.status_code == 401, r.text


async def test_invalid_role_is_rejected(client, fresh_agent):
    agent = await fresh_agent()
    s = await _open(client, agent)

    r = await client.post(f"/api/agents/me/voice/sessions/{s['session_id']}/turns",
                          headers=_bearer(s["token"]), json={"role": "root", "content_text": "x"})
    assert r.status_code == 422, r.text


# ── Tool calls ───────────────────────────────────────────────────────────────

async def test_tool_call_is_logged_then_completed(client, fresh_agent):
    agent = await fresh_agent()
    s = await _open(client, agent)
    sid, tok = s["session_id"], s["token"]

    r = await client.post(f"/api/agents/me/voice/sessions/{sid}/tool-calls", headers=_bearer(tok),
                          json={"tool_name": "vantage__api_agents_me_wallets_get",
                                "tool_source": "vantage_mcp", "arguments": {"network": "solana"}})
    assert r.status_code == 201, r.text
    call_id = r.json()["tool_call_id"]

    r = await client.patch(f"/api/agents/me/voice/sessions/{sid}/tool-calls/{call_id}",
                           headers=_bearer(tok),
                           json={"result": {"error": "rate limited"}, "is_error": True, "duration_ms": 412})
    assert r.status_code == 200, r.text

    calls = (await client.get(f"/api/agents/me/voice/sessions/{sid}/transcript", headers=_h(agent))).json()["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["is_error"] == 1
    assert calls[0]["duration_ms"] == 412


async def test_tool_call_requires_a_name(client, fresh_agent):
    agent = await fresh_agent()
    s = await _open(client, agent)

    r = await client.post(f"/api/agents/me/voice/sessions/{s['session_id']}/tool-calls",
                          headers=_bearer(s["token"]), json={"arguments": {}})
    assert r.status_code == 422, r.text


# ── Search ───────────────────────────────────────────────────────────────────

async def test_transcript_search_is_scoped_to_the_agent(client, fresh_agent):
    mine = await fresh_agent()
    theirs = await fresh_agent()
    mine_s = await _open(client, mine)
    theirs_s = await _open(client, theirs)

    await client.post(f"/api/agents/me/voice/sessions/{mine_s['session_id']}/turns",
                      headers=_bearer(mine_s["token"]),
                      json={"role": "user", "content_text": "move the treasury into stablecoins"})
    await client.post(f"/api/agents/me/voice/sessions/{theirs_s['session_id']}/turns",
                      headers=_bearer(theirs_s["token"]),
                      json={"role": "user", "content_text": "move the treasury into stablecoins"})

    r = await client.get("/api/agents/me/voice/sessions/search", headers=_h(mine), params={"q": "treasury"})
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert len(results) == 1
    assert results[0]["session_id"] == mine_s["session_id"]


async def test_search_path_is_not_swallowed_by_the_session_id_route(client, fresh_agent):
    """/sessions/search must route to search, not be read as a session id."""
    agent = await fresh_agent()
    r = await client.get("/api/agents/me/voice/sessions/search", headers=_h(agent), params={"q": "anything"})
    assert r.status_code == 200, r.text
    assert "results" in r.json()


async def test_search_survives_fts_metacharacters(client, fresh_agent):
    """A spoken query is user input; an unbalanced quote must not 500."""
    agent = await fresh_agent()
    s = await _open(client, agent)
    await client.post(f"/api/agents/me/voice/sessions/{s['session_id']}/turns",
                      headers=_bearer(s["token"]), json={"role": "user", "content_text": "hello there"})

    for q in ['"', 'foo OR', 'NEAR(', 'a"b']:
        r = await client.get("/api/agents/me/voice/sessions/search", headers=_h(agent), params={"q": q})
        assert r.status_code == 200, f"{q!r} -> {r.text}"


# ── TTL ──────────────────────────────────────────────────────────────────────

async def test_idle_sessions_expire_and_their_tokens_stop_working(client, fresh_agent):
    agent = await fresh_agent()
    s = await _open(client, agent, ttl_seconds=60)
    sid, tok = s["session_id"], s["token"]

    # Age the session past its idle deadline without waiting for wall clock.
    from backend.db import get_db
    async with get_db() as db:
        await db.execute(
            "UPDATE voice_sessions SET last_activity_at=datetime('now','-2 hours') WHERE id=?", (sid,))
        await db.commit()

    assert await store.expire_idle_sessions() >= 1

    r = await client.post(f"/api/agents/me/voice/sessions/{sid}/turns",
                          headers=_bearer(tok), json={"role": "user", "content_text": "still there?"})
    assert r.status_code == 401, r.text

    r = await client.get(f"/api/agents/me/voice/sessions/{sid}", headers=_h(agent))
    assert r.json()["status"] == "stopped"
    assert r.json()["stop_reason"] == "idle_timeout"


async def test_heartbeat_keeps_a_quiet_session_alive(client, fresh_agent):
    agent = await fresh_agent()
    s = await _open(client, agent, ttl_seconds=60)
    sid, tok = s["session_id"], s["token"]

    from backend.db import get_db
    async with get_db() as db:
        await db.execute(
            "UPDATE voice_sessions SET last_activity_at=datetime('now','-30 seconds') WHERE id=?", (sid,))
        await db.commit()

    r = await client.post(f"/api/agents/me/voice/sessions/{sid}/heartbeat", headers=_bearer(tok))
    assert r.status_code == 200, r.text

    assert await store.expire_idle_sessions() == 0
    r = await client.get(f"/api/agents/me/voice/sessions/{sid}", headers=_h(agent))
    assert r.json()["status"] == "active"


# ── Stats ────────────────────────────────────────────────────────────────────

async def test_status_reports_turn_and_tool_counts(client, fresh_agent):
    agent = await fresh_agent()
    s = await _open(client, agent)
    sid, tok = s["session_id"], s["token"]

    await client.post(f"/api/agents/me/voice/sessions/{sid}/turns", headers=_bearer(tok),
                      json={"role": "user", "content_text": "one"})
    await client.post(f"/api/agents/me/voice/sessions/{sid}/tool-calls", headers=_bearer(tok),
                      json={"tool_name": "vantage__whoami"})

    r = await client.get(f"/api/agents/me/voice/sessions/{sid}", headers=_h(agent))
    body = r.json()
    assert body["turn_count"] == 1
    assert body["tool_call_count"] == 1
