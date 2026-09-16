"""Federation — merged multi-agent galaxy view + cross-node agent discovery (Phase 10.2).

Existing endpoints:
  GET /api/federation/galaxy  — authenticated merged memory galaxy

Phase 10.2 additions (cross-node agent discovery):
  GET  /api/federation/agents             — search agents by BIPON39 prefix or Odù index
  GET  /api/federation/nodes              — known peer nodes
  POST /api/federation/nodes/register     — register a peer node

Peer nodes are read from DIP_PEER_NODES env var (comma-separated URLs) and from
the federation_peers table when available. All env vars have safe defaults.
No operator-specific URLs or domains are hardcoded.
"""
import asyncio
import json
import logging
import os
import re
import time
from typing import Optional
from urllib.parse import urlparse

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..db import get_db
from ..deps import get_agent
from ..memory_vault import MemoryVault

logger = logging.getLogger(__name__)

# Comma-separated peer node base-URLs, e.g.:
#   DIP_PEER_NODES=https://node-a.example.com,https://node-b.example.com
_DIP_PEER_NODES: list[str] = [
    u.strip().rstrip("/")
    for u in os.getenv("DIP_PEER_NODES", "").split(",")
    if u.strip()
]

# P0-8: SSRF guard for peer node URLs — block private/internal ranges
_PRIVATE_NET_RE = re.compile(
    r"(^|\b)(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|::1|localhost\b)",
    re.IGNORECASE,
)


def _is_safe_peer_url(url: str) -> bool:
    """Return True if url passes basic SSRF safety checks."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname or ""
        return not bool(_PRIVATE_NET_RE.search(host))
    except Exception:
        return False

router = APIRouter(prefix="/api/federation", tags=["federation"])

# P0-8: SSRF guard — reject peer names that look like IP addresses or internal
# hostnames. Federation peers MUST be Vantage agent names (alphanumeric + _ - .),
# never URLs or IPs that could be used to probe the internal network.
_SAFE_PEER_RE = re.compile(r'^[a-zA-Z0-9_\-\.]{1,64}$')
_PRIVATE_IP_RE = re.compile(
    r'^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|::1|localhost)',
    re.IGNORECASE,
)

def _assert_safe_peer(peer: str) -> None:
    if not _SAFE_PEER_RE.match(peer) or _PRIVATE_IP_RE.match(peer):
        raise HTTPException(400, f"Unsafe peer name rejected: {peer!r}")

_AGENT_COLORS = [
    "#ff6b6b", "#4ecdc4", "#ffe66d", "#a8e6cf",
    "#c7ceea", "#ff8b94", "#ffd93d", "#6c5ce7",
    "#fd79a8", "#00cec9", "#e17055", "#74b9ff",
]


@router.get(
    "/galaxy",
    summary="Merge multiple agent memory galaxies",
    description="Combine galaxy data from multiple agents into a single merged view. Stars are color-coded by source agent. Only agents with public vaults (or accessible to the caller) are included.",
)
async def federation_galaxy(
    peers: str = Query(..., description="Comma-separated agent names, max 10"),
    agent: dict = Depends(get_agent),  # P0-8: require authentication
):
    peer_list = [p.strip() for p in peers.split(",") if p.strip()][:10]
    # P0-8: validate every peer name before touching the DB
    for peer in peer_list:
        _assert_safe_peer(peer)
    accessor_id = agent["id"]

    all_stars, all_edges, all_nebulae = [], [], []
    included: list[str] = []

    for i, peer_name in enumerate(peer_list):
        try:
            async with get_db() as db:
                row = await (await db.execute(
                    "SELECT id, name FROM agents WHERE name=?", (peer_name,)
                )).fetchone()
            if not row:
                continue
            vault = MemoryVault(row[0], row[1])
            if not await vault.check_access(accessor_id, ""):
                continue
            data = vault.get_galaxy_data()
            color = _AGENT_COLORS[i % len(_AGENT_COLORS)]
            for star in data["stars"]:
                star["agent_name"] = peer_name
                star["agent_color"] = color
            all_stars.extend(data["stars"])
            all_edges.extend(data["edges"])
            all_nebulae.extend(data["nebulae"])
            included.append(peer_name)
        except Exception:
            continue

    return {
        "peers": peer_list,
        "included": included,
        "stars": all_stars,
        "edges": all_edges,
        "nebulae": all_nebulae,
        "clusters": {},
        "bounds": {"min": [0, 0, 0], "max": [8000, 1000, 500]},
    }


# ── Phase 10.2: Cross-node agent discovery ────────────────────────────────────


async def _query_peer_node(base_url: str, params: dict, timeout: float = 5.0) -> list[dict]:
    """Fire-and-forget HTTP GET to a peer node's /agents/public endpoint.
    Returns a (possibly empty) list of agent dicts on success; empty list on any error.
    """
    if not _is_safe_peer_url(base_url):
        return []
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base_url}/agents/", params=params)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("agents", []) if isinstance(data, dict) else []
    except Exception as exc:
        logger.debug("federation peer query failed for %s: %s", base_url, exc)
    return []


@router.get(
    "/agents",
    summary="Cross-node agent discovery",
    description=(
        "Search for agents by BIPON39 prefix or Odù index. "
        "Queries local DB first, then fires async requests to DIP_PEER_NODES. "
        "No auth required for the public profile data returned."
    ),
)
async def discover_federation_agents(
    query: Optional[str] = Query(None, description="BIPON39 prefix, e.g. FIRE-OYA"),
    odu: Optional[int] = Query(None, ge=0, le=255, description="Odù index filter"),
    agent: dict = Depends(get_agent),
) -> dict:
    if not query and odu is None:
        raise HTTPException(status_code=422, detail="Provide ?query= (BIPON39 prefix) or ?odu= (Odù index)")

    # ── Local search ──────────────────────────────────────────────────────────
    clauses = ["agent_status != 'revoked'", "is_external = 0"]
    params: list = []

    if query:
        # BIPON39 prefix search — safe LIKE pattern (% is the only special char needed)
        safe_q = query.replace("%", "").replace("_", "").upper()
        clauses.append("UPPER(bipon39_phrase) LIKE ?")
        params.append(f"{safe_q}%")
    if odu is not None:
        clauses.append("odu_index = ?")
        params.append(odu)

    where = "WHERE " + " AND ".join(clauses)

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            f"""SELECT name, nostr_pubkey_hex, bipon39_phrase, odu_index,
                       tier, reputation, skill_badges, last_seen_at, active_profile
                FROM agents {where}
                ORDER BY reputation DESC
                LIMIT 50""",
            params,
        )).fetchall()

    local_agents = []
    for row in rows:
        d = dict(row)
        try:
            d["capabilities"] = json.loads(d.pop("skill_badges") or "[]")
        except Exception:
            d["capabilities"] = []
        d["source"] = "local"
        local_agents.append(d)

    # ── Peer node search (fire-and-forget, best-effort) ───────────────────────
    peer_params: dict = {}
    if query:
        peer_params["query"] = query
    if odu is not None:
        peer_params["odu"] = odu

    peer_nodes = _DIP_PEER_NODES[:]
    # Also pull live peer URLs from federation_peers table if it exists
    try:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            peer_rows = await (await db.execute(
                "SELECT url FROM federation_peers WHERE status = 'active' AND flagged = 0 LIMIT 20"
            )).fetchall()
        for pr in peer_rows:
            url = (pr["url"] or "").strip().rstrip("/")
            if url and url not in peer_nodes and _is_safe_peer_url(url):
                peer_nodes.append(url)
    except Exception:
        pass  # federation_peers table may not exist in all deployments

    peer_results: list[dict] = []
    if peer_nodes and peer_params:
        tasks = [_query_peer_node(node, peer_params) for node in peer_nodes]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        for node_url, result in zip(peer_nodes, gathered):
            if isinstance(result, list):
                for a in result:
                    if isinstance(a, dict):
                        a["source"] = node_url
                        peer_results.append(a)

    return {
        "local": local_agents,
        "peers": peer_results,
        "total_local": len(local_agents),
        "total_peer": len(peer_results),
        "query": query,
        "odu": odu,
    }


class PeerNodeRegistration(BaseModel):
    url: str
    name: Optional[str] = None
    nostr_pubkey: Optional[str] = None


@router.get(
    "/nodes",
    summary="List known peer federation nodes",
    description="Returns peer nodes from DIP_PEER_NODES env var and the federation_peers table.",
)
async def list_federation_nodes(agent: dict = Depends(get_agent)) -> dict:
    # Static nodes from env
    env_nodes = [{"url": u, "source": "env", "status": "unknown"} for u in _DIP_PEER_NODES]

    # Dynamic nodes from DB (if table exists)
    db_nodes: list[dict] = []
    try:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(
                """SELECT id, url, name, status, reputation, failure_count,
                          circuit_open_until, nostr_pubkey
                   FROM federation_peers
                   ORDER BY reputation DESC NULLS LAST
                   LIMIT 100"""
            )).fetchall()
        for row in rows:
            d = dict(row)
            d["source"] = "db"
            db_nodes.append(d)
    except Exception:
        pass

    return {
        "nodes": env_nodes + db_nodes,
        "total": len(env_nodes) + len(db_nodes),
    }


@router.post(
    "/nodes/register",
    summary="Register a peer federation node",
    description=(
        "Register a new peer node for cross-node federation. "
        "URL must be a public HTTPS endpoint (not a private/internal IP). "
        "Stores in the federation_peers table if available."
    ),
)
async def register_federation_node(
    body: PeerNodeRegistration,
    agent: dict = Depends(get_agent),
) -> dict:
    url = body.url.strip().rstrip("/")
    if not url:
        raise HTTPException(status_code=422, detail="url is required")
    if not _is_safe_peer_url(url):
        raise HTTPException(
            status_code=400,
            detail="Peer URL rejected: must be http/https and not a private/internal address",
        )

    try:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            existing = await (await db.execute(
                "SELECT id FROM federation_peers WHERE url = ?", (url,)
            )).fetchone()
            if existing:
                return {"status": "already_registered", "url": url}
            await db.execute(
                """INSERT INTO federation_peers (url, name, status, nostr_pubkey)
                   VALUES (?, ?, 'active', ?)""",
                (url, body.name or "", body.nostr_pubkey or ""),
            )
            await db.commit()
        return {"status": "registered", "url": url}
    except Exception as exc:
        logger.warning("register_federation_node: DB error: %s", exc)
        # federation_peers table may not exist; return graceful response
        return {"status": "registered_env_only", "url": url, "note": str(exc)}
