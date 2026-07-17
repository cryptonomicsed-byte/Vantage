"""Memory Galaxy API — agent second brain as a node/edge graph."""
import json, time
import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from backend.db import DB_PATH
from backend.deps import get_agent
from ..db import get_db

router = APIRouter(prefix="/api/agents", tags=["memory_galaxy"])


class NodeCreate(BaseModel):
    id: str
    label: str
    node_type: str
    category: str = "unknown"
    strength: float = 0.5
    color: str = "#4488ff"
    glow: float = 0.5
    pulse_rate: float = 0.02
    size: float = 12
    pos_x: float = 0
    pos_y: float = 0
    pos_z: float = 0
    metadata: dict = {}


class EdgeCreate(BaseModel):
    source_id: str
    target_id: str
    edge_type: str = "related"
    strength: float = 0.5


def _node_row(row) -> dict:
    return {
        "id": row["id"],
        "label": row["label"],
        "type": row["node_type"],
        "category": row["category"],
        "strength": row["strength"],
        "color": row["color"],
        "glow_intensity": row["glow"],
        "pulse_rate": row["pulse_rate"],
        "size": row["size"],
        "position_3d": {"x": row["pos_x"], "y": row["pos_y"], "z": row["pos_z"]},
        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        "last_updated": row["updated_at"],
    }


@router.get("/me/memory-galaxy")
async def get_memory_galaxy(agent: dict = Depends(get_agent)):
    """Get the agent's full memory galaxy — all nodes and edges."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        nodes = await (await db.execute(
            "SELECT * FROM agent_memory_nodes WHERE agent_id=? ORDER BY strength DESC",
            (agent["id"],)
        )).fetchall()

        edges = await (await db.execute(
            """SELECT e.* FROM agent_memory_edges e
               WHERE e.source_id IN (SELECT id FROM agent_memory_nodes WHERE agent_id=?)
                  OR e.target_id IN (SELECT id FROM agent_memory_nodes WHERE agent_id=?)""",
            (agent["id"], agent["id"])
        )).fetchall()

        node_list = [_node_row(n) for n in nodes]
        edge_list = [{
            "source": e["source_id"], "target": e["target_id"],
            "type": e["edge_type"], "strength": e["strength"],
        } for e in edges]

        return {
            "galaxy_metadata": {
                "version": "2.0.0",
                "agent_name": agent["name"],
                "total_nodes": len(node_list),
                "total_edges": len(edge_list),
                "unified": True,
            },
            "nodes": node_list,
            "edges": edge_list,
        }


@router.post("/me/memory-galaxy/node")
async def create_memory_node(data: NodeCreate, agent: dict = Depends(get_agent)):
    """Create or update a memory node."""
    async with get_db() as db:
        await db.execute(
            """INSERT OR REPLACE INTO agent_memory_nodes
               (id, agent_id, label, node_type, category, strength, color, glow, pulse_rate, size, pos_x, pos_y, pos_z, metadata, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (data.id, agent["id"], data.label, data.node_type, data.category,
             data.strength, data.color, data.glow, data.pulse_rate, data.size,
             data.pos_x, data.pos_y, data.pos_z, json.dumps(data.metadata))
        )
        await db.commit()
    return {"ok": True, "node_id": data.id}


@router.post("/me/memory-galaxy/edge")
async def create_memory_edge(data: EdgeCreate, agent: dict = Depends(get_agent)):
    """Create an edge between two nodes (both must belong to agent)."""
    async with get_db() as db:
        # Verify both nodes belong to this agent
        for nid in [data.source_id, data.target_id]:
            row = await (await db.execute(
                "SELECT id FROM agent_memory_nodes WHERE id=? AND agent_id=?", (nid, agent["id"])
            )).fetchone()
            if not row:
                raise HTTPException(404, f"Node {nid} not found")

        cur = await db.execute(
            "INSERT INTO agent_memory_edges (source_id, target_id, edge_type, strength) VALUES (?,?,?,?)",
            (data.source_id, data.target_id, data.edge_type, data.strength)
        )
        await db.commit()
    return {"ok": True, "edge_id": cur.lastrowid}


@router.get("/me/memory-galaxy/stats")
async def get_galaxy_stats(agent: dict = Depends(get_agent)):
    """Quick stats: node counts by type, total edges, recent additions."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        type_counts = await (await db.execute(
            "SELECT node_type, COUNT(*) as cnt FROM agent_memory_nodes WHERE agent_id=? GROUP BY node_type",
            (agent["id"],)
        )).fetchall()

        total_nodes = sum(r["cnt"] for r in type_counts)

        edge_count = (await (await db.execute(
            """SELECT COUNT(*) as cnt FROM agent_memory_edges
               WHERE source_id IN (SELECT id FROM agent_memory_nodes WHERE agent_id=?)
                  OR target_id IN (SELECT id FROM agent_memory_nodes WHERE agent_id=?)""",
            (agent["id"], agent["id"])
        )).fetchone())["cnt"]

        recent = await (await db.execute(
            "SELECT id, label, node_type, updated_at FROM agent_memory_nodes WHERE agent_id=? ORDER BY updated_at DESC LIMIT 5",
            (agent["id"],)
        )).fetchall()

        return {
            "total_nodes": total_nodes,
            "total_edges": edge_count,
            "by_type": {r["node_type"]: r["cnt"] for r in type_counts},
            "recent": [{"id": r["id"], "label": r["label"], "type": r["node_type"]} for r in recent],
        }
