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

**The declaration count and the execution scope are two different limits.**
select_tools() resolves a session's allowlist to everything it may execute --
uncapped; a "*" allowlist matches the whole ~700-route catalog. Gemini's Live
API cannot take a declaration list anywhere near that size, so
to_gemini_declarations() decides separately what the model sees up front:
every tool directly, below DIRECT_DECLARE_LIMIT, or just vantage_find_tools
and vantage_call_tool above it -- two declarations that reach the same full
scope through search-then-call instead of truncating it. The alternative,
slicing the sorted list at some fixed count, silently favours whichever tool
names sort first alphabetically and calls that "access"; it isn't.

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

# Routes whose damage potential is not visible from the HTTP method. Workspace
# exec is the clearest case: it is a POST, but `rm -rf` through it is every bit
# as destructive as a DELETE, so keying the gate purely on method would let the
# single most powerful tool in the catalog past the confirmation the weakest
# ones need. Matched as path globs.
DESTRUCTIVE_PATHS = (
    "/api/workspace/exec",
    "/api/workspace/write",
    "/api/workspace/remove",
    # Anything that moves money or mints a key. These are POSTs, so a
    # method-only gate would have let a spoken sentence place an order or
    # generate a wallet with no confirmation, while stopping a far more
    # harmless DELETE.
    "/api/trading/orders*",
    "/api/trading/execute*",
    "/api/trading/wallets*",
    "/api/trading/strategies/*/toggle",
    "/api/agents/me/wallets*",
)

# Gemini function names must be [a-zA-Z0-9_.-]; Vantage operation ids are not.
_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_.-]")

TOOL_PREFIX = "vantage__"
COMPOSIO_TOOL = "composio_execute"
FIND_TOOLS_TOOL = "vantage_find_tools"
CALL_TOOL_TOOL = "vantage_call_tool"

# Below this, every matched tool gets its own direct declaration -- no
# indirection needed for a small preset like "memory + copilot". At or above
# it, declaring each one individually would either blow past what the Live API
# accepts or silently favour whichever names sort first alphabetically, so the
# full set is instead reached through vantage_find_tools + vantage_call_tool:
# two declarations, constant regardless of how many routes an allowlist like
# "*" matches.
DIRECT_DECLARE_LIMIT = 40


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

    No allowlist means no tools -- the safe default. Otherwise this is the
    session's full execution scope: everything the allowlist matches, with no
    cap. A "*" allowlist can match all ~700 routes, and that is fine here --
    this list feeds the ToolDispatcher, not the model's context. What the
    model actually sees is decided separately, in to_gemini_declarations(),
    because Gemini's declaration limit is a presentation problem, not a reason
    to narrow what the session is allowed to touch.
    """
    if not allowlist:
        return []
    tools = [
        t for t in _route_tools(app)
        if _matches(t["name"], t["path"], t["tags"], allowlist)
    ]
    # Deterministic order so a session's tool list doesn't shuffle between runs.
    tools.sort(key=lambda t: t["name"])

    if _matches(COMPOSIO_TOOL, "/api/composio/execute", ["composio"], allowlist):
        tools.append(_composio_declaration())
    return tools


def search_tools(tools: list[dict], query: str, limit: int = 15) -> list[dict]:
    """Keyword search over a tool list's name, path, description and tags.

    Backs vantage_find_tools: the mechanism that makes "full access" navigable
    instead of a wall of declarations. Terms are ANDed against a haystack of
    all four fields per tool, ranked by how many terms hit -- not a full-text
    engine, just enough to turn "what can move SOL" into a short, relevant
    list instead of nothing (single-source substring match) or everything
    (no ranking at all).
    """
    query = (query or "").strip().lower()
    if not query:
        return tools[:limit]
    terms = query.split()

    def score(tool: dict) -> int:
        haystack = " ".join([
            tool["name"], tool["path"], tool.get("description", ""),
            " ".join(tool.get("tags", [])),
        ]).lower()
        return sum(1 for term in terms if term in haystack)

    ranked = sorted(
        (t for t in tools if score(t) > 0),
        key=lambda t: (-score(t), t["name"]),
    )
    return ranked[:limit]


def is_destructive(tool: dict) -> bool:
    """Whether a tool needs the session's explicit destructive opt-in.

    Path patterns only apply to methods that change state. Without that guard
    a pattern like "/api/trading/orders*" also gates GET /orders/{id}, so
    merely reading back an order would demand the same confirmation as placing
    one -- which trains people to enable destructive mode for everything.
    """
    if tool["method"] in DESTRUCTIVE_METHODS:
        return True
    if tool["method"] not in MUTATING_METHODS:
        return False
    return any(fnmatch.fnmatch(tool["path"], pattern) for pattern in DESTRUCTIVE_PATHS)


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


def _render_declaration(tool: dict) -> dict:
    """One tool descriptor as a Gemini function declaration.

    Parameters are intentionally loose: a faithful JSON Schema per route would
    mean walking every Pydantic model in the app. Path parameters are declared
    (they are required to build the URL at all) and everything else travels in
    a free-form body the endpoint itself validates -- and rejects with its real
    422 if the model gets it wrong, which is a better error than a guess.
    """
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

    return {
        "name": tool["name"],
        "description": f"{tool['method']} {tool['path']} — {tool['description']}",
        "parameters": {"type": "object", "properties": properties, "required": required},
    }


def _find_tools_declaration() -> dict:
    return {
        "name": FIND_TOOLS_TOOL,
        "description": (
            "Search this session's full set of available Vantage tools by keyword "
            "(e.g. 'wallet balance', 'create order', 'pump.fun watchlist'). Returns "
            "matching tool names with their method, path and description. Call this "
            "before vantage_call_tool whenever you don't already know the exact tool "
            "name -- most of this session's tools are reachable this way rather than "
            "declared individually."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "keywords for what you want to do"},
            },
            "required": ["query"],
        },
    }


def _call_tool_declaration() -> dict:
    return {
        "name": CALL_TOOL_TOOL,
        "description": (
            "Call any Vantage tool from this session's allowed set by its exact name "
            "(as returned by vantage_find_tools). Arguments are that tool's own "
            "parameters -- path parameters plus either a body or query object, "
            "matching what vantage_find_tools described for it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "exact tool name from vantage_find_tools"},
                "arguments": {"type": "object", "description": "that tool's own arguments"},
            },
            "required": ["name", "arguments"],
        },
    }


def to_gemini_declarations(tools: list[dict]) -> list[dict]:
    """Render a session's tool scope as Gemini function declarations.

    Below DIRECT_DECLARE_LIMIT, every tool gets its own declaration -- the
    common case (a small preset like memory+copilot) needs no indirection.
    At or above it, only vantage_find_tools and vantage_call_tool are
    declared; the full set stays reachable through them because
    ToolDispatcher is built from the same, uncapped list this function
    receives. This is what replaced truncating the list to the Live API's
    limit and silently keeping whichever ~128 tool names happened to sort
    first alphabetically.
    """
    if len(tools) > DIRECT_DECLARE_LIMIT:
        logger.info(
            "voice session has %d tools -- declaring vantage_find_tools/"
            "vantage_call_tool instead of one declaration per tool", len(tools),
        )
        return [_find_tools_declaration(), _call_tool_declaration()]
    return [_render_declaration(tool) for tool in tools]


_DEFAULT_IDENTITY = (
    "You are Vantage's voice assistant: a spoken interface to the Vantage "
    "platform, talking to the person live rather than exchanging text. Speak "
    "naturally and concisely -- prefer a sentence or two over a list, and say "
    "numbers and symbols the way you'd say them aloud rather than as markdown."
)


def system_instruction_for(persona: str, tools: list[dict], allow_destructive: bool) -> str:
    """Build the instruction a voice session actually needs.

    An empty system instruction is not a neutral default -- it's a model that
    doesn't know Vantage exists, doesn't know it has vantage_find_tools for a
    large scope, and has no way to explain a confirmation_required refusal
    rather than silently retrying or claiming success. The mechanical
    guidance below is appended even when a caller supplies their own persona,
    because a custom persona has no way to know about vantage_find_tools
    either -- only the identity/tone half is something a caller should own.
    """
    identity = persona.strip() or _DEFAULT_IDENTITY

    guidance = []
    if not tools:
        guidance.append(
            "This session has no tools enabled -- you can talk, but you cannot "
            "look anything up or take any action. If asked to do something that "
            "needs a tool, say so plainly rather than guessing or improvising."
        )
    elif len(tools) > DIRECT_DECLARE_LIMIT:
        guidance.append(
            f"You have a large set of Vantage tools available in this session "
            f"({len(tools)} total), reached through vantage_find_tools (search "
            "by keyword, e.g. 'wallet balance' or 'create order') and "
            "vantage_call_tool (call the exact name it returns, with that "
            "tool's own arguments). Search first whenever you don't already "
            "know the exact tool name -- most of your tools are not declared "
            "individually and are only reachable this way."
        )
    else:
        guidance.append(f"You have {len(tools)} Vantage tools declared directly in this session; call them by name.")

    if allow_destructive:
        guidance.append(
            "Destructive and money-moving actions (orders, deletions, wallet "
            "operations) are enabled for this session. Say out loud what "
            "you're about to do before anything irreversible or costly, since "
            "the person is listening rather than watching a screen to review it."
        )
    else:
        guidance.append(
            "Destructive and money-moving actions are NOT enabled for this "
            "session. If a tool call comes back with status "
            "'confirmation_required', tell the person it needs a session with "
            "destructive actions turned on -- do not retry it and do not "
            "describe it as having succeeded."
        )

    return identity + "\n\n" + " ".join(guidance)


class ToolDispatcher:
    """Executes tools for one voice session, as its agent."""

    def __init__(self, app, exec_token: str, tools: list[dict], allow_destructive: bool = False):
        self.app = app
        self.exec_token = exec_token
        self.allow_destructive = allow_destructive
        self._by_name = {t["name"]: t for t in tools}

    def known(self, name: str) -> bool:
        return name in self._by_name or name in (FIND_TOOLS_TOOL, CALL_TOOL_TOOL)

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._by_name)

    def _find_tools(self, args: dict) -> dict:
        """vantage_find_tools: keyword search over this session's full scope,
        not just whatever got a direct declaration. What makes a large
        allowlist ("*", a broad tag) actually reachable instead of silently
        capped."""
        query = str((args or {}).get("query") or "").strip()
        if not query:
            return {"status": "error", "error": "query is required"}
        matches = search_tools(list(self._by_name.values()), query)
        return {
            "status": "ok",
            "result": {
                "matches": [
                    {"name": t["name"], "method": t["method"], "path": t["path"],
                     "description": t["description"]}
                    for t in matches
                ],
                "count": len(matches),
                "hint": ("Call vantage_call_tool with one of these names."
                         if matches else "No matches -- try different keywords."),
            },
        }

    async def _call_tool(self, args: dict) -> dict:
        """vantage_call_tool: resolve the named tool and re-enter execute() so
        it gets the exact same destructive gate, path substitution, ASGI
        dispatch and rendering as a directly-declared call -- indirection
        changes how the model reaches a tool, not what running it means."""
        args = args or {}
        name = str(args.get("name") or "").strip()
        if not name:
            return {"status": "error", "error": "name is required"}
        if name in (FIND_TOOLS_TOOL, CALL_TOOL_TOOL):
            # Not a security boundary -- there's nothing to escalate to here --
            # but unguarded this recurses into itself on a nested call and
            # eventually raises RecursionError, which breaks execute()'s
            # "never raises" contract instead of just answering the model.
            return {"status": "error", "error": f"'{name}' cannot be called via vantage_call_tool; call it directly."}
        inner_args = args.get("arguments")
        if not isinstance(inner_args, dict):
            inner_args = {}
        return await self.execute(name, inner_args)

    async def execute(self, name: str, args: dict) -> dict:
        """Run one tool. Never raises -- a failure is a result the model can
        react to, and an exception here would kill the whole voice session."""
        if name == FIND_TOOLS_TOOL:
            return self._find_tools(args)
        if name == CALL_TOOL_TOOL:
            return await self._call_tool(args)

        tool = self._by_name.get(name)
        if tool is None:
            # Being explicit beats a generic failure: the model can pick again.
            return {
                "status": "unknown_tool",
                "error": f"'{name}' is not available in this voice session.",
                "available": self.tool_names[:25],
            }

        if is_destructive(tool) and not self.allow_destructive:
            return {
                "status": "confirmation_required",
                "error": (
                    f"{name} can destroy data and this session did not enable "
                    "destructive tools. Ask the user to confirm and start a session "
                    "with destructive tools enabled."
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
