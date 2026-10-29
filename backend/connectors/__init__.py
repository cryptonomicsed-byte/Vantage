"""Connector layer — generic MCP connectors only (see lounge_mcp.py).

No provider names live anywhere in the lounge or skill code: every external
backend is an MCP server URL registered by a guild member, with credentials
sealed at rest. Native pipelines (code/video/trade/social) remain the
default fallback.
"""
