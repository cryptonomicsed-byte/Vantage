"""
MemoryIntelligence — thin async client for the Julia memory service (:7778).

All methods are graceful no-ops when JULIA_MEMORY_URL is empty, so the
Vantage backend works without the Julia sidecar present.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class MemoryIntelligence:
    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def predict_next_activity(self, agent_name: str) -> dict[str, Any]:
        """POST /vantage/predict — time-series activity prediction."""
        return await self._post("/vantage/predict", {"agent_id": agent_name, "series": [], "horizon": 7})

    async def find_similar(self, content: str, top_k: int = 5) -> list[dict[str, Any]]:
        """POST /vantage/similar — semantic similarity search."""
        result = await self._post("/vantage/similar", {"content": content, "top_k": top_k})
        if isinstance(result, list):
            return result
        return []

    async def mine_patterns(self, scope: str) -> list[dict[str, Any]]:
        """POST /vantage/patterns — behavioral pattern mining."""
        result = await self._post("/vantage/patterns", {"scope": scope})
        return result.get("patterns", []) if isinstance(result, dict) else []

    async def validate_trace_entropy(self, trace_data: str) -> dict[str, Any]:
        """POST /nist/validate — NIST SP 800-22 entropy battery."""
        return await self._post("/nist/validate", {"data": trace_data})

    async def feed_trace(self, payload: dict[str, Any]) -> None:
        """POST /vantage/ingest — feed a trace into the memory DAG (fire-and-forget)."""
        await self._post("/vantage/ingest", payload)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        if not self._base:
            return {}
        url = f"{self._base}{path}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.debug("Julia memory call failed (%s): %s", url, exc)
            return {}
