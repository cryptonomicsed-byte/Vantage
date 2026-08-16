"""Realtime voice engines for Vantage-hosted voice sessions.

Phase 2 of the voice integration: the model connection that used to live in
Vantage-Voice-'s Express server moves behind this interface, so Vantage's own
WebSocket endpoint can relay audio without a second deployment in the path.

The engine is an interface rather than a direct SDK call for three reasons:

  * The relay logic (auth, transcript persistence, tool-call logging, cleanup)
    is the part that carries the bugs, and it is fully testable against a fake
    engine without an API key or a network.
  * Vantage already supports more than one voice backend -- Gemini Live, the
    Groq/ElevenLabs cascade, and the legacy HuggingFace-S2S subprocess. They
    differ only in how frames become events.
  * google-genai is imported lazily inside the Gemini engine. A module-level
    import of an optional dependency is exactly what took `import backend.main`
    down on any box without /opt/ares; the same mistake with an LLM SDK would
    be worse, because it would fail closed on every route rather than one.

Status: the relay and the event contract are covered by tests. The Gemini
transport itself talks to a live Google service and has NOT been exercised
against it here -- there is no API key in this environment. Treat
GeminiLiveEngine as needing a smoke test on first deploy.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional, Protocol

logger = logging.getLogger(__name__)

# Gemini Live speaks 16kHz PCM in, 24kHz PCM out. These are the rates the
# browser recorder and player are built around; changing one means changing
# both ends and the resampler in between.
INPUT_SAMPLE_RATE = 16_000
OUTPUT_SAMPLE_RATE = 24_000

DEFAULT_GEMINI_LIVE_MODEL = "gemini-3.1-flash-live-preview"


# ── Event contract ───────────────────────────────────────────────────────────

AUDIO = "audio"
INPUT_TRANSCRIPT = "input_transcript"
OUTPUT_TRANSCRIPT = "output_transcript"
TURN_COMPLETE = "turn_complete"
INTERRUPTED = "interrupted"
TOOL_CALL = "tool_call"
ERROR = "error"


@dataclass
class VoiceEvent:
    """One thing the model did. Deliberately flat: the relay switches on `kind`
    and never has to know which engine produced it."""
    kind: str
    audio: bytes = b""
    text: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_call_id: str = ""
    message: str = ""

    def to_client_message(self) -> Optional[dict]:
        """Render as the JSON the browser expects.

        This is the wire format Vantage-Voice-'s React client already speaks,
        kept identical on purpose so the ported frontend and the standalone app
        can both point at this endpoint during the transition.
        """
        if self.kind == AUDIO:
            return {"type": "audio", "audio": base64.b64encode(self.audio).decode("ascii")}
        if self.kind == INPUT_TRANSCRIPT:
            return {"type": "transcript", "sender": "user", "text": self.text, "isFinal": True}
        if self.kind == OUTPUT_TRANSCRIPT:
            return {"type": "transcript", "sender": "model", "text": self.text, "isFinal": False}
        if self.kind == TURN_COMPLETE:
            return {"type": "transcript", "sender": "model", "text": "", "isFinal": True}
        if self.kind == INTERRUPTED:
            return {"type": "interrupted"}
        if self.kind == TOOL_CALL:
            return {"type": "tool_call", "toolName": self.tool_name, "toolArgs": self.tool_args}
        if self.kind == ERROR:
            return {"type": "error", "message": self.message}
        return None


class VoiceEngine(Protocol):
    """What the relay needs from a realtime model connection."""

    async def start(self) -> None: ...
    async def send_audio(self, pcm: bytes) -> None: ...
    async def send_text(self, text: str) -> None: ...
    async def send_tool_result(self, call_id: str, name: str, result: Any) -> None: ...
    def events(self) -> AsyncIterator[VoiceEvent]: ...
    async def close(self) -> None: ...


# ── Gemini Live ──────────────────────────────────────────────────────────────

class GeminiLiveEngine:
    """Gemini Live transport.

    Holds the SDK session open and translates its responses into VoiceEvents.
    The SDK import happens in start(), not at module scope, so a Vantage
    install without google-genai still imports and serves every other route --
    it just can't open a Gemini voice session.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_GEMINI_LIVE_MODEL,
        system_instruction: str = "",
        voice: str = "",
        tools: Optional[list[dict]] = None,
    ):
        self._api_key = api_key
        self._model = model
        self._system_instruction = system_instruction
        self._voice = voice
        self._tools = tools or []
        self._session = None
        self._ctx = None

    async def start(self) -> None:
        try:
            from google import genai  # noqa: PLC0415 - optional dependency, see module docstring
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise RuntimeError(
                "google-genai is not installed; Gemini Live voice sessions are unavailable. "
                "Install it or use engine='cascade'."
            ) from exc

        self._types = types
        client = genai.Client(api_key=self._api_key, http_options={"api_version": "v1alpha"})

        config: dict[str, Any] = {
            "response_modalities": ["AUDIO"],
            # Both directions transcribed: the input transcript is what makes a
            # voice turn searchable, and without it a session persists audio
            # with no text at all.
            "input_audio_transcription": {},
            "output_audio_transcription": {},
        }
        if self._system_instruction:
            config["system_instruction"] = self._system_instruction
        if self._voice:
            config["speech_config"] = {
                "voice_config": {"prebuilt_voice_config": {"voice_name": self._voice}}
            }
        if self._tools:
            config["tools"] = [{"function_declarations": self._tools}]

        self._ctx = client.aio.live.connect(model=self._model, config=config)
        self._session = await self._ctx.__aenter__()

    async def send_audio(self, pcm: bytes) -> None:
        if not self._session:
            return
        await self._session.send_realtime_input(
            audio=self._types.Blob(data=pcm, mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}")
        )

    async def send_text(self, text: str) -> None:
        if not self._session:
            return
        await self._session.send_client_content(
            turns={"role": "user", "parts": [{"text": text}]}, turn_complete=True
        )

    async def send_tool_result(self, call_id: str, name: str, result: Any) -> None:
        if not self._session:
            return
        await self._session.send_tool_response(
            function_responses=[{"id": call_id, "name": name, "response": {"result": result}}]
        )

    async def events(self) -> AsyncIterator[VoiceEvent]:
        if not self._session:
            return
        async for response in self._session.receive():
            # Audio first: it is the latency-critical path.
            data = getattr(response, "data", None)
            if data:
                yield VoiceEvent(kind=AUDIO, audio=data)

            server_content = getattr(response, "server_content", None)
            if server_content is not None:
                inp = getattr(server_content, "input_transcription", None)
                if inp is not None and getattr(inp, "text", ""):
                    yield VoiceEvent(kind=INPUT_TRANSCRIPT, text=inp.text)

                out = getattr(server_content, "output_transcription", None)
                if out is not None and getattr(out, "text", ""):
                    yield VoiceEvent(kind=OUTPUT_TRANSCRIPT, text=out.text)

                if getattr(server_content, "interrupted", False):
                    yield VoiceEvent(kind=INTERRUPTED)
                if getattr(server_content, "turn_complete", False):
                    yield VoiceEvent(kind=TURN_COMPLETE)

            tool_call = getattr(response, "tool_call", None)
            if tool_call is not None:
                for fc in getattr(tool_call, "function_calls", []) or []:
                    yield VoiceEvent(
                        kind=TOOL_CALL,
                        tool_name=getattr(fc, "name", "") or "",
                        tool_args=dict(getattr(fc, "args", {}) or {}),
                        tool_call_id=getattr(fc, "id", "") or "",
                    )

    async def close(self) -> None:
        if self._ctx is not None:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception as exc:
                logger.debug("gemini live close: %s", exc)
            finally:
                self._ctx = None
                self._session = None


# ── Engine selection ─────────────────────────────────────────────────────────

def resolve_gemini_api_key(agent: Optional[dict] = None) -> str:
    """Prefer the agent's own BYOK Gemini key, fall back to the instance key.

    Per-agent keys matter for attribution: a shared pool means Gemini spend
    cannot be traced to whoever actually spoke.
    """
    if agent:
        for column in ("gemini_api_key", "google_api_key"):
            key = (agent.get(column) or "").strip()
            if key:
                return key
    from .config import settings
    return (getattr(settings, "GEMINI_API_KEY", "") or "").strip()


async def create_engine(
    engine: str,
    *,
    api_key: str,
    system_instruction: str = "",
    voice: str = "",
    tools: Optional[list[dict]] = None,
    model: str = DEFAULT_GEMINI_LIVE_MODEL,
) -> VoiceEngine:
    """Build (but do not start) an engine for a session.

    Tests patch this to inject a fake, which is the seam that makes the relay
    testable without an API key or a network.
    """
    if engine == "gemini_live":
        if not api_key:
            raise RuntimeError(
                "No Gemini API key available for this agent. Set one on the agent "
                "(BYOK) or configure VANTAGE_GEMINI_API_KEY."
            )
        return GeminiLiveEngine(
            api_key=api_key,
            model=model,
            system_instruction=system_instruction,
            voice=voice,
            tools=tools,
        )
    raise RuntimeError(
        f"engine '{engine}' cannot be hosted by Vantage yet; "
        "only 'gemini_live' is implemented in-process."
    )
