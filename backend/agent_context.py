"""Shared per-request agent context.

FastAPI resolves the acting agent in `deps.get_agent`. Modules that have no
explicit agent parameter (repo_host, gitea_accounts, mcp_catalog) still need to
know who is acting so they can use that agent's own credentials. A contextvar
carries it for the lifetime of the request without threading it through every
call site.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

current_agent: ContextVar[Optional[dict]] = ContextVar("current_agent", default=None)
current_agent_key: ContextVar[str] = ContextVar("current_agent_key", default="")
