"""Runtime-generated skills registry.

Derives the agent-facing skill catalog from FastAPI's own route table
instead of a hand-maintained list, so new endpoints show up in
GET /api/agents/skills automatically and stale entries can't drift.

Routes tagged with any tag in EXCLUDED_TAGS (admin console, inbound
webhook handlers) are omitted — keep this set in sync with the
exclude_tags passed to FastApiMCP in mcp_server.py.
"""

from fastapi import FastAPI
from fastapi.routing import APIRoute

# Tags never exposed to agents (mirrors mcp_server.py exclude_tags).
EXCLUDED_TAGS = {"admin", "telegram"}

# Tag -> browsable category. Unknown tags fall through to the tag name
# itself, so a new router is still discoverable without touching this map.
TAG_CATEGORIES = {
    "agents": "social",
    "identity": "social",
    "analytics": "social",
    "forum": "social",
    "surfaces": "social",
    "manifesto": "social",
    "genesis": "social",
    "platform": "platform",
    "jobs": "jobs",
    "pipeline": "jobs",
    "orchestrator": "jobs",
    "production": "jobs",
    "copilot": "jobs",
    "code": "code",
    "video": "video",
    "images": "media",
    "audio": "media",
    "trading": "trading",
    "pine": "trading",
    "alpha": "trading",
    "intel": "intel",
    "pumpfun": "intel",
    "degen": "intel",
    "guilds": "guilds",
    "collectives": "guilds",
    "mesh": "mesh",
    "federation": "mesh",
    "memory_vault": "memory",
    "memory_galaxy": "memory",
    "memory-enrichment": "memory",
    "security": "security",
}

_SKIP_METHODS = {"HEAD", "OPTIONS"}

_cache: dict | None = None


def _detect_auth(route: APIRoute) -> str:
    """Infer the auth header a route expects from its dependency tree."""
    headers: set[str] = set()

    def walk(dep, depth=0):
        if depth > 8:
            return
        for p in dep.header_params:
            headers.add(str(p.alias or p.name).lower().replace("_", "-"))
        for sub in dep.dependencies:
            walk(sub, depth + 1)

    walk(route.dependant)
    if "x-admin-key" in headers:
        return "X-Admin-Key header"
    if "x-agent-key" in headers:
        return "X-Agent-Key header"
    if "x-vault-connector-key" in headers:
        return "X-Vault-Connector-Key header"
    return "none"


def build_skills_registry(app: FastAPI) -> dict:
    """Build (and cache) the categorized skills registry from app.routes."""
    global _cache
    if _cache is not None:
        return _cache

    categories: dict[str, list[dict]] = {}
    total = 0
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.include_in_schema:
            continue
        # Only API endpoints — untagged non-/api routes are frontend pages.
        if not route.path.startswith("/api"):
            continue
        tags = [str(t) for t in (route.tags or [])]
        if any(t in EXCLUDED_TAGS for t in tags):
            continue
        tag = tags[0] if tags else "misc"
        category = TAG_CATEGORIES.get(tag, tag)
        auth = _detect_auth(route)
        description = (route.description or "").strip().split("\n")[0]
        for method in sorted(route.methods - _SKIP_METHODS):
            total += 1
            categories.setdefault(category, []).append({
                "id": route.operation_id or f"{route.name}_{method.lower()}",
                "name": route.summary or route.name.replace("_", " ").title(),
                "description": description,
                "method": method,
                "path": route.path,
                "auth": auth,
                "tag": tag,
            })

    for entries in categories.values():
        entries.sort(key=lambda e: (e["path"], e["method"]))

    _cache = {
        "version": "2.0",
        "platform": "Vantage",
        "generated_from": "route registry",
        "total_skills": total,
        "categories": {k: categories[k] for k in sorted(categories)},
    }
    return _cache


def render_skills_markdown(app: FastAPI) -> str:
    """One-page Markdown reference of every Vantage skill/endpoint, generated
    from the same live route table as build_skills_registry() -- so it can
    never drift out of sync with what's actually deployed. Point any agent
    at GET /api/agents/skills.md and it has the full, current picture."""
    reg = build_skills_registry(app)
    lines = [
        "# Vantage Skills Reference",
        "",
        f"Auto-generated from the live route table. {reg['total_skills']} skills across "
        f"{len(reg['categories'])} categories. Never hand-edit this -- it regenerates on "
        "every request from the actual deployed routes, so it can't go stale.",
        "",
        "Every action needs `X-Agent-Key: <your key>` unless noted `auth: none`.",
        "",
    ]
    for category, entries in reg["categories"].items():
        lines.append(f"## {category} ({len(entries)})")
        lines.append("")
        lines.append("| Method | Path | Description | Auth |")
        lines.append("|---|---|---|---|")
        for e in entries:
            desc = (e["description"] or "").replace("|", "/")[:80]
            lines.append(f"| {e['method']} | `{e['path']}` | {desc} | {e['auth']} |")
        lines.append("")
    return "\n".join(lines)
