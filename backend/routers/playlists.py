"""Playlists/queue -- cross-surface "easy pickup" storage for anything a
human or agent wants to save: Cinema titles, Audio tracks (real broadcast
rows, kind='broadcast'), and Live TV channels or any other externally-
sourced item with no broadcast row of its own (kind='external', stores
title/url/thumbnail directly).

Agent-first: every action here is a plain X-Agent-Key-authed REST endpoint,
same as everything else in Vantage -- there is no UI-only path. An agent
can create/list/add/remove/delete playlists exactly like a human can
through the frontend.
"""
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import get_db
from ..deps import get_agent, _parse_body

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


@router.post("", operation_id="create_playlist")
async def create_playlist(request: Request, agent: dict = Depends(get_agent)):
    body = await _parse_body(request)
    name = str(body.get("name", "")).strip()[:120] or "Untitled Playlist"
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO playlists (agent_id, name) VALUES (?, ?)", (agent["id"], name)
        )
        await db.commit()
    return {"id": cur.lastrowid, "name": name}


@router.get("", operation_id="list_my_playlists")
async def list_playlists(agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT p.id, p.name, p.created_at, COUNT(i.id) as item_count
               FROM playlists p LEFT JOIN playlist_items i ON i.playlist_id = p.id
               WHERE p.agent_id = ? GROUP BY p.id ORDER BY p.created_at DESC""",
            (agent["id"],),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _own_playlist(playlist_id: int, agent_id: int) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM playlists WHERE id=? AND agent_id=?", (playlist_id, agent_id)
        )).fetchone()
    if not row:
        raise HTTPException(404, "Playlist not found")
    return dict(row)


@router.get("/{playlist_id}", operation_id="get_playlist")
async def get_playlist(playlist_id: int, agent: dict = Depends(get_agent)):
    playlist = await _own_playlist(playlist_id, agent["id"])
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT i.id, i.kind, i.position, i.added_at, i.broadcast_id,
                      i.external_title, i.external_url, i.external_thumbnail, i.external_kind,
                      b.title as b_title, b.stream_url as b_stream_url, b.thumbnail_url as b_thumbnail,
                      b.content_type as b_content_type, b.surface as b_surface, b.duration_seconds as b_duration,
                      a.name as b_agent_name
               FROM playlist_items i
               LEFT JOIN broadcasts b ON b.id = i.broadcast_id
               LEFT JOIN agents a ON a.id = b.agent_id
               WHERE i.playlist_id = ? ORDER BY i.position ASC, i.added_at ASC""",
            (playlist_id,),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    items = []
    for r in rows:
        if r["kind"] == "broadcast" and r["broadcast_id"]:
            items.append({
                "id": r["id"], "kind": "broadcast", "broadcast_id": r["broadcast_id"],
                "title": r["b_title"], "url": r["b_stream_url"], "thumbnail": r["b_thumbnail"],
                "content_type": r["b_content_type"], "surface": r["b_surface"],
                "duration_sec": r["b_duration"], "agent_name": r["b_agent_name"],
                "added_at": r["added_at"],
            })
        else:
            items.append({
                "id": r["id"], "kind": "external", "external_kind": r["external_kind"],
                "title": r["external_title"], "url": r["external_url"], "thumbnail": r["external_thumbnail"],
                "added_at": r["added_at"],
            })
    return {"id": playlist["id"], "name": playlist["name"], "created_at": playlist["created_at"], "items": items}


@router.patch("/{playlist_id}", operation_id="rename_playlist")
async def rename_playlist(playlist_id: int, request: Request, agent: dict = Depends(get_agent)):
    await _own_playlist(playlist_id, agent["id"])
    body = await _parse_body(request)
    name = str(body.get("name", "")).strip()[:120]
    if not name:
        raise HTTPException(422, "name is required")
    async with get_db() as db:
        await db.execute("UPDATE playlists SET name=? WHERE id=?", (name, playlist_id))
        await db.commit()
    return {"ok": True, "name": name}


@router.delete("/{playlist_id}", operation_id="delete_playlist")
async def delete_playlist(playlist_id: int, agent: dict = Depends(get_agent)):
    await _own_playlist(playlist_id, agent["id"])
    async with get_db() as db:
        await db.execute("DELETE FROM playlist_items WHERE playlist_id=?", (playlist_id,))
        await db.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
        await db.commit()
    return {"ok": True}


@router.post("/{playlist_id}/items", operation_id="add_playlist_item")
async def add_item(playlist_id: int, request: Request, agent: dict = Depends(get_agent)):
    """Add either a real broadcast (broadcast_id) -- a Cinema title or Audio
    track -- or an external item (external_title/external_url/
    external_thumbnail/external_kind, e.g. a Live TV channel)."""
    await _own_playlist(playlist_id, agent["id"])
    body = await _parse_body(request)
    broadcast_id: Optional[int] = body.get("broadcast_id")

    async with get_db() as db:
        async with db.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM playlist_items WHERE playlist_id=?", (playlist_id,)
        ) as cur:
            next_pos = (await cur.fetchone())[0]

        if broadcast_id:
            cur = await db.execute(
                """INSERT INTO playlist_items (playlist_id, kind, broadcast_id, position)
                   VALUES (?, 'broadcast', ?, ?)""",
                (playlist_id, broadcast_id, next_pos),
            )
        else:
            title = str(body.get("external_title", "")).strip()[:300]
            url = str(body.get("external_url", "")).strip()
            if not title or not url:
                raise HTTPException(422, "external_title and external_url are required when broadcast_id is absent")
            thumbnail = str(body.get("external_thumbnail", "")).strip()
            external_kind = str(body.get("external_kind", "channel")).strip()[:40]
            cur = await db.execute(
                """INSERT INTO playlist_items
                   (playlist_id, kind, external_title, external_url, external_thumbnail, external_kind, position)
                   VALUES (?, 'external', ?, ?, ?, ?, ?)""",
                (playlist_id, title, url, thumbnail, external_kind, next_pos),
            )
        await db.commit()
    return {"ok": True, "item_id": cur.lastrowid}


@router.delete("/{playlist_id}/items/{item_id}", operation_id="remove_playlist_item")
async def remove_item(playlist_id: int, item_id: int, agent: dict = Depends(get_agent)):
    await _own_playlist(playlist_id, agent["id"])
    async with get_db() as db:
        await db.execute(
            "DELETE FROM playlist_items WHERE id=? AND playlist_id=?", (item_id, playlist_id)
        )
        await db.commit()
    return {"ok": True}
