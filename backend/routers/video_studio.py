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
from backend.thumbnails import generate_thumbnail, get_default_thumbnail

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
        

        await db.commit()
        
        return {
            "id": project_id,
            "title": data.title,
            "status": "draft",
            "gitea_repo": "",
            "next_step": "POST /api/video/projects/{id}/scenes then POST /api/video/projects/{id}/render"
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
            # Auto-generate thumbnail from rendered video
            thumb_url = None
            try:
                thumb_url = generate_thumbnail(output_path, project_id, project.get("template", "custom"))
            except Exception:
                pass
            await db.execute(
                """UPDATE video_renders SET status='completed', output_path=?,
                   completed_at=?, file_size_bytes=? WHERE id=?""",
                (output_path, datetime.now().isoformat(), file_size, render_id)
            )
            await db.execute(
                "UPDATE video_projects SET render_url=?, status='rendered', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (output_path, project_id)
            )
            # Save thumbnail
            if thumb_url:
                await db.execute(
                    "UPDATE video_projects SET thumbnail_url=? WHERE id=?",
                    (thumb_url, project_id)
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
    elif engine == "vimax":
        return await render_vimax(project, scenes, output_path)
    else:
        return await render_hyperframes(project, scenes, output_path)  # default

async def render_hyperframes(project: dict, scenes: list, output_path: str) -> str:
    """Render using HyperFrames HTML engine."""
    import asyncio as _asyncio
    
    # Build HTML composition
    html = build_html_composition(project, scenes)
    project_dir = f"{ENGINE_DIR}/projects/{project['id']}"
    os.makedirs(project_dir, exist_ok=True)
    html_path = f"{project_dir}/index.html"
    with open(html_path, "w") as f:
        f.write(html)
    
    # Render via HyperFrames CLI
    cmd = [
        HYPERFRAMES_CLI, "render",
        "--input", project_dir,
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


async def render_vimax(project: dict, scenes: list, output_path: str) -> str:
    """ViMax: Director -> Screenwriter -> Producer -> HyperFrames render.
    Uses the agent's configured LLM (encrypted at rest)."""
    from backend.crypto_utils import decrypt_key_for_agent
    import urllib.request as _urlreq

    # Load agent with LLM config
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        agent_row = await (await db.execute(
            "SELECT * FROM agents WHERE id=?", (project["agent_id"],)
        )).fetchone()
    agent = dict(agent_row)

    if not agent.get("llm_api_key_encrypted"):
        raise RuntimeError("No LLM key. PATCH /api/agents/me/llm first.")

    try:
        llm_key = decrypt_key_for_agent(agent["llm_api_key_encrypted"], agent)
        provider = agent.get("llm_provider", "deepseek")
        model = agent.get("llm_model", "deepseek-v4-flash")
    except Exception:
        raise RuntimeError("Failed to decrypt LLM key. Re-configure.")

    # Build director prompt
    title = project.get("title", "Untitled")
    desc = project.get("description", "")
    scene_names = [s.get("description", s.get("title", "")) for s in scenes]
    total_dur = project.get("duration_sec", 15)

    prompt = f"""Direct a video: Title={title}. Description={desc}. Content={scene_names}. Total={total_dur}s.
Return JSON array of scenes: [{{"title":"...","visual_direction":"...","duration_sec":3,"transition":"fade"}}].
Use dark background, glowing cyan text (#00ffcc), cinematic pacing."""

    base_urls = {"deepseek": "https://api.deepseek.com/v1"}
    base = base_urls.get(provider, f"https://api.{provider}.com/v1")

    req = _urlreq.Request(
        f"{base}/chat/completions",
        data=json.dumps({"model": model, "messages": [{"role":"user","content":prompt}], "temperature":0.7}).encode(),
        headers={"Authorization": f"Bearer {llm_key}", "Content-Type": "application/json"},
        method="POST"
    )

    try:
        resp = json.loads(_urlreq.urlopen(req, timeout=30).read())
        raw = resp["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"ViMax LLM failed: {e}")

    # Parse JSON from LLM response
    raw = raw.strip()
    for delim in ("```json", "```"):
        if delim in raw:
            raw = raw.split(delim)[1].split("```")[0]
            break
    plan = json.loads(raw)

    # Handle both array and object response formats
    if isinstance(plan, list):
        scene_list = plan
    elif isinstance(plan, dict):
        scene_list = plan.get("scenes", [plan])
    else:
        scene_list = []

    vimax_scenes = []
    for s in scene_list:
        if isinstance(s, dict):
            vimax_scenes.append({
                "title": s.get("title", "Scene"),
                "description": s.get("visual_direction", s.get("description", "")),
                "html_content": s.get("html", ""),
                "duration_sec": s.get("duration_sec", 3),
                "transition": s.get("transition", "fade"),
            })

    if vimax_scenes:
        return await render_hyperframes(project, vimax_scenes, output_path)
    return await render_hyperframes(project, scenes, output_path)



async def render_remotion(project: dict, scenes: list, output_path: str) -> str:
    """Render using Remotion React template engine."""
    import asyncio as _asyncio
    import subprocess as _sp

    remotion_dir = "/opt/ares/video-engine/remotion-templates"
    template = project.get("template", "text-scenes")
    template_to_composition = {
        "trading-recap": "trading-recap",
        "agent-birth": "agent-birth",
        "market-update": "market-update",
        "custom": "text-scenes",
    }
    composition_id = template_to_composition.get(template, "text-scenes")

    # Build props from scenes
    props = {"scenes": []}
    if template == "trading-recap":
        props = {
            "title": project.get("title", "Trading Recap"),
            "symbol": "SOL",
            "pnl": "+12.4%",
            "trades": [{"symbol": s.get("title","?"),"side":"BUY","pnl":10.5} for s in scenes],
        }
    elif template == "agent-birth":
        props = {
            "agentName": project.get("title", "New Agent"),
            "archetype": "Trader",
            "odu": "183",
        }
    elif template == "market-update":
        symbols = [s.get("title","?") for s in scenes]
        props = {
            "title": project.get("title", "Market Update"),
            "highlights": [{"symbol": s, "change": "+5.2%"} for s in symbols],
        }
    else:
        props = {
            "scenes": [{"title": s.get("title","?"), "description": s.get("description",""), "duration": int(s.get("duration_sec",3)*30)} for s in scenes],
        }

    # Write props file
    props_path = f"{remotion_dir}/props.json"
    with open(props_path, "w") as f:
        json.dump(props, f)

    # Render with Remotion CLI
    cmd = [
        "npx", "remotion", "render",
        "index.tsx",
        "--composition-id", composition_id,
        "--props", props_path,
        "--output", output_path,
        "--codec", "h264",
    ]
    result = await _asyncio.to_thread(
        lambda: _sp.run(cmd, capture_output=True, text=True, timeout=120, cwd=remotion_dir, env={**os.environ, "NODE_PATH": remotion_dir + "/node_modules"})
    )

    if result.returncode != 0:
        raise RuntimeError(f"Remotion render failed: {result.stderr[:500]}")

    return output_path


def build_html_composition(project: dict, scenes: list) -> str:
    """Build HTML composition for HyperFrames rendering."""
    width = project.get("width", 1920)
    height = project.get("height", 1080)
    fps = project.get("fps", 30)
    
    total_duration = sum(s.get("duration_sec", 3) for s in scenes)
    
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ width: {width}px; height: {height}px; background: #0a0a1a; font-family: system-ui, sans-serif; overflow: hidden; }}
  .scene {{ position: absolute; width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; opacity: 0; }}
  .scene.active {{ opacity: 1; }}
  .title {{ color: #00ffcc; font-size: 64px; font-weight: 700; }}
  .subtitle {{ color: #aabbcc; font-size: 28px; margin-top: 16px; }}
  .tag {{ display: inline-block; background: rgba(0,255,200,0.15); color: #00ffcc; padding: 8px 20px; border-radius: 24px; font-size: 20px; margin-top: 24px; }}
</style>
</head>
<body>
<div id="composition" data-composition-id="main" data-width="{width}" data-height="{height}" data-duration="{total_duration}">
"""
    
    for i, scene in enumerate(scenes):
        duration = scene.get("duration_sec", 3)
        title = scene.get("title", f"Scene {i+1}")
        desc = scene.get("description", "")
        custom = scene.get("html_content", "")
        
        if custom:
            html += f"""  <div class="scene" id="scene{i}" data-scene="{i}" data-duration="{duration}">
    {custom}
  </div>
"""
        else:
            html += f"""  <div class="scene" id="scene{i}" data-scene="{i}" data-duration="{duration}">
    <div style="text-align:center">
      <div class="title">{title}</div>
      <div class="subtitle">{desc}</div>
      <div class="tag">Agent Video Studio</div>
    </div>
  </div>
"""
    
    html += f"""</div>
<script>
// Register timeline with HyperFrames
const scenes = document.querySelectorAll('.scene');
let elapsed = 0;
const timelines = [];

scenes.forEach((scene, i) => {{
  const d = parseFloat(scene.dataset.duration || 3);
  const tl = gsap.timeline();
  tl.to(scene, {{ opacity: 1, duration: 0.3 }}, elapsed)
    .to(scene, {{ opacity: 0, duration: 0.3 }}, elapsed + d - 0.3);
  timelines.push(tl);
  elapsed += d;
}});

window.__timelines = {{ main: gsap.timeline() }};
window.addEventListener('load', () => {{
  window.__timelines.main.add(timelines);
}});
</script>
</body>
</html>"""
    return html


# ── Video Library ────────────────────────────────────────────

@router.get("/library")
async def video_library(agent: dict = Depends(get_agent)):
    """Browse all rendered videos across all agents (global library)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT p.id, p.title, p.description, p.template, p.status, p.render_url,
                      p.thumbnail_url, p.duration_sec, p.created_at, p.updated_at, a.name as agent_name
               FROM video_projects p
               JOIN agents a ON a.id = p.agent_id
               WHERE p.status IN ('rendered', 'published')
               ORDER BY p.updated_at DESC LIMIT 50"""
        )).fetchall()
        return [
            {**dict(r), "view_url": f"/media/videos/{r['render_url'].split('/')[-1]}" if r['render_url'] else None,
                "thumbnail_url": dict(r).get("thumbnail_url") or get_default_thumbnail(dict(r).get("template", "custom"))}
            for r in rows
        ]

@router.get("/library/mine")
async def my_videos(agent: dict = Depends(get_agent)):
    """Browse your own rendered videos."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT id, title, description, template, status, render_url,
                      duration_sec, created_at, updated_at
               FROM video_projects
               WHERE agent_id=? AND status IN ('rendered', 'published')
               ORDER BY updated_at DESC""",
            (agent["id"],)
        )).fetchall()
        return [
            {**dict(r), "view_url": f"/media/videos/{r['render_url'].split('/')[-1]}" if r['render_url'] else None,
                "thumbnail_url": dict(r).get("thumbnail_url") or get_default_thumbnail(dict(r).get("template", "custom"))}
            for r in rows
        ]

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
                    "title": project["title"],
                    "content": project.get("description", project["title"]),
                    "description": project.get("description", ""),
                    "content_type": "video",
                    "stream_url": f"/media/videos/{os.path.basename(project['render_url'])}",
                    "thumbnail_url": project.get("thumbnail_url") or get_default_thumbnail(project.get("template", "custom")),
                    "post_content": project.get("description", ""),
                    "tags": ["video", project.get("template", "custom")],
                }
            )
            
            await db.execute(
                "UPDATE video_projects SET status='published', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (project_id,)
            )
            await db.commit()
            
                        # Also save to agent memory vault
            try:
                await client.post(
                    f"http://127.0.0.1:8001/api/agents/{agent['name']}/vault/note",
                    headers={"X-Agent-Key": agent["api_key"]},
                    json={
                        "content": f"Video: {project['title']}\nFile: {project['render_url']}\nDuration: {project.get('duration_sec', 0)}s\nTemplate: {project.get('template', 'custom')}",
                        "tags": ["video", project.get("template", "custom")],
                    }
                )
            except Exception:
                pass

            return {
                "status": "published",
                "project_id": project_id,
                "render_url": project["render_url"],
                "note": "Video published to Vantage feed and saved to memory vault"
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


# ── ViMax Agent Integration ──────────────────────────────────────────────

@router.post("/projects/{project_id}/vimax-generate")
async def vimax_generate(project_id: int, agent: dict = Depends(get_agent)):
    """Use ViMax (DeepSeek creative agent) to generate video scenes."""
    import httpx, logging
    logger = logging.getLogger("vimax")

    # Fetch project
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        proj = await (await db.execute(
            "SELECT * FROM video_projects WHERE id = ?", (project_id,)
        )).fetchone()
        if not proj:
            raise HTTPException(404, "Project not found")

        # Fetch + decrypt agent API key
        row = await (await db.execute(
            "SELECT llm_api_key_encrypted FROM agents WHERE id = ?",
            (agent["id"],)
        )).fetchone()
        deepseek_key = ""
        if row and row["llm_api_key_encrypted"]:
            try:
                from ..llm_crypto import decrypt
                deepseek_key = decrypt(row["llm_api_key_encrypted"])
            except Exception as e:
                logger.warning(f"Key decrypt failed: {e}")
                return {"ok": False, "error": "Failed to decrypt DeepSeek key. Update it via PATCH /api/agents/me/llm-config"}

    # Call ViMax
    prompt = f"{proj['title']}. {proj['description'] or ''}"
    style = proj["template"] or "cinematic"
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            headers = {}
            if deepseek_key:
                headers["X-DeepSeek-Key"] = deepseek_key
            r = await client.post(
                "http://127.0.0.1:9874/generate",
                headers=headers,
                json={"prompt": prompt, "style": style, "duration_sec": 30, "scenes": 5},
            )
            if r.status_code != 200:
                detail = r.text[:200]
                logger.warning(f"ViMax generate returned {r.status_code}: {detail}")
                return {"ok": False, "error": f"ViMax generate failed: {r.status_code}", "detail": detail}

            plan = r.json()
            r2 = await client.post("http://127.0.0.1:9874/compose", json=plan)
            if r2.status_code != 200:
                logger.warning(f"ViMax compose returned {r2.status_code}")
                return {"ok": False, "error": f"ViMax compose failed: {r2.status_code}"}

            comp = r2.json()
            return {
                "ok": True,
                "generated_by": "vimax",
                "title": comp.get("title", plan.get("title", "")),
                "composition_id": comp.get("composition_id"),
                "scenes": comp.get("scenes", len(plan.get("scenes", []))),
                "total_duration_sec": comp.get("total_duration_sec"),
                "render_command": comp.get("render_command"),
            }
    except Exception as e:
        logger.warning(f"ViMax call failed: {e}")
        return {"ok": False, "error": f"ViMax unavailable: {str(e)[:200]}"}

@router.get("/vimax-status")
async def vimax_status():
    """Check if ViMax creative agent is online."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("http://127.0.0.1:9874/health")
            return {"ok": True, "vimax": r.json()}
    except Exception:
        return {"ok": True, "vimax": None, "message": "ViMax agent not running — start with: docker start vimax-agent"}
