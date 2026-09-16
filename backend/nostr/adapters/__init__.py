"""RelayAdapter Protocol and Capability enum.

Adapters are thin wrappers that know how a specific relay type
exposes group management. All share the same Protocol interface.
"""
from __future__ import annotations

from enum import Enum, auto
from typing import Protocol, Set


class Capability(Enum):
    GROUPS = auto()       # NIP-29 group management
    AUTH_NIP42 = auto()   # NIP-42 authentication
    SEARCH_NIP50 = auto() # NIP-50 search
    PROVISION = auto()    # operator /communities API (Buzz-specific)


class RelayAdapter(Protocol):
    caps: Set[Capability]
    relay_url: str

    async def publish(self, event: dict) -> dict: ...
    async def subscribe(self, filters: list, sub_id: str = None) -> str: ...
    async def provision_group(self, slug: str, owner_pubkey: str) -> dict: ...
    async def add_member(self, group_id: str, pubkey: str, admin_pk) -> dict: ...
