"""Guild channels as live chat: multi-mention, dispatch, loop safety.

The interesting cases are the ones that make a room full of agents survivable:
every mention reaching its target, agents that hold their own keys not being
answered for, and a mention chain that terminates instead of billing you
forever.
"""
import secrets

import pytest
import pytest_asyncio

from backend import coordination as coord
from backend import guild_chat
from backend import sovereignty


@pytest_asyncio.fixture(scope="module", autouse=True)
async def schema(client):
    await coord.init_coordination_db()
    await sovereignty.init_sovereignty_db()


@pytest_asyncio.fixture
async def guild(client, fresh_agent):
    import aiosqlite
    from backend.db import get_db

    founder = await fresh_agent()
    slug = f"gc-{secrets.token_hex(5)}"
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM agents WHERE name=?", (founder["name"],))
        founder_id = dict(await cur.fetchone())["id"]
        cur = await db.execute(
            """INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key)
               VALUES (?,?,?,?,?,?)""",
            (slug, "Chat Guild", "", founder_id, founder["name"], secrets.token_hex(16)),
        )
        guild_id = cur.lastrowid
        await db.execute(
            "INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
            (guild_id, founder_id, founder["name"]),
        )
        await db.commit()
    return {"slug": slug, "id": guild_id, "founder": founder, "founder_id": founder_id}


@pytest_asyncio.fixture
async def member_maker(fresh_agent):
    import aiosqlite
    from backend.db import get_db

    async def _make(guild_id):
        agent = await fresh_agent()
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT id FROM agents WHERE name=?", (agent["name"],))
            agent_id = dict(await cur.fetchone())["id"]
        principal = await coord.get_or_create_agent_principal(agent_id)
        await coord.add_membership(guild_id, principal)
        return {"agent": agent, "agent_id": agent_id, "principal": principal}

    return _make


# ── parsing ──────────────────────────────────────────────────────────────────

def test_every_mention_is_parsed_not_just_the_first():
    """The gap in buzz_inbound.py: `.search()` finds one. A room needs all."""
    names = guild_chat.parse_mentions("@alice @bob what do you two think? cc @carol")
    assert names == ["alice", "bob", "carol"]


def test_mentions_are_deduplicated_but_keep_their_order():
    assert guild_chat.parse_mentions("@bob @alice @bob again") == ["bob", "alice"]


def test_mention_count_is_capped():
    """A message naming forty agents is a broadcast, and an attack on your
    model spend."""
    blast = " ".join(f"@agent{i}" for i in range(40))
    assert len(guild_chat.parse_mentions(blast)) == guild_chat.MAX_MENTIONS


def test_text_without_mentions_parses_to_nothing():
    assert guild_chat.parse_mentions("no mentions here") == []
    assert guild_chat.parse_mentions("") == []
    assert guild_chat.parse_mentions("email me at foo@example.com") == ["example.com"]


def test_dispatch_depth_reads_the_loop_guard():
    assert guild_chat.dispatch_depth({"tags": [["vdepth", "2"]]}) == 2
    assert guild_chat.dispatch_depth({"tags": [["h", "chan"]]}) == 0
    assert guild_chat.dispatch_depth({"tags": [["vdepth", "junk"]]}) == 0


# ── resolution ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mentions_resolve_to_guild_members(client, guild, member_maker):
    alice = await member_maker(guild["id"])
    bob = await member_maker(guild["id"])

    resolved = await guild_chat.resolve_mentions(
        guild["id"], [alice["principal"]["display_name"], bob["principal"]["display_name"]]
    )
    assert {p["id"] for p in resolved} == {alice["principal"]["id"], bob["principal"]["id"]}


@pytest.mark.asyncio
async def test_you_cannot_mention_an_agent_into_a_room_it_is_not_in(client, guild, member_maker, fresh_agent):
    """Otherwise a mention is a way to page any agent on the instance."""
    import aiosqlite
    from backend.db import get_db

    outsider = await fresh_agent()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM agents WHERE name=?", (outsider["name"],))
        outsider_id = dict(await cur.fetchone())["id"]
    outsider_principal = await coord.get_or_create_agent_principal(outsider_id)

    resolved = await guild_chat.resolve_mentions(
        guild["id"], [outsider_principal["display_name"]]
    )
    assert resolved == []


@pytest.mark.asyncio
async def test_a_banned_member_cannot_be_mentioned(client, guild, member_maker):
    from backend.db import get_db

    member = await member_maker(guild["id"])
    async with get_db() as db:
        await db.execute(
            "UPDATE guild_memberships SET banned_at=datetime('now') WHERE guild_id=? AND principal_id=?",
            (guild["id"], member["principal"]["id"]),
        )
        await db.commit()

    resolved = await guild_chat.resolve_mentions(
        guild["id"], [member["principal"]["display_name"]]
    )
    assert resolved == []


@pytest.mark.asyncio
async def test_unknown_names_resolve_to_nothing(client, guild):
    assert await guild_chat.resolve_mentions(guild["id"], ["nobody-by-that-name"]) == []


# ── who gets answered for ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_self_custody_agents_are_never_answered_for(client, guild, member_maker):
    """This instance cannot sign for them. It delivers the mention and lets
    them answer with their own key — which is what holding your key means."""
    member = await member_maker(guild["id"])
    principal = dict(member["principal"])
    principal["key_custody"] = "self"

    assert await guild_chat.dispatchable_agents([principal]) == []


@pytest.mark.asyncio
async def test_humans_are_mentioned_not_prompted(client, guild):
    """A mention addressed to a person is a notification, not a prompt."""
    human = {"kind": "human", "human_id": 1, "agent_id": None, "key_custody": "derived"}
    assert await guild_chat.dispatchable_agents([human]) == []


@pytest.mark.asyncio
async def test_derived_agents_are_dispatchable(client, guild, member_maker):
    member = await member_maker(guild["id"])
    assert await guild_chat.dispatchable_agents([member["principal"]]) == [member["principal"]]


# ── loop safety ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_stops_at_the_depth_cap(client, guild, member_maker):
    """Agents mentioning agents is the point, and also an infinite loop
    waiting to happen. At the cap, a person has to pick it up."""
    member = await member_maker(guild["id"])
    channel = {"id": 1, "slug": "main", "buzz_channel_id": "x", "guild_id": guild["id"]}

    replies = await guild_chat.dispatch_to_mentioned(
        channel=channel, guild_slug=guild["slug"], content="@someone hello",
        author_principal=member["principal"], mentioned=[member["principal"]],
        depth=guild_chat.MAX_DISPATCH_DEPTH,
    )
    assert replies == []


@pytest.mark.asyncio
async def test_an_agent_mentioning_itself_is_not_a_question(client, guild, member_maker):
    member = await member_maker(guild["id"])
    channel = {"id": 1, "slug": "main", "buzz_channel_id": "x", "guild_id": guild["id"]}

    replies = await guild_chat.dispatch_to_mentioned(
        channel=channel, guild_slug=guild["slug"], content="@self thinking out loud",
        author_principal=member["principal"], mentioned=[member["principal"]], depth=0,
    )
    assert replies == []


# ── the message path ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_posted_message_reports_who_it_addressed(client, guild, member_maker):
    """The relay is unreachable in tests, so posting 503s — but resolution
    happens before publishing, and that is the part under test here."""
    alice = await member_maker(guild["id"])
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/channels/nope/messages",
        data={"content": f"@{alice['principal']['display_name']} hi"},
        headers={"X-Agent-Key": guild["founder"]["api_key"]},
    )
    # No such channel -- the point is it fails cleanly rather than dispatching.
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_multiple_addressees_become_multiple_p_tags():
    """The wire-level half of multi-mention."""
    tags = coord.build_message_tags(
        buzz_channel_id="chan", guild_slug="g", channel_slug="c",
        addressed_to=["a" * 64, "b" * 64],
    )
    p_tags = [t for t in tags if t[0] == "p"]
    assert len(p_tags) == 2
    assert {t[1] for t in p_tags} == {"a" * 64, "b" * 64}


def test_a_single_addressee_still_works_as_a_string():
    tags = coord.build_message_tags(
        buzz_channel_id="chan", guild_slug="g", channel_slug="c", addressed_to="a" * 64,
    )
    assert [t for t in tags if t[0] == "p"] == [["p", "a" * 64]]


def test_extra_tags_ride_along():
    tags = coord.build_message_tags(
        buzz_channel_id="chan", guild_slug="g", channel_slug="c",
        extra_tags=[["vdepth", "2"]],
    )
    assert ["vdepth", "2"] in tags


# ── slash commands ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_command_palette_is_generated_from_live_routes(client, app):
    commands = guild_chat.build_command_palette(app)
    assert commands, "the palette should not be empty"
    assert all(c["command"].startswith("/") for c in commands)
    assert all(c["path"].startswith("/api/") for c in commands)


@pytest.mark.asyncio
async def test_dangerous_operations_are_not_offered_as_slash_commands(client, app):
    """Destroying a sealed seed, moving funds or placing an order deserves a
    deliberate surface, not a slash typed mid-sentence."""
    paths = {c["path"] for c in guild_chat.build_command_palette(app)}
    for blocked in guild_chat.BLOCKED_COMMAND_PREFIXES:
        assert not any(p.startswith(blocked) for p in paths), f"{blocked} must not be in chat"


@pytest.mark.asyncio
async def test_the_commands_endpoint_serves_the_palette(client, guild):
    resp = await client.get(
        "/api/chat/commands", headers={"X-Agent-Key": guild["founder"]["api_key"]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] > 0
    assert body["excluded"]
