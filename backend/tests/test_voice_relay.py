"""The Phase 2 audio relay: browser <-> Vantage <-> model.

Runs against a fake engine so the relay's real responsibilities — auth,
transcript persistence, tool-call handling, cleanup on disconnect — are covered
without an API key or a network. Tool dispatch is covered in
test_voice_tools.py. The Gemini transport itself is a thin
translation layer over the SDK and is not exercised here.
"""
import asyncio
import base64
import json

import pytest
from fastapi.testclient import TestClient

from backend import voice_live, voice_session_store as store


class FakeEngine:
    """Scripted engine. `script` is the events it emits once started."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.started = False
        self.closed = False
        self.audio_in: list[bytes] = []
        self.text_in: list[str] = []
        self.tool_results: list[tuple] = []
        self._released = asyncio.Event()

    async def start(self):
        self.started = True

    async def send_audio(self, pcm: bytes):
        self.audio_in.append(pcm)

    async def send_text(self, text: str):
        self.text_in.append(text)

    async def send_tool_result(self, call_id, name, result):
        self.tool_results.append((call_id, name, result))

    async def events(self):
        for event in self.script:
            yield event
        # Then hold the connection open like a real session would, instead of
        # ending the stream and racing the test's assertions.
        await self._released.wait()

    async def close(self):
        self.closed = True
        self._released.set()


@pytest.fixture
def fake_engine(monkeypatch):
    """Install a fake engine and hand the test a handle to configure it."""
    holder = {}

    def install(script=None):
        engine = FakeEngine(script)
        holder["engine"] = engine

        async def _create(*_args, **_kwargs):
            return engine

        monkeypatch.setattr(voice_live, "create_engine", _create)
        return engine

    install()
    return install


@pytest.fixture
def sync_client(app, client, monkeypatch):
    """Starlette's TestClient — httpx's ASGI transport can't do websockets.

    Entered as a context manager on purpose. Without it, TestClient spins up a
    fresh portal per call and tears it down the moment the websocket block
    exits, which kills the relay partway through its cleanup — so the turn
    flush, tool-call result and session stop never land. Holding one portal
    open for the test lets that cleanup finish, which is exactly what these
    tests are asserting on.

    Entering it also runs the app lifespan, so the background loops that shell
    out to binaries this container doesn't have (Agent.TV -> edge-tts) are
    stubbed first. They are unrelated to voice.
    """
    from backend import agenttv_channel, buzz_inbound

    async def _noop(*_args, **_kwargs):
        return None

    # Agent.TV shells out to edge-tts; the buzz listener talks to the Docker
    # socket. Neither exists in a test container and neither is voice-related.
    monkeypatch.setattr(agenttv_channel, "start_all_channels", _noop)
    monkeypatch.setattr(buzz_inbound, "run_inbound_listener", _noop)
    with TestClient(app) as c:
        yield c


async def _open_session(client, agent, **body):
    r = await client.post("/api/agents/me/voice/sessions",
                          headers={"X-Agent-Key": agent["api_key"]}, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _drain(ws, count, timeout_each=5):
    out = []
    for _ in range(count):
        out.append(json.loads(ws.receive_text()))
    return out


def _completed_calls(session_id):
    """Waits for a tool-call row that has actually been resolved."""
    async def check():
        rows = await store.list_tool_calls(session_id)
        return rows if rows and rows[0]["result_json"] else None
    return check


async def _eventually(check, timeout=5.0, interval=0.02):
    """Poll until `check()` returns something truthy, then return it.

    The relay finishes its cleanup (flush the open turn, log the tool result,
    stop the session) after the socket closes, so a test that asserts the
    instant the `with` block exits is racing the server. Returns the last value
    on timeout so the caller's own assert produces the failure message.
    """
    import time
    deadline = time.monotonic() + timeout
    while True:
        result = await check()
        if result or time.monotonic() > deadline:
            return result
        await asyncio.sleep(interval)


# ── Auth ─────────────────────────────────────────────────────────────────────

async def test_relay_rejects_a_bad_token(client, fresh_agent, sync_client, fake_engine):
    agent = await fresh_agent()
    s = await _open_session(client, agent)

    with pytest.raises(Exception):
        with sync_client.websocket_connect(
            f"/api/agents/me/voice/sessions/{s['session_id']}/ws?key=vvoice_{'0' * 48}"
        ) as ws:
            ws.receive_text()


async def test_relay_rejects_a_token_for_a_different_session(client, fresh_agent, sync_client, fake_engine):
    agent = await fresh_agent()
    a = await _open_session(client, agent)
    b = await _open_session(client, agent)

    with pytest.raises(Exception):
        with sync_client.websocket_connect(
            f"/api/agents/me/voice/sessions/{b['session_id']}/ws?key={a['token']}"
        ) as ws:
            ws.receive_text()


async def test_relay_rejects_a_stopped_sessions_token(client, fresh_agent, sync_client, fake_engine):
    agent = await fresh_agent()
    s = await _open_session(client, agent)
    await client.post(f"/api/agents/me/voice/sessions/{s['session_id']}/stop",
                      headers={"X-Agent-Key": agent["api_key"]})

    with pytest.raises(Exception):
        with sync_client.websocket_connect(
            f"/api/agents/me/voice/sessions/{s['session_id']}/ws?key={s['token']}"
        ) as ws:
            ws.receive_text()


# ── Relay behaviour ──────────────────────────────────────────────────────────

async def test_relay_greets_and_forwards_audio_to_the_model(client, fresh_agent, sync_client, fake_engine):
    engine = fake_engine()
    agent = await fresh_agent()
    s = await _open_session(client, agent)
    pcm = b"\x01\x02\x03\x04"

    with sync_client.websocket_connect(
        f"/api/agents/me/voice/sessions/{s['session_id']}/ws?key={s['token']}"
    ) as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "connected"
        assert hello["sessionId"] == s["session_id"]

        ws.send_text(json.dumps({"type": "audio", "audio": base64.b64encode(pcm).decode()}))
        ws.send_text(json.dumps({"type": "ping"}))
        assert json.loads(ws.receive_text())["type"] == "pong"

    assert engine.started
    assert pcm in engine.audio_in


async def test_relay_streams_model_audio_back_to_the_browser(client, fresh_agent, sync_client, fake_engine):
    fake_engine([voice_live.VoiceEvent(kind=voice_live.AUDIO, audio=b"\xaa\xbb")])
    agent = await fresh_agent()
    s = await _open_session(client, agent)

    with sync_client.websocket_connect(
        f"/api/agents/me/voice/sessions/{s['session_id']}/ws?key={s['token']}"
    ) as ws:
        assert json.loads(ws.receive_text())["type"] == "connected"
        audio = json.loads(ws.receive_text())

    assert audio["type"] == "audio"
    assert base64.b64decode(audio["audio"]) == b"\xaa\xbb"


async def test_a_completed_turn_is_persisted_as_transcript(client, fresh_agent, sync_client, fake_engine):
    fake_engine([
        voice_live.VoiceEvent(kind=voice_live.INPUT_TRANSCRIPT, text="what is my "),
        voice_live.VoiceEvent(kind=voice_live.INPUT_TRANSCRIPT, text="balance"),
        voice_live.VoiceEvent(kind=voice_live.OUTPUT_TRANSCRIPT, text="checking now"),
        voice_live.VoiceEvent(kind=voice_live.TURN_COMPLETE),
    ])
    agent = await fresh_agent()
    s = await _open_session(client, agent)

    with sync_client.websocket_connect(
        f"/api/agents/me/voice/sessions/{s['session_id']}/ws?key={s['token']}"
    ) as ws:
        _drain(ws, 5)  # connected + 2 user transcripts + model transcript + turn end

    turns = await store.get_transcript(s["session_id"])
    assert [t["role"] for t in turns] == ["user", "assistant"]
    # Streamed fragments are joined into one utterance, not one row per chunk.
    assert turns[0]["content_audio_transcript"] == "what is my balance"
    assert turns[1]["content_text"] == "checking now"


async def test_transcripts_are_searchable_after_the_call(client, fresh_agent, sync_client, fake_engine):
    fake_engine([
        voice_live.VoiceEvent(kind=voice_live.INPUT_TRANSCRIPT, text="rotate the treasury"),
        voice_live.VoiceEvent(kind=voice_live.TURN_COMPLETE),
    ])
    agent = await fresh_agent()
    s = await _open_session(client, agent)

    with sync_client.websocket_connect(
        f"/api/agents/me/voice/sessions/{s['session_id']}/ws?key={s['token']}"
    ) as ws:
        _drain(ws, 3)

    r = await client.get("/api/agents/me/voice/sessions/search",
                         headers={"X-Agent-Key": agent["api_key"]}, params={"q": "treasury"})
    assert [hit["session_id"] for hit in r.json()["results"]] == [s["session_id"]]


async def test_an_unfinished_turn_is_still_persisted_on_disconnect(client, fresh_agent, sync_client, fake_engine):
    """A caller who hangs up mid-sentence should not lose what they said."""
    fake_engine([voice_live.VoiceEvent(kind=voice_live.INPUT_TRANSCRIPT, text="wait, actually")])
    agent = await fresh_agent()
    s = await _open_session(client, agent)

    with sync_client.websocket_connect(
        f"/api/agents/me/voice/sessions/{s['session_id']}/ws?key={s['token']}"
    ) as ws:
        _drain(ws, 2)

    turns = await _eventually(lambda: store.get_transcript(s["session_id"]))
    assert [t["content_audio_transcript"] for t in turns] == ["wait, actually"]


async def test_disconnect_closes_the_engine_and_the_session(client, fresh_agent, sync_client, fake_engine):
    engine = fake_engine()
    agent = await fresh_agent()
    s = await _open_session(client, agent)

    with sync_client.websocket_connect(
        f"/api/agents/me/voice/sessions/{s['session_id']}/ws?key={s['token']}"
    ) as ws:
        _drain(ws, 1)

    assert engine.closed
    async def stopped():
        row = await store.get_session(s["session_id"])
        return row if row and row["status"] == "stopped" else None

    row = await _eventually(stopped)
    assert row is not None and row["status"] == "stopped"
    assert row["stop_reason"] == "client_disconnected"
    # And the token must not survive the call.
    assert await store.resolve_ws_token(s["token"]) is None


# ── Tool calls ───────────────────────────────────────────────────────────────

async def test_a_session_without_an_allowlist_has_no_tools(client, fresh_agent, sync_client, fake_engine):
    """No allowlist means no tools, and the model is told so explicitly rather
    than left to assume the call ran."""
    engine = fake_engine([
        voice_live.VoiceEvent(
            kind=voice_live.TOOL_CALL, tool_name="vantage__whoami_get",
            tool_args={}, tool_call_id="fc-1",
        )
    ])
    agent = await fresh_agent()
    s = await _open_session(client, agent)  # no tools requested

    with sync_client.websocket_connect(
        f"/api/agents/me/voice/sessions/{s['session_id']}/ws?key={s['token']}"
    ) as ws:
        _drain(ws, 1)
        announced = json.loads(ws.receive_text())

    assert announced["type"] == "tool_call"
    assert len(engine.tool_results) == 1
    call_id, _name, result = engine.tool_results[0]
    assert call_id == "fc-1"
    assert result["status"] == "unknown_tool"

    calls = await _eventually(_completed_calls(s["session_id"]))
    assert calls is not None and len(calls) == 1
    assert calls[0]["is_error"] == 1


async def test_an_allowed_tool_actually_executes(client, fresh_agent, sync_client, fake_engine):
    """End to end: the model asks, the relay runs it as the agent, and the real
    endpoint's answer comes back."""
    engine = fake_engine([
        voice_live.VoiceEvent(
            kind=voice_live.TOOL_CALL, tool_name="vantage__whoami_get",
            tool_args={}, tool_call_id="fc-2",
        )
    ])
    agent = await fresh_agent()
    s = await _open_session(client, agent, tools=["tag:copilot"])

    with sync_client.websocket_connect(
        f"/api/agents/me/voice/sessions/{s['session_id']}/ws?key={s['token']}"
    ) as ws:
        _drain(ws, 1)
        assert json.loads(ws.receive_text())["type"] == "tool_call"
        assert json.loads(ws.receive_text())["type"] == "tool_result"

    assert len(engine.tool_results) == 1
    _cid, _name, result = engine.tool_results[0]
    assert result["status"] == "ok", result
    assert agent["name"] in str(result["result"])

    calls = await _eventually(_completed_calls(s["session_id"]))
    assert calls is not None and calls[0]["is_error"] == 0


# ── Event contract ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("event,expected", [
    (voice_live.VoiceEvent(kind=voice_live.INPUT_TRANSCRIPT, text="hi"),
     {"type": "transcript", "sender": "user", "text": "hi", "isFinal": True}),
    (voice_live.VoiceEvent(kind=voice_live.OUTPUT_TRANSCRIPT, text="yo"),
     {"type": "transcript", "sender": "model", "text": "yo", "isFinal": False}),
    (voice_live.VoiceEvent(kind=voice_live.TURN_COMPLETE),
     {"type": "transcript", "sender": "model", "text": "", "isFinal": True}),
    (voice_live.VoiceEvent(kind=voice_live.INTERRUPTED), {"type": "interrupted"}),
    (voice_live.VoiceEvent(kind=voice_live.ERROR, message="boom"),
     {"type": "error", "message": "boom"}),
])
def test_events_render_the_wire_format_the_client_expects(event, expected):
    assert event.to_client_message() == expected


def test_audio_events_render_as_base64():
    ev = voice_live.VoiceEvent(kind=voice_live.AUDIO, audio=b"\x00\xff")
    assert ev.to_client_message() == {"type": "audio", "audio": base64.b64encode(b"\x00\xff").decode()}


async def test_engine_factory_refuses_an_unhostable_engine():
    with pytest.raises(RuntimeError, match="cannot be hosted"):
        await voice_live.create_engine("huggingface_s2s", api_key="k")


async def test_engine_factory_requires_a_key():
    with pytest.raises(RuntimeError, match="No Gemini API key"):
        await voice_live.create_engine("gemini_live", api_key="")


def test_agent_byok_key_wins_over_the_instance_key():
    assert voice_live.resolve_gemini_api_key({"gemini_api_key": "agent-key"}) == "agent-key"
