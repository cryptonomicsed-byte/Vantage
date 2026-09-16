"""Generic NIP-29 relay adapter.

Works against any relay that supports NIP-29 (kinds 9007/9000/9021).
No docker, no operator API. Uses NostrSession + nostr.groups.
"""
from __future__ import annotations

from typing import Set

from . import Capability
from ..client import NostrSession
from .. import groups as _groups


class Nip29Adapter:
    """Adapter for standard NIP-29 relays."""

    caps: Set[Capability] = {Capability.GROUPS, Capability.AUTH_NIP42}

    def __init__(self, relay_url: str, admin_pk=None):
        self.relay_url = relay_url
        self._admin_pk = admin_pk  # set at runtime from derive_instance_keypair()

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
        raise NotImplementedError("subscribe not supported in one-shot adapter; use NostrSession directly")

    async def provision_group(self, slug: str, owner_pubkey: str) -> dict:
        """Publish kind 9007 to create the NIP-29 group."""
        pk = await self._get_admin_pk()
        return await _groups.provision_group(
            self.relay_url, pk, slug, name=slug, about=f"Guild: {slug}"
        )

    async def add_member(self, group_id: str, pubkey: str, admin_pk=None) -> dict:
        """Publish kind 9000 (put-user) to add pubkey to the group."""
        if admin_pk is None:
            admin_pk = await self._get_admin_pk()
        return await _groups.add_member(self.relay_url, admin_pk, group_id, pubkey)
