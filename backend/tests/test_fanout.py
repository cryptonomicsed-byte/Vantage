"""Phase 4: fan-out.

The point of these is the failure mode the old serial loop had. A socket
that has stopped reading used to stall delivery for everyone behind it in the
list; it must now cost only itself.
"""
import asyncio

import pytest

from backend import fanout
from backend.routers import conductor as bridge


class FakeSocket:
    def __init__(self, behaviour="ok", delay=0.0):
        self.behaviour = behaviour
        self.delay = delay
        self.received = []

    async def send_json(self, payload):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.behaviour == "raise":
            raise RuntimeError("peer is gone")
        if self.behaviour == "hang":
            await asyncio.sleep(3600)
        self.received.append(payload)


@pytest.mark.asyncio
async def test_every_live_socket_receives_the_payload():
    sockets = {FakeSocket() for _ in range(5)}
    dead = await fanout.deliver(sockets, {"type": "ping"})

    assert dead == set()
    assert all(s.received == [{"type": "ping"}] for s in sockets)


@pytest.mark.asyncio
async def test_a_broken_socket_is_reported_and_the_rest_still_get_it():
    good_a, good_b = FakeSocket(), FakeSocket()
    broken = FakeSocket(behaviour="raise")

    dead = await fanout.deliver({good_a, good_b, broken}, {"n": 1})

    assert dead == {broken}
    assert good_a.received and good_b.received


@pytest.mark.asyncio
async def test_a_slow_socket_does_not_delay_the_others():
    """The defect in the original serial loop. With concurrent delivery the
    fast peers are served while the slow one is still being waited on."""
    slow = FakeSocket(delay=0.4)
    fast = [FakeSocket() for _ in range(3)]

    start = asyncio.get_event_loop().time()
    await fanout.deliver({slow, *fast}, {"n": 2})
    elapsed = asyncio.get_event_loop().time() - start

    assert all(s.received for s in fast)
    assert slow.received
    # Serial delivery would sum the delays; concurrent overlaps them.
    assert elapsed < 0.4 * 2


@pytest.mark.asyncio
async def test_a_socket_that_never_completes_is_dropped_not_waited_on_forever(monkeypatch):
    monkeypatch.setattr(fanout, "_SEND_TIMEOUT", 0.1)
    hung = FakeSocket(behaviour="hang")
    good = FakeSocket()

    dead = await fanout.deliver({hung, good}, {"n": 3})

    assert dead == {hung}
    assert good.received


@pytest.mark.asyncio
async def test_delivering_to_nobody_is_not_an_error():
    assert await fanout.deliver(set(), {"n": 4}) == set()


@pytest.mark.asyncio
async def test_conductor_forwarding_is_a_noop_when_none_is_configured(monkeypatch):
    """Deployments before Phase 2 must be entirely unaffected."""
    monkeypatch.setattr(bridge, "CONDUCTOR_URL", "")
    await fanout.to_conductor("guild.test", {"type": "x"})


@pytest.mark.asyncio
async def test_conductor_forwarding_failures_are_swallowed(monkeypatch):
    """A live feed update that does not arrive is cosmetic; anything that
    matters is already in the relay log."""
    monkeypatch.setattr(bridge, "CONDUCTOR_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(bridge, "_SHARED_SECRET", "s" * 16)
    await fanout.to_conductor("guild.test", {"type": "x"})


@pytest.mark.asyncio
async def test_gossip_still_reaches_in_process_subscribers(monkeypatch):
    """The compatibility path: existing /ws/gossip consumers must keep
    working while the migration is in flight."""
    from backend.agents import _gossip_channels
    from backend.utils import _broadcast_gossip

    monkeypatch.setattr(bridge, "CONDUCTOR_URL", "")
    socket = FakeSocket()
    _gossip_channels["guild.compat"] = {socket}
    try:
        await _broadcast_gossip("guild.compat", {"type": "channel_message"})
        assert socket.received == [{"channel": "guild.compat", "type": "channel_message"}]
    finally:
        _gossip_channels.pop("guild.compat", None)


@pytest.mark.asyncio
async def test_dead_gossip_subscribers_are_pruned(monkeypatch):
    from backend.agents import _gossip_channels
    from backend.utils import _broadcast_gossip

    monkeypatch.setattr(bridge, "CONDUCTOR_URL", "")
    broken = FakeSocket(behaviour="raise")
    _gossip_channels["guild.prune"] = {broken}
    try:
        await _broadcast_gossip("guild.prune", {"type": "x"})
        assert _gossip_channels["guild.prune"] == set()
    finally:
        _gossip_channels.pop("guild.prune", None)
