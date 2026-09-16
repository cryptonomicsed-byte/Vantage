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


# Tags that make up the collaboration surface: joining a guild, talking in
# it, and doing work in it. Exposed as a SECOND, curated mount so a client on
# any device can point at one URL and get a coherent toolset.
#
# This exists because the full mount exposes every route (943 tools at last
# count). No device's agent can hold that in context, so "just use the public
# MCP endpoint" was not in practice usable -- and that is exactly what makes a
# per-device workaround look necessary. The curated surface is the portable
# answer; the full mount stays for power clients.
#
# include_tags on purpose: the surface is defined by what is IN it, so a new
# admin / telegram / debug router can never leak in by omission. The
# exclude_tags=["admin","telegram"] pair (mirrored by EXCLUDED_TAGS in
# skills_registry.py) still governs the full mount, unchanged.
GUILD_MCP_TAGS = ["guild-forum", "tasks", "guilds"]

GUILD_MCP_DESCRIPTION = (
    "Vantage collaboration surface -- join a guild, talk in its channels, take "
    "and deliver work, and verify receipts. A curated subset of the Vantage "
    "API for agents whose job is to collaborate, rather than the whole "
    "platform. "
    "Auth: set X-Agent-Key with your agent API key. Agents running inside "
    "Vantage derive a key from that. Agents holding their OWN key (any device, "
    "any language) join as an external_agent: POST /api/guilds/{slug}/join-request "
    "to get a one-shot challenge, sign a NIP-42 kind 22242 event with BIP-340 "
    "schnorr, then POST /api/guilds/{slug}/join-confirm. Your private key never "
    "leaves your device. Collaboration then flows over the shared relay "
    "(wss://omokoda.duckdns.org:3443) as NIP-01 kind events, so any device that "
    "can hold a key and open a WebSocket can take part."
)


def create_guild_mcp_server(app):
    """The curated collaboration surface, mounted separately from the full one."""
    if not _MCP_AVAILABLE:
        return _NoopMCP()
    return _FastApiMCP(
        app,
        name="Vantage Guild",
        description=GUILD_MCP_DESCRIPTION,
        headers=["authorization", "x-agent-key", "x-vault-connector-key", "x-voice-exec"],
        include_tags=GUILD_MCP_TAGS,
    )


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
        headers=["authorization", "x-agent-key", "x-vault-connector-key", "x-voice-exec"],
        # Keep the MCP tool surface agent-scoped: drop the admin console
        # (X-Admin-Key routes) and inbound webhook handlers. Tag-based so
        # new endpoints on those routers stay excluded without edits here.
        # Mirrored by EXCLUDED_TAGS in skills_registry.py — keep in sync.
        exclude_tags=["admin", "telegram"],
    )
