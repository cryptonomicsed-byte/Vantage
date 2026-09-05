"""Freenet adapter for Vantage.

Architecture:
  Vantage (authoritative) ←→ FreenetService ←→ Freenet node (decentralized)

Freenet provides replicated shared state for:
  - Guild rooms (GuildRoomContract)
  - Workspace collaboration (WorkspaceContract)
  - Public agent activity
  - Shared documents

Contracts run on untrusted peers — never put private keys or secrets here.
Private state belongs in Ọmọ Kọ́dà2's Freenet Delegate.

Implementation phases:
  F0 — Study (freenet-core, river, freenet-agent-skills in third_party/)
  F1 — Local node connection (this file + client.py)
  F2 — TypeScript SDK bridge (frontend/src/features/freenet/)
  F3 — GuildRoom contract
  F4 — Workspace contract
  F5 — Ọmọ Kọ́dà2 delegate bridge
  F6 — VantageEvent ↔ FreenetEvent bridge
  F7 — Nostr bridge
  F8 — Federation
  F9 — Production multi-node
"""
from .service import FreenetService, get_freenet_service
from .types import (
    FreenetStatus,
    ContractKey,
    FreenetEvent,
    FreenetEventType,
    GuildRoomState,
    WorkspaceState,
)

__all__ = [
    "FreenetService",
    "get_freenet_service",
    "FreenetStatus",
    "ContractKey",
    "FreenetEvent",
    "FreenetEventType",
    "GuildRoomState",
    "WorkspaceState",
]
