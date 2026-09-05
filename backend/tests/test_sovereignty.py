"""Key custody: an account taking ownership of its own identity.

The migration itself is the easy half. The half that matters is what happens
*after*: this instance must genuinely stop being able to sign, and the ~18
modules that derive agent keys directly must fail loudly rather than mint a
phantom identity under a pubkey the account does not control.
"""
import secrets

import pytest
import pytest_asyncio
from coincurve import PrivateKey

from backend import coordination as coord
from backend import sovereignty
from backend.buzz_client import build_event
from backend.buzz_identity import derive_buzz_keypair, public_key_xonly_hex
from backend.coordination_join import KIND_CLIENT_AUTH


@pytest_asyncio.fixture(scope="module", autouse=True)
async def schema(client):
    await coord.init_coordination_db()
    await sovereignty.init_sovereignty_db()


@pytest_asyncio.fixture(autouse=True)
async def _no_rate_limit():
    """These endpoints are capped at 10/minute, which is right for an
    irreversible identity operation and wrong for a test file that exercises
    it two dozen times from one address. The cap stays in production; it is
    lifted here so the tests measure behaviour rather than the limiter.
    """
    from backend.routers.sovereignty import _limiter

    _limiter.enabled = False
    yield
    _limiter.enabled = True


@pytest_asyncio.fixture
async def agent(client, fresh_agent):
    import aiosqlite
    from backend.db import get_db

    made = await fresh_agent()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM agents WHERE name=?", (made["name"],))
        made["id"] = dict(await cur.fetchone())["id"]
    return made


class OwnKey:
    """A keypair the account generated itself. Vantage sees only `pubkey`."""

    def __init__(self):
        self.pk = PrivateKey()
        self.pubkey = public_key_xonly_hex(self.pk)

    def sign(self, challenge):
        return build_event(
            self.pk, kind=KIND_CLIENT_AUTH, content="",
            tags=[["relay", "ws://localhost:3000"], ["challenge", challenge]],
        )


def _hdr(agent):
    return {"X-Agent-Key": agent["api_key"]}


async def _migrate(client, agent, key):
    ch = await client.post(
        "/api/identity/custody/challenge", data={"pubkey": key.pubkey}, headers=_hdr(agent)
    )
    assert ch.status_code == 200, ch.text
    challenge = ch.json()["challenge"]
    return await client.post(
        "/api/identity/custody/confirm",
        json={"signed_event": key.sign(challenge), "challenge": challenge,
              "acknowledge_irreversible": True},
        headers=_hdr(agent),
    )


# ── before migration ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_accounts_start_under_instance_custody(client, agent):
    resp = await client.get("/api/identity/custody", headers=_hdr(agent))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key_custody"] == "derived"
    assert body["sovereign"] is False
    assert "can sign on its behalf" in body["explanation"]


@pytest.mark.asyncio
async def test_the_instance_can_sign_before_migration(client, agent):
    key = await derive_buzz_keypair(agent["id"])
    assert public_key_xonly_hex(key)


# ── the migration ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_agent_can_take_custody_of_its_identity(client, agent):
    key = OwnKey()
    resp = await _migrate(client, agent, key)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key_custody"] == "self"
    assert body["pubkey"] == key.pubkey
    assert body["sealed_seed_destroyed"] is True


@pytest.mark.asyncio
async def test_the_instance_cannot_sign_afterwards(client, agent):
    """The guarantee. Every one of the ~18 modules that derive agent keys
    goes through this chokepoint."""
    await _migrate(client, agent, OwnKey())

    with pytest.raises(sovereignty.SelfCustodyError) as exc:
        await derive_buzz_keypair(agent["id"])
    assert "holds its own key" in str(exc.value)


@pytest.mark.asyncio
async def test_the_sealed_seed_is_actually_destroyed(client, agent):
    """Not tombstoned, not re-encrypted — NULL. A retained seed would leave
    the instance able to sign, which is the power being given up."""
    await _migrate(client, agent, OwnKey())

    from backend.db import get_db
    async with get_db() as db:
        cur = await db.execute(
            "SELECT sealed_seed_enc, sealed_seed_hex FROM agents WHERE id=?", (agent["id"],)
        )
        row = await cur.fetchone()
    assert row[0] is None
    assert row[1] is None


@pytest.mark.asyncio
async def test_the_principal_switches_to_the_new_pubkey(client, agent):
    """Identity continuity: the same principal row, a new key. History
    published under the old pubkey stays attributed to the same principal."""
    principal_before = await coord.get_or_create_agent_principal(agent["id"])
    key = OwnKey()
    await _migrate(client, agent, key)

    principal_after = await coord._get_principal_by_pubkey(key.pubkey)
    assert principal_after["id"] == principal_before["id"]
    assert principal_after["key_custody"] == "self"


@pytest.mark.asyncio
async def test_coordination_refuses_to_sign_for_a_migrated_principal(client, agent):
    """signing_key_for_principal keys off custody, not principal kind — a
    native agent that migrated must be treated exactly like an outside one."""
    key = OwnKey()
    await _migrate(client, agent, key)

    principal = await coord._get_principal_by_pubkey(key.pubkey)
    assert principal["kind"] == "agent"          # still a native agent
    assert await coord.signing_key_for_principal(principal) is None


@pytest.mark.asyncio
async def test_the_migration_is_recorded_for_audit(client, agent):
    principal = await coord.get_or_create_agent_principal(agent["id"])
    prior = principal["pubkey"]
    key = OwnKey()
    await _migrate(client, agent, key)

    from backend.db import get_db
    async with get_db() as db:
        cur = await db.execute(
            "SELECT prior_pubkey, new_pubkey FROM custody_migrations "
            "WHERE subject_kind='agent' AND subject_id=?", (agent["id"],)
        )
        row = await cur.fetchone()
    assert row[0] == prior
    assert row[1] == key.pubkey


# ── what it refuses ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_confirming_without_acknowledging_is_refused(client, agent):
    key = OwnKey()
    ch = await client.post(
        "/api/identity/custody/challenge", data={"pubkey": key.pubkey}, headers=_hdr(agent)
    )
    challenge = ch.json()["challenge"]

    resp = await client.post(
        "/api/identity/custody/confirm",
        json={"signed_event": key.sign(challenge), "challenge": challenge},
        headers=_hdr(agent),
    )
    assert resp.status_code == 422
    assert "irreversible" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_a_key_you_do_not_hold_cannot_take_your_identity(client, agent):
    """Claim one pubkey, sign with another — the whole reason for the proof."""
    claimed, attacker = OwnKey(), OwnKey()
    ch = await client.post(
        "/api/identity/custody/challenge", data={"pubkey": claimed.pubkey}, headers=_hdr(agent)
    )
    challenge = ch.json()["challenge"]

    resp = await client.post(
        "/api/identity/custody/confirm",
        json={"signed_event": attacker.sign(challenge), "challenge": challenge,
              "acknowledge_irreversible": True},
        headers=_hdr(agent),
    )
    assert resp.status_code == 401
    assert "does not match" in resp.json()["detail"]
    # And custody is unchanged.
    assert (await sovereignty.agent_custody(agent["id"])) == "derived"


@pytest.mark.asyncio
async def test_a_challenge_cannot_be_replayed(client, agent):
    key = OwnKey()
    ch = await client.post(
        "/api/identity/custody/challenge", data={"pubkey": key.pubkey}, headers=_hdr(agent)
    )
    challenge = ch.json()["challenge"]
    body = {"signed_event": key.sign(challenge), "challenge": challenge,
            "acknowledge_irreversible": True}

    first = await client.post("/api/identity/custody/confirm", json=body, headers=_hdr(agent))
    assert first.status_code == 200
    second = await client.post("/api/identity/custody/confirm", json=body, headers=_hdr(agent))
    assert second.status_code == 401


@pytest.mark.asyncio
async def test_one_account_cannot_migrate_using_another_accounts_challenge(client, agent, fresh_agent):
    import aiosqlite
    from backend.db import get_db

    other = await fresh_agent()
    key = OwnKey()
    ch = await client.post(
        "/api/identity/custody/challenge", data={"pubkey": key.pubkey}, headers=_hdr(agent)
    )
    challenge = ch.json()["challenge"]

    resp = await client.post(
        "/api/identity/custody/confirm",
        json={"signed_event": key.sign(challenge), "challenge": challenge,
              "acknowledge_irreversible": True},
        headers={"X-Agent-Key": other["api_key"]},
    )
    assert resp.status_code == 401
    assert "different account" in resp.json()["detail"]
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM agents WHERE name=?", (other["name"],))
        other_id = dict(await cur.fetchone())["id"]
    assert (await sovereignty.agent_custody(other_id)) == "derived"


@pytest.mark.asyncio
async def test_a_pubkey_already_in_use_is_refused(client, agent, fresh_agent):
    """Two principals sharing an identity would make the log ambiguous about
    who said what."""
    key = OwnKey()
    await _migrate(client, agent, key)

    second = await fresh_agent()
    resp = await client.post(
        "/api/identity/custody/challenge", data={"pubkey": key.pubkey},
        headers={"X-Agent-Key": second["api_key"]},
    )
    assert resp.status_code == 422
    assert "already belongs" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_migrating_twice_is_refused(client, agent):
    await _migrate(client, agent, OwnKey())
    resp = await client.post(
        "/api/identity/custody/challenge", data={"pubkey": OwnKey().pubkey}, headers=_hdr(agent)
    )
    assert resp.status_code == 409
    assert "already self-custody" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_a_malformed_pubkey_is_refused(client, agent):
    resp = await client.post(
        "/api/identity/custody/challenge", data={"pubkey": "z" * 64}, headers=_hdr(agent)
    )
    assert resp.status_code == 422
    assert "hex" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_custody_requires_authentication(client):
    assert (await client.get("/api/identity/custody")).status_code == 401
