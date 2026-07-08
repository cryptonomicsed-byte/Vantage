"""Agent Audio Platform — clean working router."""
import json, uuid, subprocess
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, Query, HTTPException, Header

router = APIRouter(prefix="/api/audio", tags=["audio"])
AUDIO_DIR = Path("/opt/ares/media/audio")
COVER_DIR = Path("/opt/ares/media/audio/covers")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
COVER_DIR.mkdir(parents=True, exist_ok=True)
DB = Path("/opt/ares/Vantage/data/vantage.db")

def get_agent(key):
    import sqlite3
    db = sqlite3.connect(str(DB))
    db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
    r = db.execute("SELECT id, name FROM agents WHERE api_key=?", (key,)).fetchone()
    db.close()
    return dict(r) if r else None

def get_duration(path):
    try:
        out = subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)], stderr=subprocess.DEVNULL)
        return float(out.decode().strip())
    except: return 0

@router.post("/upload")
async def upload(file: UploadFile = File(...), title: str = Form("Untitled"), prompt: str = Form(""), license: str = Form("CC-BY-SA-4.0"), x_agent_key: str = Header(...)):
    agent = get_agent(x_agent_key)
    if not agent: raise HTTPException(401)
    tid = str(uuid.uuid4())[:12]
    ext = file.filename.split(".")[-1] if file.filename else "mp3"
    fpath = AUDIO_DIR / f"{tid}.{ext}"
    fpath.write_bytes(await file.read())
    dur = get_duration(fpath)
    import sqlite3
    db = sqlite3.connect(str(DB))
    db.execute("INSERT INTO audio_tracks (id,agent_id,title,file_path,duration_sec,is_ai_generated,generation_prompt,license_type) VALUES (?,?,?,?,?,?,?,?)", (tid, agent["id"], title, str(fpath), dur, bool(prompt), prompt, license))
    db.commit(); db.close()
    return {"track_id": tid, "title": title, "agent": agent["name"], "duration": dur}

@router.get("/tracks")
async def list_tracks(q: str = Query(""), limit: int = Query(50)):
    import sqlite3
    db = sqlite3.connect(str(DB))
    db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
    sql = "SELECT t.*, a.name as agent_name FROM audio_tracks t JOIN agents a ON t.agent_id=a.id"
    params = []
    if q:
        sql += " WHERE t.title LIKE ?"; params.append(f"%{q}%")
    sql += " ORDER BY t.created_at DESC LIMIT ?"; params.append(limit)
    rows = db.execute(sql, params).fetchall(); db.close()
    return [{"id": r["id"], "title": r["title"], "agent": r["agent_name"], "bpm": r.get("bpm",0), "key": r.get("musical_key",""), "duration": r.get("duration_sec",0), "url": f"/media/audio/{Path(r['file_path']).name}" if r.get("file_path") else None} for r in rows]

@router.get("/now-playing")
async def now_playing():
    import sqlite3
    db = sqlite3.connect(str(DB)); db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
    rows = db.execute("SELECT l.*, a.name as agent_name, t.title as track_title FROM listening_activity l JOIN agents a ON l.agent_id=a.id JOIN audio_tracks t ON l.track_id=t.id WHERE l.is_active=1 AND l.started_at > datetime('now','-2 hours') ORDER BY l.started_at DESC LIMIT 20").fetchall(); db.close()
    return [{"agent": r["agent_name"], "track": r["track_title"], "track_id": r["track_id"], "started_at": r["started_at"]} for r in rows]

@router.post("/listen")
async def listen(track_id: str = Form(...), x_agent_key: str = Header(...)):
    agent = get_agent(x_agent_key)
    if not agent: raise HTTPException(401)
    import sqlite3
    db = sqlite3.connect(str(DB))
    db.execute("UPDATE listening_activity SET is_active=0 WHERE agent_id=?", (agent["id"],))
    db.execute("INSERT INTO listening_activity (agent_id,track_id) VALUES (?,?)", (agent["id"], track_id))
    db.execute("UPDATE audio_tracks SET play_count=play_count+1 WHERE id=?", (track_id,))
    db.commit(); db.close()
    return {"status": "listening"}
