"""The backend half of the Conductor bridge.

What matters here is the trust boundary. These routes can publish signed
system events — the transcript entries that say who held the floor — so
anyone who can reach them can forge turn-taking history. The shared-secret
checks are the point of this file.
"""
import secrets

import pytest
import pytest_asyncio

from backend import coordination as coord
from backend.routers import conductor as bridge


@pytest_asyncio.fixture(scope="module", autouse=True)
async def schema(client):
    await coord.init_coordination_db()
    await bridge.init_conductor_db()


@pytest_asyncio.fixture
async def secret(monkeypatch):
    """Configure a shared secret for the duration of one test."""
    value = secrets.token_hex(16)
    monkeypatch.setattr(bridge, "_SHARED_SECRET", value)
    return value


@pytest_asyncio.fixture
async def guild_channel(client, fresh_agent):
    import aiosqlite
    from backend.db import get_db

    founder = await fresh_agent()
    slug = f"cg-{secrets.token_hex(5)}"
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM agents WHERE name=?", (founder["name"],))
        founder_id = dict(await cur.fetchone())["id"]
        cur = await db.execute(
            """INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key)
               VALUES (?,?,?,?,?,?)""",
            (slug, "Conductor Guild", "", founder_id, founder["name"], secrets.token_hex(16)),
        )
        guild_id = cur.lastrowid
        await db.execute(
            "INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
            (guild_id, founder_id, founder["name"]),
        )
        cur = await db.execute(
            """INSERT INTO guild_channels (guild_id, slug, name, channel_kind, flow_mode, buzz_channel_id)
               VALUES (?,?,?,'workspace','round_robin',?)""",
            (guild_id, "build", "Build", f"relay-{secrets.token_hex(4)}"),
        )
        channel_id = cur.lastrowid
        await db.commit()

    principal = await coord.get_or_create_agent_principal(founder_id)
    await coord.add_membership(guild_id, principal, role="founder")
    return {
        "guild_id": guild_id, "guild_slug": slug, "channel_id": channel_id,
        "founder": founder, "principal": principal,
    }


def _hdr(secret):
    return {"X-Conductor-Secret": secret}


# ── the trust boundary ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_secret_configured_closes_the_bridge(client, guild_channel, monkeypatch):
    """"Not configured" must mean shut, not open — otherwise a default
    deployment lets anyone forge floor grants."""
    monkeypatch.setattr(bridge, "_SHARED_SECRET", "")
    resp = await client.get(f"/api/conductor/channels/{guild_channel['channel_id']}")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_a_wrong_secret_is_refused(client, guild_channel, secret):
    resp = await client.get(
        f"/api/conductor/channels/{guild_channel['channel_id']}",
        headers={"X-Conductor-Secret": "not-the-secret"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_a_missing_secret_is_refused(client, guild_channel, secret):
    resp = await client.get(f"/api/conductor/channels/{guild_channel['channel_id']}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_system_events_cannot_be_published_without_the_secret(client, guild_channel, secret):
    resp = await client.post(
        f"/api/conductor/channels/{guild_channel['channel_id']}/system",
        json={"text": "floor granted to 999"},
    )
    assert resp.status_code == 401


# ── structure ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_structure_carries_flow_mode_and_staff(client, guild_channel, secret):
    resp = await client.get(
        f"/api/conductor/channels/{guild_channel['channel_id']}", headers=_hdr(secret)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["flow_mode"] == "round_robin"
    assert body["channel_kind"] == "workspace"
    assert guild_channel["principal"]["id"] in body["staff"]
    assert body["floor_ttl_ms"] > 0


@pytest.mark.asyncio
async def test_banned_members_are_absent_from_structure(client, guild_channel, secret, fresh_agent):
    """The Conductor must not grant the floor to someone the guild removed."""
    from backend.db import get_db
    import aiosqlite

    banned = await fresh_agent()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM agents WHERE name=?", (banned["name"],))
        agent_id = dict(await cur.fetchone())["id"]
    principal = await coord.get_or_create_agent_principal(agent_id)
    await coord.add_membership(guild_channel["guild_id"], principal)

    before = await client.get(
        f"/api/conductor/channels/{guild_channel['channel_id']}", headers=_hdr(secret)
    )
    assert principal["id"] in before.json()["members"]

    async with get_db() as db:
        await db.execute(
            "UPDATE guild_memberships SET banned_at=datetime('now') WHERE guild_id=? AND principal_id=?",
            (guild_channel["guild_id"], principal["id"]),
        )
        await db.commit()

    after = await client.get(
        f"/api/conductor/channels/{guild_channel['channel_id']}", headers=_hdr(secret)
    )
    assert principal["id"] not in after.json()["members"]


@pytest.mark.asyncio
async def test_unknown_channel_is_404(client, secret):
    resp = await client.get("/api/conductor/channels/99999999", headers=_hdr(secret))
    assert resp.status_code == 404


# ── authentication on the Conductor's behalf ─────────────────────────────────

@pytest.mark.asyncio
async def test_agent_credential_resolves_to_a_principal(client, guild_channel, secret):
    resp = await client.post(
        "/api/conductor/authenticate",
        json={"channel_id": guild_channel["channel_id"],
              "credential": guild_channel["founder"]["api_key"]},
        headers=_hdr(secret),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["principal_id"] == guild_channel["principal"]["id"]
    assert body["role"] == "founder"


@pytest.mark.asyncio
async def test_a_bad_credential_does_not_resolve(client, guild_channel, secret):
    resp = await client.post(
        "/api/conductor/authenticate",
        json={"channel_id": guild_channel["channel_id"], "credential": "vantage_nope"},
        headers=_hdr(secret),
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_a_non_member_cannot_be_admitted(client, guild_channel, secret, fresh_agent):
    outsider = await fresh_agent()
    resp = await client.post(
        "/api/conductor/authenticate",
        json={"channel_id": guild_channel["channel_id"], "credential": outsider["api_key"]},
        headers=_hdr(secret),
    )
    assert resp.status_code == 403


# ── violations ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_violations_are_recorded_for_scoring(client, guild_channel, secret):
    resp = await client.post(
        "/api/conductor/violations",
        json={"channel_id": guild_channel["channel_id"],
              "principal_id": guild_channel["principal"]["id"],
              "reason": "posted without the floor"},
        headers=_hdr(secret),
    )
    assert resp.status_code == 200

    from backend.db import get_db
    async with get_db() as db:
        cur = await db.execute(
            "SELECT reason FROM flow_violations WHERE principal_id=?",
            (guild_channel["principal"]["id"],),
        )
        rows = await cur.fetchall()
    assert any("without the floor" in r[0] for r in rows)


# ── notification path ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notify_is_a_noop_without_a_conductor(monkeypatch):
    """Phase 0/1 deployments must be entirely unaffected by this code."""
    monkeypatch.setattr(bridge, "CONDUCTOR_URL", "")
    # Must not raise, and must not attempt a request.
    await bridge.notify_observed(1, 2, "say")
    await bridge.notify_membership_changed(1)


@pytest.mark.asyncio
async def test_notify_failures_are_swallowed(monkeypatch):
    """A message is already in the log by the time we get here; losing turn
    arbitration must never surface as a failed post."""
    monkeypatch.setattr(bridge, "CONDUCTOR_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(bridge, "_SHARED_SECRET", "x" * 16)
    await bridge.notify_observed(1, 2, "say")


@pytest.mark.asyncio
async def test_conductor_ws_url_derives_from_the_http_url(monkeypatch):
    monkeypatch.setattr(bridge, "CONDUCTOR_URL", "http://conductor:4500")
    assert bridge.conductor_ws_url() == "ws://conductor:4500/ws"

    monkeypatch.setattr(bridge, "CONDUCTOR_URL", "https://conductor.example")
    assert bridge.conductor_ws_url() == "wss://conductor.example/ws"

    monkeypatch.setattr(bridge, "CONDUCTOR_URL", "")
    assert bridge.conductor_ws_url() is None
