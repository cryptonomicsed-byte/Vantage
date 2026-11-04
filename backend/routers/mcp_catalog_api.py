"""REST surface for the Vantage tool catalog.

Agents (and the Settings UI) ask "what can I do?" and "which tools fit this
request?" instead of loading a thousand schemas.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from .. import mcp_catalog
from ..deps import get_agent

router = APIRouter(prefix="/api/mcp/catalog", tags=["mcp"])


@router.get("/categories")
async def categories(agent: dict = Depends(get_agent)):
    cats = await mcp_catalog.categories()
    return {"categories": cats, "total_categories": len(cats),
            "total_tools": sum(c["count"] for c in cats)}


@router.get("/search")
async def search(
    q: str = Query("", description="free text; empty lists the category"),
    category: Optional[str] = Query(None),
    limit: int = Query(12, ge=1, le=50),
    agent: dict = Depends(get_agent),
):
    hits = await mcp_catalog.search(q, category=category, limit=limit)
    return {"query": q, "category": category, "count": len(hits), "tools": hits}
