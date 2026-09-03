"""Live event fan-out.

Phase 4 of docs/VANTAGE_SWARM_COORDINATION_SPEC.md. Vantage's original
fan-out is `_gossip_channels` in main.py: a dict of WebSocket sets held in
one Python process, delivered to in a serial `await` loop. It has three real
faults.

  1. **It cannot span workers.** The dict lives in one process, so with two
     uvicorn workers each holds half the subscribers and every event reaches
     only half of them.
  2. **One slow socket blocks the rest.** A serial `await ws.send_json(...)`
     loop stalls on the first unresponsive peer, delaying everyone behind it.
  3. **A restart drops every subscription.**

This module routes broadcasts through the Conductor, whose `Registry`-based
pub/sub has none of those properties, while keeping the in-process path so
existing `/ws/gossip` subscribers keep working untouched. The in-process
send is also made concurrent here, which fixes fault 2 even for deployments
that never run a Conductor.

Retiring the in-process path entirely would break every current consumer
(SwarmMap, ActivityTicker, the mesh views), so it stays until they have moved
to the Conductor. "Retire into" is a migration, not a deletion.
"""
import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# A send that has not completed in this long is a peer that is not reading.
# Dropping it beats holding a broadcast open for everyone else.
_SEND_TIMEOUT = 5.0


async def _send_one(ws: Any, payload: dict) -> bool:
    try:
        await asyncio.wait_for(ws.send_json(payload), timeout=_SEND_TIMEOUT)
        return True
    except Exception:
        return False


async def deliver(sockets: set, payload: dict) -> set:
    """Send to every socket concurrently. Returns the ones that failed.

    Concurrent rather than serial: this is fault 2 above. One peer that has
    stopped reading should cost that peer its subscription, not delay the
    broadcast for everyone else.
    """
    targets = list(sockets)
    if not targets:
        return set()
    results = await asyncio.gather(*(_send_one(ws, payload) for ws in targets))
    return {ws for ws, ok in zip(targets, results) if not ok}


async def to_conductor(topic: str, event: dict) -> None:
    """Forward a broadcast to the Conductor's pub/sub.

    Fire-and-forget by design: a live feed update that does not arrive is a
    cosmetic loss, and the durable record of anything that matters is already
    in the relay log. No Conductor configured makes this a no-op, which is
    what keeps pre-Phase-2 deployments identical.
    """
    from .routers.conductor import CONDUCTOR_URL, _SHARED_SECRET

    if not CONDUCTOR_URL or not _SHARED_SECRET:
        return

    import httpx

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(
                f"{CONDUCTOR_URL.rstrip('/')}/broadcast",
                json={"topic": topic, "event": event},
                headers={"X-Conductor-Secret": _SHARED_SECRET},
            )
    except Exception as exc:
        logger.debug("conductor broadcast failed (non-fatal): %s", exc)
