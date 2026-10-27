"""NostrGateway — single source of truth for relay URLs across all Buzz-touching code.

Replaces hardcoded relay URLs scattered across buzz_pairing, buzz_registration,
nip29_bridge, and config. All Buzz/Nostr modules import from here instead.

Registry is loaded from env var NOSTR_RELAY_REGISTRY (JSON list of relay entries).
Falls back to built-in defaults so Vantage boots without env config changes.

Relay entry shape:
    { "url": "wss://...", "role": "primary"|"backup"|"nipae"|"publish-only", "nips": [1, 9, 42] }

Role semantics:
    primary      – main relay for agent <-> Buzz session traffic
    backup       – failover when primary unreachable
    nipae        – delivery receipts (kind:27235 / NIP-AE traffic)
    publish-only – write-only announces (no subscription)
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

RelayRole = Literal["primary", "backup", "nipae", "publish-only"]

_DEFAULT_REGISTRY: list[dict] = [
    {
        "url": "wss://omokoda.duckdns.org:3443",
        "http_url": "https://omokoda.duckdns.org:3443",
        "role": "primary",
        "nips": [1, 9, 42, 44],
    },
    {
        "url": "wss://relay.damus.io",
        "role": "nipae",
        "nips": [1, 9, 42],
    },
]


@dataclass
class RelayEntry:
    url: str
    role: RelayRole
    nips: list[int] = field(default_factory=list)
    http_url: str = ""

    def __post_init__(self) -> None:
        if not self.http_url and self.url.startswith("wss://"):
            self.http_url = "https://" + self.url[6:]
        elif not self.http_url and self.url.startswith("ws://"):
            self.http_url = "http://" + self.url[5:]


class NostrGateway:
    """Registry-backed relay selector. Instantiated once at module level."""

    def __init__(self, entries: list[RelayEntry]) -> None:
        self._entries = entries

    @property
    def primary(self) -> RelayEntry:
        return self._first("primary")

    @property
    def primary_ws_url(self) -> str:
        return self.primary.url

    @property
    def primary_http_url(self) -> str:
        return self.primary.http_url

    @property
    def nipae_url(self) -> str:
        return self._first("nipae").url

    def ws_url(self, role: RelayRole = "primary") -> str:
        return self._first(role).url

    def http_url(self, role: RelayRole = "primary") -> str:
        return self._first(role).http_url

    def relays_for_nip(self, nip: int) -> list[RelayEntry]:
        return [e for e in self._entries if nip in e.nips]

    def all_by_role(self, role: RelayRole) -> list[RelayEntry]:
        return [e for e in self._entries if e.role == role]

    def _first(self, role: RelayRole) -> RelayEntry:
        for entry in self._entries:
            if entry.role == role:
                return entry
        raise LookupError(f"No relay configured for role={role!r}")


def _load_registry() -> list[RelayEntry]:
    raw = os.environ.get("NOSTR_RELAY_REGISTRY", "")
    if raw:
        try:
            data = json.loads(raw)
            entries = [RelayEntry(**item) for item in data]
            logger.debug("NostrGateway: loaded %d relays from env", len(entries))
            return entries
        except Exception as exc:
            logger.warning("NostrGateway: bad NOSTR_RELAY_REGISTRY (%s), using defaults", exc)
    return [RelayEntry(**item) for item in _DEFAULT_REGISTRY]


# Module-level singleton
gateway = NostrGateway(_load_registry())

# Backward-compat aliases
PUBLIC_RELAY_WS_URL: str = gateway.primary_ws_url
PUBLIC_RELAY_HTTP_URL: str = gateway.primary_http_url
NIPAE_RELAY_WS_URL: str = gateway.nipae_url
