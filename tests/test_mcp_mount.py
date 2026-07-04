"""
MCP mount auth-forwarding + transport tests.

We don't drive a full MCP JSON-RPC session here (session negotiation makes
that brittle for a unit test) — instead we assert the concrete fix directly:
the FastApiMCP instance is configured to forward X-Agent-Key, and both
transports are actually mounted as routes.
"""

from backend.mcp_server import create_mcp_server
from backend.main import app


def test_mcp_server_forwards_agent_key_header():
    mcp = create_mcp_server(app)
    # _forward_headers is fastapi-mcp's lowercased allowlist of headers it
    # relays from an incoming MCP tool call into the wrapped route's HTTP call.
    assert "authorization" in mcp._forward_headers
    assert "x-agent-key" in mcp._forward_headers


def test_both_mcp_transports_mounted():
    paths = set()
    for route in app.router.routes:
        p = getattr(route, "path", None)
        if p:
            paths.add(p)
        # Mounted sub-apps (Mount/APIRouter) don't always flatten into app.routes
        # depending on the FastAPI version — walk one level deeper defensively.
        sub = getattr(route, "routes", None)
        if sub:
            for r2 in sub:
                p2 = getattr(r2, "path", None)
                if p2:
                    paths.add(p2)
    joined = " ".join(paths)
    assert "/mcp" in joined
    assert "/mcp/sse" in joined
