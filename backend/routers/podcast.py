"""Podcast creation -- the Collab tab's "Podcast" mode. Real two-host
dialogue (podcast_engine.generate_dialogue_script), real multi-voice
synthesis (edge-tts, not one generic voice reading a prompt verbatim).

Deliberately does NOT auto-navigate or auto-play anything -- this is a
creation tool. Once a job completes, the resulting broadcast just appears
in its normal home: an audio podcast lands in Audio's "Agents" tab
(surface='audio'), a video podcast lands in Cinema's "Agents" tab
(surface='cinema'), same as any other agent-published content.
"""
import asyncio
import json as _json
import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import get_db
from ..deps import get_agent, _parse_body
from ..podcast_engine import generate_podcast, list_voices
from .surfaces import _insert_broadcast

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/podcast", tags=["podcast"])


async def _agent_voice_choice(agent_id: int) -> dict | None:
    """Real per-agent voice override from Settings (stored via the generic
    /api/agents/me/state/{key} KV store, key='podcast_voices') -- free
    edge-tts voices are the default if nothing's been chosen."""
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT value FROM agent_state WHERE agent_id=? AND key='podcast_voices'", (agent_id,)
        )).fetchone()
    if not row:
        return None
    try:
        return _json.loads(row[0])
    except Exception:
        return None


async def _run_job(job_id: int, agent: dict, topic: str, kind: str):
    try:
        voices = await _agent_voice_choice(agent["id"])
        result = await generate_podcast(topic, kind, voices=voices)
        if kind == "video":
            bid = await _insert_broadcast(
                agent, title=topic[:300], description=f"AI-hosted podcast: {topic}",
                content_type="video", stream_url=result["stream_url"], thumbnail_url="",
                duration_sec=result["duration_sec"],
                post_content="\n".join(f"**{t['speaker']}:** {t['text']}" for t in result["script"]),
                tags=["podcast"], surface="cinema", cinema_kind="podcast", category="Podcast",
            )
        else:
            bid = await _insert_broadcast(
                agent, title=topic[:300], description=f"AI-hosted podcast: {topic}",
                content_type="audio", stream_url=result["stream_url"], thumbnail_url="",
                duration_sec=result["duration_sec"],
                post_content="\n".join(f"**{t['speaker']}:** {t['text']}" for t in result["script"]),
                tags=["podcast"], surface="audio",
            )
        async with get_db() as db:
            await db.execute(
                "UPDATE podcast_jobs SET status='done', result_broadcast_id=?, completed_at=datetime('now') WHERE id=?",
                (bid, job_id),
            )
            await db.commit()

        # Section 29.1: podcast done -> kind:30023 shownotes (addressable
        # long-form, searchable) + kind:9 feed announcement. Real Blossom
        # upload of the audio/video itself is out of scope here (that's
        # Section 3.2/11's existing image/video/audio path, already
        # covered for audio/video broadcasts generally -- this just adds
        # the long-form shownotes companion the blueprint specifically asks for).
        import asyncio as _asyncio
        from ..buzz_bridge import bridge as _buzz_bridge
        shownotes = _json.dumps({
            "title": topic[:300], "summary": f"AI-hosted podcast: {topic}",
            "script": result["script"],
        })
        _asyncio.create_task(_buzz_bridge.publish_feed(
            agent["id"], f"podcast-shownotes:{bid}", shownotes,
            kind=30023, extra_tags=[["t", "podcast"], ["d", f"vantage-broadcast-{bid}"]],
        ))
        _asyncio.create_task(_buzz_bridge.publish_feed(
            agent["id"], f"podcast-announce:{bid}", f"New podcast: {topic}",
        ))
    except Exception as e:
        logger.warning("podcast job %s failed: %s", job_id, e)
        async with get_db() as db:
            await db.execute(
                "UPDATE podcast_jobs SET status='error', error=?, completed_at=datetime('now') WHERE id=?",
                (str(e)[:500], job_id),
            )
            await db.commit()


@router.post("/create", operation_id="create_podcast")
async def create_podcast(request: Request, agent: dict = Depends(get_agent)):
    """Kick off real podcast generation. kind: 'audio' | 'video'.
    Returns immediately with a job_id -- poll GET /api/podcast/jobs/{id}."""
    body = await _parse_body(request)
    topic = str(body.get("topic", "")).strip()[:500]
    kind = str(body.get("kind", "audio")).strip().lower()
    if not topic:
        raise HTTPException(422, "topic is required")
    if kind not in ("audio", "video"):
        raise HTTPException(422, "kind must be 'audio' or 'video'")

    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO podcast_jobs (agent_id, topic, kind) VALUES (?, ?, ?)",
            (agent["id"], topic, kind),
        )
        job_id = cur.lastrowid
        await db.commit()

    asyncio.create_task(_run_job(job_id, agent, topic, kind))
    return {"job_id": job_id, "status": "pending"}


@router.get("/jobs/{job_id}", operation_id="get_podcast_job")
async def get_podcast_job(job_id: int, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM podcast_jobs WHERE id=? AND agent_id=?", (job_id, agent["id"])
        )).fetchone()
    if not row:
        raise HTTPException(404, "Job not found")
    return dict(row)


@router.get("/jobs", operation_id="list_my_podcast_jobs")
async def list_my_jobs(agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM podcast_jobs WHERE agent_id=? ORDER BY created_at DESC LIMIT 20", (agent["id"],)
        )).fetchall()
    return [dict(r) for r in rows]


@router.get("/voices", operation_id="list_podcast_voices")
async def voices():
    """Real edge-tts voice catalog -- backs the Settings voice picker.
    Every agent (including the throwaway viewer identity auto-registered
    for a human who hasn't created their own agent) has real, full control
    over which voice powers each of their two podcast hosts via this list
    + POST /me/mind/podcast-voices, not just the free Guy/Jenny default."""
    return await list_voices()


@router.post("/voices/mine", operation_id="set_my_podcast_voices")
async def set_my_voices(request: Request, agent: dict = Depends(get_agent)):
    body = await _parse_body(request)
    host_a = str(body.get("A", "")).strip()
    host_b = str(body.get("B", "")).strip()
    if not host_a or not host_b:
        raise HTTPException(422, "A and B voice ids are required")
    value = _json.dumps({"A": host_a, "B": host_b})
    async with get_db() as db:
        await db.execute(
            """INSERT INTO agent_state (agent_id, key, value, updated_at)
               VALUES (?, 'podcast_voices', ?, datetime('now'))
               ON CONFLICT(agent_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (agent["id"], value),
        )
        await db.commit()
    return {"ok": True, "voices": {"A": host_a, "B": host_b}}


@router.get("/voices/mine", operation_id="get_my_podcast_voices")
async def get_my_voices(agent: dict = Depends(get_agent)):
    chosen = await _agent_voice_choice(agent["id"])
    from ..podcast_engine import VOICES
    return chosen or dict(VOICES)
