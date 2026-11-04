"""Give an agent a real, multi-source MCP tool loop.

Three tool sources, one loop:

    1. Vantage's own surface  - selected per turn from the category catalog
                                (mcp_catalog), never all 1,046 at once
    2. External MCP servers   - the agent's connected servers (mcp_hub),
                                e.g. Phantom docs, a video MCP, a trading MCP
    3. Composio               - the agent's CONNECTED toolkits only, so a
                                connected HeyGen/Higgsfield/Slack account is
                                used automatically when the request matches

Shape:

    user text -> model (tools attached) -> tool_calls -> execute -> model
              -> ... up to MAX_STEPS -> final assistant text

Selection, not enumeration, is the point: Vantage's surface is queried by
keyword and category and only the best few are attached. External and
Composio tools are cheap to enumerate per agent, so they are included whole.

Any failure returns None so the caller degrades to plain conversation rather
than surfacing a tool error to a voice user.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

import logging

import httpx

from . import mcp_catalog, mcp_hub

log = logging.getLogger(__name__)
from .db import get_db

GATEWAY_URL = os.environ.get("VANTAGE_OMNIROUTE_URL", "http://127.0.0.1:8300").rstrip("/")
GATEWAY_KEY = os.environ.get("VANTAGE_OMNIROUTE_API_KEY", "")
TOOL_MODEL = os.environ.get("VANTAGE_TOOL_MODEL", "deepseek/deepseek-v4-flash")
MAX_STEPS = int(os.environ.get("VANTAGE_TOOL_MAX_STEPS", "3"))
CACHE_TTL = float(os.environ.get("VANTAGE_TOOL_CACHE_TTL", "300"))
MAX_VANTAGE_TOOLS = int(os.environ.get("VANTAGE_TOOL_MAX_VANTAGE", "10"))
MAX_MCP_TOOLS = int(os.environ.get("VANTAGE_TOOL_MAX_MCP", "60"))
MAX_COMPOSIO_TOOLS = int(os.environ.get("VANTAGE_TOOL_MAX_COMPOSIO", "30"))

_cache: dict[int, tuple[float, list[dict], dict[str, dict]]] = {}


# ── source 1: the agent's external MCP servers ─────────────────────────────
async def _servers(agent_id: int) -> list[dict]:
    async with get_db() as db:
        db.row_factory = None
        cur = await db.execute("SELECT * FROM agent_mcp_servers WHERE agent_id=?", (agent_id,))
        rows = await cur.fetchall()
        names = [d[0] for d in (cur.description or [])]
    return [dict(zip(names, r)) for r in rows]


async def _mcp_toolset(agent_row: dict) -> tuple[list[dict], dict[str, dict]]:
    agent_id = int(agent_row["id"])
    hit = _cache.get(agent_id)
    if hit and (time.time() - hit[0]) < CACHE_TTL:
        return hit[1], hit[2]

    schemas: list[dict] = []
    dispatch: dict[str, dict] = {}
    for srv in await _servers(agent_id):
        tools = []
        raw = srv.get("tools_json")
        if raw:
            try:
                tools = json.loads(raw)
            except Exception:
                tools = []
        if not tools:
            try:
                res = await mcp_hub.probe(agent_id, srv)
            except Exception:
                continue
            if not res.get("ok"):
                continue
            tools = res.get("tools") or []

        for t in tools:
            if len(schemas) >= MAX_MCP_TOOLS:
                break
            fname = f"s{srv['id']}__{t['name']}"[:64]
            schema = t.get("input_schema") or {}
            if not isinstance(schema, dict) or schema.get("type") != "object":
                schema = {"type": "object", "properties": {}}
            schemas.append({
                "type": "function",
                "function": {
                    "name": fname,
                    "description": (t.get("description") or t["name"])[:400],
                    "parameters": schema,
                },
            })
            dispatch[fname] = {"kind": "mcp", "server": srv, "tool": t["name"]}

    _cache[agent_id] = (time.time(), schemas, dispatch)
    return schemas, dispatch


# ── source 2: Composio, connected toolkits only ────────────────────────────
async def _composio_toolset(agent_row: dict) -> tuple[list[dict], dict[str, dict]]:
    schemas: list[dict] = []
    dispatch: dict[str, dict] = {}
    try:
        # Import must survive however this module is loaded: as
        # backend.mcp_agent_tools, as mcp_agent_tools from inside backend/, or
        # through uvicorn's package root. A bare relative import raised
        # "beyond top-level package" in production and silently disabled
        # every Composio toolkit.
        try:
            from ..routers import composio as cx
        except ImportError:
            try:
                from backend.routers import composio as cx
            except ImportError:
                from routers import composio as cx
        client = cx._get_client()          # raises if COMPOSIO_API_KEY is unset
        user_id = cx._composio_user_id(agent_row)
        conns = await cx.list_connections(agent_row)
    except Exception as exc:
        # Never silent: a swallowed error here makes every Composio toolkit
        # look absent, which is indistinguishable from "nothing connected".
        log.warning("composio toolset unavailable: %s: %s", type(exc).__name__, exc)
        return schemas, dispatch

    # Composio returns camelCase here (toolkitSlug/status). Reading snake_case
    # silently matched nothing, so every toolkit looked disconnected.
    connected, real = set(), set()
    for c in (conns or {}).get("connections", []) if isinstance(conns, dict) else []:
        slug = (c.get("toolkit_slug") or c.get("toolkitSlug")
                or c.get("toolkit") or "").lower()
        status = (c.get("status") or "").upper()
        # EXPIRED accounts cannot execute; only live ones get tools attached.
        if slug and status in ("ACTIVE", "CONNECTED", ""):
            connected.add(slug)
            real.add(slug)

    # Toolkits that need no OAuth have no connected-account row, so relying on
    # connections alone silently hid them. They execute with just a user_id
    # (verified: HACKERNEWS_GET_TOP_STORIES returns live data unconnected).
    noauth = [s.strip().lower() for s in
              os.environ.get("VANTAGE_COMPOSIO_NOAUTH",
                             "hackernews,text_to_pdf,weathermap,composio_search").split(",") if s.strip()]
    connected.update(noauth)

    # A user-connected toolkit outranks a free no-auth one, and each gets a
    # share of the budget: a global first-come cap let composio_search consume
    # all 30 slots and hid every other source.
    ordered = sorted(real) + sorted(connected - real)
    budget = max(4, MAX_COMPOSIO_TOOLS // max(1, len(ordered)))

    for slug in ordered:
        try:
            payload = await cx.list_toolkit_tools(slug, agent_row)
            tools = payload.get("tools", payload) if isinstance(payload, dict) else payload
        except Exception:
            continue
        taken = 0
        for t in (tools or []):
            if taken >= budget or len(schemas) >= MAX_COMPOSIO_TOOLS:
                break
            tslug = t.get("slug") or t.get("name") or ""
            if not tslug:
                continue
            fname = f"composio__{tslug}"[:64]
            params = t.get("parameters") or t.get("input_parameters") or {}
            if not isinstance(params, dict) or params.get("type") != "object":
                params = {"type": "object", "properties": params if isinstance(params, dict) else {}}
            schemas.append({
                "type": "function",
                "function": {
                    "name": fname,
                    "description": (t.get("description") or tslug)[:400],
                    "parameters": params,
                },
            })
            taken += 1
            dispatch[fname] = {"kind": "composio", "client": client,
                               "user_id": user_id, "slug": tslug}
    return schemas, dispatch


# ── source 3: Vantage's own surface, selected by the current request ───────
async def _vantage_toolset(text: str) -> tuple[list[dict], dict[str, dict]]:
    schemas: list[dict] = []
    dispatch: dict[str, dict] = {}
    try:
        hits = await mcp_catalog.search(text, limit=MAX_VANTAGE_TOOLS)
    except Exception:
        return schemas, dispatch

    for t in hits:
        fname = f"vantage__{t['name']}"[:64]
        props: dict = {}
        required: list[str] = []
        for p in t.get("params") or []:
            name = p.get("name")
            if not name:
                continue
            props[name] = {"type": (p.get("schema") or {}).get("type", "string"),
                           "description": (p.get("description") or "")[:200]}
            if p.get("required"):
                required.append(name)
        schema = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        schemas.append({
            "type": "function",
            "function": {
                "name": fname,
                "description": (t.get("summary") or t["name"])[:400],
                "parameters": schema,
            },
        })
        dispatch[fname] = {"kind": "vantage", "tool": t}
    return schemas, dispatch


# ── execution ──────────────────────────────────────────────────────────────
def _flatten_mcp(result: dict) -> str:
    if not result.get("ok"):
        return f"tool error: {result.get('error') or result.get('content')}"
    parts = [str(c["text"]) for c in (result.get("content") or []) if c.get("text")]
    if result.get("structured"):
        parts.append(json.dumps(result["structured"])[:2000])
    return ("\n".join(parts) or "(no output)")[:4000]


async def _execute(target: dict, args: dict, agent_row: dict) -> str:
    kind = target["kind"]
    if kind == "mcp":
        res = await mcp_hub.call_tool(int(agent_row["id"]), target["server"], target["tool"], args)
        return _flatten_mcp(res)
    if kind == "composio":
        try:
            res = target["client"].tools.execute(
                target["slug"], user_id=target["user_id"], arguments=args,
                dangerously_skip_version_check=True,
            )
            return json.dumps(res)[:4000]
        except Exception as exc:
            return f"composio error: {exc}"
    if kind == "custom":
        try:
            try:
                from .. import custom_tools as ct
            except ImportError:
                from backend import custom_tools as ct
            return (await ct.execute_action(target, args))[:4000]
        except Exception as exc:
            return f"integration error: {exc}"
    if kind == "vantage":
        res = await mcp_catalog.call_tool(target["tool"], args)
        return (res.get("body") if res.get("ok") else f"error: {res.get('error') or res.get('body')}")[:4000]
    return "unknown tool"


async def _anchor(agent_row: dict, tool_name: str, output: str) -> None:
    """Write an action receipt for a consequential tool call.

    Receipts are the accountability primitive: the agent may be unpredictable,
    it must not be unaccountable. Before this, justice_layer was only reached
    from the (gate-off) trading path, so action_receipts stayed at zero rows.
    """
    try:
        try:
            from .. import justice_layer as jl
        except ImportError:
            from backend import justice_layer as jl
    except Exception as exc:
        log.warning("receipts unavailable: %s: %s", type(exc).__name__, exc)
        return
    try:
        ok = not str(output).lower().startswith(("tool error", "error:", "request failed",
                                                 "integration error", "composio error"))
        await jl.anchor_receipt(
            principal_id=f"agent:{agent_row['id']}",
            action_kind=tool_name,
            capability="mcp_tool",
            evidence={"tool": tool_name, "ok": ok, "chars": len(str(output))},
            kind="mcp_tool_call",
            action_target=tool_name[:200],
            action_outcome="success" if ok else "error",
        )
    except Exception as exc:
        log.warning("receipt write failed for %s: %s: %s",
                    tool_name, type(exc).__name__, exc)


async def run_tool_loop(agent_row: dict, text: str) -> Optional[str]:
    """Return the assistant's final text, or None if no tools apply/failed."""
    if not GATEWAY_KEY:
        return None

    mcp_s, mcp_d = await _mcp_toolset(agent_row)
    comp_s, comp_d = await _composio_toolset(agent_row)
    van_s, van_d = await _vantage_toolset(text)

    # user-defined HTTP integrations and standing skills
    int_s, int_d, skills = [], {}, ""
    try:
        try:
            from .. import custom_tools as ct
        except ImportError:
            from backend import custom_tools as ct
        int_s, int_d = await ct.integration_toolset(int(agent_row["id"]))
        skills = await ct.skills_prompt(int(agent_row["id"]))
    except Exception as exc:
        log.warning("custom tools unavailable: %s: %s", type(exc).__name__, exc)

    schemas = van_s + mcp_s + comp_s + int_s
    dispatch = {**van_d, **mcp_d, **comp_d, **int_d}
    if not schemas:
        return None

    name = agent_row.get("name", "the agent")
    system = (f"You are {name}'s assistant on Vantage. Use the provided tools when they can "
              f"answer the request; otherwise answer directly. Be concise - replies may be "
              f"spoken aloud.")
    if skills:
        system = system + "\n\n" + skills
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]

    for _ in range(MAX_STEPS):
        try:
            async with httpx.AsyncClient(timeout=60.0) as cl:
                r = await cl.post(
                    f"{GATEWAY_URL}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
                    json={"model": TOOL_MODEL, "messages": messages,
                          "tools": schemas, "tool_choice": "auto", "max_tokens": 900},
                )
            if r.status_code != 200:
                return None
            msg = (r.json().get("choices") or [{}])[0].get("message") or {}
        except Exception:
            return None

        calls = msg.get("tool_calls") or []
        if not calls:
            content = (msg.get("content") or "").strip()
            return content or None

        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": calls})
        for call in calls:
            fn = call.get("function") or {}
            fname = fn.get("name") or ""
            target = dispatch.get(fname)
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            if target:
                try:
                    out = await _execute(target, args, agent_row)
                    await _anchor(agent_row, fname, out)
                except Exception as exc:
                    # one broken tool must not abort the whole turn
                    log.warning("tool %s failed: %s: %s", fname, type(exc).__name__, exc)
                    out = f"tool {fname} failed: {exc}"
            else:
                out = f"unknown tool {fname}"
            messages.append({"role": "tool", "tool_call_id": call.get("id") or fname,
                             "content": out})

    try:
        messages.append({"role": "user", "content": "Summarise the result for the user."})
        async with httpx.AsyncClient(timeout=45.0) as cl:
            r = await cl.post(
                f"{GATEWAY_URL}/v1/chat/completions",
                headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
                json={"model": TOOL_MODEL, "messages": messages, "max_tokens": 400},
            )
        if r.status_code == 200:
            return ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or None
    except Exception:
        pass
    return None
