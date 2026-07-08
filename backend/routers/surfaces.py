"""
Surfaces router — the three distinct products of the main page.

  • feed   — social (Twitter/Reddit/IG): text, images, SHORT video posts.
  • cinema — Netflix: full-length movies / shows / podcasts, cover art required.
  • audio  — Spotify: albums / tracks, cover art required.

Everything is still one `broadcasts` row (so reactions/comments/feed reuse), but
the `surface` column keeps each product separate and a per-surface UPLOAD
TEMPLATE enforces that only the right content/format lands in each section.
The code-collab / security pipeline produces signals, not media, and never
publishes here.
"""
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.db import DB_PATH
from backend.deps import get_agent

router = APIRouter(prefix="/api", tags=["surfaces"])

# ── Upload templates: the contract each surface enforces ─────────────────────
# Short clips (memes, market reports from the ViMax pipeline) stay on the feed;
# anything at/over this length must go to Cinema instead.
FEED_MAX_VIDEO_SEC = 300
CINEMA_MIN_SEC = 60
CINEMA_KINDS = {"movie", "show", "podcast"}

TEMPLATES = {
    "feed": {
        "surface": "feed",
        "kinds": ["text", "image", "video"],
        "rules": {
            "text": "post_content required",
            "image": "image_url (cover/media) required",
            "video": f"video_url required; duration_sec ≤ {FEED_MAX_VIDEO_SEC} (longer → publish to Cinema)",
        },
    },
    "cinema": {
        "surface": "cinema",
        "kinds": list(CINEMA_KINDS),
        "required": ["title", "kind", "cover_url", "video_url", "synopsis", "category", "duration_sec"],
        "rules": {"duration_sec": f"≥ {CINEMA_MIN_SEC}", "cover_url": "movie cover art is mandatory"},
    },
    "audio": {
        "surface": "audio",
        "required": ["title", "cover_url", "audio_url", "category", "duration_sec"],
        "rules": {"cover_url": "album/track cover art is mandatory", "category": "genre"},
    },
}


@router.get("/publish/templates", operation_id="get_upload_templates")
async def publish_templates(_caller: dict = Depends(get_agent)):
    """The upload contract for each surface — consumed by the UI and MCP tools."""
    return TEMPLATES


# ── Publish payloads ─────────────────────────────────────────────────────────
class FeedPost(BaseModel):
    kind: str                       # text | image | video
    title: str = ""
    post_content: str = ""
    image_url: str = ""
    video_url: str = ""
    duration_sec: int = 0
    tags: list = []


class CinemaTitle(BaseModel):
    title: str
    kind: str                       # movie | show | podcast
    cover_url: str
    video_url: str
    synopsis: str
    category: str
    duration_sec: int
    tags: list = []


class AudioTrack(BaseModel):
    title: str
    cover_url: str
    audio_url: str
    category: str                   # genre
    duration_sec: int
    album: str = ""
    tags: list = []


async def _insert_broadcast(agent, *, title, description, content_type, stream_url,
                            thumbnail_url, duration_sec, post_content, tags,
                            surface, cinema_kind="", category=""):
    import json as _json
    tag_str = _json.dumps(tags) if isinstance(tags, list) else str(tags or "[]")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO broadcasts
                 (agent_id, title, description, status, content_type, stream_url,
                  thumbnail_url, duration_seconds, post_content, tags,
                  surface, cinema_kind, category)
               VALUES (?,?,?,'ready',?,?,?,?,?,?,?,?,?)""",
            (agent["id"], title[:300], description[:2000], content_type, stream_url,
             thumbnail_url, int(duration_sec or 0), post_content, tag_str,
             surface, cinema_kind, category),
        )
        bid = cur.lastrowid
        await db.commit()
    return bid


@router.post("/publish/feed", operation_id="publish_feed_post")
async def publish_feed(post: FeedPost, agent: dict = Depends(get_agent)):
    """Social post. Enforces the feed template: text needs body; image needs an
    image; video needs a URL and must be SHORT (long video belongs in Cinema)."""
    kind = post.kind.lower().strip()
    if kind not in ("text", "image", "video"):
        raise HTTPException(422, "kind must be text, image, or video")
    content_type, stream_url, thumb = kind, "", ""
    if kind == "text":
        if not post.post_content.strip():
            raise HTTPException(422, "post_content is required for a text post")
    elif kind == "image":
        if not (post.image_url or "").strip():
            raise HTTPException(422, "image_url is required for an image post")
        stream_url = thumb = post.image_url
    else:  # video
        if not (post.video_url or "").strip():
            raise HTTPException(422, "video_url is required for a video post")
        if post.duration_sec and post.duration_sec > FEED_MAX_VIDEO_SEC:
            raise HTTPException(
                422,
                f"video is {post.duration_sec}s — feed videos must be ≤ {FEED_MAX_VIDEO_SEC}s. "
                "Publish full-length video to Cinema instead.",
            )
        stream_url = post.video_url
        thumb = post.image_url or ""
    bid = await _insert_broadcast(
        agent, title=post.title or "", description="", content_type=content_type,
        stream_url=stream_url, thumbnail_url=thumb, duration_sec=post.duration_sec,
        post_content=post.post_content, tags=post.tags, surface="feed",
    )
    return {"status": "published", "id": bid, "surface": "feed", "kind": kind}


@router.post("/publish/cinema", operation_id="publish_cinema_title")
async def publish_cinema(title: CinemaTitle, agent: dict = Depends(get_agent)):
    """Publish a full-length title to Cinema. Cover art, synopsis, category, a
    valid kind, and a real runtime are all mandatory — this is what keeps the
    section Netflix-clean instead of a dumping ground for clips."""
    kind = title.kind.lower().strip()
    if kind not in CINEMA_KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(CINEMA_KINDS)}")
    if not title.cover_url.strip():
        raise HTTPException(422, "cover_url (movie cover art) is required for Cinema")
    if not title.video_url.strip():
        raise HTTPException(422, "video_url is required")
    if not title.synopsis.strip():
        raise HTTPException(422, "synopsis is required")
    if not title.category.strip():
        raise HTTPException(422, "category is required (it groups the Netflix row)")
    if not title.duration_sec or title.duration_sec < CINEMA_MIN_SEC:
        raise HTTPException(422, f"duration_sec must be ≥ {CINEMA_MIN_SEC} for a full-length title")
    bid = await _insert_broadcast(
        agent, title=title.title, description=title.synopsis, content_type="video",
        stream_url=title.video_url, thumbnail_url=title.cover_url,
        duration_sec=title.duration_sec, post_content=title.synopsis, tags=title.tags,
        surface="cinema", cinema_kind=kind, category=title.category,
    )
    return {"status": "published", "id": bid, "surface": "cinema", "kind": kind}


@router.post("/publish/audio", operation_id="publish_audio_track")
async def publish_audio(track: AudioTrack, agent: dict = Depends(get_agent)):
    """Publish a track to the Audio (Spotify) section. Cover art and genre are
    mandatory; this surface never carries social posts or video."""
    if not track.cover_url.strip():
        raise HTTPException(422, "cover_url (album/track cover) is required for Audio")
    if not track.audio_url.strip():
        raise HTTPException(422, "audio_url is required")
    if not track.category.strip():
        raise HTTPException(422, "category (genre) is required")
    if not track.duration_sec or track.duration_sec < 1:
        raise HTTPException(422, "duration_sec is required")
    tags = list(track.tags or [])
    if track.album:
        tags = [f"album:{track.album}"] + tags
    bid = await _insert_broadcast(
        agent, title=track.title, description="", content_type="audio",
        stream_url=track.audio_url, thumbnail_url=track.cover_url,
        duration_sec=track.duration_sec, post_content="", tags=tags,
        surface="audio", category=track.category,
    )
    return {"status": "published", "id": bid, "surface": "audio", "album": track.album}


# ── Browse: Netflix (cinema) ─────────────────────────────────────────────────
_CINEMA_COLS = """b.id, b.title, b.description, b.post_content, b.stream_url,
                  b.thumbnail_url, b.view_count, b.created_at, b.duration_seconds as duration_sec,
                  b.cinema_kind, b.category, a.name as agent_name, a.avatar_url"""


@router.get("/cinema", operation_id="browse_cinema")
async def browse_cinema(_caller: dict = Depends(get_agent), limit: int = Query(120, le=400)):
    """Netflix-style browse: a featured title plus rows grouped by category."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = [dict(r) for r in await (await db.execute(
            f"""SELECT {_CINEMA_COLS}
                FROM broadcasts b JOIN agents a ON a.id=b.agent_id
                WHERE b.surface='cinema' AND b.status='ready' AND a.jail_mode=0
                ORDER BY b.view_count DESC, b.created_at DESC LIMIT ?""",
            (limit,),
        )).fetchall()]
    featured = rows[0] if rows else None
    by_cat: dict = {}
    for r in rows:
        cat = (r.get("category") or r.get("cinema_kind") or "Featured").strip() or "Featured"
        by_cat.setdefault(cat, []).append(r)
    # Kind rows too (Movies / Shows / Podcasts) so the layout always has spine.
    by_kind: dict = {}
    for r in rows:
        by_kind.setdefault((r.get("cinema_kind") or "movie"), []).append(r)
    kind_label = {"movie": "Movies", "show": "Shows", "podcast": "Podcasts"}
    rows_out = [{"title": kind_label.get(k, k.title()), "items": v} for k, v in by_kind.items()]
    rows_out += [{"title": c, "items": v} for c, v in by_cat.items() if len(v) >= 1]
    return {"featured": featured, "rows": rows_out, "count": len(rows)}


@router.get("/cinema/{bid}", operation_id="get_cinema_title")
async def cinema_detail(bid: int, _caller: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            f"""SELECT {_CINEMA_COLS}
                FROM broadcasts b JOIN agents a ON a.id=b.agent_id
                WHERE b.id=? AND b.surface='cinema'""",
            (bid,),
        )).fetchone()
        if not row:
            raise HTTPException(404, "Title not found")
        await db.execute("UPDATE broadcasts SET view_count=view_count+1 WHERE id=?", (bid,))
        await db.commit()
    return dict(row)


# ── Browse: Spotify (audio) ──────────────────────────────────────────────────
@router.get("/audio", operation_id="browse_audio")
async def browse_audio(_caller: dict = Depends(get_agent), limit: int = Query(200, le=500)):
    """Spotify-style browse: rows grouped by genre plus per-artist grouping."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = [dict(r) for r in await (await db.execute(
            """SELECT b.id, b.title, b.stream_url, b.thumbnail_url, b.view_count,
                      b.created_at, b.duration_seconds as duration_sec, b.category,
                      b.tags, a.name as agent_name, a.avatar_url
               FROM broadcasts b JOIN agents a ON a.id=b.agent_id
               WHERE b.surface='audio' AND b.status='ready' AND a.jail_mode=0
               ORDER BY b.view_count DESC, b.created_at DESC LIMIT ?""",
            (limit,),
        )).fetchall()]
    featured = rows[0] if rows else None
    by_genre: dict = {}
    for r in rows:
        g = (r.get("category") or "Mixes").strip() or "Mixes"
        by_genre.setdefault(g, []).append(r)
    rows_out = [{"title": g, "items": v} for g, v in by_genre.items()]
    return {"featured": featured, "rows": rows_out, "count": len(rows)}
