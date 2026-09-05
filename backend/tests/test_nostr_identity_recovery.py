"""POST /me/bind-nostr and POST /me/recover-via-nostr -- key recovery for
agent identities via a bound Nostr (NIP-06-compatible) keypair, so losing
the raw Vantage API key doesn't mean permanently losing account access.
Also regression-covers two bugs found and fixed in the same pass in the
pre-existing POST /federation/nostr-auth: it stored the newly-minted API
key in agents.api_key as PLAINTEXT (every other agent stores only the
sha256 hash), and — as a direct consequence — the "local_api_key" it
returned could never actually authenticate anything, since get_agent()
matches on sha256(presented key) against the stored value.
"""
import hashlib
import time

import pytest
from coincurve import PrivateKey

from backend.utils import _federation_nonces


def _sign_challenge_event(privkey: PrivateKey, nonce: str, kind: int = 22242) -> dict:
    """Builds and signs a NIP-01 event exactly the way the server verifies
    it: id = sha256(serialized [0, pubkey, created_at, kind, tags, content])."""
    import json as _json

    pubkey_hex = privkey.public_key_xonly.format().hex()
    created_at = int(time.time())
    tags = [["challenge", nonce]]
    content = ""
    ser = _json.dumps(
        [0, pubkey_hex, created_at, kind, tags, content],
        separators=(",", ":"), ensure_ascii=False,
    )
    event_id = hashlib.sha256(ser.encode("utf-8")).hexdigest()
    sig = privkey.sign_schnorr(bytes.fromhex(event_id)).hex()
    return {
        "id": event_id, "pubkey": pubkey_hex, "created_at": created_at,
        "kind": kind, "tags": tags, "content": content, "sig": sig,
    }


async def _get_nonce(client) -> str:
    resp = await client.get("/api/agents/federation/nostr-challenge")
    assert resp.status_code == 200, resp.text
    return resp.json()["nonce"]


# ── /me/bind-nostr ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bind_nostr_requires_auth(client):
    privkey = PrivateKey()
    nonce = await _get_nonce(client)
    event = _sign_challenge_event(privkey, nonce)
    resp = await client.post("/api/agents/me/bind-nostr", json={"event": event})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_bind_nostr_success_sets_pubkey(client, fresh_agent):
    agent = await fresh_agent()
    privkey = PrivateKey()
    pubkey_hex = privkey.public_key_xonly.format().hex()
    nonce = await _get_nonce(client)
    event = _sign_challenge_event(privkey, nonce)

    resp = await client.post(
        "/api/agents/me/bind-nostr",
        json={"event": event},
        headers={"X-Agent-Key": agent["api_key"]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["nostr_pubkey"] == pubkey_hex
    assert data["agent_name"] == agent["name"]


@pytest.mark.asyncio
async def test_bind_nostr_rejects_pubkey_already_bound_to_another_agent(client, fresh_agent):
    agent_a = await fresh_agent()
    agent_b = await fresh_agent()
    privkey = PrivateKey()

    nonce1 = await _get_nonce(client)
    event1 = _sign_challenge_event(privkey, nonce1)
    resp1 = await client.post(
        "/api/agents/me/bind-nostr", json={"event": event1},
        headers={"X-Agent-Key": agent_a["api_key"]},
    )
    assert resp1.status_code == 200, resp1.text

    # agent_b tries to bind the SAME pubkey agent_a already claimed.
    nonce2 = await _get_nonce(client)
    event2 = _sign_challenge_event(privkey, nonce2)
    resp2 = await client.post(
        "/api/agents/me/bind-nostr", json={"event": event2},
        headers={"X-Agent-Key": agent_b["api_key"]},
    )
    assert resp2.status_code == 409, resp2.text


@pytest.mark.asyncio
async def test_bind_nostr_rejects_invalid_signature(client, fresh_agent):
    agent = await fresh_agent()
    privkey = PrivateKey()
    other_privkey = PrivateKey()
    nonce = await _get_nonce(client)
    event = _sign_challenge_event(privkey, nonce)
    # Claim a DIFFERENT pubkey than the one that actually signed.
    event["pubkey"] = other_privkey.public_key_xonly.format().hex()

    resp = await client.post(
        "/api/agents/me/bind-nostr", json={"event": event},
        headers={"X-Agent-Key": agent["api_key"]},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_nonce_is_single_use(client, fresh_agent):
    agent = await fresh_agent()
    privkey = PrivateKey()
    nonce = await _get_nonce(client)
    event = _sign_challenge_event(privkey, nonce)

    resp1 = await client.post(
        "/api/agents/me/bind-nostr", json={"event": event},
        headers={"X-Agent-Key": agent["api_key"]},
    )
    assert resp1.status_code == 200, resp1.text

    # Same signed event/nonce again -- must be rejected, not replayable.
    resp2 = await client.post(
        "/api/agents/me/bind-nostr", json={"event": event},
        headers={"X-Agent-Key": agent["api_key"]},
    )
    assert resp2.status_code == 401


# ── /me/recover-via-nostr ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recover_unbound_pubkey_404s(client):
    privkey = PrivateKey()
    nonce = await _get_nonce(client)
    event = _sign_challenge_event(privkey, nonce)
    resp = await client.post("/api/agents/me/recover-via-nostr", json={"event": event})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_recover_via_nostr_full_round_trip(client, fresh_agent):
    """Bind, then recover with no X-Agent-Key at all, then confirm the
    NEWLY minted key actually authenticates -- the real regression test for
    the plaintext-storage bug: previously a returned "local_api_key" could
    never pass get_agent()'s sha256-hash comparison."""
    agent = await fresh_agent()
    privkey = PrivateKey()

    bind_nonce = await _get_nonce(client)
    bind_event = _sign_challenge_event(privkey, bind_nonce)
    bind_resp = await client.post(
        "/api/agents/me/bind-nostr", json={"event": bind_event},
        headers={"X-Agent-Key": agent["api_key"]},
    )
    assert bind_resp.status_code == 200, bind_resp.text

    recover_nonce = await _get_nonce(client)
    recover_event = _sign_challenge_event(privkey, recover_nonce)
    recover_resp = await client.post(
        "/api/agents/me/recover-via-nostr", json={"event": recover_event},
    )
    assert recover_resp.status_code == 200, recover_resp.text
    data = recover_resp.json()
    assert data["ok"] is True
    assert data["agent_name"] == agent["name"]
    new_key = data["api_key"]
    assert new_key and new_key != agent["api_key"]
    assert "warning" in data and "wallet" in data["warning"].lower()

    # The real proof: the new key must actually authenticate.
    whoami = await client.get(
        "/api/agents/me/profile", headers={"X-Agent-Key": new_key},
    )
    assert whoami.status_code == 200, whoami.text
    assert whoami.json()["name"] == agent["name"]


@pytest.mark.asyncio
async def test_recover_via_nostr_mints_a_different_key_each_time(client, fresh_agent):
    agent = await fresh_agent()
    privkey = PrivateKey()

    bind_nonce = await _get_nonce(client)
    bind_event = _sign_challenge_event(privkey, bind_nonce)
    await client.post(
        "/api/agents/me/bind-nostr", json={"event": bind_event},
        headers={"X-Agent-Key": agent["api_key"]},
    )

    nonce1 = await _get_nonce(client)
    resp1 = await client.post(
        "/api/agents/me/recover-via-nostr", json={"event": _sign_challenge_event(privkey, nonce1)},
    )
    nonce2 = await _get_nonce(client)
    resp2 = await client.post(
        "/api/agents/me/recover-via-nostr", json={"event": _sign_challenge_event(privkey, nonce2)},
    )
    assert resp1.status_code == 200 and resp2.status_code == 200
    key1, key2 = resp1.json()["api_key"], resp2.json()["api_key"]
    assert key1 != key2

    # Only the MOST RECENT recovered key should still authenticate --
    # each recovery overwrites the stored hash.
    still_works = await client.get("/api/agents/me/profile", headers={"X-Agent-Key": key2})
    assert still_works.status_code == 200
    no_longer_works = await client.get("/api/agents/me/profile", headers={"X-Agent-Key": key1})
    assert no_longer_works.status_code == 401


@pytest.mark.asyncio
async def test_recover_via_nostr_rejects_bad_signature(client, fresh_agent):
    agent = await fresh_agent()
    privkey = PrivateKey()
    other_privkey = PrivateKey()

    bind_nonce = await _get_nonce(client)
    await client.post(
        "/api/agents/me/bind-nostr",
        json={"event": _sign_challenge_event(privkey, bind_nonce)},
        headers={"X-Agent-Key": agent["api_key"]},
    )

    nonce = await _get_nonce(client)
    event = _sign_challenge_event(privkey, nonce)
    event["pubkey"] = other_privkey.public_key_xonly.format().hex()
    resp = await client.post("/api/agents/me/recover-via-nostr", json={"event": event})
    assert resp.status_code == 401


# ── /federation/nostr-auth regression: returned key must actually work ────

@pytest.mark.asyncio
async def test_federation_nostr_auth_returns_a_working_key(client):
    """Regression test for the plaintext-at-rest bug: first-time shadow
    agent creation used to store the raw key directly in agents.api_key
    (every other agent stores sha256(key)), so the returned
    local_api_key could never pass get_agent()'s hash check."""
    privkey = PrivateKey()
    nonce = await _get_nonce(client)
    event = _sign_challenge_event(privkey, nonce)
    resp = await client.post(
        "/api/agents/federation/nostr-auth",
        json={"event": event, "agent_name": "RemoteTest"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    key = data["local_api_key"]

    whoami = await client.get("/api/agents/me/profile", headers={"X-Agent-Key": key})
    assert whoami.status_code == 200, whoami.text
    assert whoami.json()["name"] == data["local_agent_name"]


@pytest.mark.asyncio
async def test_federation_nostr_auth_rotates_key_on_repeat_call(client):
    """Second bug in the same endpoint: repeat calls for the same shadow
    agent returned the identical stored (plaintext) key every time. Now
    that storage is hashed, a repeat call can't return the same raw key
    again -- it mints and stores a fresh one, so the old one stops
    working. Confirms that's real, not just "no crash"."""
    privkey = PrivateKey()

    nonce1 = await _get_nonce(client)
    resp1 = await client.post(
        "/api/agents/federation/nostr-auth",
        json={"event": _sign_challenge_event(privkey, nonce1)},
    )
    nonce2 = await _get_nonce(client)
    resp2 = await client.post(
        "/api/agents/federation/nostr-auth",
        json={"event": _sign_challenge_event(privkey, nonce2)},
    )
    assert resp1.status_code == 200 and resp2.status_code == 200
    assert resp1.json()["local_agent_name"] == resp2.json()["local_agent_name"]
    key1, key2 = resp1.json()["local_api_key"], resp2.json()["local_api_key"]
    assert key1 != key2

    still_works = await client.get("/api/agents/me/profile", headers={"X-Agent-Key": key2})
    assert still_works.status_code == 200
    no_longer_works = await client.get("/api/agents/me/profile", headers={"X-Agent-Key": key1})
    assert no_longer_works.status_code == 401
