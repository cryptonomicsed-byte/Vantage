"""FreenetService — the single interface Vantage uses to talk to Freenet.

No other module should import freenet internals directly.
The rest of Vantage calls get_freenet_service() and uses the interface below.

Current state: F1 (local node connection stub).
Node communication will use the Freenet WebSocket API once freenet-core
is running locally (third_party/freenet-core).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from .types import (
    ContractKey,
    FreenetEvent,
    FreenetEventType,
    FreenetNodeInfo,
    FreenetStatus,
    GuildRoomState,
    WorkspaceState,
)

log = logging.getLogger(__name__)

_service: Optional["FreenetService"] = None


class FreenetService:
    """Thin async interface over a local Freenet node.

    Phase F1: stub that tracks connectivity state and queues events.
    Phase F2: replace _send/_recv with real WebSocket calls to the node.
    """

    def __init__(self, node_url: str = "ws://127.0.0.1:50509"):
        self._node_url = node_url
        self._status = FreenetStatus.DISCONNECTED
        self._node_info = FreenetNodeInfo(
            node_id=None,
            status=FreenetStatus.DISCONNECTED,
        )
        self._subscriptions: Dict[str, List[Callable]] = {}
        self._ws = None
        self._lock = asyncio.Lock()

    # ── connection ──────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """Connect to the local Freenet node over WebSocket.
        Returns True if connected, False if the node is not running.
        Phase F1: logs the attempt and marks DISCONNECTED (node not yet running).
        Phase F2: replace with real aiohttp/websockets connect.
        """
        async with self._lock:
            self._status = FreenetStatus.CONNECTING
            try:
                # Phase F2: ws connect here
                # self._ws = await websockets.connect(self._node_url)
                # Phase F1: node not yet running, mark as disconnected gracefully
                log.info("Freenet node not yet running at %s (Phase F1 — expected)", self._node_url)
                self._status = FreenetStatus.DISCONNECTED
                self._node_info = FreenetNodeInfo(
                    node_id=None,
                    status=FreenetStatus.DISCONNECTED,
                    error="Node not running — start freenet-core locally",
                )
                return False
            except Exception as exc:
                self._status = FreenetStatus.ERROR
                self._node_info = FreenetNodeInfo(
                    node_id=None,
                    status=FreenetStatus.ERROR,
                    error=str(exc),
                )
                log.warning("Freenet connect failed: %s", exc)
                return False

    async def disconnect(self) -> None:
        async with self._lock:
            if self._ws:
                await self._ws.close()
                self._ws = None
            self._status = FreenetStatus.DISCONNECTED

    # ── contract operations ──────────────────────────────────────────────────

    async def create_contract(self, wasm_code: bytes, params: bytes) -> ContractKey:
        """Deploy a new Freenet WASM contract. Phase F3+."""
        self._require_connected()
        raise NotImplementedError("Freenet contract deployment — Phase F3")

    async def get_contract(self, key: ContractKey) -> Optional[Dict[str, Any]]:
        """Fetch current contract state. Phase F3+."""
        self._require_connected()
        raise NotImplementedError("Freenet get_contract — Phase F3")

    async def update_contract(self, key: ContractKey, delta: bytes) -> None:
        """Apply a state delta to a contract. Phase F3+."""
        self._require_connected()
        raise NotImplementedError("Freenet update_contract — Phase F3")

    # ── subscriptions ────────────────────────────────────────────────────────

    async def subscribe(self, contract_key: str, handler: Callable[[FreenetEvent], None]) -> str:
        """Subscribe to contract state changes. Phase F3+."""
        sub_id = f"sub_{contract_key[:8]}_{len(self._subscriptions)}"
        self._subscriptions.setdefault(contract_key, []).append(handler)
        log.debug("Subscribed to %s (sub_id=%s)", contract_key, sub_id)
        return sub_id

    async def unsubscribe(self, sub_id: str) -> None:
        log.debug("Unsubscribed %s", sub_id)

    # ── room operations (GuildRoomContract) ──────────────────────────────────

    async def create_room(self, guild_id: str, room_id: str, room_name: str) -> Optional[ContractKey]:
        """Create a new GuildRoom contract. Phase F3+."""
        log.info("Freenet create_room queued: guild=%s room=%s (Phase F3)", guild_id, room_id)
        return None

    async def join_room(self, contract_key: ContractKey, member_id: str, display_name: str) -> bool:
        log.info("Freenet join_room: %s as %s (Phase F3)", contract_key, member_id)
        return False

    async def leave_room(self, contract_key: ContractKey, member_id: str) -> bool:
        return False

    async def publish_room_message(self, contract_key: ContractKey, sender_id: str, content: str,
                                   reply_to: Optional[str] = None) -> bool:
        log.info("Freenet publish_room_message queued (Phase F3)")
        return False

    async def get_room_state(self, contract_key: ContractKey) -> Optional[GuildRoomState]:
        return None

    # ── workspace operations (WorkspaceContract) ─────────────────────────────

    async def get_workspace_state(self, contract_key: ContractKey) -> Optional[WorkspaceState]:
        return None

    async def update_workspace(self, contract_key: ContractKey, delta: Dict[str, Any]) -> bool:
        log.info("Freenet update_workspace queued (Phase F4)")
        return False

    # ── events ───────────────────────────────────────────────────────────────

    async def publish_event(self, event: FreenetEvent) -> bool:
        """Publish an event (typically from VantageEvent bridge). Phase F6+."""
        log.debug("Freenet event queued: %s (Phase F6)", event.event_type)
        return False

    async def read_events(self, contract_key: str, since_sequence: int = 0) -> List[FreenetEvent]:
        return []

    # ── state ────────────────────────────────────────────────────────────────

    async def get_state(self, contract_key: str) -> Optional[Dict[str, Any]]:
        return None

    async def apply_delta(self, contract_key: str, delta: Dict[str, Any]) -> bool:
        return False

    # ── peers ────────────────────────────────────────────────────────────────

    async def get_peers(self) -> List[Dict[str, Any]]:
        return []

    async def health(self) -> FreenetNodeInfo:
        return self._node_info

    # ── internal ─────────────────────────────────────────────────────────────

    def _require_connected(self) -> None:
        if self._status != FreenetStatus.CONNECTED:
            raise RuntimeError(
                f"Freenet node not connected (status={self._status.value}). "
                "Start freenet-core locally and call connect() first."
            )

    @property
    def status(self) -> FreenetStatus:
        return self._status

    @property
    def is_connected(self) -> bool:
        return self._status == FreenetStatus.CONNECTED


def get_freenet_service() -> FreenetService:
    global _service
    if _service is None:
        from ..config import settings
        node_url = getattr(settings, "FREENET_NODE_URL", "ws://127.0.0.1:50509")
        _service = FreenetService(node_url=node_url)
    return _service
