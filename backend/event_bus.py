"""Unified VantageEvent bus.

Every significant state change in Vantage emits a VantageEvent.
Consumers subscribe by event_type. The bus is in-process (asyncio queues);
persistence and fan-out to Nostr/Freenet happen via registered sinks.

Usage:
    from .event_bus import emit, subscribe

    # emit from anywhere
    await emit(VantageEvent(
        event_type="TaskClaimed",
        actor_id=agent_id,
        aggregate_id=str(task_id),
        aggregate_type="task",
        payload={"task_id": task_id, "agent_name": agent_name},
    ))

    # subscribe (e.g. in a router startup)
    async def my_handler(event: VantageEvent):
        ...
    subscribe("TaskClaimed", my_handler)
"""
import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

import aiosqlite

from .db import DB_PATH

logger = logging.getLogger(__name__)

# ── VantageEvent dataclass ────────────────────────────────────────────────────

@dataclass
class VantageEvent:
    event_type: str
    aggregate_id: str
    aggregate_type: str
    payload: dict
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    actor_id: Optional[int] = None
    actor_name: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload_hash: Optional[str] = None
    previous_event_hash: Optional[str] = None
    signature: Optional[str] = None
    source: str = "vantage"
    # P0-4: full identity chain — every event carries these so Mycelium/Zàngbétò
    # can trace back to the human principal, session, and causal receipt.
    principal_id:  Optional[str] = None
    session_id:    Optional[str] = None
    execution_id:  Optional[str] = None
    receipt_id:    Optional[str] = None

    def __post_init__(self):
        # Auto-compute payload_hash if not provided
        if self.payload_hash is None and self.payload is not None:
            canonical = json.dumps(self.payload, sort_keys=True, separators=(",", ":"))
            self.payload_hash = hashlib.sha256(canonical.encode()).hexdigest()


# ── Internal bus state ────────────────────────────────────────────────────────

_event_queue: asyncio.Queue = asyncio.Queue()
_queue_loop: "asyncio.AbstractEventLoop | None" = None


def _queue() -> asyncio.Queue:
    """The queue for the loop that is running now.

    An asyncio.Queue binds to the first loop that awaits on it. A module-level
    queue therefore breaks the moment the app runs on a second loop (a second
    TestClient in one process, a worker restart): every get() raises "bound to a
    different event loop". Rebinding per loop keeps the bus usable; events left
    on a dead loop's queue had no consumer anyway.
    """
    global _event_queue, _queue_loop
    loop = asyncio.get_running_loop()
    if _queue_loop is not loop:
        if _queue_loop is not None:
            _event_queue = asyncio.Queue()
        _queue_loop = loop
    return _event_queue


_ERROR_BACKOFF_MIN = 0.05
_ERROR_BACKOFF_MAX = 5.0
_subscribers: dict[str, list[Callable]] = {}
_sinks: list[Callable] = []


# ── Public API ────────────────────────────────────────────────────────────────

def subscribe(event_type: str, handler: Callable) -> None:
    """Register a coroutine handler for a given event_type.

    Handlers are called in registration order. Exceptions in individual
    handlers are caught and logged so they don't drop other handlers.
    Use event_type="*" to receive all events.
    """
    _subscribers.setdefault(event_type, []).append(handler)


def register_sink(sink: Callable) -> None:
    """Register a sink that receives every event (for persistence / fan-out).

    The sink coroutine signature must be: async def my_sink(event: VantageEvent).
    Exceptions are caught and logged.
    """
    _sinks.append(sink)


async def emit(event: VantageEvent) -> None:
    """Put an event on the queue. Returns immediately (non-blocking)."""
    await _queue().put(event)


# ── SQLite audit log ──────────────────────────────────────────────────────────

async def _ensure_event_log_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=10000")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor_id INTEGER,
                actor_name TEXT,
                aggregate_id TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                payload TEXT NOT NULL,
                payload_hash TEXT,
                previous_event_hash TEXT,
                source TEXT NOT NULL DEFAULT 'vantage',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_event_log_type ON event_log(event_type)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_event_log_actor ON event_log(actor_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_event_log_aggregate ON event_log(aggregate_id)")
        await db.commit()


async def _persist_event(event: VantageEvent) -> None:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=10000")
            await db.execute(
                """INSERT INTO event_log
                   (event_id, event_type, actor_id, actor_name, aggregate_id, aggregate_type,
                    timestamp, payload, payload_hash, previous_event_hash, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.event_id,
                    event.event_type,
                    event.actor_id,
                    event.actor_name,
                    event.aggregate_id,
                    event.aggregate_type,
                    event.timestamp,
                    json.dumps(event.payload),
                    event.payload_hash,
                    event.previous_event_hash,
                    event.source,
                ),
            )
            await db.commit()
    except Exception as exc:
        logger.warning("event_bus: failed to persist event %s: %s", event.event_id, exc)


# ── Dispatch loop ─────────────────────────────────────────────────────────────

async def _dispatch_loop() -> None:
    """Background coroutine: drains the queue and fans out to subscribers + sinks."""
    await _ensure_event_log_table()
    logger.info("event_bus: dispatch loop started")
    backoff = _ERROR_BACKOFF_MIN
    while True:
        try:
            queue = _queue()
            event: VantageEvent = await queue.get()
            # Persist to audit log
            await _persist_event(event)

            # Fan out to type-specific subscribers
            handlers = list(_subscribers.get(event.event_type, []))
            # Also fans to wildcard subscribers
            handlers += list(_subscribers.get("*", []))
            for handler in handlers:
                try:
                    await handler(event)
                except Exception as exc:
                    logger.warning(
                        "event_bus: handler %s failed for event %s: %s",
                        getattr(handler, "__name__", repr(handler)),
                        event.event_type,
                        exc,
                    )

            # Fan out to registered sinks
            for sink in list(_sinks):
                try:
                    await sink(event)
                except Exception as exc:
                    logger.warning(
                        "event_bus: sink %s failed for event %s: %s",
                        getattr(sink, "__name__", repr(sink)),
                        event.event_type,
                        exc,
                    )

            queue.task_done()
            backoff = _ERROR_BACKOFF_MIN
        except asyncio.CancelledError:
            logger.info("event_bus: dispatch loop cancelled")
            break
        except Exception as exc:
            # Never retry without yielding. An error that recurs instantly (a
            # loop-bound queue, a dead DB) otherwise becomes a hot spin that
            # starves the event loop: observed 3.5M identical log lines in three
            # minutes and an app whose startup never completed.
            logger.error("event_bus: unexpected error in dispatch loop: %s "
                         "(retrying in %.2fs)", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _ERROR_BACKOFF_MAX)


async def start_dispatch_loop() -> None:
    """Entry point called from app startup. Runs the dispatch loop forever."""
    await _dispatch_loop()
