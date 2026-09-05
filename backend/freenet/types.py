"""Freenet adapter types — shared by all freenet modules."""
from __future__ import annotations

import dataclasses
from enum import Enum
from typing import Any, Dict, List, Optional


class FreenetStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class FreenetEventType(str, Enum):
    # Contract lifecycle
    CONTRACT_CREATED = "contract.created"
    CONTRACT_UPDATED = "contract.updated"
    CONTRACT_DELTA = "contract.delta"

    # Room events
    ROOM_CREATED = "room.created"
    ROOM_MESSAGE = "room.message"
    ROOM_REACTION = "room.reaction"
    ROOM_MEMBER_JOINED = "room.member.joined"
    ROOM_MEMBER_LEFT = "room.member.left"

    # Workspace events
    WORKSPACE_UPDATED = "workspace.updated"
    WORKSPACE_DOCUMENT = "workspace.document"

    # Peer events
    PEER_CONNECTED = "peer.connected"
    PEER_DISCONNECTED = "peer.disconnected"

    # Internal
    HEALTH = "health"
    ERROR = "error"


@dataclasses.dataclass
class ContractKey:
    """A Freenet contract identity — the BLAKE3 hash of the contract WASM + params."""
    key: str  # base58-encoded contract key
    instance_id: Optional[str] = None  # human-readable alias

    def __str__(self) -> str:
        return self.key


@dataclasses.dataclass
class FreenetEvent:
    event_type: FreenetEventType
    contract_key: Optional[str]
    payload: Dict[str, Any]
    sequence: int = 0
    timestamp: Optional[str] = None


@dataclasses.dataclass
class GuildRoomMember:
    member_id: str
    display_name: str
    nostr_pubkey: Optional[str] = None
    joined_at: Optional[str] = None
    role: str = "member"


@dataclasses.dataclass
class GuildRoomMessage:
    message_id: str
    sender_id: str
    content: str
    timestamp: str
    reply_to: Optional[str] = None
    reactions: Dict[str, List[str]] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class GuildRoomState:
    """State replicated by GuildRoomContract across Freenet peers.

    Never store private keys, sealed seeds, or any secret here.
    Contract state is visible to all participating peers.
    """
    guild_id: str
    room_id: str
    room_name: str
    members: Dict[str, GuildRoomMember] = dataclasses.field(default_factory=dict)
    messages: List[GuildRoomMessage] = dataclasses.field(default_factory=list)
    permissions: Dict[str, List[str]] = dataclasses.field(default_factory=dict)
    sequence: int = 0
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class WorkspaceState:
    """State replicated by WorkspaceContract across Freenet peers.

    Artifact references, shared docs, workspace activity.
    Never private memory (that stays in Ọmọ Kọ́dà2).
    """
    guild_id: str
    workspace_id: str
    workspace_name: str
    members: Dict[str, Dict[str, Any]] = dataclasses.field(default_factory=dict)
    shared_documents: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    artifact_refs: List[str] = dataclasses.field(default_factory=list)
    activity: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    sequence: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class FreenetNodeInfo:
    node_id: Optional[str]
    status: FreenetStatus
    peer_count: int = 0
    contract_count: int = 0
    subscription_count: int = 0
    version: Optional[str] = None
    error: Optional[str] = None
