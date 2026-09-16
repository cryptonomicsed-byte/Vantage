"""Buzz-specific relay adapter.

The ONLY home for docker-exec and the /operator/communities API calls.
Falls back to NIP-29 for relays that do not support the operator API.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Set

from . import Capability
from .nip29 import Nip29Adapter

logger = logging.getLogger(__name__)

RELAY_CONTAINER = "buzz-prod-relay-1"


async def _docker_exec(*args: str) -> tuple[int, str, str]:
    """Run buzz-admin in the relay container. Only use for Buzz-managed relays."""
    proc = await asyncio.create_subprocess_exec(
        "docker", "exec", RELAY_CONTAINER, "buzz-admin", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


class BuzzAdapter(Nip29Adapter):
    """Adapter for Buzz-managed relays. Adds operator API and docker-exec paths."""

    caps: Set[Capability] = {
        Capability.GROUPS,
        Capability.AUTH_NIP42,
        Capability.PROVISION,
    }

    def __init__(self, relay_url: str, http_url: str = "", admin_pk=None):
        super().__init__(relay_url, admin_pk)
        self._http_url = http_url

    async def provision_group(self, slug: str, owner_pubkey: str) -> dict:
        """Try /operator/communities first; fall back to NIP-29 kind 9007."""
        if self._http_url:
            from ...buzz_guild_provisioning import provision_guild_community
            result = await provision_guild_community(slug, owner_pubkey)
            if result.get("ok"):
                return result
            logger.warning(
                "BuzzAdapter: operator API failed (%s), falling back to NIP-29",
                result.get("error"),
            )
        # Fall back to generic NIP-29 9007
        return await super().provision_group(slug, owner_pubkey)

    async def add_member(self, group_id: str, pubkey: str, admin_pk=None) -> dict:
        """Try docker-exec first; fall back to NIP-29 kind 9000."""
        try:
            code, out, err = await _docker_exec("add-member", "--pubkey", pubkey, "--role", "member")
            blob = (out + err).lower()
            if code == 0 or "already" in blob or "exists" in blob:
                return {"ok": True, "method": "docker-exec"}
            logger.warning(
                "BuzzAdapter: docker-exec add-member failed for %s: %s",
                pubkey[:8], err.strip() or out.strip(),
            )
        except Exception as exc:
            logger.warning("BuzzAdapter: docker-exec unavailable: %s", exc)
        # Fall back to NIP-29 kind 9000
        return await super().add_member(group_id, pubkey, admin_pk)
