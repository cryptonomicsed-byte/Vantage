"""
ParrotClient — thin async client for the parrot-security scan sidecar
(ClamAV + YARA + binwalk on a single file, ops/parrot-security/).

Unlike backend/supermemory_client.py and the other enrichment sidecars, this
client fails CLOSED, not open: it exists to gate untrusted bytes, so
"can't reach the scanner" must not be silently treated as "the file is
clean." The only case that preserves current (pre-gate) behavior is the
sidecar being unconfigured entirely (PARROT_SECURITY_URL unset) — a
deploy-time choice, not a runtime failure.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ParrotClient:
    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self._base)

    async def scan(self, content: bytes, filename: str, kind: str) -> dict[str, Any]:
        """POST /scan — returns {"configured", "clean", "findings", "risk_score"}.

        - Unconfigured (no base URL): {"configured": False, "clean": True, "findings": []}
          — the gate is a no-op, matching pre-existing behavior.
        - Configured and reachable: passes through the scanner's own verdict.
        - Configured but unreachable/erroring: {"configured": True, "clean": False, ...}
          — fails closed, since this is the actual security control.
        """
        if not self._base:
            return {"configured": False, "clean": True, "findings": [], "risk_score": 0.0}

        url = f"{self._base}/scan"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    files={"file": (filename, content)},
                    data={"kind": kind},
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "configured": True,
                    "clean": bool(data.get("clean", False)),
                    "findings": data.get("findings", []),
                    "risk_score": data.get("risk_score", 0.0),
                }
        except Exception as exc:
            logger.warning("parrot-security scan failed for %s (%s): %s", filename, url, exc)
            return {
                "configured": True,
                "clean": False,
                "findings": [{"error": f"scanner unreachable: {exc}"}],
                "risk_score": 1.0,
            }
