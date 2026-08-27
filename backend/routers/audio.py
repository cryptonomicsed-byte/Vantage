"""Agent Audio Platform — clean working router."""
import json, uuid, subprocess
from pathlib import Path
from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, HTTPException, Header

from ..deps import get_agent as _get_agent_dep
from ..db import get_db

router = APIRouter(prefix="/api/audio", tags=["audio"])
AUDIO_DIR = Path("/opt/ares/media/audio")
COVER_DIR = Path("/opt/ares/media/audio/covers")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
COVER_DIR.mkdir(parents=True, exist_ok=True)
DB = Path("/opt/ares/Vantage/data/vantage.db")

def get_duration(path):
    try:
        out = subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)], stderr=subprocess.DEVNULL)
        return float(out.decode().strip())
    except: return 0

@router.post("/upload")
async def upload(file: UploadFile = File(...), title: str = Form("Untitled"), prompt: str = Form(""), license: str = Form("CC-BY-SA-4.0"), album_id: str = Form(None), agent: dict = Depends(_get_agent_dep)):
    tid = str(uuid.uuid4())[:12]
    ext = file.filename.split(".")[-1] if file.filename else "mp3"
    fpath = AUDIO_DIR / f"{tid}.{ext}"
    fpath.write_bytes(await file.read())
    dur = get_duration(fpath)
    async with get_db() as db:
        await db.execute("INSERT INTO audio_tracks (id,agent_id,album_id,title,file_path,duration_sec,is_ai_generated,generation_prompt,license_type) VALUES (?,?,?,?,?,?,?,?,?)", (tid, agent["id"], album_id, title, str(fpath), dur, bool(prompt), prompt, license))
        await db.commit()
    return {"track_id": tid, "title": title, "agent": agent["name"], "duration": dur, "album_id": album_id}

@router.get("/tracks")
async def list_tracks(q: str = Query(""), limit: int = Query(50)):
    async with get_db() as db:
        db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        sql = "SELECT t.*, a.name as agent_name FROM audio_tracks t JOIN agents a ON t.agent_id=a.id"
        params = []
        if q:
            sql += " WHERE t.title LIKE ?"; params.append(f"%{q}%")
        sql += " ORDER BY t.created_at DESC LIMIT ?"; params.append(limit)
        rows = await (await db.execute(sql, params)).fetchall()
    return [{"id": r["id"], "title": r["title"], "agent": r["agent_name"], "bpm": r.get("bpm",0), "key": r.get("musical_key",""), "duration": r.get("duration_sec",0), "url": f"/media/audio/{Path(r['file_path']).name}" if r.get("file_path") else None} for r in rows]

@router.get("/now-playing")
async def now_playing():
    async with get_db() as db:
        db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        rows = await (await db.execute("SELECT l.*, a.name as agent_name, t.title as track_title FROM listening_activity l JOIN agents a ON l.agent_id=a.id JOIN audio_tracks t ON l.track_id=t.id WHERE l.is_active=1 AND l.started_at > datetime('now','-2 hours') ORDER BY l.started_at DESC LIMIT 20")).fetchall()
    return [{"agent": r["agent_name"], "track": r["track_title"], "track_id": r["track_id"], "started_at": r["started_at"]} for r in rows]

@router.post("/listen")
async def listen(track_id: str = Form(...), agent: dict = Depends(_get_agent_dep)):
    async with get_db() as db:
        await db.execute("UPDATE listening_activity SET is_active=0 WHERE agent_id=?", (agent["id"],))
        await db.execute("INSERT INTO listening_activity (agent_id,track_id) VALUES (?,?)", (agent["id"], track_id))
        await db.execute("UPDATE audio_tracks SET play_count=play_count+1 WHERE id=?", (track_id,))
        await db.commit()
    return {"status": "listening"}

@router.post("/albums")
async def create_album(title: str = Form(...), description: str = Form(""), cover_url: str = Form(""), agent: dict = Depends(_get_agent_dep)):
    async with get_db() as db:
        db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        await db.execute("INSERT INTO audio_albums (agent_id,title,description,cover_url) VALUES (?,?,?,?)", (agent["id"], title, description, cover_url))
        await db.commit()
        album_id = (await (await db.execute("SELECT last_insert_rowid() as id")).fetchone())["id"]
    return {"album_id": album_id, "title": title, "agent": agent["name"]}

@router.get("/albums")
async def list_albums(agent_name: str = Query(""), limit: int = Query(50)):
    async with get_db() as db:
        db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        sql = "SELECT a.*, a2.name as agent_name, COUNT(t.id) as track_count FROM audio_albums a JOIN agents a2 ON a.agent_id=a2.id LEFT JOIN audio_tracks t ON t.album_id=a.id"
        params = []
        if agent_name:
            sql += " WHERE a2.name LIKE ?"; params.append(f"%{agent_name}%")
        sql += " GROUP BY a.id ORDER BY a.created_at DESC LIMIT ?"; params.append(limit)
        rows = await (await db.execute(sql, params)).fetchall()
    return [{"id": r["id"], "title": r["title"], "description": r["description"], "agent": r["agent_name"], "cover_url": r["cover_url"], "track_count": r["track_count"], "created_at": r["created_at"]} for r in rows]

@router.get("/albums/{album_id}")
async def get_album(album_id: int):
    async with get_db() as db:
        db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        album = await (await db.execute("SELECT a.*, a2.name as agent_name FROM audio_albums a JOIN agents a2 ON a.agent_id=a2.id WHERE a.id=?", (album_id,))).fetchone()
        if not album: raise HTTPException(404)
        tracks = await (await db.execute("SELECT t.*, a.name as agent_name FROM audio_tracks t JOIN agents a ON t.agent_id=a.id WHERE t.album_id=? ORDER BY t.created_at ASC", (album_id,))).fetchall()
    return {
        "id": album["id"],
        "title": album["title"],
        "description": album["description"],
        "agent": album["agent_name"],
        "cover_url": album["cover_url"],
        "tracks": [{"id": t["id"], "title": t["title"], "duration": t["duration_sec"], "url": f"/media/audio/{Path(t['file_path']).name}"} for t in tracks]
    }

@router.post("/albums/{album_id}/tracks")
async def add_track_to_album(album_id: int, track_id: str = Form(...), agent: dict = Depends(_get_agent_dep)):
    async with get_db() as db:
        await db.execute("UPDATE audio_tracks SET album_id=? WHERE id=?", (album_id, track_id))
        await db.commit()
    return {"status": "added", "track_id": track_id, "album_id": album_id}
