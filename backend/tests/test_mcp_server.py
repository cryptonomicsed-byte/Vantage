"""MCP server wiring — confirms the headers every MCP tool call needs to
authenticate are actually forwarded. A route behind Depends(get_agent) or
Depends(get_vault_connector) silently 401s over MCP if its auth header
isn't in this allowlist, since fastapi-mcp only forwards 'authorization'
by default.
"""
from backend.mcp_server import create_mcp_server


def test_mcp_server_forwards_agent_and_vault_connector_headers(app):
    mcp = create_mcp_server(app)
    forwarded = getattr(mcp, "_forward_headers", set())
    assert "authorization" in forwarded
    assert "x-agent-key" in forwarded
    assert "x-vault-connector-key" in forwarded
