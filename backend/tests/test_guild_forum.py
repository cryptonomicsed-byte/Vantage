"""Phase 0 coordination layer: principals, channels, indexing, read gating.

These run without a relay, which is deliberate rather than a compromise —
the relay-down path is a real production state and the design commits to
failing loudly in it. So the tests cover both halves: channel creation
degrades to an unprovisioned channel, posting through it returns 503, and
the read path is exercised by feeding index_event() synthetic relay events,
which is exactly the shape the background indexer hands it.
"""
import time

import pytest
import pytest_asyncio

from backend import coordination as coord


@pytest_asyncio.fixture(scope="module", autouse=True)
async def coordination_schema(client):
    """The session `client` fixture only runs init_agents_db()."""
    await coord.init_coordination_db()


@pytest_asyncio.fixture
async def guild(client, fresh_agent):
    """A guild plus its founder agent.

    Inserted directly rather than through POST /api/guilds, for the same
    reason the repo's own fresh_agent fixture bypasses registration: that
    endpoint is capped at 5/minute and this file needs a guild per test.
    Writing only guild_members here is also the more honest fixture — it
    reproduces a guild created by the legacy path, which is what the lazy
    reconciliation in coord.get_membership has to cope with in production.
    """
    import secrets
    import aiosqlite
    from backend.db import get_db

    founder = await fresh_agent()
    slug = f"tg-{secrets.token_hex(5)}"
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM agents WHERE name=?", (founder["name"],))
        founder_id = dict(await cur.fetchone())["id"]
        cur = await db.execute(
            """INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key)
               VALUES (?,?,?,?,?,?)""",
            (slug, "Test Guild", "coordination tests", founder_id, founder["name"],
             secrets.token_hex(16)),
        )
        guild_id = cur.lastrowid
        await db.execute(
            "INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
            (guild_id, founder_id, founder["name"]),
        )
        await db.commit()
    return {"slug": slug, "id": guild_id, "founder": founder, "founder_id": founder_id}


def _hdr(agent):
    return {"X-Agent-Key": agent["api_key"]}


def _fake_event(*, buzz_channel_id, pubkey, content, guild_slug, channel_slug,
                event_id=None, msg_type="say", root=None, reply=None, work_ref=None):
    """A relay event shaped exactly like one the indexer would receive."""
    tags = [["h", buzz_channel_id]]
    if root:
        tags.append(["e", root, "", "root"])
    if reply:
        tags.append(["e", reply, "", "reply"])
    tags += [["vg", guild_slug, channel_slug], ["vt", msg_type]]
    if work_ref:
        tags.append(["vw", work_ref])
    return {
        "id": event_id or f"{abs(hash((buzz_channel_id, content, pubkey, root, reply))):064x}"[:64],
        "pubkey": pubkey,
        "created_at": int(time.time()),
        "kind": 9,
        "tags": tags,
        "content": content,
        "sig": "0" * 128,
    }


async def _provisioned_channel(client, guild, agent, slug="general", **kw):
    """Create a channel and force a relay id onto it.

    The relay isn't reachable in tests, so provisioning legitimately fails.
    Stamping an id here is what lets the read/index path be tested at all —
    and it mirrors what a successful provision would have written.
    """
    payload = {"channel_slug": slug, "name": kw.pop("name", slug.title())}
    payload.update(kw)
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/channels", data=payload, headers=_hdr(agent)
    )
    assert resp.status_code == 200, resp.text
    channel_id = resp.json()["id"]

    from backend.db import get_db
    buzz_id = f"test-channel-{channel_id}"
    async with get_db() as db:
        await db.execute(
            "UPDATE guild_channels SET buzz_channel_id=? WHERE id=?", (buzz_id, channel_id)
        )
        await db.commit()
    return {"id": channel_id, "slug": slug, "buzz_channel_id": buzz_id}


# ── principals ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_principal_is_stable_and_keyed_by_derived_pubkey(client, fresh_agent):
    agent = await fresh_agent()
    from backend.db import get_db
    import aiosqlite
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM agents WHERE name=?", (agent["name"],))
        agent_id = dict(await cur.fetchone())["id"]

    first = await coord.get_or_create_agent_principal(agent_id)
    second = await coord.get_or_create_agent_principal(agent_id)

    assert first["id"] == second["id"], "principal must be stable across calls"
    assert first["kind"] == "agent"
    assert first["key_custody"] == "derived"
    assert len(first["pubkey"]) == 64


@pytest.mark.asyncio
async def test_signing_key_unavailable_for_external_principals():
    """The join boundary's core promise: we never hold an outside agent's key."""
    external = {"kind": "external_agent", "agent_id": None, "human_id": None}
    assert await coord.signing_key_for_principal(external) is None


# ── channels ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_channel_creation_degrades_when_relay_is_down(client, guild):
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/channels",
        data={"channel_slug": "lobby", "name": "Lobby"},
        headers=_hdr(guild["founder"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["relay_provisioned"] is False
    assert "503" in (body["note"] or ""), "the failure mode must be stated, not silent"


@pytest.mark.asyncio
async def test_posting_to_unprovisioned_channel_returns_503(client, guild):
    await client.post(
        f"/api/guilds/{guild['slug']}/channels",
        data={"channel_slug": "quiet", "name": "Quiet"},
        headers=_hdr(guild["founder"]),
    )
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/channels/quiet/messages",
        data={"content": "hello"},
        headers=_hdr(guild["founder"]),
    )
    assert resp.status_code == 503, resp.text
    assert "Relay unavailable" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_sub_guild_depth_is_capped_at_one(client, guild):
    await client.post(
        f"/api/guilds/{guild['slug']}/channels",
        data={"channel_slug": "top", "name": "Top"}, headers=_hdr(guild["founder"]),
    )
    ok = await client.post(
        f"/api/guilds/{guild['slug']}/channels",
        data={"channel_slug": "mid", "name": "Mid", "parent": "top"},
        headers=_hdr(guild["founder"]),
    )
    assert ok.status_code == 200, ok.text

    too_deep = await client.post(
        f"/api/guilds/{guild['slug']}/channels",
        data={"channel_slug": "deep", "name": "Deep", "parent": "mid"},
        headers=_hdr(guild["founder"]),
    )
    assert too_deep.status_code == 422
    assert "one level deep" in too_deep.json()["detail"]


@pytest.mark.asyncio
async def test_non_staff_cannot_create_channels(client, guild, fresh_agent):
    outsider = await fresh_agent()
    await client.post(f"/api/guilds/{guild['slug']}/membership", headers=_hdr(outsider))
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/channels",
        data={"channel_slug": "nope", "name": "Nope"}, headers=_hdr(outsider),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_workspace_channel_is_a_channel_with_a_sandbox_flag(client, guild):
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/channels",
        data={"channel_slug": "build", "name": "Build", "channel_kind": "workspace"},
        headers=_hdr(guild["founder"]),
    )
    assert resp.status_code == 200, resp.text
    channel = await coord.get_channel_by_id(resp.json()["id"])
    assert channel["channel_kind"] == "workspace"
    assert channel["sandbox_bound"] == 1


# ── indexing ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_index_event_is_idempotent_on_event_id(client, guild):
    channel = await _provisioned_channel(client, guild, guild["founder"], slug="idx")
    event = _fake_event(
        buzz_channel_id=channel["buzz_channel_id"], pubkey="a" * 64,
        content="first post", guild_slug=guild["slug"], channel_slug="idx",
    )
    assert await coord.index_event(event) is not None
    await coord.index_event(event)  # replayed by a reconnect

    messages = await coord.list_messages(channel["id"])
    assert len([m for m in messages if m["content"] == "first post"]) == 1


@pytest.mark.asyncio
async def test_index_event_ignores_unknown_channels(client):
    event = _fake_event(
        buzz_channel_id="not-a-channel-we-own", pubkey="b" * 64,
        content="stray", guild_slug="nope", channel_slug="nope",
    )
    assert await coord.index_event(event) is None


@pytest.mark.asyncio
async def test_forged_system_events_are_rejected(client, guild):
    """`vt=system` is the Conductor's alone — otherwise any relay member
    could forge floor grants into the transcript."""
    channel = await _provisioned_channel(client, guild, guild["founder"], slug="sys")
    event = _fake_event(
        buzz_channel_id=channel["buzz_channel_id"], pubkey="c" * 64,
        content="floor_granted to me", guild_slug=guild["slug"],
        channel_slug="sys", msg_type="system",
    )
    assert await coord.index_event(event) is None


@pytest.mark.asyncio
async def test_banned_principals_are_filtered_at_index_time(client, guild, fresh_agent):
    """Relay roles are deployment-wide, so a guild ban can only be enforced
    here. Spec risk §9.2."""
    channel = await _provisioned_channel(client, guild, guild["founder"], slug="mod")
    banned = await fresh_agent()
    await client.post(f"/api/guilds/{guild['slug']}/membership", headers=_hdr(banned))

    from backend.db import get_db
    import aiosqlite
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM agents WHERE name=?", (banned["name"],))
        agent_id = dict(await cur.fetchone())["id"]
    principal = await coord.get_or_create_agent_principal(agent_id)
    guild_row_id = (await coord.get_channel_by_id(channel["id"]))["guild_id"]
    async with get_db() as db:
        await db.execute(
            "UPDATE guild_memberships SET banned_at=datetime('now') WHERE guild_id=? AND principal_id=?",
            (guild_row_id, principal["id"]),
        )
        await db.commit()

    event = _fake_event(
        buzz_channel_id=channel["buzz_channel_id"], pubkey=principal["pubkey"],
        content="still here", guild_slug=guild["slug"], channel_slug="mod",
    )
    assert await coord.index_event(event) is None


@pytest.mark.asyncio
async def test_untagged_kind_9_still_indexes_as_a_plain_post(client, guild):
    """A client that knows nothing but kind 9 must still be able to take
    part — that is what makes the join boundary framework-agnostic."""
    channel = await _provisioned_channel(client, guild, guild["founder"], slug="bare")
    bare = {
        "id": "d" * 64, "pubkey": "e" * 64, "created_at": int(time.time()),
        "kind": 9, "tags": [["h", channel["buzz_channel_id"]]],
        "content": "posted by a plain nostr client", "sig": "0" * 128,
    }
    assert await coord.index_event(bare) is not None
    messages = await coord.list_messages(channel["id"])
    assert messages[0]["msg_type"] == "say"
    assert messages[0]["author"].startswith("relay:")


# ── threads and reads ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_threads_group_replies_and_count_them(client, guild):
    channel = await _provisioned_channel(client, guild, guild["founder"], slug="threads")
    root = _fake_event(
        buzz_channel_id=channel["buzz_channel_id"], pubkey="f" * 64,
        content="root post", guild_slug=guild["slug"], channel_slug="threads",
        event_id="1" * 64,
    )
    await coord.index_event(root)
    for i in range(3):
        await coord.index_event(_fake_event(
            buzz_channel_id=channel["buzz_channel_id"], pubkey="f" * 64,
            content=f"reply {i}", guild_slug=guild["slug"], channel_slug="threads",
            event_id=f"{i + 2}" * 64, root="1" * 64, reply="1" * 64,
        ))

    top_level = await coord.list_messages(channel["id"])
    assert [m["content"] for m in top_level] == ["root post"], "replies must not appear as posts"
    assert top_level[0]["reply_count"] == 3

    resp = await client.get(
        f"/api/guilds/{guild['slug']}/channels/threads/threads/{'1' * 64}",
        headers=_hdr(guild["founder"]),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 4  # root + 3 replies, oldest first
    assert resp.json()["messages"][0]["content"] == "root post"


@pytest.mark.asyncio
async def test_private_channels_are_hidden_from_non_members(client, guild, fresh_agent):
    """Relay ACLs for private channels are unverified (spec §9.1), so reads
    are gated here."""
    await client.post(
        f"/api/guilds/{guild['slug']}/channels",
        data={"channel_slug": "secret", "name": "Secret", "visibility": "private"},
        headers=_hdr(guild["founder"]),
    )
    outsider = await fresh_agent()

    listing = await client.get(f"/api/guilds/{guild['slug']}/channels", headers=_hdr(outsider))
    assert "secret" not in [c["slug"] for c in listing.json()["channels"]]

    direct = await client.get(
        f"/api/guilds/{guild['slug']}/channels/secret/messages", headers=_hdr(outsider)
    )
    assert direct.status_code == 404, "a private channel must not confirm it exists"


@pytest.mark.asyncio
async def test_public_channels_are_readable_anonymously(client, guild):
    await client.post(
        f"/api/guilds/{guild['slug']}/channels",
        data={"channel_slug": "open-door", "name": "Open", "visibility": "public"},
        headers=_hdr(guild["founder"]),
    )
    resp = await client.get(f"/api/guilds/{guild['slug']}/channels/open-door/messages")
    assert resp.status_code == 200, resp.text


# ── membership ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_join_updates_both_membership_tables(client, guild, fresh_agent):
    """guild_members is kept in step so routers/guilds.py stays correct
    through the migration."""
    joiner = await fresh_agent()
    resp = await client.post(f"/api/guilds/{guild['slug']}/membership", headers=_hdr(joiner))
    assert resp.status_code == 200, resp.text

    from backend.db import get_db
    import aiosqlite
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM agents WHERE name=?", (joiner["name"],))
        agent_id = dict(await cur.fetchone())["id"]
        cur = await db.execute(
            "SELECT COUNT(*) FROM guild_members gm JOIN guilds g ON g.id=gm.guild_id "
            "WHERE g.slug=? AND gm.agent_id=?", (guild["slug"], agent_id),
        )
        legacy_rows = (await cur.fetchone())[0]
    assert legacy_rows == 1


@pytest.mark.asyncio
async def test_membership_endpoint_reports_what_the_caller_may_do(client, guild, fresh_agent):
    stranger = await fresh_agent()
    before = await client.get(f"/api/guilds/{guild['slug']}/membership", headers=_hdr(stranger))
    assert before.json()["member"] is False

    await client.post(f"/api/guilds/{guild['slug']}/membership", headers=_hdr(stranger))
    after = await client.get(f"/api/guilds/{guild['slug']}/membership", headers=_hdr(stranger))
    assert after.json()["member"] is True
    assert after.json()["role"] == "member"


@pytest.mark.asyncio
async def test_anonymous_membership_check_does_not_401(client, guild):
    resp = await client.get(f"/api/guilds/{guild['slug']}/membership")
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is False


@pytest.mark.asyncio
async def test_non_members_cannot_post(client, guild, fresh_agent):
    await _provisioned_channel(client, guild, guild["founder"], slug="members-only")
    outsider = await fresh_agent()
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/channels/members-only/messages",
        data={"content": "let me in"}, headers=_hdr(outsider),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_non_open_flow_is_refused_until_the_conductor_exists(client, guild):
    channel = await _provisioned_channel(
        client, guild, guild["founder"], slug="turns", flow_mode="round_robin"
    )
    assert channel["slug"] == "turns"
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/channels/turns/messages",
        data={"content": "my turn?"}, headers=_hdr(guild["founder"]),
    )
    assert resp.status_code == 409
    assert "Conductor" in resp.json()["detail"]
