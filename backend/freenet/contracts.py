"""Freenet contract definitions for Vantage.

Each contract is a WASM module compiled from Rust (in third_party/freenet-core
or a Vantage-specific contracts/ directory). This module holds:
  - Contract key registry (known deployed contracts)
  - State schema validation
  - Delta computation helpers

Phase F3: GuildRoomContract
Phase F4: WorkspaceContract
Phase F5+: ArtifactRegistryContract, ReputationContract
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from .types import ContractKey, GuildRoomMessage, GuildRoomState, WorkspaceState


class GuildRoomContract:
    """Python representation of the Freenet GuildRoom WASM contract.

    The actual contract logic runs on Freenet peers as WASM. This class
    provides the Python-side state management and delta computation.

    Merge semantics:
      - messages: append-only, deduplicated by message_id
      - members: last-write-wins per member_id (by joined_at timestamp)
      - reactions: set-union per message
      - sequence: max(local, remote) + 1
    """

    CONTRACT_ID = "guild-room-v1"

    @staticmethod
    def new_state(guild_id: str, room_id: str, room_name: str) -> GuildRoomState:
        import datetime
        return GuildRoomState(
            guild_id=guild_id,
            room_id=room_id,
            room_name=room_name,
            created_at=datetime.datetime.utcnow().isoformat(),
        )

    @staticmethod
    def merge(base: GuildRoomState, incoming: GuildRoomState) -> GuildRoomState:
        """Merge two room states. Idempotent and commutative (CRDT-like).
        Called when Freenet delivers a state update from a peer.
        """
        merged_members = {**base.members}
        for mid, member in incoming.members.items():
            existing = merged_members.get(mid)
            if not existing or (member.joined_at or "") > (existing.joined_at or ""):
                merged_members[mid] = member

        seen = {m.message_id for m in base.messages}
        merged_messages = list(base.messages)
        for msg in incoming.messages:
            if msg.message_id not in seen:
                merged_messages.append(msg)
                seen.add(msg.message_id)
        merged_messages.sort(key=lambda m: m.timestamp)

        return GuildRoomState(
            guild_id=base.guild_id,
            room_id=base.room_id,
            room_name=base.room_name,
            members=merged_members,
            messages=merged_messages,
            permissions={**base.permissions, **incoming.permissions},
            sequence=max(base.sequence, incoming.sequence) + 1,
            created_at=base.created_at,
        )

    @staticmethod
    def compute_delta(old: GuildRoomState, new: GuildRoomState) -> Dict[str, Any]:
        """Compute a minimal delta between two states for efficient sync."""
        old_ids = {m.message_id for m in old.messages}
        new_messages = [m for m in new.messages if m.message_id not in old_ids]

        old_members = set(old.members.keys())
        new_member_ids = set(new.members.keys())

        return {
            "new_messages": [vars(m) for m in new_messages],
            "joined_members": {
                mid: vars(m) for mid, m in new.members.items()
                if mid not in old_members
            },
            "left_members": list(old_members - new_member_ids),
            "sequence": new.sequence,
        }

    @staticmethod
    def state_hash(state: GuildRoomState) -> str:
        canonical = json.dumps(state.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()


class WorkspaceContract:
    """Python representation of the Freenet Workspace WASM contract.

    Phase F4. Shared documents, workspace activity, artifact references.
    Never private memory (stays in Ọmọ Kọ́dà2).
    """

    CONTRACT_ID = "workspace-v1"

    @staticmethod
    def new_state(guild_id: str, workspace_id: str, workspace_name: str) -> WorkspaceState:
        return WorkspaceState(
            guild_id=guild_id,
            workspace_id=workspace_id,
            workspace_name=workspace_name,
        )

    @staticmethod
    def add_artifact_ref(state: WorkspaceState, artifact_id: str) -> WorkspaceState:
        if artifact_id not in state.artifact_refs:
            state.artifact_refs.append(artifact_id)
            state.sequence += 1
        return state

    @staticmethod
    def state_hash(state: WorkspaceState) -> str:
        canonical = json.dumps(state.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()
