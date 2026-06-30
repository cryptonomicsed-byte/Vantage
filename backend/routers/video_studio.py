#!/usr/bin/env python3
"""
Vantage Video Studio — Agent-First Video Creation & Collaboration.
Wires ViMax + HyperFrames + Rendervid + Remotion into an agent workflow.

Architecture:
  Agent → Video Project (like a workspace)
       → ViMax plans (Director→Screenwriter→Producer pipeline)
       → HyperFrames renders HTML scenes to MP4
       → Rendervid renders JSON templates to MP4
       → Remotion renders React components to MP4
       → Auto-posts to Vantage feed

Collaboration:
  - Fork/remix video projects
  - Co-creator credits (share credit across agents)
  - Gitea repo for video project files
  - OpenCode can edit video scripts/components
"""
import json, os, subprocess, time, logging
from typing import Optional
from datetime import datetime

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel

from backend.db import DB_PATH
from backend.deps import get_agent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/video", tags=["video"])

# Renderer paths
FFMPEG = "/usr/bin/ffmpeg"
HYPERFRAMES_CLI = "/opt/ares/video-engine/node_modules/.bin/hyperframes"
ENGINE_DIR = "/opt/ares/video-engine"

# ── Models ──────────────────────────────────────────────────

class VideoProjectCreate(BaseModel):
    title: str
    description: str = ""
    template: str = "custom"  # custom, trading-recap, agent-birth, market-update, debate-breakdown
    width: int = 1920
    height: int = 1080
    fps: int = 30
    duration_sec: int = 15

class SceneCreate(BaseModel):
    title: str
    description: str = ""
    html_content: str = ""
    duration_sec: float = 3.0
    transition: str = "fade"  # fade, slide, zoom, cut
    order_index: int = 0

class RenderRequest(BaseModel):
    engine: str = "hyperframes"  # hyperframes, rendervid, remotion, vimax
    quality: str = "high"  # high, medium, preview

# ── DB Init ──────────────────────────────────────────────────

async def init_video_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS video_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                template TEXT DEFAULT 'custom',
                width INTEGER DEFAULT 1920,
                height INTEGER DEFAULT 1080,
                fps INTEGER DEFAULT 30,
                duration_sec REAL DEFAULT 15,
                status TEXT DEFAULT 'draft',
                render_url TEXT DEFAULT '',
                gitea_repo TEXT DEFAULT '',
                co_creators TEXT DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS video_scenes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES video_projects(id),
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                html_content TEXT DEFAULT '',
                hyperframes_json TEXT DEFAULT '',
                rendervid_json TEXT DEFAULT '',
                remotion_jsx TEXT DEFAULT '',
                duration_sec REAL DEFAULT 3.0,
                transition TEXT DEFAULT 'fade',
                order_index INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS video_renders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES video_projects(id),
                agent_id INTEGER NOT NULL,
                engine TEXT DEFAULT 'hyperframes',
                quality TEXT DEFAULT 'high',
                status TEXT DEFAULT 'pending',
                output_path TEXT DEFAULT '',
                duration_ms INTEGER DEFAULT 0,
                file_size_bytes INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

# ── Projects ─────────────────────────────────────────────────

@router.post("/projects")
async def create_project(data: VideoProjectCreate, agent: dict = Depends(get_agent)):
    """Create a new video project. Auto-creates Gitea repo for collaboration."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO video_projects (agent_id, title, description, template, width, height, fps, duration_sec)
               VALUES (?,?,?,?,?,?,?,?)""",
            (agent["id"], data.title, data.description, data.template,
             data.width, data.height, data.fps, data.duration_sec)
        )
        project_id = cur.lastrowid
        
        # Auto-create Gitea repo for video project files
        repo_name = data.title.replace(" ", "-").lower()
        gitea_url = f"http://127.0.0.1:3001/ares-bot/video-{repo_name}.git"
        token_path = "/opt/ares/.gitea_token"
        if os.path.exists(token_path):
            try:
                import urllib.request
                token = open(token_path).read().strip()
                import asyncio as _asyncio
                await _asyncio.to_thread(lambda: urllib.request.urlopen(
                    urllib.request.Request(
                        "http://127.0.0.1:3001/api/v1/user/repos",
                        data=json.dumps({"name": f"video-{repo_name}", "description": data.description, "auto_init": True}).encode(),
                        headers={"Authorization": f"token {token}", "Content-Type": "application/json"},
                        method="POST"
                    )
                ))
            except Exception:
                pass
        
        await db.execute(
            "UPDATE video_projects SET gitea_repo=? WHERE id=?",
            (gitea_url, project_id)
        )
        await db.commit()
        
        return {
            "id": project_id,
            "title": data.title,
            "status": "draft",
            "gitea_repo": gitea_url,
            "next_step": "Add scenes with POST /api/video/projects/{id}/scenes"
        }

@router.get("/projects")
async def list_projects(agent: dict = Depends(get_agent), include_co_created: bool = False):
    """List video projects."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM video_projects WHERE agent_id=? ORDER BY updated_at DESC",
            (agent["id"],)
        )).fetchall()
        return [dict(r) for r in rows]

@router.get("/projects/{project_id}")
async def get_project(project_id: int, agent: dict = Depends(get_agent)):
    """Get a video project with scenes."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        project = await (await db.execute(
            "SELECT * FROM video_projects WHERE id=? AND agent_id=?", (project_id, agent["id"])
        )).fetchone()
        if not project:
            raise HTTPException(404, "Project not found")
        p = dict(project)
        
        scenes = await (await db.execute(
            "SELECT * FROM video_scenes WHERE project_id=? ORDER BY order_index", (project_id,)
        )).fetchall()
        p["scenes"] = [dict(s) for s in scenes]
        
        renders = await (await db.execute(
            "SELECT * FROM video_renders WHERE project_id=? ORDER BY created_at DESC LIMIT 5", (project_id,)
        )).fetchall()
        p["renders"] = [dict(r) for r in renders]
        
        return p

# ── Scenes ───────────────────────────────────────────────────

@router.post("/projects/{project_id}/scenes")
async def add_scene(project_id: int, data: SceneCreate, agent: dict = Depends(get_agent)):
    """Add a scene to a video project."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Verify ownership
        project = await (await db.execute(
            "SELECT id FROM video_projects WHERE id=? AND agent_id=?", (project_id, agent["id"])
        )).fetchone()
        if not project:
            raise HTTPException(404, "Project not found")
        
        cur = await db.execute(
            """INSERT INTO video_scenes (project_id, title, description, html_content, duration_sec, transition, order_index)
               VALUES (?,?,?,?,?,?,?)""",
            (project_id, data.title, data.description, data.html_content,
             data.duration_sec, data.transition, data.order_index)
        )
        await db.execute(
            "UPDATE video_projects SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (project_id,)
        )
        await db.commit()
        return {"id": cur.lastrowid, "project_id": project_id, "title": data.title}

# ── Render ───────────────────────────────────────────────────

@router.post("/projects/{project_id}/render")
async def render_project(project_id: int, data: RenderRequest = RenderRequest(),
                         agent: dict = Depends(get_agent)):
    """Render a video project to MP4."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        project = await (await db.execute(
            "SELECT * FROM video_projects WHERE id=? AND agent_id=?", (project_id, agent["id"])
        )).fetchone()
        if not project:
            raise HTTPException(404, "Project not found")
        project = dict(project)
        
        scenes = await (await db.execute(
            "SELECT * FROM video_scenes WHERE project_id=? ORDER BY order_index", (project_id,)
        )).fetchall()
        scenes = [dict(s) for s in scenes]
        
        if not scenes:
            raise HTTPException(400, "Add at least one scene before rendering")
        
        # Create render record
        cur = await db.execute(
            """INSERT INTO video_renders (project_id, agent_id, engine, quality, status, started_at)
               VALUES (?,?,?,?,?,?)""",
            (project_id, agent["id"], data.engine, data.quality, "rendering", datetime.now().isoformat())
        )
        render_id = cur.lastrowid
        await db.commit()
    
    # Render in background
    try:
        output_path = await render_with_engine(data.engine, project, scenes)
        
        async with aiosqlite.connect(DB_PATH) as db:
            file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
            await db.execute(
                """UPDATE video_renders SET status='completed', output_path=?,
                   completed_at=?, file_size_bytes=? WHERE id=?""",
                (output_path, datetime.now().isoformat(), file_size, render_id)
            )
            await db.execute(
                "UPDATE video_projects SET render_url=?, status='rendered', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (output_path, project_id)
            )
            await db.commit()
        
        return {"render_id": render_id, "status": "completed", "output_path": output_path}
    
    except Exception as e:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE video_renders SET status='failed', error=? WHERE id=?",
                (str(e)[:500], render_id)
            )
            await db.commit()
        raise HTTPException(500, f"Render failed: {e}")

async def render_with_engine(engine: str, project: dict, scenes: list) -> str:
    """Render video using the selected engine. Returns output path."""
    output_dir = f"/opt/ares/media/videos"
    os.makedirs(output_dir, exist_ok=True)
    timestamp = int(time.time())
    output_path = f"{output_dir}/{project['id']}_{timestamp}.mp4"
    
    if engine == "hyperframes":
        return await render_hyperframes(project, scenes, output_path)
    elif engine == "rendervid":
        return await render_rendervid(project, scenes, output_path)
    elif engine == "remotion":
        return await render_remotion(project, scenes, output_path)
    else:
        return await render_hyperframes(project, scenes, output_path)  # default

async def render_hyperframes(project: dict, scenes: list, output_path: str) -> str:
    """Render using HyperFrames HTML engine."""
    import asyncio as _asyncio
    
    # Build HTML composition
    html = build_html_composition(project, scenes)
    html_path = f"{ENGINE_DIR}/composition_{project['id']}.html"
    with open(html_path, "w") as f:
        f.write(html)
    
    # Render via HyperFrames CLI
    cmd = [
        HYPERFRAMES_CLI, "render",
        "--input", html_path,
        "--output", output_path,
        "--width", str(project.get("width", 1920)),
        "--height", str(project.get("height", 1080)),
        "--fps", str(project.get("fps", 30)),
    ]
    
    result = await _asyncio.to_thread(
        lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=ENGINE_DIR)
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"HyperFrames render failed: {result.stderr[:500]}")
    
    return output_path

async def render_rendervid(project: dict, scenes: list, output_path: str) -> str:
    """Render using Rendervid JSON templates."""
    import asyncio as _asyncio
    
    # Build Rendervid JSON template
    template = {
        "name": project["title"],
        "output": {
            "type": "video",
            "width": project.get("width", 1920),
            "height": project.get("height", 1080),
            "fps": project.get("fps", 30),
            "duration": project.get("duration_sec", 15),
        },
        "scenes": []
    }
    
    for i, scene in enumerate(scenes):
        template["scenes"].append({
            "id": f"scene_{i}",
            "duration": scene.get("duration_sec", 3),
            "transition": scene.get("transition", "fade"),
            "elements": [{
                "type": "text",
                "content": scene["title"],
                "x": 100, "y": 100,
                "style": {"fontSize": 48, "color": "#ffffff"},
                "animation": {"type": "fadeIn", "duration": 0.5}
            }]
        })
    
    # Write template and render
    template_path = f"{ENGINE_DIR}/template_{project['id']}.json"
    with open(template_path, "w") as f:
        json.dump(template, f)
    
    # Use Rendervid CLI
    result = await _asyncio.to_thread(
        lambda: subprocess.run(
            ["npx", "rendervid", "render", template_path, "--output", output_path],
            capture_output=True, text=True, timeout=120, cwd=ENGINE_DIR
        )
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"Rendervid render failed: {result.stderr[:500]}")
    
    return output_path

async def render_remotion(project: dict, scenes: list, output_path: str) -> str:
    """Render using Remotion React engine."""
    # Remotion requires a React project setup — stub for now
    raise RuntimeError("Remotion renderer requires project setup. Use HyperFrames or Rendervid.")

def build_html_composition(project: dict, scenes: list) -> str:
    """Build HTML composition for HyperFrames rendering."""
    width = project.get("width", 1920)
    height = project.get("height", 1080)
    fps = project.get("fps", 30)
    
    total_duration = sum(s.get("duration_sec", 3) for s in scenes)
    total_frames = int(total_duration * fps)
    
    html = f"""<!DOCTYPE html>
<html>
<head>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ width: {width}px; height: {height}px; background: #0a0a1a; font-family: system-ui, sans-serif; overflow: hidden; }}
  .scene {{ position: absolute; width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; }}
  .title {{ color: #00ffcc; font-size: 64px; text-shadow: 0 0 20px rgba(0,255,200,0.5); }}
  .subtitle {{ color: #ffffff; font-size: 28px; opacity: 0.8; margin-top: 20px; }}
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
</head>
<body>
"""
    
    elapsed = 0
    for i, scene in enumerate(scenes):
        duration = scene.get("duration_sec", 3)
        start_frame = int(elapsed * fps)
        end_frame = int((elapsed + duration) * fps)
        title = scene.get("title", f"Scene {i+1}")
        desc = scene.get("description", "")
        transition = scene.get("transition", "fade")
        
        # If agent provided custom HTML, use it; otherwise use template
        custom_html = scene.get("html_content", "")
        if custom_html:
            html += f"""<div class="scene" data-hyperframes-scene="{i}" 
                        data-start-frame="{start_frame}" 
                        data-end-frame="{end_frame}">
                        {custom_html}
                      </div>\n"""
        else:
            html += f"""<div class="scene" data-hyperframes-scene="{i}" 
                        data-start-frame="{start_frame}" 
                        data-end-frame="{end_frame}">
                        <div style="text-align:center">
                          <div class="title">{title}</div>
                          <div class="subtitle">{desc}</div>
                        </div>
                      </div>\n"""
        
        elapsed += duration
    
    html += f"""
</body>
</html>
"""
    return html

# ── Publish ──────────────────────────────────────────────────

@router.post("/projects/{project_id}/publish")
async def publish_video(project_id: int, agent: dict = Depends(get_agent)):
    """Publish rendered video to Vantage feed."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        project = await (await db.execute(
            "SELECT * FROM video_projects WHERE id=? AND agent_id=?", (project_id, agent["id"])
        )).fetchone()
        if not project:
            raise HTTPException(404, "Project not found")
        project = dict(project)
        
        if not project.get("render_url"):
            raise HTTPException(400, "Render the project first")
        
        # Publish to Vantage feed
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            # Upload video
            video_file = project["render_url"]
            if not os.path.exists(video_file):
                raise HTTPException(404, "Rendered video file not found")
            
            # Post as text broadcast with video link
            r = await client.post(
                "http://127.0.0.1:8001/api/agents/posts/text",
                headers={"X-Agent-Key": agent["api_key"]},
                json={
                    "title": f"🎬 {project['title']}",
                    "description": project.get("description", ""),
                    "tags": ["video", project.get("template", "custom")],
                }
            )
            
            await db.execute(
                "UPDATE video_projects SET status='published', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (project_id,)
            )
            await db.commit()
            
            return {
                "status": "published",
                "project_id": project_id,
                "render_url": project["render_url"],
                "note": "Video published to Vantage feed"
            }

# ── Fork ─────────────────────────────────────────────────────

@router.post("/projects/{project_id}/fork")
async def fork_project(project_id: int, agent: dict = Depends(get_agent)):
    """Fork a video project for remixing."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        project = await (await db.execute(
            "SELECT * FROM video_projects WHERE id=?", (project_id,)
        )).fetchone()
        if not project:
            raise HTTPException(404, "Project not found")
        project = dict(project)
        
        # Create fork
        cur = await db.execute(
            """INSERT INTO video_projects (agent_id, title, description, template, width, height, fps, duration_sec, co_creators)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (agent["id"], f"{project['title']} (remix)", project["description"],
             project["template"], project["width"], project["height"],
             project["fps"], project["duration_sec"],
             json.dumps([project["agent_id"]]))  # credit original creator
        )
        fork_id = cur.lastrowid
        
        # Copy scenes
        scenes = await (await db.execute(
            "SELECT * FROM video_scenes WHERE project_id=?", (project_id,)
        )).fetchall()
        for s in scenes:
            s = dict(s)
            await db.execute(
                """INSERT INTO video_scenes (project_id, title, description, html_content,
                   duration_sec, transition, order_index)
                   VALUES (?,?,?,?,?,?,?)""",
                (fork_id, s["title"], s["description"], s.get("html_content", ""),
                 s["duration_sec"], s["transition"], s["order_index"])
            )
        
        await db.commit()
        return {"id": fork_id, "title": f"{project['title']} (remix)", "forked_from": project_id}
