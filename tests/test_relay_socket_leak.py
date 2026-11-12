"""Regression: relay sessions must never outlive a failed connect/auth or a
dropped stream. Live, buzz_inbound's reconnect loop orphaned one socket per
auth timeout (~40/hour); keepalive pings held each open until the process hit
its 1024-fd limit and every request, including sqlite opens, failed (502)."""
import asyncio

import pytest

from backend import buzz_inbound
from backend.nostr import client as nostr_client


class _FakeWS:
    opened = []

    def __init__(self):
        self.closed = False
        _FakeWS.opened.append(self)

    async def close(self):
        self.closed = True

    async def send(self, _):
        pass

    async def recv(self):
        await asyncio.sleep(3600)


async def _fake_connect(*_a, **_k):
    return _FakeWS()


@pytest.fixture(autouse=True)
def _fake_ws(monkeypatch):
    _FakeWS.opened = []
    monkeypatch.setattr(nostr_client.websockets, "connect", _fake_connect)


@pytest.mark.asyncio
async def test_session_close_is_idempotent_and_reconnect_releases_old_socket():
    s = nostr_client.NostrSession("ws://relay.invalid", pk=None)
    await s.connect()
    first = s.ws
    await s.connect()
    assert first.closed and not s.ws.closed
    await s.close()
    await s.close()
    assert s.ws is None and all(w.closed for w in _FakeWS.opened)


@pytest.mark.asyncio
async def test_async_with_closes_on_error():
    with pytest.raises(RuntimeError):
        async with nostr_client.NostrSession("ws://relay.invalid", pk=None):
            raise RuntimeError("boom")
    assert len(_FakeWS.opened) == 1 and _FakeWS.opened[0].closed


@pytest.mark.asyncio
async def test_inbound_listener_closes_socket_on_every_failed_pass(monkeypatch):
    async def _channel():
        return "chan"

    async def _pk():
        return None

    async def _yes(*_a, **_k):
        return True

    async def _member(*_a, **_k):
        return True, "ok"

    async def _ts():
        return 0

    async def _auth_timeout(self, *a, **k):
        raise asyncio.TimeoutError()

    passes = {"n": 0}
    real_sleep = asyncio.sleep

    async def _sleep(_s):
        passes["n"] += 1
        if passes["n"] >= 5:
            raise asyncio.CancelledError()
        await real_sleep(0)

    from backend import relay_admin
    monkeypatch.setattr(buzz_inbound, "get_main_feed_channel", _channel)
    monkeypatch.setattr(buzz_inbound, "derive_instance_keypair", _pk)
    monkeypatch.setattr(buzz_inbound, "public_key_xonly_hex", lambda _pk: "ab" * 32)
    monkeypatch.setattr(relay_admin, "relay_has_channel", _yes)
    monkeypatch.setattr(relay_admin, "ensure_member", _member)
    monkeypatch.setattr(buzz_inbound, "_get_last_ts", _ts)
    monkeypatch.setattr(nostr_client.NostrSession, "authenticate", _auth_timeout)
    monkeypatch.setattr(buzz_inbound.asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await buzz_inbound.run_inbound_listener()
    assert len(_FakeWS.opened) >= 4
    assert all(w.closed for w in _FakeWS.opened), "a failed pass orphaned its relay socket"
async def test_auth_timeout_names_the_relay_that_never_challenged():
    """A relay with no NIP-42 never sends AUTH. The error used to be a bare
    TimeoutError(), which hid that the session was pointed at the wrong relay."""
    import asyncio

    import pytest
    from coincurve import PrivateKey

    from backend.nostr.client import NostrSession

    class _SilentWS:
        async def recv(self):
            await asyncio.sleep(3600)

    s = NostrSession("wss://relay.example/vantage-relay", PrivateKey())
    s.ws = _SilentWS()
    with pytest.raises(asyncio.TimeoutError) as exc:
        await s.authenticate(timeout=0.05)
    assert "wss://relay.example/vantage-relay" in str(exc.value)
    assert "NIP-42" in str(exc.value)
