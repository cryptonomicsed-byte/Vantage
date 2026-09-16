"""Presence is a declaration, and the useful question is not "is it there".

The cases that matter: the vocabulary staying closed across two languages,
per-channel state falling back to instance-wide rather than vanishing, and
`routable` excluding the states that mean somebody else has to move first.
"""
import secrets

import aiosqlite
import pytest
import pytest_asyncio

from backend import coordination as coord
from backend import presence
from backend.db import get_db


@pytest_asyncio.fixture(scope="module", autouse=True)
async def schema(client):
    await coord.init_coordination_db()
    await presence.init_presence_db()


@pytest_asyncio.fixture
async def principal_maker(fresh_agent):
    async def _make():
        agent = await fresh_agent()
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT id FROM agents WHERE name=?", (agent["name"],))
            agent_id = dict(await cur.fetchone())["id"]
        return await coord.get_or_create_agent_principal(agent_id)
    return _make


@pytest_asyncio.fixture
async def guild(client, fresh_agent):
    founder = await fresh_agent()
    slug = f"pr-{secrets.token_hex(5)}"
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM agents WHERE name=?", (founder["name"],))
        founder_id = dict(await cur.fetchone())["id"]
        cur = await db.execute(
            """INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key)
               VALUES (?,?,?,?,?,?)""",
            (slug, "Presence Guild", "", founder_id, founder["name"], secrets.token_hex(16)),
        )
        guild_id = cur.lastrowid
        await db.commit()
    return {"id": guild_id, "slug": slug}


@pytest_asyncio.fixture
async def channel(guild):
    """A provisioned-on-the-relay channel row, so presence.set_state's
    _publish_blocked_turn finds a buzz_channel_id and gets as far as trying
    to sign — which is all these tests need, since the actual signing call
    is monkeypatched."""
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO guild_channels (guild_id, slug, name, channel_kind, flow_mode, buzz_channel_id)
               VALUES (?,?,?,'workspace','open',?)""",
            (guild["id"], "build", "Build", f"relay-{secrets.token_hex(4)}"),
        )
        channel_id = cur.lastrowid
        await db.commit()
    return channel_id


# ── the vocabulary ───────────────────────────────────────────────────────────

def test_the_vocabulary_matches_the_conductors():
    """Two implementations of one closed vocabulary drift silently. This is
    the assertion that makes the drift loud -- if Conductor.Flow's
    @work_states changes, this list has to change with it."""
    import pathlib
    import re

    flow = pathlib.Path(__file__).resolve().parents[2] / "ops/conductor/lib/conductor/flow.ex"
    match = re.search(r"@work_states ~w\(([^)]*)\)a", flow.read_text())
    assert match, "could not find @work_states in flow.ex"
    assert match.group(1).split() == presence.STATES


def test_an_unknown_state_is_not_valid():
    assert not presence.is_valid("vibing")
    assert not presence.is_valid("")
    assert not presence.is_valid(None)
    assert presence.is_valid("needs_review")


def test_blocked_and_needs_review_are_not_routable():
    """The reason the vocabulary exists at all."""
    assert "blocked" not in presence.ROUTABLE
    assert "needs_review" not in presence.ROUTABLE
    assert "offline" not in presence.ROUTABLE
    assert presence.ROUTABLE <= set(presence.STATES)


# ── storage ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_setting_a_state_reads_back(client, principal_maker):
    p = await principal_maker()
    await presence.set_state(principal_id=p["id"], state="working", mirror=False)
    assert (await presence.get_state(p["id"]))["state"] == "working"


@pytest.mark.asyncio
async def test_an_undeclared_principal_is_available_not_missing(client, principal_maker):
    """Silence is not evidence of being blocked. Treating it as such would
    make the scheduler useless on the day this shipped."""
    p = await principal_maker()
    state = await presence.get_state(p["id"])
    assert state["state"] == presence.DEFAULT_STATE
    assert state["declared"] is False
    assert state["routable"] is True


@pytest.mark.asyncio
async def test_an_unknown_state_is_refused_rather_than_stored(client, principal_maker):
    p = await principal_maker()
    with pytest.raises(ValueError):
        await presence.set_state(principal_id=p["id"], state="almost done", mirror=False)


@pytest.mark.asyncio
async def test_setting_the_instance_wide_state_twice_updates_one_row(client, principal_maker):
    """channel_id is NULL here, and SQLite does not treat NULL as equal to
    itself -- so an ON CONFLICT clause would never fire and every update
    would insert a duplicate."""
    p = await principal_maker()
    await presence.set_state(principal_id=p["id"], state="working", mirror=False)
    await presence.set_state(principal_id=p["id"], state="blocked", mirror=False)
    async with get_db() as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM principal_presence WHERE principal_id=? AND channel_id IS NULL",
            (p["id"],),
        )
        assert (await cur.fetchone())[0] == 1
    assert (await presence.get_state(p["id"]))["state"] == "blocked"


@pytest.mark.asyncio
async def test_a_channel_state_does_not_overwrite_the_instance_wide_one(
    client, principal_maker
):
    """An agent can be free in one workspace and blocked in another;
    collapsing those loses the only useful part."""
    p = await principal_maker()
    await presence.set_state(principal_id=p["id"], state="available", mirror=False)
    await presence.set_state(principal_id=p["id"], channel_id=77, state="blocked", mirror=False)

    assert (await presence.get_state(p["id"]))["state"] == "available"
    assert (await presence.get_state(p["id"], 77))["state"] == "blocked"


@pytest.mark.asyncio
async def test_a_channel_with_no_state_falls_back_to_the_instance_wide_one(
    client, principal_maker
):
    p = await principal_maker()
    await presence.set_state(principal_id=p["id"], state="working", mirror=False)
    assert (await presence.get_state(p["id"], 12345))["state"] == "working"


@pytest.mark.asyncio
async def test_a_work_reference_rides_along_with_the_state(client, principal_maker):
    p = await principal_maker()
    await presence.set_state(principal_id=p["id"], state="working",
                             work_ref="tro:9", mirror=False)
    assert (await presence.get_state(p["id"]))["work_ref"] == "tro:9"


# ── routing ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_routable_excludes_the_blocked_and_includes_the_silent(
    client, guild, principal_maker
):
    free, busy, stuck, silent = [await principal_maker() for _ in range(4)]
    for p in (free, busy, stuck, silent):
        await coord.add_membership(guild["id"], p)
    await presence.set_state(principal_id=free["id"], state="available", mirror=False)
    await presence.set_state(principal_id=busy["id"], state="thinking", mirror=False)
    await presence.set_state(principal_id=stuck["id"], state="blocked", mirror=False)

    ids = {r["principal_id"] for r in await presence.routable_in_guild(guild["id"])}
    assert free["id"] in ids
    assert busy["id"] in ids, "thinking is still worth handing work to"
    assert silent["id"] in ids, "an undeclared member is available"
    assert stuck["id"] not in ids


@pytest.mark.asyncio
async def test_a_banned_member_is_never_routable(client, guild, principal_maker):
    p = await principal_maker()
    await coord.add_membership(guild["id"], p)
    async with get_db() as db:
        await db.execute(
            "UPDATE guild_memberships SET banned_at=datetime('now') WHERE guild_id=? AND principal_id=?",
            (guild["id"], p["id"]),
        )
        await db.commit()
    ids = {r["principal_id"] for r in await presence.routable_in_guild(guild["id"])}
    assert p["id"] not in ids


@pytest.mark.asyncio
async def test_channel_presence_lists_who_declared_a_state_there(client, principal_maker):
    p = await principal_maker()
    await presence.set_state(principal_id=p["id"], channel_id=4242, state="needs_review",
                             mirror=False)
    rows = await presence.channel_presence(4242)
    mine = [r for r in rows if r["principal_id"] == p["id"]]
    assert mine and mine[0]["state"] == "needs_review"
    assert mine[0]["routable"] is False


# ── the wire ─────────────────────────────────────────────────────────────────

def test_work_state_does_not_mint_a_new_event_kind():
    """NIP-38 already covers this. The `d` tag is what keeps it from
    overwriting the vibe status published on the same kind."""
    from backend import buzz_status
    from backend import nostr_kinds

    assert presence.KIND_USER_STATUS == buzz_status.KIND_USER_STATUS
    assert presence.KIND_USER_STATUS == nostr_kinds.kind("user_status")
    assert presence.STATUS_D_TAG != "general"


# ── source: declared vs observed vs timeout ───────────────────────────────────

def test_an_unknown_source_is_not_valid():
    assert presence.is_valid_source("declared")
    assert presence.is_valid_source("observed")
    assert presence.is_valid_source("timeout")
    assert not presence.is_valid_source("vibes")
    assert not presence.is_valid_source(None)


@pytest.mark.asyncio
async def test_source_defaults_to_declared(client, principal_maker):
    p = await principal_maker()
    result = await presence.set_state(principal_id=p["id"], state="working", mirror=False)
    assert result["source"] == "declared"
    assert (await presence.get_state(p["id"]))["source"] == "declared"


@pytest.mark.asyncio
async def test_source_is_stored_and_read_back(client, principal_maker):
    p = await principal_maker()
    await presence.set_state(
        principal_id=p["id"], state="offline", source="timeout", mirror=False,
    )
    assert (await presence.get_state(p["id"]))["source"] == "timeout"


@pytest.mark.asyncio
async def test_an_undeclared_principal_has_no_source(client, principal_maker):
    p = await principal_maker()
    assert (await presence.get_state(p["id"]))["source"] is None


@pytest.mark.asyncio
async def test_an_unknown_source_is_refused_rather_than_stored(client, principal_maker):
    p = await principal_maker()
    with pytest.raises(ValueError):
        await presence.set_state(
            principal_id=p["id"], state="working", source="vibes", mirror=False,
        )


# ── the blocked turn ─────────────────────────────────────────────────────────

@pytest.fixture
def blocked_turn_calls(monkeypatch):
    """Stand in for coordination.publish_blocked_message so these tests never
    need a real relay -- same convention as workspace.py's `sandbox` fixture."""
    calls = []

    async def fake(**kwargs):
        calls.append(kwargs)
        return {"id": "fake-event-id"}

    monkeypatch.setattr(coord, "publish_blocked_message", fake)
    return calls


@pytest.mark.asyncio
async def test_transitioning_into_blocked_publishes_a_turn(
    client, principal_maker, channel, blocked_turn_calls
):
    p = await principal_maker()
    await presence.set_state(
        principal_id=p["id"], channel_id=channel, state="blocked",
        detail="waiting on a human review", mirror=False,
    )
    assert len(blocked_turn_calls) == 1
    call = blocked_turn_calls[0]
    assert call["state"] == "blocked"
    assert call["blocking_reason"] == "waiting on a human review"
    assert call["principal"]["id"] == p["id"]


@pytest.mark.asyncio
async def test_needs_review_also_publishes_a_turn(
    client, principal_maker, channel, blocked_turn_calls
):
    p = await principal_maker()
    await presence.set_state(
        principal_id=p["id"], channel_id=channel, state="needs_review", mirror=False,
    )
    assert len(blocked_turn_calls) == 1
    assert blocked_turn_calls[0]["state"] == "needs_review"


@pytest.mark.asyncio
async def test_a_routable_state_does_not_publish_a_turn(
    client, principal_maker, channel, blocked_turn_calls
):
    p = await principal_maker()
    await presence.set_state(
        principal_id=p["id"], channel_id=channel, state="working", mirror=False,
    )
    assert blocked_turn_calls == []


@pytest.mark.asyncio
async def test_instance_wide_blocked_does_not_publish_a_turn(
    client, principal_maker, blocked_turn_calls
):
    """There is no channel turn stream to put it in without a channel_id."""
    p = await principal_maker()
    await presence.set_state(principal_id=p["id"], state="blocked", mirror=False)
    assert blocked_turn_calls == []


@pytest.mark.asyncio
async def test_a_failed_publish_does_not_fail_the_state_change(
    client, principal_maker, channel, monkeypatch
):
    async def boom(**kwargs):
        raise RuntimeError("relay is down")

    monkeypatch.setattr(coord, "publish_blocked_message", boom)
    p = await principal_maker()
    result = await presence.set_state(
        principal_id=p["id"], channel_id=channel, state="blocked", mirror=False,
    )
    assert result["state"] == "blocked"
    assert (await presence.get_state(p["id"], channel))["state"] == "blocked"
