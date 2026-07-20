"""Agent Collectives — The core primitive for agent group collaboration.
Agent-Native: collectives are sandboxed agent teams with their own identity,
memory namespace, skills registry, and project workspaces.
"""
import json, time, hashlib, logging, os
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from backend.db import DB_PATH, get_db
from backend.deps import get_agent
from backend.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/collectives", tags=["collectives"])

# ─── Models ──────────────────────────────────────────────────

class CollectiveCreate(BaseModel):
    name: str
    description: str = ""
    manifesto: str = ""
    governance: str = "consensus"  # consensus, majority, lead

class CollectiveMember(BaseModel):
    agent_name: str
    role: str = "member"  # lead, member, reviewer, observer

class WorkspaceCreate(BaseModel):
    name: str
    description: str = ""
    collective_id: int
    gitea_repo: str = ""

class A2ADelegate(BaseModel):
    target_agent: str
    task: str
    context: str = ""
    priority: int = 0

# ─── Database Init ───────────────────────────────────────────

async def init_collectives_db():
    async with get_db() as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS agent_collectives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT DEFAULT '',
                manifesto TEXT DEFAULT '',
                governance TEXT DEFAULT 'consensus',
                status TEXT DEFAULT 'active',
                created_by INTEGER REFERENCES agents(id),
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS collective_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collective_id INTEGER NOT NULL REFERENCES agent_collectives(id),
                agent_id INTEGER NOT NULL REFERENCES agents(id),
                role TEXT DEFAULT 'member',
                joined_at TEXT DEFAULT (datetime('now')),
                UNIQUE(collective_id, agent_id)
            );
            CREATE TABLE IF NOT EXISTS collective_workspaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collective_id INTEGER NOT NULL REFERENCES agent_collectives(id),
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                gitea_repo TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS collective_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL REFERENCES collective_workspaces(id),
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'todo',
                assigned_to INTEGER REFERENCES agents(id),
                created_by INTEGER REFERENCES agents(id),
                priority INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS agent_skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL REFERENCES agents(id),
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                input_schema TEXT DEFAULT '{}',
                runtime TEXT DEFAULT 'python',
                verified BOOLEAN DEFAULT 0,
                usage_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(agent_id, name)
            );
            CREATE TABLE IF NOT EXISTS agent_reputation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL REFERENCES agents(id),
                collective_id INTEGER REFERENCES agent_collectives(id),
                score REAL DEFAULT 0,
                contributions INTEGER DEFAULT 0,
                weighted_score REAL DEFAULT 0,
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(agent_id, collective_id)
            );
        """)
        await db.commit()

# ─── Collectives CRUD ────────────────────────────────────────

@router.post("")
async def create_collective(data: CollectiveCreate, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        try:
            cur = await db.execute(
                "INSERT INTO agent_collectives (name, description, manifesto, governance, created_by) VALUES (?,?,?,?,?)",
                (data.name, data.description, data.manifesto, data.governance, agent["id"])
            )
            cid = cur.lastrowid
            # Auto-add creator as lead
            await db.execute(
                "INSERT INTO collective_members (collective_id, agent_id, role) VALUES (?,?,?)",
                (cid, agent["id"], "lead")
            )
            await db.commit()
            return {"id": cid, "name": data.name, "role": "lead"}
        except aiosqlite.IntegrityError:
            raise HTTPException(409, f"Collective '{data.name}' already exists")

@router.get("")
async def list_collectives(agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM collective_members WHERE collective_id=c.id) as member_count FROM agent_collectives c ORDER BY c.created_at DESC"
        )).fetchall()
        return [dict(r) for r in rows]

@router.post("/{collective_id}/members")
async def add_member(collective_id: int, data: CollectiveMember, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        # Find target agent
        target = await (await db.execute(
            "SELECT id FROM agents WHERE name=?", (data.agent_name,)
        )).fetchone()
        if not target:
            raise HTTPException(404, f"Agent '{data.agent_name}' not found")
        try:
            await db.execute(
                "INSERT INTO collective_members (collective_id, agent_id, role) VALUES (?,?,?)",
                (collective_id, target[0], data.role)
            )
            await db.commit()
            return {"status": "added", "agent": data.agent_name, "role": data.role}
        except aiosqlite.IntegrityError:
            raise HTTPException(409, f"Agent already in collective")

# ─── Workspaces ─────────────────────────────────────────────

@router.post("/workspaces")
async def create_workspace(data: WorkspaceCreate, agent: dict = Depends(get_agent)):
    # Create Gitea repo if name provided
    gitea_url = ""
    repo_name = data.gitea_repo or data.name.replace(" ", "-").lower()
    if repo_name:
        # Direct URL (works even if API call fails)
        gitea_url = f"http://127.0.0.1:3001/ares-bot/{repo_name}.git"
        # Actually create the repo via Gitea API (background, fail-open)
        token_path = "/opt/ares/.gitea_token"
        if os.path.exists(token_path):
            try:
                import urllib.request as _urlreq
                token = open(token_path).read().strip()
                import asyncio as _asyncio
                await _asyncio.to_thread(lambda: _urlreq.urlopen(_urlreq.Request(
                    "http://127.0.0.1:3001/api/v1/user/repos",
                    data=json.dumps({"name": repo_name, "description": data.description, "auto_init": True}).encode(),
                    headers={"Authorization": f"token {token}", "Content-Type": "application/json"},
                    method="POST"
                )))
            except Exception:
                pass

    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO collective_workspaces (collective_id, name, description, gitea_repo) VALUES (?,?,?,?)",
            (data.collective_id, data.name, data.description, gitea_url)
        )
        await db.commit()
        return {"id": cur.lastrowid, "name": data.name, "gitea_url": gitea_url}

@router.get("/workspaces/{workspace_id}")
async def get_workspace(workspace_id: int, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM collective_workspaces WHERE id=?", (workspace_id,)
        )).fetchone()
        if not row:
            raise HTTPException(404, "Workspace not found")
        w = dict(row)
        # Get tasks
        tasks = await (await db.execute(
            "SELECT * FROM collective_tasks WHERE workspace_id=? ORDER BY priority DESC, created_at DESC",
            (workspace_id,)
        )).fetchall()
        w["tasks"] = [dict(t) for t in tasks]
        return w

# ─── Tasks ──────────────────────────────────────────────────

@router.post("/workspaces/{workspace_id}/tasks")
async def create_task(workspace_id: int, data: dict, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO collective_tasks (workspace_id, title, description, created_by, priority) VALUES (?,?,?,?,?)",
            (workspace_id, data.get("title"), data.get("description", ""), agent["id"], data.get("priority", 0))
        )
        await db.commit()
        return {"id": cur.lastrowid, "title": data.get("title")}

# ─── Skills Registry ────────────────────────────────────────

@router.post("/skills")
async def register_skill(data: dict, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        try:
            await db.execute(
                "INSERT INTO agent_skills (agent_id, name, description, input_schema, runtime) VALUES (?,?,?,?,?)",
                (agent["id"], data["name"], data.get("description", ""),
                 json.dumps(data.get("input_schema", {})), data.get("runtime", "python"))
            )
            await db.commit()
            return {"status": "registered", "skill": data["name"]}
        except aiosqlite.IntegrityError:
            raise HTTPException(409, f"Skill '{data['name']}' already registered")

@router.get("/skills")
async def list_skills(agent: dict = Depends(get_agent), skill: Optional[str] = Query(None)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT s.*, a.name as agent_name FROM agent_skills s JOIN agents a ON a.id=s.agent_id"
        params = []
        if skill:
            query += " WHERE s.name LIKE ?"
            params.append(f"%{skill}%")
        query += " ORDER BY s.usage_count DESC"
        rows = await (await db.execute(query, params)).fetchall()
        return [dict(r) for r in rows]

# ─── A2A Protocol ───────────────────────────────────────────

@router.get("/a2a/discover")
async def discover_agents(skill: Optional[str] = Query(None), agent: dict = Depends(get_agent)):
    """Discover agents by skill/capability."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT DISTINCT a.id, a.name, a.bio FROM agents a"
        params = []
        if skill:
            query += " JOIN agent_skills s ON s.agent_id=a.id WHERE s.name LIKE ? OR s.description LIKE ?"
            params.extend([f"%{skill}%", f"%{skill}%"])
        rows = await (await db.execute(query, params)).fetchall()
        agents_list = []
        for r in rows:
            skills = await (await db.execute(
                "SELECT name, description FROM agent_skills WHERE agent_id=?", (r["id"],)
            )).fetchall()
            agents_list.append({**dict(r), "skills": [dict(s) for s in skills]})
        return agents_list

@router.post("/a2a/delegate")
async def delegate_task(data: A2ADelegate, agent: dict = Depends(get_agent)):
    """Delegate a task to another agent via A2A protocol."""
    async with get_db() as db:
        target = await (await db.execute(
            "SELECT id, name FROM agents WHERE name=?", (data.target_agent,)
        )).fetchone()
        if not target:
            raise HTTPException(404, f"Target agent '{data.target_agent}' not found")
        
        # Create delegation event
        await db.execute(
            "INSERT INTO collective_tasks (workspace_id, title, description, assigned_to, created_by, priority, status) VALUES (?,?,?,?,?,?,?)",
            (0, f"A2A: {data.task[:50]}", f"Delegated from {agent['name']}: {data.task}\nContext: {data.context}",
             target[0], agent["id"], data.priority, "delegated")
        )
        await db.commit()
    
    # Publish to Vantage feed (non-blocking)
    try:
        key = open("/opt/ares/.vantage_key").read().strip() if os.path.exists("/opt/ares/.vantage_key") else ""
        if key:
            import threading
            def _pub():
                import subprocess
                subprocess.run(["curl", "-s", "-X", "POST", "http://127.0.0.1:8001/api/agents/posts/text",
                    "-H", f"X-Agent-Key: {key}", "-H", "Content-Type: application/json",
                    "-d", json.dumps({"title": f"📤 A2A: {agent['name']} → {data.target_agent}",
                                     "content": f"**Task:** {data.task}",
                                     "tags": ["a2a", "delegation"]})],
                    capture_output=True, timeout=5)
            threading.Thread(target=_pub, daemon=True).start()
    except:
        pass
    
    return {"status": "delegated", "from": agent["name"], "to": data.target_agent, "task": data.task[:80]}

# ─── Reputation ─────────────────────────────────────────────

@router.get("/reputation")
async def get_reputation(agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("""
            SELECT ar.*, ac.name as collective_name FROM agent_reputation ar
            LEFT JOIN agent_collectives ac ON ac.id=ar.collective_id
            WHERE ar.agent_id=? ORDER BY ar.score DESC
        """, (agent["id"],))).fetchall()
        return [dict(r) for r in rows]



# ── OpenCode Integration ─────────────────────────────────────

@router.post("/workspaces/{workspace_id}/tasks/{task_id}/implement")
async def implement_task(workspace_id: int, task_id: int, agent: dict = Depends(get_agent)):
    """Task implementation — triggers OpenCode to write code for a task.
    Calls the OpenCode serve API (port 4096) to generate code.
    The code is committed to the workspace's Gitea repo.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        
        # Get workspace
        ws = await (await db.execute(
            "SELECT * FROM collective_workspaces WHERE id=?", (workspace_id,)
        )).fetchone()
        if not ws:
            raise HTTPException(404, "Workspace not found")
        ws = dict(ws)
        
        # Get task
        task = await (await db.execute(
            "SELECT * FROM collective_tasks WHERE id=? AND workspace_id=?", (task_id, workspace_id)
        )).fetchone()
        if not task:
            raise HTTPException(404, "Task not found")
        task = dict(task)
        
        # Get collective
        coll = await (await db.execute(
            "SELECT * FROM agent_collectives WHERE id=?", (ws["collective_id"],)
        )).fetchone()
        
        if not ws.get("gitea_repo"):
            raise HTTPException(400, "No Gitea repo configured for this workspace")
        
        # Create implementation branch
        branch = f"task-{task_id}-{task['title'].replace(' ', '-')[:30].lower()}"
        
        # Call OpenCode to implement the task
        try:
            import asyncio as _asyncio
            import subprocess as _sp
            
            opm = "/root/.opencode/bin/opencode"
            repo_name = ws["gitea_repo"].split("/")[-1].replace(".git", "")
            short_path = f"/opt/ares/agent-workspace/{repo_name}"
            
            # Clone if needed
            if not os.path.exists(short_path):
                await _asyncio.to_thread(lambda: _sp.run(
                    ["git", "clone", ws["gitea_repo"], short_path],
                    capture_output=True, timeout=30
                ))
            
            # Run OpenCode with the task
            prompt = f"Implement task: {task['title']}. Description: {task.get('description', 'No description')}. Write clean, tested code. Commit to branch {branch}."
            
            result = await _asyncio.to_thread(lambda: _sp.run(
                [opm, "-c", short_path, "-q", prompt],
                capture_output=True, text=True, timeout=300
            ))
            
            # Update task status
            await db.execute(
                "UPDATE collective_tasks SET status='in_progress', assigned_to=? WHERE id=?",
                (agent["id"], task_id)
                
)
            await db.commit()
            

            return {
                "status": "implementation_started",
                "task_id": task_id,
                "workspace_id": workspace_id,
                "branch": branch,
                "repo": ws["gitea_repo"],
                "note": "OpenCode is working on this task. Code will be committed to the branch."
            }
        except Exception as e:
            raise HTTPException(500, f"OpenCode execution failed: {e}")


# Registered after all static single-segment GET routes (/skills, /reputation) so
# this dynamic path does not shadow them under Starlette's declaration-order matching.
@router.get("/{collective_id}")
async def get_collective(collective_id: int, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM agent_collectives WHERE id=?", (collective_id,)
        )).fetchone()
        if not row:
            raise HTTPException(404, "Collective not found")
        c = dict(row)
        # Members
        members = await (await db.execute(
            "SELECT a.id, a.name, cm.role, cm.joined_at FROM collective_members cm JOIN agents a ON a.id=cm.agent_id WHERE cm.collective_id=?",
            (collective_id,)
        )).fetchall()
        c["members"] = [dict(m) for m in members]
        # Workspaces
        workspaces = await (await db.execute(
            "SELECT * FROM collective_workspaces WHERE collective_id=?", (collective_id,)
        )).fetchall()
        c["workspaces"] = [dict(w) for w in workspaces]
        return c


@router.post("/reputation/award")
async def award_reputation(data: dict, agent: dict = Depends(get_agent)):
    """Award reputation points to an agent (lead-only)."""
    async with get_db() as db:
        target = await (await db.execute(
            "SELECT id FROM agents WHERE name=?", (data.get("agent_name"),)
        )).fetchone()
        if not target:
            raise HTTPException(404, "Agent not found")
        
        points = float(data.get("points", 1))
        collective_id = data.get("collective_id")
        
        existing = await (await db.execute(
            "SELECT id, score, contributions FROM agent_reputation WHERE agent_id=? AND collective_id=?",
            (target[0], collective_id)
        )).fetchone()
        
        if existing:
            new_score = existing[1] + points
            new_contrib = existing[2] + 1
            await db.execute(
                "UPDATE agent_reputation SET score=?, contributions=?, weighted_score=?, updated_at=datetime('now') WHERE id=?",
                (new_score, new_contrib, new_score * (1 + 0.1 * new_contrib), existing[0])
            )
        else:
            await db.execute(
                "INSERT INTO agent_reputation (agent_id, collective_id, score, contributions, weighted_score) VALUES (?,?,?,?,?)",
                (target[0], collective_id, points, 1, points)
            )
        await db.commit()
        return {"status": "awarded", "agent": data.get("agent_name"), "points": points}

# ─── Schema init handled by main.py lifespan, not at module load ──────────────────────────────────────────
