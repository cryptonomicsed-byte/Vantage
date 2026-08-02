"""On-demand speech-to-speech voice sessions for Copilot.

Owns the process lifecycle: this is Vantage's OWN deployment of the real
huggingface/speech-to-speech pipeline (already installed at /opt/s2s on the
VPS, confirmed via `pip show speech-to-speech` -> Home-page
github.com/huggingface/speech-to-speech), not a dependency on Hermes's
always-on systemd service. That service (speech-to-speech.service) ran
`--num_pipelines 2`, double-loading every model (STT+TTS) into RAM for no
reason Vantage needs -- 3.47GB / ~42% of the whole box, contributing to a
live swap-maxed crisis found 2026-08-02. Vantage always uses
--num_pipelines 1, and only runs the process while a Copilot voice session
is actually open, not 24/7.

Wiring: the pipeline's --llm_backend responses-api points at Vantage's own
loopback shim (routers/voice_responses.py), authenticated with a random
per-session token, and --model_name is set to the requesting agent's own
name -- the shim reads that name back out of the bearer token mapping and
routes into _dispatch_chat() for THAT agent, so voice replies come from
whichever agent/provider the user actually has active in Copilot, not a
hardcoded "hermes-agent" model id.

Single global slot: this box's resources (2 vCPU, 7.8GB RAM) don't support
more than one live pipeline at a time even at num_pipelines=1 -- a second
/start call while one is already running stops the first (last caller
wins) rather than trying to run two concurrent pipelines.
"""
import asyncio
import logging
import secrets
import signal
import time
from typing import Optional

logger = logging.getLogger(__name__)

S2S_BIN = "/opt/s2s/bin/speech-to-speech"
WS_HOST = "127.0.0.1"
WS_PORT = 8770  # distinct from Hermes's old 8765, so nothing can confuse the two
IDLE_TIMEOUT_SECONDS = 300  # auto-stop if the client never opens a client audio ws
VANTAGE_INTERNAL_BASE_URL = "http://127.0.0.1:8001/api/internal/voice"

# session_token -> {"agent_id": int, "agent_name": str}. Populated on start(),
# consulted by routers/voice_responses.py to know which agent to dispatch
# into. One entry at a time in practice (single global slot), but keyed by
# token rather than a bare global so a stale request from a just-stopped
# session can't accidentally hit a newly-started, different agent's session.
_active_tokens: dict[str, dict] = {}

_state: dict = {
    "process": None,       # asyncio.subprocess.Process | None
    "token": None,
    "agent_id": None,
    "agent_name": None,
    "started_at": None,
    "idle_watchdog": None,  # asyncio.Task | None
}


def get_status() -> dict:
    if _state["process"] is None:
        return {"running": False}
    return {
        "running": True,
        "agent_id": _state["agent_id"],
        "agent_name": _state["agent_name"],
        "started_at": _state["started_at"],
        "ws_url": f"ws://{WS_HOST}:{WS_PORT}",
    }


def resolve_token(token: str) -> Optional[dict]:
    """Used by the Responses-API shim to find which agent a session
    belongs to. Returns None for an unknown/expired token -- the shim
    rejects the call rather than guessing an agent."""
    return _active_tokens.get(token)


async def _idle_watchdog(token: str):
    """Safety net: if nothing ever connects to the pipeline's audio
    websocket, don't let the process (and its loaded models) sit in RAM
    forever. Real usage stops the session explicitly via /me/voice/stop
    when the user closes the Copilot voice UI -- this is a fallback for
    the case where that never happens (tab closed, crash, etc)."""
    try:
        await asyncio.sleep(IDLE_TIMEOUT_SECONDS)
        if _state["token"] == token:
            logger.warning("voice session %s idle-timed-out after %ss, stopping", token, IDLE_TIMEOUT_SECONDS)
            await stop_session()
    except asyncio.CancelledError:
        pass


async def start_session(agent_id: int, agent_name: str) -> dict:
    """Starts (or restarts, if one was already running) a single voice
    pipeline process bound to this agent. num_pipelines=1 always."""
    if _state["process"] is not None:
        logger.info("voice session already running for agent_id=%s, stopping it before starting agent_id=%s", _state["agent_id"], agent_id)
        await stop_session()

    token = "vvoice_" + secrets.token_hex(24)
    _active_tokens.clear()  # single global slot -- only the newest token is ever valid
    _active_tokens[token] = {"agent_id": agent_id, "agent_name": agent_name}

    args = [
        S2S_BIN,
        "--stt", "faster-whisper",
        "--tts", "kokoro",
        "--kokoro_voice", "af_heart",
        "--llm_backend", "responses-api",
        "--num_pipelines", "1",
        "--model_name", agent_name,
        "--responses_api_base_url", VANTAGE_INTERNAL_BASE_URL,
        "--responses_api_api_key", token,
        "--ws_host", WS_HOST,
        "--ws_port", str(WS_PORT),
    ]

    logger.info("starting voice session for agent_id=%s agent_name=%s (num_pipelines=1)", agent_id, agent_name)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    _state["process"] = proc
    _state["token"] = token
    _state["agent_id"] = agent_id
    _state["agent_name"] = agent_name
    _state["started_at"] = time.time()
    _state["idle_watchdog"] = asyncio.ensure_future(_idle_watchdog(token))

    return {"ok": True, "ws_url": f"ws://{WS_HOST}:{WS_PORT}", "agent_id": agent_id}


async def stop_session() -> dict:
    proc = _state["process"]
    if proc is None:
        return {"ok": True, "was_running": False}

    if _state["idle_watchdog"]:
        _state["idle_watchdog"].cancel()

    try:
        proc.send_signal(signal.SIGTERM)
        await asyncio.wait_for(proc.wait(), timeout=10)
    except (ProcessLookupError, asyncio.TimeoutError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass

    stopped_agent = _state["agent_id"]
    _active_tokens.clear()
    _state.update(process=None, token=None, agent_id=None, agent_name=None, started_at=None, idle_watchdog=None)
    logger.info("voice session stopped (was agent_id=%s)", stopped_agent)
    return {"ok": True, "was_running": True}
