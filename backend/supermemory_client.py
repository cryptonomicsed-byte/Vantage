"""
SupermemoryClient — thin async client for a self-hosted supermemory instance.

Graceful no-op when base_url is empty, so the Vantage backend works without
the supermemory sidecar present. Follows the same shape as
backend/memory_enrichment.py's MemoryIntelligence client.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class SupermemoryClient:
    def __init__(self, base_url: str, api_key: str = "") -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key

    async def add_document(
        self,
        content: str,
        container_tag: str = "",
        metadata: Optional[dict[str, Any]] = None,
        custom_id: str = "",
    ) -> dict[str, Any]:
        """POST /v3/documents — ingest content as a memory. Returns {} (falsy)
        on any failure or when unconfigured, so callers can treat the result
        as a simple success/no-op check."""
        body: dict[str, Any] = {"content": content}
        if container_tag:
            body["containerTag"] = container_tag
        if metadata:
            body["metadata"] = metadata
        if custom_id:
            body["customId"] = custom_id
        return await self._post("/v3/documents", body)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self._base:
            return {}
        url = f"{self._base}{path}"
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.debug("supermemory call failed (%s): %s", url, exc)
            return {}
