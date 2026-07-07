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
            "Set X-Agent-Key header with your agent API key to authenticate. "
            "To push external conversations into an agent's memory vault, mint a scoped, "
            "ingest-only connector token via POST /{agent_name}/vault/external/connectors "
            "(requires X-Agent-Key) and pass it as X-Vault-Connector-Key on the ingest tool."
        ),
        # Forward auth headers through MCP tool calls — without this, every
        # MCP-invoked call into a route behind Depends(get_agent) or
        # Depends(get_vault_connector) 401s, since fastapi-mcp only forwards
        # "authorization" by default. x-vault-connector-key lets any
        # MCP-speaking client (Claude, ChatGPT via a custom connector, Codex,
        # etc.) push conversations into an agent's vault through its own
        # scoped token, without ever handling the agent's real X-Agent-Key.
        headers=["authorization", "x-agent-key", "x-vault-connector-key"],
    )
