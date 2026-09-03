"""Phase 3: guild-scoped scoring.

The interesting tests here are the ones about what the ranking *resists*. A
score that a swarm of agents can farm is worse than no score, so spam damping
and the claim/artifact join are the properties worth pinning.
"""
import secrets
import time

import pytest
import pytest_asyncio

from backend import coordination as coord
from backend import coordination_scoring as scoring
from backend.routers import conductor as bridge


@pytest_asyncio.fixture(scope="module", autouse=True)
async def schema(client):
    await coord.init_coordination_db()
    await bridge.init_conductor_db()
    await scoring.init_scoring_db()


@pytest_asyncio.fixture
async def guild(client, fresh_agent):
    import aiosqlite
    from backend.db import get_db

    founder = await fresh_agent()
    slug = f"sg-{secrets.token_hex(5)}"
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM agents WHERE name=?", (founder["name"],))
        founder_id = dict(await cur.fetchone())["id"]
        cur = await db.execute(
            """INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key)
               VALUES (?,?,?,?,?,?)""",
            (slug, "Scoring Guild", "", founder_id, founder["name"], secrets.token_hex(16)),
        )
        guild_id = cur.lastrowid
        cur = await db.execute(
            """INSERT INTO guild_channels (guild_id, slug, name, buzz_channel_id)
               VALUES (?,?,?,?)""",
            (guild_id, "main", "Main", f"relay-{secrets.token_hex(4)}"),
        )
        channel_id = cur.lastrowid
        await db.commit()
    return {"slug": slug, "id": guild_id, "channel_id": channel_id}


@pytest_asyncio.fixture
async def principal_maker(fresh_agent):
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
        return principal

    return _make


_counter = {"n": 0}


async def _message(guild, principal, msg_type="say", work_ref=None, root=None, content="hi"):
    """Insert a message the way the indexer would, without needing a relay."""
    from backend.db import get_db

    _counter["n"] += 1
    event_id = f"{_counter['n']:064x}"
    async with get_db() as db:
        await db.execute(
            """INSERT INTO channel_messages
                 (event_id, channel_id, buzz_channel_id, pubkey, principal_id,
                  thread_root_event_id, msg_type, work_ref, content, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (event_id, guild["channel_id"], "relay", principal["pubkey"], principal["id"],
             root, msg_type, work_ref, content, int(time.time())),
        )
        await db.commit()
    return event_id


# ── the scoring function itself ──────────────────────────────────────────────

def test_message_count_is_log_damped():
    """Ten times the messages must not be ten times the score, or spamming
    the forum becomes the winning strategy."""
    ten = scoring.score_of({"messages": 10})
    hundred = scoring.score_of({"messages": 100})
    thousand = scoring.score_of({"messages": 1000})

    assert hundred > ten
    assert thousand > hundred
    # Each tenfold increase adds the same modest amount, not tenfold.
    assert hundred < ten * 2
    assert thousand < ten * 3


def test_shipped_work_outweighs_a_great_deal_of_talking():
    talker = scoring.score_of({"messages": 500})
    worker = scoring.score_of({"messages": 5, "work_closed": 2})
    assert worker > talker


def test_penalties_actually_subtract():
    clean = scoring.score_of({"messages": 50, "artifacts": 2})
    violating = scoring.score_of({"messages": 50, "artifacts": 2, "violations": 3})
    reported = scoring.score_of({"messages": 50, "artifacts": 2, "reports_actioned": 1})

    assert violating < clean
    assert reported < clean
    # A upheld moderation report should sting more than a few flow slips.
    assert reported < violating


def test_an_empty_record_scores_zero():
    assert scoring.score_of({}) == 0.0


# ── collection from the log ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_says_are_counted(client, guild, principal_maker):
    principal = await principal_maker(guild["id"])
    for _ in range(3):
        await _message(guild, principal)

    components = await scoring.collect_components(guild["id"])
    assert components[principal["id"]]["messages"] == 3


@pytest.mark.asyncio
async def test_a_claim_alone_earns_nothing(client, guild, principal_maker):
    """This is what makes the ranking expensive to farm: claiming work is
    free, so claiming must not pay."""
    principal = await principal_maker(guild["id"])
    await _message(guild, principal, msg_type="claim", work_ref="tro:1")

    components = await scoring.collect_components(guild["id"])
    # A principal with nothing but a claim earns no bucket at all, which is
    # the same statement as a zero: claiming is free, so it must not pay.
    assert components.get(principal["id"], {}).get("work_closed", 0) == 0


@pytest.mark.asyncio
async def test_a_claim_closed_by_an_artifact_counts(client, guild, principal_maker):
    principal = await principal_maker(guild["id"])
    await _message(guild, principal, msg_type="claim", work_ref="tro:7")
    await _message(guild, principal, msg_type="artifact", work_ref="tro:7")

    components = await scoring.collect_components(guild["id"])
    assert components[principal["id"]]["work_closed"] == 1
    assert components[principal["id"]]["artifacts"] == 1


@pytest.mark.asyncio
async def test_you_cannot_close_someone_elses_claim(client, guild, principal_maker):
    claimer = await principal_maker(guild["id"])
    other = await principal_maker(guild["id"])
    await _message(guild, claimer, msg_type="claim", work_ref="tro:9")
    await _message(guild, other, msg_type="artifact", work_ref="tro:9")

    components = await scoring.collect_components(guild["id"])
    assert components.get(claimer["id"], {}).get("work_closed", 0) == 0


@pytest.mark.asyncio
async def test_an_artifact_without_a_reference_does_not_count(client, guild, principal_maker):
    principal = await principal_maker(guild["id"])
    await _message(guild, principal, msg_type="artifact", work_ref=None)

    components = await scoring.collect_components(guild["id"])
    assert components.get(principal["id"], {}).get("artifacts", 0) == 0


@pytest.mark.asyncio
async def test_a_proposal_needs_engagement_from_distinct_others(client, guild, principal_maker):
    author = await principal_maker(guild["id"])
    ally = await principal_maker(guild["id"])

    # One responder is not enough, and the author replying to themselves
    # certainly is not.
    root = await _message(guild, author, msg_type="propose", content="plan A")
    await _message(guild, author, root=root, content="bump")
    await _message(guild, ally, root=root, content="looks good")

    components = await scoring.collect_components(guild["id"])
    assert components[author["id"]]["proposals_engaged"] == 0

    second = await principal_maker(guild["id"])
    await _message(guild, second, root=root, content="agreed")

    components = await scoring.collect_components(guild["id"])
    assert components[author["id"]]["proposals_engaged"] == 1


@pytest.mark.asyncio
async def test_flow_violations_are_collected(client, guild, principal_maker):
    principal = await principal_maker(guild["id"])
    from backend.db import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO flow_violations (channel_id, principal_id, reason) VALUES (?,?,?)",
            (guild["channel_id"], principal["id"], "posted without the floor"),
        )
        await db.commit()

    components = await scoring.collect_components(guild["id"])
    assert components[principal["id"]]["violations"] == 1


# ── rollup and endpoints ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rollup_ranks_by_score(client, guild, principal_maker):
    worker = await principal_maker(guild["id"])
    talker = await principal_maker(guild["id"])

    await _message(guild, worker, msg_type="claim", work_ref="tro:100")
    await _message(guild, worker, msg_type="artifact", work_ref="tro:100")
    for _ in range(30):
        await _message(guild, talker)

    await scoring.recompute_guild(guild["id"])
    board = await scoring.guild_leaderboard(guild["id"])

    assert board[0]["principal_id"] == worker["id"], "shipped work should outrank chatter"
    assert board[0]["rank"] == 1
    assert board[0]["components"]["work_closed"] == 1


@pytest.mark.asyncio
async def test_a_recompute_clears_stale_rows(client, guild, principal_maker):
    principal = await principal_maker(guild["id"])
    await _message(guild, principal)
    await scoring.recompute_guild(guild["id"])
    assert len(await scoring.guild_leaderboard(guild["id"])) >= 1

    from backend.db import get_db
    async with get_db() as db:
        await db.execute("DELETE FROM channel_messages WHERE channel_id=?", (guild["channel_id"],))
        await db.commit()

    await scoring.recompute_guild(guild["id"])
    assert await scoring.guild_leaderboard(guild["id"]) == []


@pytest.mark.asyncio
async def test_banned_principals_drop_off_the_board(client, guild, principal_maker):
    principal = await principal_maker(guild["id"])
    await _message(guild, principal, msg_type="artifact", work_ref="tro:5")
    await scoring.recompute_guild(guild["id"])
    assert any(r["principal_id"] == principal["id"] for r in await scoring.guild_leaderboard(guild["id"]))

    from backend.db import get_db
    async with get_db() as db:
        await db.execute(
            "UPDATE guild_memberships SET banned_at=datetime('now') WHERE guild_id=? AND principal_id=?",
            (guild["id"], principal["id"]),
        )
        await db.commit()

    board = await scoring.guild_leaderboard(guild["id"])
    assert not any(r["principal_id"] == principal["id"] for r in board)


@pytest.mark.asyncio
async def test_leaderboard_endpoint_explains_itself(client, guild, principal_maker):
    principal = await principal_maker(guild["id"])
    await _message(guild, principal, msg_type="artifact", work_ref="tro:11")

    resp = await client.get(f"/api/guilds/{guild['slug']}/leaderboard?refresh=true")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["count"] >= 1
    # A ranking nobody can explain is a ranking nobody trusts.
    assert body["weights"]["work_closed"] > body["weights"]["message"]
    assert body["weights"]["violation"] < 0
    assert body["computed_at"]
    assert "components" in body["leaderboard"][0]


@pytest.mark.asyncio
async def test_global_board_is_a_rollup_of_guild_work(client, guild, principal_maker):
    principal = await principal_maker(guild["id"])
    await _message(guild, principal, msg_type="artifact", work_ref="tro:22")
    await scoring.recompute_guild(guild["id"])

    rows = await scoring.global_leaderboard()
    mine = next((r for r in rows if r["principal_id"] == principal["id"]), None)
    assert mine is not None
    assert mine["guilds"] >= 1
    assert mine["score"] > 0
