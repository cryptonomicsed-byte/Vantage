"""Generic plain NIP-01 relay adapter (damus, nos.lol, etc.)

No group management support — provision_group and add_member raise
NotImplementedError. Use for read/write-only relays.
"""
from __future__ import annotations

from typing import Set

from . import Capability
from ..client import NostrSession


class GenericAdapter:
    """Plain NIP-01 relay with NIP-42 auth only. No group management."""

    caps: Set[Capability] = {Capability.AUTH_NIP42}

    def __init__(self, relay_url: str, admin_pk=None):
        self.relay_url = relay_url
        self._admin_pk = admin_pk

    async def _get_admin_pk(self):
        if self._admin_pk is not None:
            return self._admin_pk
        from ...buzz_identity import derive_instance_keypair
        return await derive_instance_keypair()

    async def publish(self, kind: int, content: str, tags=None) -> dict:
        pk = await self._get_admin_pk()
        sess = NostrSession(self.relay_url, pk)
        await sess.connect()
        await sess.authenticate()
        try:
            return await sess.publish(kind, content, tags)
        finally:
            await sess.close()

    async def subscribe(self, filters: list, sub_id: str = None) -> str:
        raise NotImplementedError("subscribe not supported in one-shot adapter")

    async def provision_group(self, slug: str, owner_pubkey: str) -> dict:
        raise NotImplementedError(
            f"relay {self.relay_url} does not support NIP-29 group provisioning"
        )

    async def add_member(self, group_id: str, pubkey: str, admin_pk=None) -> dict:
        raise NotImplementedError(
            f"relay {self.relay_url} does not support NIP-29 member management"
        )
