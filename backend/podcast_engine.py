"""Real podcast generation: two-host dialogue script (not a monologue read
verbatim) + real multi-voice synthesis (not one generic voice reading the
prompt word-for-word).

Ported pattern (not code -- different stack/license) from the podcastfy
research: (1) its multi-turn Q&A/dialogue prompting approach, reimplemented
here against OmniRoute (already proven reachable from this host, same
gateway Copilot's fallback uses) instead of a paid LLM API; (2) its
free/local edge-tts backend for actual distinct per-speaker voices instead
of Piper's single robotic voice -- free, no API key, no GPU required
(confirmed live: `pip install edge-tts` in this venv, real neural voices).

Two output kinds:
  - audio: concatenated dialogue audio -> publish to Audio (surface='audio')
  - video: same audio + a simple static visual composited via ffmpeg ->
    publish to Cinema (surface='cinema')

Both reuse backend/routers/surfaces.py's _insert_broadcast directly rather
than duplicating the publish logic.
"""
import asyncio
import json
import logging
import re
import uuid
import sys
from pathlib import Path

import httpx

from .config import settings

logger = logging.getLogger(__name__)

# The systemd service invokes /opt/ares/venv/bin/python directly (not an
# activated shell), so bare "edge-tts" isn't on PATH -- resolve it relative
# to the running interpreter's own venv instead of hardcoding a path.
EDGE_TTS_BIN = str(Path(sys.executable).with_name("edge-tts"))

SCRATCH_DIR = Path("/opt/ares/media/podcasts")  # per-turn TTS + concat intermediates, not web-served
SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

# Final output lands directly in the already-mounted static dirs (see
# main.py's app.mount calls) so no new mount/route is needed.
AUDIO_OUT_DIR = Path("/opt/ares/media/audio")
VIDEO_OUT_DIR = Path("/opt/ares/media/videos")

VOICES = {"A": "en-US-GuyNeural", "B": "en-US-JennyNeural"}


async def _omniroute_complete(prompt: str) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{settings.OMNIROUTE_URL.rstrip('/')}/v1/chat/completions",
            json={
                "model": settings.OMNIROUTE_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            headers={"Authorization": f"Bearer {settings.OMNIROUTE_API_KEY}"},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def generate_dialogue_script(topic: str, num_turns: int = 10) -> list[dict]:
    """Real two-host conversation, not a narrated monologue -- the core fix
    for "the AI is literally just reading the prompt word for word". Each
    turn is a short, natural back-and-forth line, alternating hosts, with
    real reactions/follow-ups to what the other host just said."""
    prompt = f"""Write a natural, engaging two-host podcast dialogue about: {topic}

Two hosts, A and B, having a real back-and-forth conversation -- not two
monologues. Each line should be short (1-3 sentences), reference or react to
what the other host just said at least some of the time, include natural
verbal tics (a laugh, "right,", "okay so", genuine curiosity/disagreement),
and cover the topic substantively across {num_turns} total turns.

Output STRICT JSON only, a list of objects, no markdown fences:
[{{"speaker": "A", "text": "..."}}, {{"speaker": "B", "text": "..."}}, ...]"""

    raw = await _omniroute_complete(prompt)
    match = re.search(r"\[[\s\S]*\]", raw)
    if not match:
        raise ValueError(f"Could not parse dialogue JSON from LLM response: {raw[:200]}")
    turns = json.loads(match.group(0))
    cleaned = []
    for t in turns:
        speaker = str(t.get("speaker", "A")).strip().upper()
        text = str(t.get("text", "")).strip()
        if speaker not in ("A", "B") or not text:
            continue
        cleaned.append({"speaker": speaker, "text": text})
    if not cleaned:
        raise ValueError("LLM produced no usable dialogue turns")
    return cleaned


async def _synthesize_turn(text: str, voice: str, out_path: Path):
    proc = await asyncio.create_subprocess_exec(
        EDGE_TTS_BIN, "--voice", voice, "--text", text, "--write-media", str(out_path),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"edge-tts failed: {stderr.decode()[:300]}")


_voices_cache: list[dict] | None = None


async def list_voices() -> list[dict]:
    """Real edge-tts voice catalog (47 English neural voices, confirmed
    live) -- backs the provider-choice settings UI. Free, no API key,
    cached in-process after first call since the list is effectively
    static per deployment."""
    global _voices_cache
    if _voices_cache is not None:
        return _voices_cache
    proc = await asyncio.create_subprocess_exec(
        EDGE_TTS_BIN, "--list-voices",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    voices = []
    for line in out.decode().splitlines()[1:]:  # skip header row
        parts = line.split()
        if len(parts) < 2 or not parts[0].startswith(("en-", "es-", "fr-", "de-")):
            continue
        voices.append({"id": parts[0], "gender": parts[1] if len(parts) > 1 else ""})
    _voices_cache = voices
    return voices


async def synthesize_dialogue(turns: list[dict], work_dir: Path, voices: dict | None = None) -> tuple[Path, list[dict]]:
    """Synthesize each turn with its speaker's distinct voice, concatenate
    into one continuous audio track via ffmpeg (real multi-voice, not one
    generic reader). Also returns each turn's [start, end) in the final
    track so the video composite can burn in real synced captions instead
    of one static title card for the whole episode. `voices` overrides the
    free default {"A": ..., "B": ...} -- e.g. a per-agent choice from
    Settings, real edge-tts voice ids from list_voices()."""
    voice_map = voices or VOICES
    turn_files = []
    for i, turn in enumerate(turns):
        voice = voice_map.get(turn["speaker"], VOICES["A"])
        out = work_dir / f"turn_{i:03d}.mp3"
        await _synthesize_turn(turn["text"], voice, out)
        turn_files.append(out)

    timings = []
    cursor = 0.0
    for turn, f in zip(turns, turn_files):
        dur = await _ffprobe_duration_precise(f)
        timings.append({**turn, "start": cursor, "end": cursor + dur})
        cursor += dur

    concat_list = work_dir / "concat.txt"
    concat_list.write_text("\n".join(f"file '{f.name}'" for f in turn_files))
    combined = work_dir / "combined.mp3"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", str(combined),
        cwd=str(work_dir),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {stderr.decode()[:300]}")
    return combined, timings


async def _ffprobe_duration_precise(path: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    try:
        return float(out.decode().strip())
    except ValueError:
        return 0.0


async def _ffprobe_duration(path: Path) -> int:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    try:
        return round(float(out.decode().strip()))
    except ValueError:
        return 0


HOST_NAMES = {"A": "Alex", "B": "Jordan"}
BG_PATH = VIDEO_OUT_DIR / "agenttv_bg.png"


def _escape_drawtext(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "’").replace(":", "\\:").replace(",", "\\,")


def _wrap_caption(text: str, max_chars_per_line: int = 58, max_lines: int = 2) -> str:
    """Real word-wrap for drawtext (no built-in wrapping) -- fixes captions
    running off the right edge of the frame at fontsize=26 on a 1280px-wide
    video (a real bug found via a live rendered-frame check: a longer line
    was cut off mid-word past the frame boundary)."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars_per_line and current:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
        else:
            current = candidate
    else:
        if current:
            lines.append(current)
    if len(lines) == max_lines and len(" ".join(words)) > sum(len(l) for l in lines):
        lines[-1] = lines[-1].rstrip() + "..."
    return "\n".join(lines)


async def _ensure_background() -> Path:
    """The show's gradient background, rendered ONCE to a static PNG and
    reused for every episode -- geq evaluated per-pixel per-frame for a
    live 3-5min render measured at ~2.9x realtime (a 10s test took 29s),
    which would blow the jingle's wait budget. A static image composited
    under drawtext costs almost nothing by comparison."""
    if BG_PATH.exists():
        return BG_PATH
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=0x141422:s=1280x720",
        "-vf", "geq=r='40+20*sin(2*PI*Y/H)':g='20+15*cos(2*PI*Y/H)':b='60+25*sin(2*PI*(X+Y)/(W+H))'",
        "-frames:v", "1", "-update", "1", str(BG_PATH),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"background render failed: {stderr.decode()[:300]}")
    return BG_PATH


async def composite_video(audio_path: Path, title: str, timings: list[dict], out_path: Path):
    """A real, consistent show template instead of a static title card:
    gradient background, persistent episode title bar, and per-turn
    captions ("Alex: ...") synced to the actual dialogue timing -- so a
    video podcast looks like a produced show, not a generic placeholder."""
    bg = await _ensure_background()
    safe_title = _escape_drawtext(title[:90])

    caption_filters = []
    for t in timings:
        speaker = HOST_NAMES.get(t["speaker"], t["speaker"])
        wrapped = _wrap_caption(f"{speaker}: {t['text']}")
        line = _escape_drawtext(wrapped)
        caption_filters.append(
            f"drawtext=text='{line}':fontcolor=white:fontsize=26:"
            f"x=(w-text_w)/2:y=h-140:box=1:boxcolor=black@0.55:boxborderw=14:"
            f"line_spacing=8:enable='between(t,{t['start']:.2f},{t['end']:.2f})'"
        )

    vf = (
        f"drawtext=text='{safe_title}':fontcolor=white:fontsize=34:x=(w-text_w)/2:y=48:"
        "box=1:boxcolor=black@0.4:boxborderw=10,"
        f"drawtext=text='VANTAGE RADIO':fontcolor=0x9d8cff:fontsize=16:x=(w-text_w)/2:y=100,"
        + ",".join(caption_filters)
    )

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(bg),
        "-i", str(audio_path),
        "-vf", vf,
        "-shortest", "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", str(out_path),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg composite failed: {stderr.decode()[:400]}")


async def generate_podcast(topic: str, kind: str, num_turns: int = 10, voices: dict | None = None) -> dict:
    """Full pipeline: dialogue script -> multi-voice synthesis -> (audio |
    video) output file, written directly into the already-mounted static
    dir for its kind. Returns {"stream_url": ..., "duration_sec": ..., "script": [...]}.
    Does NOT publish -- caller decides surface/cover/etc via _insert_broadcast.
    num_turns controls roughly how long the episode runs -- Agent.TV uses a
    higher count for ~3min channel segments than the one-shot Collab
    "Create Podcast" default. voices overrides the default host voices --
    a per-agent choice from Settings (see list_voices())."""
    work_id = uuid.uuid4().hex[:12]
    work_dir = SCRATCH_DIR / work_id
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        turns = await generate_dialogue_script(topic, num_turns=num_turns)
        combined_audio, timings = await synthesize_dialogue(turns, work_dir, voices=voices)

        if kind == "video":
            final = VIDEO_OUT_DIR / f"{work_id}.mp4"
            await composite_video(combined_audio, topic, timings, final)
            stream_url = f"/media/videos/{work_id}.mp4"
        else:
            final = AUDIO_OUT_DIR / f"{work_id}.mp3"
            final.write_bytes(combined_audio.read_bytes())
            stream_url = f"/media/audio/{work_id}.mp3"

        duration = await _ffprobe_duration(final)
        return {"stream_url": stream_url, "duration_sec": duration, "script": turns, "work_id": work_id}
    finally:
        # Scratch (per-turn TTS clips + concat list) is disposable once the
        # final file has been written out to the served directory.
        for f in work_dir.glob("*"):
            f.unlink(missing_ok=True)
        work_dir.rmdir()


JINGLE_PATH = VIDEO_OUT_DIR / "agenttv_jingle.mp4"
JINGLE_STREAM_URL = "/media/videos/agenttv_jingle.mp4"
JINGLE_DURATION_SEC = 30


async def ensure_jingle() -> Path:
    """Real, fixed 30s house jingle/'commercial' played between Agent.TV
    segments while the next one renders -- one asset, generated once, reused
    forever (including for user-submitted podcasts that air in the
    rotation) until there's an actual sponsor to swap in. Idempotent -- only
    (re)builds if missing."""
    if JINGLE_PATH.exists():
        return JINGLE_PATH

    voice_path = SCRATCH_DIR / "jingle_voice.mp3"
    await _synthesize_turn(
        "You are listening to Vantage Radio. This spot is reserved for a future "
        "sponsor -- reach out if that is you. Back to the show in just a moment.",
        VOICES["A"], voice_path,
    )
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=220:duration={JINGLE_DURATION_SEC},volume=0.15",
        "-i", str(voice_path),
        "-f", "lavfi", "-i", f"color=c=0x1a0a2e:s=1280x720:d={JINGLE_DURATION_SEC}",
        "-filter_complex",
        "[0:a]afade=t=in:d=1,afade=t=out:st=28:d=2[bed];"
        "[1:a]adelay=1500|1500[voice];"
        "[bed][voice]amix=inputs=2:duration=first:dropout_transition=2[aout];"
        "[2:v]drawtext=text='VANTAGE RADIO':fontcolor=white:fontsize=54:x=(w-text_w)/2:y=(h-text_h)/2-40,"
        "drawtext=text='A word from our future sponsor':fontcolor=0xaaaaee:fontsize=24:x=(w-text_w)/2:y=(h-text_h)/2+40[vout]",
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-t", str(JINGLE_DURATION_SEC), str(JINGLE_PATH),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    voice_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"jingle build failed: {stderr.decode()[:300]}")
    return JINGLE_PATH
