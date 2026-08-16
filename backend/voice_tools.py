"""Tool catalog and dispatch for Vantage-hosted voice sessions.

Lets the model in a voice session actually use the agent's tools -- both
Vantage's own ~700 endpoints and, through the generic Composio executor, the
~1000 Composio toolkits.

Two design choices worth knowing before reading:

**The catalog comes from FastAPI's route table**, the same source
skills_registry.py uses, rather than a hand-maintained list. New endpoints are
callable by voice the day they ship, and nothing can drift.

**Dispatch re-enters the app over ASGI** rather than calling endpoint functions
directly. That means every call goes through the real dependency chain --
auth, sentencing tiers, per-agent rate limits, request validation, the same
authorization checks any other caller faces. A voice caller gets no shortcut
that an HTTP caller wouldn't get, which is the property that makes handing the
model this much reach defensible.

Safety, given the model's arguments derive from whatever was said near a live
microphone:

  * Tools are opt-in per session via tools_allowlist_json. A session that
    doesn't ask for tools has none. This deliberately inverts the audit's
    sketch (where null meant "everything"): the safe state is the default.
  * Admin and webhook surfaces are never callable, matching the tags MCP and
    the skills registry already exclude.
  * Destructive verbs require the session to have opted into them explicitly;
    otherwise the model is told it needs confirmation rather than silently
    getting a refusal it may narrate as success.
  * Every call is written to voice_session_tool_calls before it runs and
    updated with the outcome, so the audit trail survives a crash mid-call.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import re
from typing import Any, Optional

from fastapi.routing import APIRoute

logger = logging.getLogger(__name__)

# Mirrors mcp_server.py's exclude_tags and skills_registry.EXCLUDED_TAGS.
EXCLUDED_TAGS = {"admin", "telegram"}

# Methods that change state. Allowed only when the session opts in, because a
# misheard sentence should not be able to delete something.
DESTRUCTIVE_METHODS = {"DELETE"}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Gemini function names must be [a-zA-Z0-9_.-]; Vantage operation ids are not.
_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_.-]")

TOOL_PREFIX = "vantage__"
COMPOSIO_TOOL = "composio_execute"

MAX_TOOLS = 128  # Gemini rejects oversized tool lists; see select_tools().


def _safe_name(operation_id: str) -> str:
    return TOOL_PREFIX + _NAME_SAFE.sub("_", operation_id)[:60]


def _route_tools(app) -> list[dict]:
    """Every agent-callable route, as a tool descriptor."""
    out: list[dict] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        if not route.path.startswith("/api"):
            continue
        tags = {str(t) for t in (route.tags or [])}
        if tags & EXCLUDED_TAGS:
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            operation_id = route.operation_id or f"{route.name}_{method.lower()}"
            out.append({
                "name": _safe_name(operation_id),
                "operation_id": operation_id,
                "method": method,
                "path": route.path,
                "description": (route.description or route.summary or route.name or "").strip().split("\n")[0][:300],
                "tags": sorted(tags),
                "path_params": sorted(route.param_convertors.keys()),
            })
    return out


def _matches(name: str, path: str, tags: list[str], patterns: list[str]) -> bool:
    """A pattern may match the tool name, the URL path, or a tag: "*" for
    everything, "vantage__api_agents_*" by name, "/api/trading/*" by path, or
    "tag:memory_vault" for a whole area."""
    for pattern in patterns:
        if pattern == "*":
            return True
        if pattern.startswith("tag:"):
            if pattern[4:] in tags:
                return True
        elif fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(path, pattern):
            return True
    return False


def select_tools(app, allowlist: Optional[list[str]]) -> list[dict]:
    """Resolve a session's allowlist to the tools it may call.

    No allowlist means no tools. Beyond the safety argument, handing a model
    ~700 declarations is also self-defeating: the list blows past what the Live
    API accepts and buries the useful ones.
    """
    if not allowlist:
        return []
    tools = [
        t for t in _route_tools(app)
        if _matches(t["name"], t["path"], t["tags"], allowlist)
    ]
    # Deterministic order so a session's tool list doesn't shuffle between runs.
    tools.sort(key=lambda t: t["name"])
    if len(tools) > MAX_TOOLS:
        logger.warning(
            "voice session allowlist matched %d tools; truncating to %d. "
            "Narrow the allowlist to choose which survive.", len(tools), MAX_TOOLS
        )
        tools = tools[:MAX_TOOLS]

    if _matches(COMPOSIO_TOOL, "/api/composio/execute", ["composio"], allowlist):
        tools.append(_composio_declaration())
    return tools


def _composio_declaration() -> dict:
    """One tool for all of Composio.

    Composio exposes tens of thousands of actions across ~1000 toolkits, which
    cannot be declared individually. Vantage already has a generic executor
    (POST /api/composio/execute) that takes a slug and arguments, so the model
    gets that one door plus the discovery endpoints to find slugs.
    """
    return {
        "name": COMPOSIO_TOOL,
        "operation_id": "composio_execute",
        "method": "POST",
        "path": "/api/composio/execute",
        "description": (
            "Execute any Composio tool by slug (e.g. GITHUB_STAR_A_REPOSITORY, "
            "GMAIL_SEND_EMAIL). Use the Composio toolkit listing tools first to find "
            "a slug. Arguments are the tool's own parameters."
        ),
        "tags": ["composio"],
        "path_params": [],
        "body_shape": {"tool_slug": "string", "arguments": "object"},
    }


def to_gemini_declarations(tools: list[dict]) -> list[dict]:
    """Render tool descriptors as Gemini function declarations.

    Parameters are intentionally loose: a faithful JSON Schema per route would
    mean walking every Pydantic model in the app. Path parameters are declared
    (they are required to build the URL at all) and everything else travels in
    a free-form body the endpoint itself validates -- and rejects with its real
    422 if the model gets it wrong, which is a better error than a guess.
    """
    declarations = []
    for tool in tools:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param in tool.get("path_params", []):
            properties[param] = {"type": "string", "description": f"Path parameter '{param}'"}
            required.append(param)
        if tool.get("body_shape"):
            for key, kind in tool["body_shape"].items():
                properties[key] = {"type": "string" if kind == "string" else "object"}
            required.extend(tool["body_shape"].keys())
        elif tool["method"] in MUTATING_METHODS:
            properties["body"] = {"type": "object", "description": "Request body"}
        else:
            properties["query"] = {"type": "object", "description": "Query parameters"}

        declarations.append({
            "name": tool["name"],
            "description": f"{tool['method']} {tool['path']} — {tool['description']}",
            "parameters": {"type": "object", "properties": properties, "required": required},
        })
    return declarations


class ToolDispatcher:
    """Executes tools for one voice session, as its agent."""

    def __init__(self, app, exec_token: str, tools: list[dict], allow_destructive: bool = False):
        self.app = app
        self.exec_token = exec_token
        self.allow_destructive = allow_destructive
        self._by_name = {t["name"]: t for t in tools}

    def known(self, name: str) -> bool:
        return name in self._by_name

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._by_name)

    async def execute(self, name: str, args: dict) -> dict:
        """Run one tool. Never raises -- a failure is a result the model can
        react to, and an exception here would kill the whole voice session."""
        tool = self._by_name.get(name)
        if tool is None:
            # Being explicit beats a generic failure: the model can pick again.
            return {
                "status": "unknown_tool",
                "error": f"'{name}' is not available in this voice session.",
                "available": self.tool_names[:25],
            }

        if tool["method"] in DESTRUCTIVE_METHODS and not self.allow_destructive:
            return {
                "status": "confirmation_required",
                "error": (
                    f"{name} deletes data and this session did not enable destructive "
                    "tools. Ask the user to confirm and start a session with "
                    "destructive tools enabled."
                ),
            }

        args = args or {}
        path = tool["path"]
        for param in tool.get("path_params", []):
            value = args.get(param)
            if value is None:
                return {"status": "error", "error": f"missing required path parameter '{param}'"}
            path = path.replace("{" + param + "}", str(value))

        body: Any = None
        params: dict = {}
        if tool.get("body_shape"):
            body = {k: args.get(k) for k in tool["body_shape"]}
        elif tool["method"] in MUTATING_METHODS:
            body = args.get("body") if isinstance(args.get("body"), dict) else {
                k: v for k, v in args.items() if k not in tool.get("path_params", [])
            }
        else:
            raw = args.get("query")
            params = raw if isinstance(raw, dict) else {
                k: v for k, v in args.items() if k not in tool.get("path_params", [])
            }

        import httpx
        try:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://voice-exec") as client:
                response = await client.request(
                    tool["method"],
                    path,
                    json=body if body is not None else None,
                    params={k: v for k, v in params.items() if v is not None} or None,
                    headers={"X-Voice-Exec": self.exec_token},
                    timeout=30.0,
                )
        except Exception as exc:
            logger.warning("voice tool %s failed: %s", name, exc)
            return {"status": "error", "error": f"{name} could not be called: {exc}"}

        return self._render(name, response)

    @staticmethod
    def _render(name: str, response) -> dict:
        try:
            payload = response.json()
        except ValueError:
            payload = response.text[:2000]

        if response.status_code >= 400:
            # Hand back the endpoint's real error. A fabricated success is the
            # failure mode that matters most here -- the model would narrate it.
            return {"status": "error", "http_status": response.status_code, "error": payload}

        # Long list responses are trimmed: the model is speaking these aloud,
        # and the full body would blow the context for no benefit.
        if isinstance(payload, dict):
            for key, value in list(payload.items()):
                if isinstance(value, list) and len(value) > 20:
                    payload[key] = value[:20]
                    payload[f"{key}_truncated_from"] = len(value)
        elif isinstance(payload, list) and len(payload) > 20:
            payload = {"items": payload[:20], "truncated_from": len(payload)}

        text = json.dumps(payload, default=str)
        if len(text) > 8000:
            return {"status": "ok", "truncated": True, "result": text[:8000]}
        return {"status": "ok", "result": payload}
