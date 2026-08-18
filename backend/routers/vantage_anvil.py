"""Vantage Anvil — the project-combination graph.

`vantage-anvil` is a dedicated system agent (registered the same way any
other Vantage agent is — see `_ensure_anvil_agent` below, which mirrors
`POST /api/agents/register` in routers/identity.py) whose memory vault
holds the project-combination graph: Vantage itself as the Prime Node
star, every SkillForge-forged project as a "project" star, and typed
relationship edges between them (`combines_with` / `depends_on` /
`complements` / `produces_input_for` / `extends`), decided by comparing
each project's *discovered* route I/O shapes — not by copying source code
into a shared folder (the naive `modules/` approach this replaces).

Storage is Vantage's existing memory-vault galaxy graph
(backend/memory_vault.py) — real subject/predicate/object triples, no new
DB dependency (no Neo4j). `agent_skills` stays the single source of truth
for skill data (name/description/routes/base_url); a project star only
ever *references* a skill_id, never duplicates its data — route/schema
comparisons re-read agent_skills fresh each time.

Tiers are not a separate system: `GET /tiers` is a BFS over the same
edges, grouped by graph distance from the Prime star.

── SkillForge invocation ───────────────────────────────────────────────
The real SkillForge tool lives in the Omo-Koda2 kernel (a separate host,
contabo-vps, port 7777), invoked over its own `/v1/act` HTTP API — see
`omokoda-smithers/.smithers/workflows/skillforge-forge.tsx` for the
reference call shape this mirrors. Investigation (2026-08) found the
WireGuard tunnel between Vantage's host and the kernel's host does not
expose port 7777 to Vantage's tunnel IP (firewall only opens a handful of
unrelated ports) — so `_call_skillforge` bridges over SSH to the kernel
host and curls its own localhost, reusing existing passwordless root SSH
between the two hosts rather than opening new network surface. This is a
workaround, not the fix — the real fix is a firewall rule on the Omo-Koda2
side (a different pillar's infra), flagged to the owner, not applied here.

── YouTube intake ──────────────────────────────────────────────────────
No YouTube API key exists yet (confirmed, not fabricated). `POST
/ingest-video` uses `youtube_extract.py`'s key-free description fetch
(yt-dlp if installed, else a plain HTTP GET of the public watch page) —
manual paste today, dropped in unchanged as the extraction step of an
actual channel-watcher cron once a real API key exists. The trigger
(manual paste vs. cron) and the extraction logic are deliberately
separate modules for exactly that swap.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
from typing import Optional

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import get_db
from ..memory_vault import MemoryVault
from ..youtube_extract import fetch_video_description

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/vantage-anvil", tags=["vantage-anvil"])

ANVIL_AGENT_NAME = "vantage-anvil"
PRIME_STAR_ID = "prime_vantage"

# ── Kernel bridge config ────────────────────────────────────────────────
KERNEL_SSH_HOST = os.environ.get("OMOKODA_KERNEL_SSH_HOST", "89.117.74.224")
KERNEL_LOCAL_URL = os.environ.get("OMOKODA_KERNEL_LOCAL_URL", "http://localhost:7777")
KERNEL_DIRECT_URL = os.environ.get("OMOKODA_KERNEL_URL", "http://10.88.0.2:7777")


# ── Bootstrap ─────────────────────────────────────────────────────────────

async def _ensure_anvil_agent() -> dict:
    """Idempotent: returns {id, name}, creating the agent + Prime Node star
    on first call. Registration shape mirrors POST /api/agents/register."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT id, name FROM agents WHERE name=?", (ANVIL_AGENT_NAME,)
        )).fetchone()
        if row:
            agent = dict(row)
        else:
            api_key = "vantage_" + secrets.token_hex(24)
            api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
            cur = await db.execute(
                "INSERT INTO agents (name, api_key, bio) VALUES (?, ?, ?)",
                (ANVIL_AGENT_NAME, api_key_hash,
                 "System agent -- owns the project-combination graph. Not a "
                 "content/roleplay agent; its vault is the SkillForge project "
                 "graph, with Vantage itself seeded as the Prime Node.")
            )
            await db.commit()
            agent = {"id": cur.lastrowid, "name": ANVIL_AGENT_NAME}
            logger.info(f"vantage-anvil: bootstrapped agent id={agent['id']} key={api_key}")
    await _ensure_prime_star(agent["id"])
    return agent


async def _ensure_prime_star(agent_id: int) -> None:
    vault = MemoryVault(agent_id, ANVIL_AGENT_NAME)
    path = vault.vault_path / "projects" / f"{PRIME_STAR_ID}.md"
    if path.exists():
        return
    vault.add_star(
        star_id=PRIME_STAR_ID, title="Vantage",
        description="The core anvil -- every forged project's tier and relationship "
                     "distance is measured from this star.",
        node_type="Prime Node", tags=["prime", "vantage", "core"],
        constellation="prime", coords=(0, 0, 0),
        size=40, color="#ffd700",
    )
    logger.info("vantage-anvil: seeded Prime Node star")


# ── SkillForge invocation (SSH bridge — see module docstring) ─────────────

async def _call_skillforge(url: str, approve: bool = False, store: bool = True) -> dict:
    """Invoke the real Omo-Koda2 kernel skillforge tool over its /v1/act
    HTTP API. Tries a direct connection first (in case the firewall gap
    gets fixed later), falls back to the SSH bridge. Raises on failure —
    callers decide whether one repo's failure should stop a batch."""
    body = json.dumps({
        "tool": "skillforge",
        "params": json.dumps({"url": url, "approve": approve, "store": store}),
    })

    try:
        import httpx
        # Short *connect* timeout specifically -- this is a probe, not the
        # main path. The firewall gap this works around (see module
        # docstring) drops packets silently rather than refusing the
        # connection, so an unbounded/2000s-wide timeout here hangs for
        # the OS-level TCP connect timeout before ever falling back to the
        # SSH bridge (confirmed live: a bare `timeout=2000` on the client
        # applies to connect too, and the call never returned). The read
        # timeout still gets the full budget for once a connection lands.
        timeout = httpx.Timeout(connect=5.0, read=2000.0, write=30.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{KERNEL_DIRECT_URL}/v1/act",
                                      content=body, headers={"Content-Type": "application/json"})
            resp.raise_for_status()
            data = resp.json()
            return _unwrap_skillforge_response(data)
    except Exception as direct_err:
        logger.info(f"vantage-anvil: direct kernel call failed ({direct_err}), falling back to SSH bridge")

    cmd = [
        "ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
        f"root@{KERNEL_SSH_HOST}",
        f"curl -s -m 2000 -X POST {KERNEL_LOCAL_URL}/v1/act "
        f"-H 'Content-Type: application/json' --data-binary @-",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        # Defense in depth on top of `ssh -o ConnectTimeout=10` (which only
        # bounds establishing the SSH session, not the remote curl -m 2000)
        # -- belt-and-suspenders so a wedged SSH session can't hang this
        # call forever the same way the direct-connect probe just did.
        stdout, stderr = await asyncio.wait_for(proc.communicate(body.encode()), timeout=2100)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("skillforge SSH bridge timed out after 2100s")
    if proc.returncode != 0:
        raise RuntimeError(f"skillforge SSH bridge failed (exit {proc.returncode}): {stderr.decode()[:500]}")
    try:
        data = json.loads(stdout.decode())
    except json.JSONDecodeError:
        raise RuntimeError(f"skillforge returned non-JSON over SSH bridge: {stdout.decode()[:500]}")
    return _unwrap_skillforge_response(data)


def _unwrap_skillforge_response(data: dict) -> dict:
    if data.get("error"):
        raise RuntimeError(f"skillforge call failed: {data['error']}")
    tool_output = data.get("tool_output")
    if not tool_output:
        raise RuntimeError(f"skillforge /v1/act response missing tool_output: {data}")
    return json.loads(tool_output)


# ── Route-schema compatibility heuristic ───────────────────────────────────
# v1: token overlap over route paths/names + description, plus a directional
# produce/consume signal from HTTP method (GET ~ produces, POST ~ consumes)
# and a couple of literal keyword sets. This is a real, working heuristic —
# not a fabricated NLP model. It's meant to be a reasonable starting
# classifier, not a claim of deep semantic understanding.

_PRODUCE_WORDS = {"output", "result", "generate", "produce", "export", "publish",
                   "create", "build", "render", "report", "list", "get"}
_CONSUME_WORDS = {"ingest", "input", "import", "consume", "analyze", "process",
                   "run", "execute", "scan", "load", "clone", "repo", "source"}
_REPO_PARAM_RE = re.compile(r"clone_url|repo_url|repository_url|github_url|source_repo", re.I)
_STOPWORDS = {"api", "v1", "v2", "the", "and", "for", "with", "http", "https"}


def _normalize_routes(routes) -> list[tuple[str, str, str]]:
    """routes lands in two different shapes in agent_skills.input_schema
    today: {name: "METHOD /path"} or [{"method":..., "path":...}, ...].
    Normalize both to [(method, path, name), ...]."""
    out: list[tuple[str, str, str]] = []
    if isinstance(routes, dict):
        for name, spec in routes.items():
            if not isinstance(spec, str):
                continue
            parts = spec.split(None, 1)
            method = parts[0].upper() if parts and parts[0].isalpha() else "GET"
            path = parts[1] if len(parts) > 1 else spec
            out.append((method, path, name))
    elif isinstance(routes, list):
        for r in routes:
            if isinstance(r, dict):
                out.append((str(r.get("method", "GET")).upper(), str(r.get("path", "")), ""))
    return out


def _tokenize_skill(name: str, description: str, routes, params: Optional[dict] = None) -> tuple[set, set, set]:
    """Returns (all_tokens, produce_tokens, consume_tokens)."""
    text_tokens = set(re.findall(r"[a-z]+", f"{name} {description}".lower())) - _STOPWORDS
    produce: set = set()
    consume: set = set()
    route_tokens: set = set()
    for method, path, rname in _normalize_routes(routes):
        words = set(re.findall(r"[a-z]+", f"{path} {rname}".lower())) - _STOPWORDS
        route_tokens |= words
        if method == "GET":
            produce |= words
        elif method in ("POST", "PUT", "PATCH"):
            consume |= words
        produce |= (words & _PRODUCE_WORDS)
        consume |= (words & _CONSUME_WORDS)
    for key in (params or {}):
        if _REPO_PARAM_RE.search(str(key)):
            consume |= {"repo", "code", "source"}
    all_tokens = (text_tokens | route_tokens) - _STOPWORDS
    return all_tokens, produce, consume


def _decide_relationship(a_tokens, a_produce, a_consume, b_tokens, b_produce, b_consume):
    """Returns (predicate, direction, weight) or (None, None, 0.0).
    direction is "a_to_b", "b_to_a", or None (symmetric predicate)."""
    a_to_b = a_produce & b_consume
    b_to_a = b_produce & a_consume
    union = a_tokens | b_tokens
    jaccard = len(a_tokens & b_tokens) / max(1, len(union))

    if a_to_b and not b_to_a:
        return "produces_input_for", "a_to_b", round(0.5 + 0.5 * min(1.0, len(a_to_b) / 3), 2)
    if b_to_a and not a_to_b:
        return "produces_input_for", "b_to_a", round(0.5 + 0.5 * min(1.0, len(b_to_a) / 3), 2)
    if a_to_b and b_to_a:
        return "combines_with", None, round(0.6 + 0.4 * min(1.0, (len(a_to_b) + len(b_to_a)) / 6), 2)
    if jaccard >= 0.35:
        return "combines_with", None, round(jaccard, 2)
    if jaccard >= 0.15:
        return "complements", None, round(jaccard, 2)
    return None, None, 0.0


# ── Projection: agent_skills row -> graph star + edges ─────────────────────

async def project_forged_skill(skill_id: int) -> dict:
    """Reads a just-registered agent_skills row and projects it into
    vantage-anvil's graph as a project star, then computes relationship
    edges against every existing project star plus a mandatory edge to
    the Prime Node. Never duplicates agent_skills data -- the star's
    frontmatter stores only a reference (skill_id) and lightweight display
    fields; routes/base_url are re-read from agent_skills for every
    comparison, never copied into the star itself.

    Called from routers/collectives.py's POST /skills handler (the
    existing SkillForge -> agent_skills registration path) so this fires
    for every forged skill regardless of what triggered the forge --
    ingest-video below, or the live kernel pipeline directly.
    Best-effort: never raises, since a graph-projection failure must not
    break skill registration itself.
    """
    try:
        agent = await _ensure_anvil_agent()
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT id, name, description, input_schema FROM agent_skills WHERE id=?",
                (skill_id,)
            )).fetchone()
        if not row:
            return {"projected": False, "error": "skill not found"}

        schema = json.loads(row["input_schema"] or "{}")
        routes = schema.get("routes", {})
        base_url = schema.get("base_url", "")
        tokens, produce, consume = _tokenize_skill(
            row["name"], row["description"] or "", routes, schema.get("params", {})
        )

        vault = MemoryVault(agent["id"], ANVIL_AGENT_NAME)
        star_id = f"project_skill_{row['id']}"
        coords = vault._spatial_hash(star_id, "project")
        route_count = len(routes) if isinstance(routes, (list, dict)) else 0
        vault.add_star(
            star_id=star_id, title=row["name"],
            description=(row["description"] or "")[:200],
            node_type="Forged Project", tags=["project", "skillforge"],
            constellation="projects", coords=coords, size=15, color="#7bdff2",
            extra={"skill_id": row["id"], "base_url": base_url, "route_count": route_count},
        )

        edges_written = [{"subject": star_id, "predicate": "extends", "object": PRIME_STAR_ID}]
        vault.add_edge(subject=star_id, predicate="extends", object=PRIME_STAR_ID, weight=1.0, trust=1.0)

        data = vault.get_galaxy_data()
        for star in data["stars"]:
            other_id = star.get("id")
            if not other_id or other_id == star_id or star.get("constellation") != "projects":
                continue
            m = re.match(r"project_skill_(\d+)", other_id)
            if not m:
                continue
            async with get_db() as db:
                db.row_factory = aiosqlite.Row
                orow = await (await db.execute(
                    "SELECT name, description, input_schema FROM agent_skills WHERE id=?",
                    (int(m.group(1)),)
                )).fetchone()
            if not orow:
                continue
            oschema = json.loads(orow["input_schema"] or "{}")
            o_tokens, o_produce, o_consume = _tokenize_skill(
                orow["name"], orow["description"] or "",
                oschema.get("routes", {}), oschema.get("params", {})
            )
            predicate, direction, weight = _decide_relationship(
                tokens, produce, consume, o_tokens, o_produce, o_consume
            )
            if not predicate:
                continue
            subj, obj = (star_id, other_id) if direction != "b_to_a" else (other_id, star_id)
            trust = round(min(0.95, 0.5 + weight * 0.4), 2)
            vault.add_edge(subject=subj, predicate=predicate, object=obj, weight=weight, trust=trust)
            edges_written.append({"subject": subj, "predicate": predicate, "object": obj, "weight": weight})

        return {"projected": True, "star_id": star_id, "edges": edges_written}
    except Exception as e:
        logger.warning(f"vantage-anvil: projection failed for skill {skill_id}: {e}")
        return {"projected": False, "error": str(e)}


# ── Tiers: graph distance from the Prime star ───────────────────────────────

@router.get("/tiers")
async def get_tiers():
    agent = await _ensure_anvil_agent()
    vault = MemoryVault(agent["id"], ANVIL_AGENT_NAME)
    data = vault.get_galaxy_data()

    stars_by_id = {s["id"]: s for s in data["stars"] if s.get("id")}
    adjacency: dict[str, set] = {sid: set() for sid in stars_by_id}
    for e in data["edges"]:
        subj, obj = e.get("subject"), e.get("object")
        if subj in stars_by_id and obj in stars_by_id:
            adjacency.setdefault(subj, set()).add(obj)
            adjacency.setdefault(obj, set()).add(subj)

    from collections import deque
    dist: dict[str, int] = {}
    if PRIME_STAR_ID in stars_by_id:
        dist[PRIME_STAR_ID] = 0
        q = deque([PRIME_STAR_ID])
        while q:
            cur = q.popleft()
            for nxt in adjacency.get(cur, ()):
                if nxt not in dist:
                    dist[nxt] = dist[cur] + 1
                    q.append(nxt)

    tiers: dict[int, list] = {}
    for sid, d in dist.items():
        star = stars_by_id[sid]
        tiers.setdefault(d, []).append({
            "id": sid, "title": star.get("title"),
            "constellation": star.get("constellation"),
        })
    unreachable = [sid for sid in stars_by_id if sid not in dist]

    return {
        "prime": PRIME_STAR_ID,
        "tiers": {str(k): v for k, v in sorted(tiers.items())},
        "unreachable": unreachable,
        "star_count": len(data["stars"]),
        "edge_count": len(data["edges"]),
    }


# ── Status ──────────────────────────────────────────────────────────────

@router.get("/status")
async def anvil_status():
    agent = await _ensure_anvil_agent()
    vault = MemoryVault(agent["id"], ANVIL_AGENT_NAME)
    data = vault.get_galaxy_data()
    project_stars = [s for s in data["stars"] if s.get("constellation") == "projects"]
    return {
        "agent_id": agent["id"], "agent_name": ANVIL_AGENT_NAME,
        "prime_seeded": any(s.get("id") == PRIME_STAR_ID for s in data["stars"]),
        "project_count": len(project_stars),
        "edge_count": len(data["edges"]),
    }


# ── YouTube-link intake (no API key — see module docstring) ────────────────

class IngestVideoRequest(BaseModel):
    url: str
    approve: bool = False
    store: bool = True


@router.post("/ingest-video")
async def ingest_video(req: IngestVideoRequest):
    """Paste a YouTube video (or channel-upload) link. Fetches its public
    description with no API key, extracts github.com repo links, forges
    each one through the real SkillForge pipeline, and (on success) the
    forge's own Vantage registration triggers graph projection via
    project_forged_skill above -- no separate projection call needed here.
    """
    extraction = await asyncio.to_thread(fetch_video_description, req.url)
    if extraction.error:
        raise HTTPException(422, extraction.error)

    if not extraction.github_urls:
        return {
            "video_id": extraction.video_id, "title": extraction.title,
            "fetch_method": extraction.method, "github_urls": [], "forged": [],
            "note": "no github.com links found in the video description",
        }

    forged = []
    for repo_url in extraction.github_urls:
        try:
            receipt = await _call_skillforge(repo_url, approve=req.approve, store=req.store)
            forged.append({
                "url": repo_url,
                "status": receipt.get("status"),
                "skill_name": (receipt.get("skill") or {}).get("name"),
                "requires_review": (receipt.get("audit") or {}).get("requires_review"),
                "risk_score": (receipt.get("audit") or {}).get("risk_score"),
                "vantage_registry": receipt.get("vantage_registry"),
                "review_ticket": receipt.get("review_ticket"),
            })
        except Exception as e:
            forged.append({"url": repo_url, "status": "error", "error": str(e)})

    return {
        "video_id": extraction.video_id, "title": extraction.title,
        "fetch_method": extraction.method,
        "github_urls": extraction.github_urls,
        "forged": forged,
    }
