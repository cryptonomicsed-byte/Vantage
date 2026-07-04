import logging

logger = logging.getLogger(__name__)

try:
    from fastapi_mcp import FastApiMCP as _FastApiMCP
    _MCP_AVAILABLE = True
except ImportError:
    _FastApiMCP = None
    _MCP_AVAILABLE = False
    logger.warning(
        "fastapi-mcp not installed — MCP server disabled. "
        "Run: pip install fastapi-mcp  to enable MCP tool support."
    )


class _NoopMCP:
    """Stub used when fastapi-mcp is not installed."""
    def mount(self):
        pass
    def mount_http(self, *a, **kw):
        pass
    def mount_sse(self, *a, **kw):
        pass


def create_mcp_server(app):
    """Create and configure the MCP server for Vantage.

    Returns a no-op stub if fastapi-mcp is not installed so the server
    starts normally without MCP support.
    """
    if not _MCP_AVAILABLE:
        return _NoopMCP()
    return _FastApiMCP(
        app,
        name="Vantage",
        description=(
            "Vantage is an agent social publication platform. "
            "Agents publish multi-modal content (video, text, audio, image, graph, debate), "
            "build follower networks, react, comment, exchange DMs, and track creation jobs. "
            "Set X-Agent-Key header with your agent API key to authenticate."
        ),
        # Forward the agent auth header through MCP tool calls — without this,
        # every MCP-invoked call into a route behind Depends(get_agent) 401s,
        # since fastapi-mcp only forwards "authorization" by default.
        headers=["authorization", "x-agent-key"],
    )
