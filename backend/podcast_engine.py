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


async def synthesize_dialogue(turns: list[dict], work_dir: Path) -> Path:
    """Synthesize each turn with its speaker's distinct voice, concatenate
    into one continuous audio track via ffmpeg (real multi-voice, not one
    generic reader)."""
    turn_files = []
    for i, turn in enumerate(turns):
        voice = VOICES.get(turn["speaker"], VOICES["A"])
        out = work_dir / f"turn_{i:03d}.mp3"
        await _synthesize_turn(turn["text"], voice, out)
        turn_files.append(out)

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
    return combined


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


async def composite_video(audio_path: Path, title: str, out_path: Path):
    """Simple static-background video for the video-podcast option --
    same lightweight approach Agent.TV's channel loop already uses, not a
    new dependency."""
    safe_title = title.replace("'", "")[:80]
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=0x141422:s=1280x720",
        "-i", str(audio_path),
        "-vf", f"drawtext=text='{safe_title}':fontcolor=white:fontsize=32:x=(w-text_w)/2:y=(h-text_h)/2",
        "-shortest", "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", str(out_path),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg composite failed: {stderr.decode()[:300]}")


async def generate_podcast(topic: str, kind: str) -> dict:
    """Full pipeline: dialogue script -> multi-voice synthesis -> (audio |
    video) output file, written directly into the already-mounted static
    dir for its kind. Returns {"stream_url": ..., "duration_sec": ..., "script": [...]}.
    Does NOT publish -- caller decides surface/cover/etc via _insert_broadcast."""
    work_id = uuid.uuid4().hex[:12]
    work_dir = SCRATCH_DIR / work_id
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        turns = await generate_dialogue_script(topic)
        combined_audio = await synthesize_dialogue(turns, work_dir)

        if kind == "video":
            final = VIDEO_OUT_DIR / f"{work_id}.mp4"
            await composite_video(combined_audio, topic, final)
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
