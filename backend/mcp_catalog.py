"""A category index over Vantage's own tool surface.

Vantage exposes ~1,046 MCP tools. No agent can hold that in context, and
dumping every schema into a request is not a tool loop, it is a denial of
service against the model's attention. This module makes the surface
*queryable* instead:

    GET /api/mcp/catalog/categories          what surface exists, grouped
    GET /api/mcp/catalog/search?q=&category= the few tools that matter now

Categories come from the FastAPI tag on each route, which is the same
grouping fastapi-mcp uses to expose the tools, so a category here is a real
section of the API rather than an invented taxonomy.

A tool's callable name is its OpenAPI operationId (what fastapi-mcp exposes).
Execution goes back over HTTP with the acting agent's own key, so Vantage's
own tools run with exactly the permissions that agent has - never elevated.
"""
from __future__ import annotations

import os
import re
import time
from typing import Optional

import httpx

from .agent_context import current_agent_key

SELF = os.environ.get("VANTAGE_SELF_URL", "http://127.0.0.1:8001").rstrip("/")
CACHE_TTL = float(os.environ.get("VANTAGE_CATALOG_TTL", "600"))
# Routes that must never be offered to an agent as tools.
HIDDEN_TAGS = {"admin", "debug", "internal"}
# Plumbing that is not a business tool: OAuth dance, health probes, docs.
# fastapi-mcp omits these from the tool surface, so offering them to an agent
# would hand it names the MCP client cannot see.
HIDDEN_PATH_PREFIXES = ("/oauth/", "/.well-known/", "/api/health", "/api/alpha",
                        "/openapi.json", "/docs", "/redoc",
                        "/api/agents/mcp/manifest", "/api/platform/capacity",
                        "/api/platform/weather", "/api/rpc")

_cache: dict = {"at": 0.0, "tools": [], "cats": {}}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _tool_name(method: str, path: str, op: dict) -> str:
    oid = op.get("operationId")
    if oid:
        return oid
    return _slug(f"{method}_{path}").replace("-", "_")


async def load(force: bool = False) -> tuple[list[dict], dict[str, list[dict]]]:
    if not force and (time.time() - _cache["at"]) < CACHE_TTL and _cache["tools"]:
        return _cache["tools"], _cache["cats"]

    async with httpx.AsyncClient(timeout=20.0) as cl:
        r = await cl.get(f"{SELF}/openapi.json")
        r.raise_for_status()
        spec = r.json()

    tools: list[dict] = []
    cats: dict[str, list[dict]] = {}
    for path, ops in (spec.get("paths") or {}).items():
        for method, op in ops.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            raw_tags = op.get("tags") or []
            # A route is hidden if ANY of its tags is sensitive. Filtering only
            # the first tag let admin routes through under a second tag, which
            # produced 112 catalogue entries that the MCP surface deliberately
            # does not expose.
            if any(t in HIDDEN_TAGS for t in raw_tags):
                continue
            if any(path.startswith(pre) for pre in HIDDEN_PATH_PREFIXES):
                continue
            # SPA catch-alls and static handlers are not tools.
            if not path.startswith("/api/"):
                continue
            # Not part of the agent-facing surface on this instance.
            low = path.lower()
            if "mcp/manifest" in low or "telegram" in low:
                continue
            tags = [t for t in (raw_tags or ["untagged"]) if t not in HIDDEN_TAGS]
            if not tags:
                continue
            name = _tool_name(method, path, op)
            if "manifest" in name.lower() or "manifest" in path.lower():
                continue
            entry = {
                "name": name,
                "method": method.upper(),
                "path": path,
                "summary": (op.get("summary") or op.get("description") or "")[:300],
                "category": tags[0],
                "categories": tags,
                "params": list((op.get("parameters") or [])),
                "has_body": bool(op.get("requestBody")),
            }
            tools.append(entry)
            for t in tags:
                cats.setdefault(t, []).append(entry)

    _cache.update({"at": time.time(), "tools": tools, "cats": cats})
    return tools, cats


async def categories() -> list[dict]:
    tools, cats = await load()
    return sorted(
        ({"category": k, "count": len(v)} for k, v in cats.items()),
        key=lambda x: -x["count"],
    )


def _score(tool: dict, words: list[str]) -> int:
    hay = (tool["name"] + " " + tool["summary"] + " " + tool["path"]).lower()
    return sum(1 for w in words if w in hay)


async def search(q: str, category: Optional[str] = None, limit: int = 12) -> list[dict]:
    tools, cats = await load()
    pool = cats.get(category, []) if category else tools
    if not q:
        return pool[:limit]
    words = [w for w in re.split(r"\W+", q.lower()) if len(w) > 2]
    scored = [(t, _score(t, words)) for t in pool]
    hits = [t for t, s in sorted(scored, key=lambda x: -x[1]) if s > 0]
    return hits[:limit]


async def call_tool(tool: dict, arguments: dict, agent_key: str = "") -> dict:
    """Execute one catalogued Vantage tool as the acting agent."""
    key = agent_key or current_agent_key.get()
    if not key:
        return {"ok": False, "error": "no agent key in context"}
    args = dict(arguments or {})
    path = tool["path"]
    for p in re.findall(r"\{(\w+)\}", path):
        if p in args:
            path = path.replace("{" + p + "}", str(args.pop(p)))
    url = f"{SELF}{path}"
    try:
        async with httpx.AsyncClient(timeout=45.0) as cl:
            if tool["method"] in ("GET", "DELETE"):
                r = await cl.request(tool["method"], url, params=args,
                                     headers={"X-Agent-Key": key})
            else:
                r = await cl.request(tool["method"], url, json=args,
                                     headers={"X-Agent-Key": key})
        body = r.text[:3000]
        return {"ok": r.status_code < 400, "status": r.status_code, "body": body}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
