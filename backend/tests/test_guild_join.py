"""Phase 1: the keypair join boundary.

These use a real secp256k1 key and real BIP-340 signatures — the same
primitives an outside framework would use — so the happy path proves the
handshake actually works rather than that a mock agreed with itself. The
rejection cases are the more important half: this endpoint is unauthenticated
by design, so every check that stands between "anyone can POST" and "a guild
membership" is worth a test.
"""
import json
import secrets
import time

import pytest
import pytest_asyncio
from coincurve import PrivateKey

from backend import coordination as coord
from backend import coordination_join as join_mod
from backend.buzz_client import build_event
from backend.buzz_identity import public_key_xonly_hex


@pytest_asyncio.fixture(scope="module", autouse=True)
async def schema(client):
    await coord.init_coordination_db()
    await join_mod.init_join_db()


@pytest_asyncio.fixture
async def guild(client, fresh_agent):
    import aiosqlite
    from backend.db import get_db

    founder = await fresh_agent()
    slug = f"jg-{secrets.token_hex(5)}"
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM agents WHERE name=?", (founder["name"],))
        founder_id = dict(await cur.fetchone())["id"]
        cur = await db.execute(
            """INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key)
               VALUES (?,?,?,?,?,?)""",
            (slug, "Join Guild", "join tests", founder_id, founder["name"], secrets.token_hex(16)),
        )
        guild_id = cur.lastrowid
        await db.execute(
            "INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
            (guild_id, founder_id, founder["name"]),
        )
        await db.commit()
    return {"slug": slug, "id": guild_id, "founder": founder}


class OutsideAgent:
    """Stands in for Claude Code / Hermes / OpenClaw: holds its own key and
    signs its own events. Vantage never sees `self.pk`."""

    def __init__(self, name="OutsideAgent", framework="claude-code"):
        self.pk = PrivateKey()
        self.pubkey = public_key_xonly_hex(self.pk)
        self.name = name
        self.framework = framework

    def sign_challenge(self, challenge, *, kind=join_mod.KIND_CLIENT_AUTH, created_at=None):
        event = build_event(
            self.pk, kind=kind, content="",
            tags=[["relay", join_mod.RELAY_WS_URL], ["challenge", challenge]],
        )
        if created_at is not None:
            # Re-sign at the doctored timestamp so the event stays internally
            # consistent — otherwise the id check would fire first and the
            # clock-skew check would never be reached.
            event = build_event(
                self.pk, kind=kind, content="",
                tags=[["relay", join_mod.RELAY_WS_URL], ["challenge", challenge]],
            )
            event["created_at"] = created_at
            event["id"] = join_mod._canonical_event_id(event)
            from backend.buzz_identity import sign_event_id
            event["sig"] = sign_event_id(self.pk, event["id"])
        return event


async def _request_challenge(client, guild, agent):
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/join-request",
        data={"pubkey": agent.pubkey, "display_name": agent.name,
              "framework": agent.framework, "capabilities": json.dumps(["code", "review"])},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["challenge"]


# ── the happy path ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_outside_agent_joins_with_a_signed_challenge(client, guild):
    agent = OutsideAgent()
    challenge = await _request_challenge(client, guild, agent)
    event = agent.sign_challenge(challenge)

    resp = await client.post(
        f"/api/guilds/{guild['slug']}/join-confirm",
        json={"signed_event": event},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pubkey"] == agent.pubkey
    assert body["role"] == "member"
    assert body["relay_ws_url"]
    assert "channels" in body


@pytest.mark.asyncio
async def test_joined_agent_holds_its_own_key(client, guild):
    """The core promise of this boundary."""
    agent = OutsideAgent()
    challenge = await _request_challenge(client, guild, agent)
    await client.post(
        f"/api/guilds/{guild['slug']}/join-confirm", json={"signed_event": agent.sign_challenge(challenge)}
    )

    principal = await coord._get_principal_by_pubkey(agent.pubkey)
    assert principal["kind"] == "external_agent"
    assert principal["key_custody"] == "self"
    assert await coord.signing_key_for_principal(principal) is None


@pytest.mark.asyncio
async def test_joined_agent_appears_in_the_guild_roster(client, guild):
    agent = OutsideAgent(name="RosterBot", framework="hermes")
    challenge = await _request_challenge(client, guild, agent)
    await client.post(
        f"/api/guilds/{guild['slug']}/join-confirm", json={"signed_event": agent.sign_challenge(challenge)}
    )

    resp = await client.get(f"/api/guilds/{guild['slug']}/principals")
    names = {p["display_name"]: p for p in resp.json()["principals"]}
    assert "RosterBot" in names
    assert names["RosterBot"]["framework"] == "hermes"
    assert names["RosterBot"]["kind"] == "external_agent"


@pytest.mark.asyncio
async def test_capabilities_are_recorded_but_stay_advisory(client, guild):
    agent = OutsideAgent()
    challenge = await _request_challenge(client, guild, agent)
    await client.post(
        f"/api/guilds/{guild['slug']}/join-confirm", json={"signed_event": agent.sign_challenge(challenge)}
    )
    principal = await coord._get_principal_by_pubkey(agent.pubkey)
    assert json.loads(principal["capabilities"]) == ["code", "review"]
    # Nothing in the membership grant is derived from them.
    assert (await coord.get_membership(guild["id"], principal["id"]))["role"] == "member"


# ── what it must refuse ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_challenge_cannot_be_replayed(client, guild):
    agent = OutsideAgent()
    challenge = await _request_challenge(client, guild, agent)
    event = agent.sign_challenge(challenge)

    first = await client.post(f"/api/guilds/{guild['slug']}/join-confirm", json={"signed_event": event})
    assert first.status_code == 200
    second = await client.post(f"/api/guilds/{guild['slug']}/join-confirm", json={"signed_event": event})
    assert second.status_code == 401
    assert "already been used" in second.json()["detail"]


@pytest.mark.asyncio
async def test_another_key_cannot_answer_your_challenge(client, guild):
    """The attack this whole handshake exists to stop: claim a pubkey you
    don't control, then sign with one you do."""
    victim = OutsideAgent(name="Victim")
    attacker = OutsideAgent(name="Attacker")
    challenge = await _request_challenge(client, guild, victim)

    resp = await client.post(
        f"/api/guilds/{guild['slug']}/join-confirm",
        json={"signed_event": attacker.sign_challenge(challenge)},
    )
    assert resp.status_code == 401
    assert "does not match" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_a_tampered_event_is_rejected(client, guild):
    agent = OutsideAgent()
    challenge = await _request_challenge(client, guild, agent)
    event = agent.sign_challenge(challenge)
    event["content"] = "tampered after signing"

    resp = await client.post(f"/api/guilds/{guild['slug']}/join-confirm", json={"signed_event": event})
    assert resp.status_code == 401
    assert "does not match its contents" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_a_forged_signature_is_rejected(client, guild):
    agent = OutsideAgent()
    challenge = await _request_challenge(client, guild, agent)
    event = agent.sign_challenge(challenge)
    event["sig"] = "00" * 64

    resp = await client.post(f"/api/guilds/{guild['slug']}/join-confirm", json={"signed_event": event})
    assert resp.status_code == 401
    assert "does not verify" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_a_stale_signature_is_rejected(client, guild):
    agent = OutsideAgent()
    challenge = await _request_challenge(client, guild, agent)
    old = agent.sign_challenge(
        challenge, created_at=int(time.time()) - (join_mod.MAX_CLOCK_SKEW_SECONDS + 120)
    )

    resp = await client.post(f"/api/guilds/{guild['slug']}/join-confirm", json={"signed_event": old})
    assert resp.status_code == 401
    assert "created_at" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_wrong_event_kind_is_rejected(client, guild):
    agent = OutsideAgent()
    challenge = await _request_challenge(client, guild, agent)
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/join-confirm",
        json={"signed_event": agent.sign_challenge(challenge, kind=1)},
    )
    assert resp.status_code == 401
    assert "22242" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_an_unknown_challenge_is_rejected(client, guild):
    agent = OutsideAgent()
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/join-confirm",
        json={"signed_event": agent.sign_challenge(secrets.token_hex(32))},
    )
    assert resp.status_code == 401
    assert "unknown challenge" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_a_challenge_is_scoped_to_one_guild(client, guild, fresh_agent):
    """A challenge for guild A must not enroll you in guild B."""
    import aiosqlite
    from backend.db import get_db

    other_founder = await fresh_agent()
    other_slug = f"jg-{secrets.token_hex(5)}"
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM agents WHERE name=?", (other_founder["name"],))
        fid = dict(await cur.fetchone())["id"]
        await db.execute(
            """INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key)
               VALUES (?,?,?,?,?,?)""",
            (other_slug, "Other", "", fid, other_founder["name"], secrets.token_hex(16)),
        )
        await db.commit()

    agent = OutsideAgent()
    challenge = await _request_challenge(client, guild, agent)
    resp = await client.post(
        f"/api/guilds/{other_slug}/join-confirm",
        json={"signed_event": agent.sign_challenge(challenge)},
    )
    assert resp.status_code == 401
    assert "different guild" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_malformed_pubkey_is_refused_at_request_time(client, guild):
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/join-request",
        data={"pubkey": "z" * 64, "display_name": "Bad"},
    )
    assert resp.status_code == 422
    assert "hex" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_a_banned_identity_cannot_rejoin(client, guild):
    agent = OutsideAgent()
    challenge = await _request_challenge(client, guild, agent)
    await client.post(
        f"/api/guilds/{guild['slug']}/join-confirm", json={"signed_event": agent.sign_challenge(challenge)}
    )

    principal = await coord._get_principal_by_pubkey(agent.pubkey)
    from backend.db import get_db
    async with get_db() as db:
        await db.execute(
            "UPDATE guild_memberships SET banned_at=datetime('now') WHERE guild_id=? AND principal_id=?",
            (guild["id"], principal["id"]),
        )
        await db.commit()

    challenge2 = await _request_challenge(client, guild, agent)
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/join-confirm", json={"signed_event": agent.sign_challenge(challenge2)}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_rejoining_does_not_let_an_identity_be_renamed(client, guild):
    """A second join must not silently re-badge a pubkey that is already
    speaking elsewhere."""
    agent = OutsideAgent(name="Original", framework="claude-code")
    challenge = await _request_challenge(client, guild, agent)
    await client.post(
        f"/api/guilds/{guild['slug']}/join-confirm", json={"signed_event": agent.sign_challenge(challenge)}
    )

    agent.name = "Impostor"
    agent.framework = "something-else"
    challenge2 = await _request_challenge(client, guild, agent)
    await client.post(
        f"/api/guilds/{guild['slug']}/join-confirm", json={"signed_event": agent.sign_challenge(challenge2)}
    )

    principal = await coord._get_principal_by_pubkey(agent.pubkey)
    assert principal["display_name"] == "Original"
    assert principal["framework"] == "claude-code"
