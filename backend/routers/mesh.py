"""Block Mesh coordination API.

Vantage is the shared collaboration layer for ọmọ Kọ́dà sovereign agents.
Agents join blocks, negotiate commitments, share resources, and broadcast events here.
Real-time updates flow through /ws/gossip (channel: block.{block_id}).

Auth: X-Agent-Key (Vantage API key). ọmọ Kọ́dà agents must have a Vantage account.
Env vars: VANTAGE_URL, VANTAGE_KEY, MESH_BLOCK_ID (set on the ọmọ Kọ́dà side).
"""
import asyncio
import datetime as _dt
import json as _json
import uuid as _uuid

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import DB_PATH
from ..deps import get_agent, _parse_body
from ..identity_verify import verify_identity
from ..mesh_discovery import suggest_neighbors
from ..reputation_pub import publish_julia_score
from ..trust_signals import emit_trust_signal, get_trust_signals
from ..utils import _broadcast_gossip

router = APIRouter(prefix="/api/mesh", tags=["mesh"])


# ── helpers ────────────────────────────────────────────────────────────────────────────────

async def _record_event(
    db: aiosqlite.Connection,
    block_id: str,
    event_type: str,
    actor_id: str | None,
    payload: dict,
) -> None:
    await db.execute(
        "INSERT INTO mesh_events (block_id, event_type, actor_id, payload_json) VALUES (?,?,?,?)",
        (block_id, event_type, actor_id, _json.dumps(payload)),
    )


# ── agent presence ────────────────────────────────────────────────────────────────────────

@router.post("/agents/join")
async def join_block(request: Request, agent: dict = Depends(get_agent)):
    """Register an ọmọ Kọ́dà agent in a block. Idempotent — safe to call on every birth."""
    body = await _parse_body(request)
    agent_id = str(body.get("agent_id") or agent["name"]).strip()[:128]
    block_id = str(body.get("block_id", "")).strip()[:128]
    role = str(body.get("role", "home")).strip()[:32]
    capabilities = body.get("capabilities") or {}

    if not block_id:
        raise HTTPException(422, "block_id required")

    # ── Sovereign identity ──────────────────────────────────────────────────
    # ọmọ Kọ́dà birth sends identity inside `capabilities`; accept top-level too.
    def _identity_field(key):
        val = capabilities.get(key)
        return body.get(key) if val is None else val

    public_key = str(_identity_field("public_key") or "")[:128]
    dna_fingerprint = str(_identity_field("dna_fingerprint") or "")[:128]
    model_fingerprint = str(_identity_field("model_fingerprint") or public_key)[:128]
    parent_id = str(_identity_field("parent_id") or "")[:128]
    signature = str(_identity_field("identity_signature") or "")[:256]
    odu_raw = _identity_field("odu_index")
    try:
        odu_index = int(odu_raw) if odu_raw is not None else None
    except (TypeError, ValueError):
        odu_index = None

    # The agent proves control of its keypair by signing its own agent_id.
    verified = 1 if verify_identity(public_key, agent_id, signature) else 0

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO mesh_agents
                   (agent_id, block_id, vantage_name, role, capabilities_json,
                    public_key, dna_fingerprint, odu_index, model_fingerprint,
                    parent_id, identity_verified, last_seen_at, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'),'active')
               ON CONFLICT(agent_id, block_id) DO UPDATE SET
                   role=excluded.role,
                   capabilities_json=excluded.capabilities_json,
                   vantage_name=excluded.vantage_name,
                   public_key=CASE WHEN excluded.public_key != ''
                       THEN excluded.public_key ELSE mesh_agents.public_key END,
                   dna_fingerprint=CASE WHEN excluded.dna_fingerprint != ''
                       THEN excluded.dna_fingerprint ELSE mesh_agents.dna_fingerprint END,
                   odu_index=COALESCE(excluded.odu_index, mesh_agents.odu_index),
                   model_fingerprint=CASE WHEN excluded.model_fingerprint != ''
                       THEN excluded.model_fingerprint ELSE mesh_agents.model_fingerprint END,
                   parent_id=CASE WHEN excluded.parent_id != ''
                       THEN excluded.parent_id ELSE mesh_agents.parent_id END,
                   identity_verified=MAX(mesh_agents.identity_verified,
                                         excluded.identity_verified),
                   last_seen_at=datetime('now'),
                   status='active'""",
            (agent_id, block_id, agent["name"], role, _json.dumps(capabilities),
             public_key, dna_fingerprint, odu_index, model_fingerprint,
             parent_id, verified),
        )
        await _record_event(
            db, block_id, "agent_joined", agent_id,
            {"role": role, "identity_verified": bool(verified)},
        )
        await db.commit()

    await _broadcast_gossip(f"block.{block_id}", {
        "type": "agent_joined",
        "agent_id": agent_id,
        "block_id": block_id,
        "role": role,
        "identity_verified": bool(verified),
    })
    return {
        "ok": True,
        "agent_id": agent_id,
        "block_id": block_id,
        "identity_verified": bool(verified),
    }


@router.delete("/agents/{agent_id}/leave")
async def leave_block(
    agent_id: str, request: Request, agent: dict = Depends(get_agent)
):
    block_id = request.query_params.get("block_id", "")
    if not block_id:
        raise HTTPException(422, "block_id query param required")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE mesh_agents SET status='offline' WHERE agent_id=? AND block_id=?",
            (agent_id, block_id),
        )
        await _record_event(db, block_id, "agent_left", agent_id, {})
        await db.commit()
    await _broadcast_gossip(f"block.{block_id}", {"type": "agent_left", "agent_id": agent_id})
    return {"ok": True}


@router.post("/agents/{agent_id}/heartbeat")
async def agent_heartbeat(
    agent_id: str, request: Request, agent: dict = Depends(get_agent)
):
    body = await _parse_body(request)
    block_id = str(body.get("block_id", "")).strip()
    if not block_id:
        raise HTTPException(422, "block_id required")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE mesh_agents SET last_seen_at=datetime('now'), status='active' WHERE agent_id=? AND block_id=?",
            (agent_id, block_id),
        )
        await db.commit()
    return {"ok": True}


# ── block queries (public) ─────────────────────────────────────────────────────────────

@router.get("/blocks/{block_id}")
async def get_block(block_id: str):
    """Full block snapshot: active agents, open proposals, available resources."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM mesh_agents WHERE block_id=? AND status='active' ORDER BY joined_at",
            (block_id,),
        ) as cur:
            agents = [dict(r) for r in await cur.fetchall()]
        async with db.execute(
            "SELECT * FROM mesh_proposals WHERE block_id=? AND status='open' ORDER BY created_at DESC",
            (block_id,),
        ) as cur:
            proposals = [dict(r) for r in await cur.fetchall()]
        async with db.execute(
            "SELECT * FROM mesh_resources WHERE block_id=? ORDER BY created_at",
            (block_id,),
        ) as cur:
            resources = [dict(r) for r in await cur.fetchall()]

    for a in agents:
        try:
            a["capabilities"] = _json.loads(a.pop("capabilities_json", "{}"))
        except Exception:
            a["capabilities"] = {}
    for p in proposals:
        try:
            p["give"] = _json.loads(p.pop("give_json", "[]"))
            p["take"] = _json.loads(p.pop("take_json", "[]"))
        except Exception:
            p["give"] = []
            p["take"] = []

    return {"block_id": block_id, "agents": agents, "proposals": proposals, "resources": resources}


@router.get("/blocks/{block_id}/agents")
async def get_block_agents(block_id: str, filter: str = "", capabilities: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        sql = "SELECT * FROM mesh_agents WHERE block_id=? AND status='active'"
        params: list = [block_id]
        if filter:
            sql += " AND (agent_id LIKE ? OR role LIKE ?)"
            params += [f"%{filter}%", f"%{filter}%"]
        sql += " ORDER BY trust_score DESC"
        async with db.execute(sql, params) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    for r in rows:
        try:
            r["capabilities"] = _json.loads(r.pop("capabilities_json", "{}"))
        except Exception:
            r["capabilities"] = {}
    return rows


@router.get("/blocks/{block_id}/events")
async def block_events(block_id: str, limit: int = 50):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM mesh_events WHERE block_id=? ORDER BY created_at DESC LIMIT ?",
            (block_id, min(limit, 200)),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        try:
            r["payload"] = _json.loads(r.pop("payload_json", "{}"))
        except Exception:
            r["payload"] = {}
    return rows


# ── proposals ────────────────────────────────────────────────────────────────────────────────

@router.post("/proposals")
async def create_proposal(request: Request, agent: dict = Depends(get_agent)):
    body = await _parse_body(request)
    block_id = str(body.get("block_id", "")).strip()
    proposer_id = str(body.get("proposer_id") or agent["name"]).strip()[:128]
    respondent_id = body.get("respondent_id")
    give = body.get("give") or []
    take = body.get("take") or []
    ttl_ms = int(body.get("ttl_ms") or 300_000)

    if not block_id:
        raise HTTPException(422, "block_id required")

    proposal_id = str(_uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO mesh_proposals
                   (id, block_id, proposer_id, respondent_id, give_json, take_json, ttl_ms)
               VALUES (?,?,?,?,?,?,?)""",
            (proposal_id, block_id, proposer_id, respondent_id,
             _json.dumps(give), _json.dumps(take), ttl_ms),
        )
        await db.execute(
            "UPDATE mesh_agents SET commitments_made = commitments_made + 1 WHERE agent_id=? AND block_id=?",
            (proposer_id, block_id),
        )
        await _record_event(db, block_id, "proposal_created", proposer_id, {"proposal_id": proposal_id})
        await db.commit()

    await _broadcast_gossip(f"block.{block_id}", {
        "type": "proposal_created",
        "proposal_id": proposal_id,
        "proposer_id": proposer_id,
        "respondent_id": respondent_id,
        "give": give,
        "take": take,
    })
    return {"proposal_id": proposal_id, "status": "open"}


@router.get("/proposals/{block_id}")
async def list_proposals(block_id: str, status: str = "open"):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM mesh_proposals WHERE block_id=? AND status=? ORDER BY created_at DESC",
            (block_id, status),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        try:
            r["give"] = _json.loads(r.pop("give_json", "[]"))
            r["take"] = _json.loads(r.pop("take_json", "[]"))
        except Exception:
            r["give"] = []
            r["take"] = []
    return rows


@router.get("/proposal/{proposal_id}")
async def get_proposal(proposal_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mesh_proposals WHERE id=?", (proposal_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Proposal not found")
    p = dict(row)
    try:
        p["give"] = _json.loads(p.pop("give_json", "[]"))
        p["take"] = _json.loads(p.pop("take_json", "[]"))
    except Exception:
        p["give"] = []
        p["take"] = []
    return p


@router.post("/proposals/{proposal_id}/respond")
async def respond_to_proposal(
    proposal_id: str, request: Request, agent: dict = Depends(get_agent)
):
    body = await _parse_body(request)
    respondent_id = str(body.get("respondent_id") or agent["name"]).strip()[:128]
    decision = str(body.get("decision", "")).strip().lower()
    counter = body.get("counter")

    if decision not in ("accept", "reject", "counter"):
        raise HTTPException(422, "decision must be accept|reject|counter")

    status_map = {"accept": "accepted", "reject": "rejected", "counter": "countered"}
    event_map = {"accept": "proposal_accepted", "reject": "proposal_rejected", "counter": "proposal_countered"}

    block_id = ""
    proposer_id = ""
    new_status = status_map[decision]

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mesh_proposals WHERE id=?", (proposal_id,)) as cur:
            prop = await cur.fetchone()
        if not prop:
            raise HTTPException(404, "Proposal not found")
        prop = dict(prop)
        if prop["status"] != "open":
            raise HTTPException(409, f"Proposal already {prop['status']}")

        block_id = prop["block_id"]
        proposer_id = prop["proposer_id"]

        await db.execute(
            "UPDATE mesh_proposals SET status=?, resolved_at=datetime('now') WHERE id=?",
            (new_status, proposal_id),
        )
        await db.execute(
            "INSERT INTO mesh_responses (proposal_id, respondent_id, decision, counter_json) VALUES (?,?,?,?)",
            (proposal_id, respondent_id, decision, _json.dumps(counter) if counter else None),
        )

        if decision == "accept":
            commitment_id = str(_uuid.uuid4())
            await db.execute(
                """INSERT INTO mesh_commitments
                       (id, proposal_id, agent_a, agent_b, kind, terms_json)
                   VALUES (?,?,?,?,'ServicePerform',?)""",
                (commitment_id, proposal_id, proposer_id, respondent_id,
                 _json.dumps({"give": prop["give_json"], "take": prop["take_json"]})),
            )
            for aid in (proposer_id, respondent_id):
                await db.execute(
                    """UPDATE mesh_agents SET
                           commitments_kept = commitments_kept + 1
                       WHERE agent_id=? AND block_id=?""",
                    (aid, block_id),
                )
            # Emit trust signals instead of hardcoding trust_score += 2.0.
            # The authoritative score is computed by Julia; we just record the signal.
            asyncio.create_task(emit_trust_signal(
                block_id=prop["block_id"],
                from_agent=proposer_id,
                to_agent=respondent_id,
                kind="commitment_fulfilled",
            ))
            asyncio.create_task(emit_trust_signal(
                block_id=prop["block_id"],
                from_agent=respondent_id,
                to_agent=proposer_id,
                kind="commitment_fulfilled",
            ))

        await _record_event(db, block_id, event_map[decision], respondent_id, {"proposal_id": proposal_id})
        await db.commit()

    await _broadcast_gossip(f"block.{block_id}", {
        "type": event_map[decision],
        "proposal_id": proposal_id,
        "respondent_id": respondent_id,
        "decision": decision,
    })
    return {"ok": True, "proposal_id": proposal_id, "status": new_status}


# ── resources ───────────────────────────────────────────────────────────────────────────────

@router.post("/resources")
async def register_resource(request: Request, agent: dict = Depends(get_agent)):
    body = await _parse_body(request)
    block_id = str(body.get("block_id", "")).strip()
    resource_type = str(body.get("resource_type", "")).strip()[:64]
    description = str(body.get("description", ""))[:500]
    capacity = int(body.get("capacity") or 1)
    owner_id = str(body.get("owner_id") or agent["name"]).strip()[:128]

    if not block_id or not resource_type:
        raise HTTPException(422, "block_id and resource_type required")

    resource_id = str(_uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO mesh_resources (id, block_id, owner_id, resource_type, description, capacity) VALUES (?,?,?,?,?,?)",
            (resource_id, block_id, owner_id, resource_type, description, capacity),
        )
        await _record_event(db, block_id, "resource_registered", owner_id,
                            {"resource_id": resource_id, "type": resource_type})
        await db.commit()
    return {"resource_id": resource_id, "block_id": block_id}


@router.get("/resources/{block_id}")
async def list_resources(block_id: str, filter: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        sql = "SELECT * FROM mesh_resources WHERE block_id=?"
        params: list = [block_id]
        if filter:
            sql += " AND (resource_type LIKE ? OR description LIKE ?)"
            params += [f"%{filter}%", f"%{filter}%"]
        sql += " ORDER BY created_at"
        async with db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]


@router.post("/resources/{resource_id}/reserve")
async def reserve_resource(
    resource_id: str, request: Request, agent: dict = Depends(get_agent)
):
    body = await _parse_body(request)
    agent_id = str(body.get("agent_id") or agent["name"]).strip()[:128]
    duration_secs = int(body.get("duration_secs") or 3600)
    purpose = str(body.get("purpose") or "general")[:256]

    reserved_until = (
        _dt.datetime.utcnow() + _dt.timedelta(seconds=duration_secs)
    ).strftime("%Y-%m-%d %H:%M:%S")
    now_str = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mesh_resources WHERE id=?", (resource_id,)) as cur:
            res = await cur.fetchone()
        if not res:
            raise HTTPException(404, "Resource not found")
        res = dict(res)
        if res["reserved_by"] and (res["reserved_until"] or "") > now_str:
            raise HTTPException(409, f"Resource already reserved by {res['reserved_by']}")

        await db.execute(
            "UPDATE mesh_resources SET reserved_by=?, reserved_until=? WHERE id=?",
            (agent_id, reserved_until, resource_id),
        )
        block_id = res["block_id"]
        await _record_event(db, block_id, "resource_reserved", agent_id,
                            {"resource_id": resource_id, "until": reserved_until, "purpose": purpose})
        await db.commit()

    await _broadcast_gossip(f"block.{block_id}", {
        "type": "resource_reserved",
        "resource_id": resource_id,
        "agent_id": agent_id,
        "reserved_until": reserved_until,
    })
    return {"ok": True, "resource_id": resource_id, "reserved_until": reserved_until}


@router.post("/resources/{resource_id}/release")
async def release_resource(
    resource_id: str, request: Request, agent: dict = Depends(get_agent)
):
    body = await _parse_body(request)
    agent_id = str(body.get("agent_id") or agent["name"]).strip()[:128]

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mesh_resources WHERE id=?", (resource_id,)) as cur:
            res = await cur.fetchone()
        if not res:
            raise HTTPException(404, "Resource not found")
        res = dict(res)
        if res.get("reserved_by") != agent_id:
            raise HTTPException(403, "You do not hold this reservation")

        await db.execute(
            "UPDATE mesh_resources SET reserved_by=NULL, reserved_until=NULL WHERE id=?",
            (resource_id,),
        )
        block_id = res["block_id"]
        await _record_event(db, block_id, "resource_released", agent_id, {"resource_id": resource_id})
        await db.commit()

    await _broadcast_gossip(f"block.{block_id}", {
        "type": "resource_released",
        "resource_id": resource_id,
        "agent_id": agent_id,
    })
    return {"ok": True, "released": True}


# ── trust ──────────────────────────────────────────────────────────────────────────────────

@router.get("/trust/{agent_id}")
async def get_trust(agent_id: str, block_id: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if block_id:
            async with db.execute(
                "SELECT * FROM mesh_agents WHERE agent_id=? AND block_id=?", (agent_id, block_id)
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        else:
            async with db.execute(
                "SELECT * FROM mesh_agents WHERE agent_id=?", (agent_id,)
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        async with db.execute(
            "SELECT COUNT(*) FROM mesh_commitments WHERE (agent_a=? OR agent_b=?) AND fulfilled=1",
            (agent_id, agent_id),
        ) as cur:
            fulfilled = (await cur.fetchone())[0]

    for r in rows:
        try:
            r["capabilities"] = _json.loads(r.pop("capabilities_json", "{}"))
        except Exception:
            r["capabilities"] = {}

    return {"agent_id": agent_id, "blocks": rows, "fulfilled_commitments": fulfilled}


@router.post("/trust/{agent_id}/signal")
async def record_trust_signal(
    agent_id: str,
    request: Request,
    agent: dict = Depends(get_agent),
):
    """
    External services push trust signals here.
    Body: {block_id, neighbor_id, kind, weight?}
    """
    body = await _parse_body(request)
    block_id = str(body.get("block_id", "default")).strip()
    neighbor_id = body.get("neighbor_id")
    kind = str(body.get("kind", "interaction")).strip()
    weight = body.get("weight")

    if not neighbor_id:
        raise HTTPException(status_code=422, detail="neighbor_id required")

    if weight is not None:
        weight = float(weight)

    await emit_trust_signal(block_id, agent_id, str(neighbor_id), kind, weight)
    signals = await get_trust_signals(block_id, agent_id, str(neighbor_id))
    asyncio.create_task(publish_julia_score(block_id, agent_id, str(neighbor_id), signals))
    return {"status": "recorded", "kind": kind}


@router.get("/blocks/{block_id}/neighbors/suggest")
async def suggest_block_neighbors(block_id: str, for_agent: str = "", limit: int = 10):
    """Suggest neighbors for an agent based on trust score and activity."""
    suggestions = await suggest_neighbors(block_id, for_agent, limit=limit)
    return {"suggestions": suggestions}


@router.get("/trust/{agent_id}/signals")
async def get_agent_trust_signals(
    agent_id: str, neighbor_id: str, block_id: str = "default"
):
    """Get trust signals between agent_id and neighbor_id (for Julia score computation)."""
    signals = await get_trust_signals(block_id, agent_id, neighbor_id)
    return {"signals": signals, "agent_id": agent_id, "neighbor_id": neighbor_id}


# ── signal / broadcast ────────────────────────────────────────────────────────────────

@router.post("/signal")
async def signal_event(request: Request, agent: dict = Depends(get_agent)):
    body = await _parse_body(request)
    block_id = str(body.get("block_id", "")).strip()
    actor_id = str(body.get("actor_id") or agent["name"]).strip()[:128]
    event_type = str(body.get("event_type") or "custom").strip()[:64]
    payload = body.get("payload") or {}

    if not block_id:
        raise HTTPException(422, "block_id required")

    async with aiosqlite.connect(DB_PATH) as db:
        await _record_event(db, block_id, event_type, actor_id, payload)
        await db.commit()

    await _broadcast_gossip(f"block.{block_id}", {
        "type": event_type,
        "actor_id": actor_id,
        "payload": payload,
    })
    return {"ok": True, "event_type": event_type}
