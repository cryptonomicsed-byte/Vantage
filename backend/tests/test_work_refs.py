"""A work reference either points at something real or it points at nothing.

The interesting cases are the ones where the old free-text string quietly
did the wrong thing: a reference to a task that does not exist scoring as if
it did, and one principal closing another's claim.
"""
import secrets

import aiosqlite
import pytest
import pytest_asyncio

from backend import coordination as coord
from backend import work_refs as wr
from backend.db import get_db


@pytest_asyncio.fixture(scope="module", autouse=True)
async def schema(client):
    await coord.init_coordination_db()
    await wr.init_work_ref_db()


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
async def open_tro(principal_maker):
    """A real open request in the guild token economy."""
    poster = await principal_maker()
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO tro_requests (agent_id, agent_name, service_type, description, status)
               VALUES (?,?,?,?,'open')""",
            (poster["agent_id"], poster["display_name"], "analysis", "Chart the pair"),
        )
        await db.commit()
        return cur.lastrowid


@pytest_asyncio.fixture
async def open_task(principal_maker):
    poster = await principal_maker()
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO task_listings (poster_id, poster_name, title, status)
               VALUES (?,?,?,'open')""",
            (poster["agent_id"], poster["display_name"], "Write the adapter"),
        )
        await db.commit()
        return cur.lastrowid


# ── the grammar ──────────────────────────────────────────────────────────────

def test_a_well_formed_reference_parses():
    assert wr.parse_work_ref("tro:123") == ("tro", "123")
    assert wr.parse_work_ref("  task:7  ") == ("task", "7")
    assert wr.parse_work_ref("commit:9f3a1c") == ("commit", "9f3a1c")


def test_free_text_is_not_a_reference():
    """This is what the field used to hold. It must not silently become one."""
    for junk in ["", None, "the thing bob asked for", "tro", "tro:", ":123", "TRO:1"]:
        assert wr.parse_work_ref(junk) is None, junk


def test_an_unknown_kind_is_refused_rather_than_guessed():
    assert wr.parse_work_ref("bounty:1") is None


def test_the_kind_table_and_the_grammar_agree():
    """Every kind the resolver advertises must be reachable by the parser."""
    for name in wr.KINDS:
        assert wr.parse_work_ref(f"{name}:1") == (name, "1")


# ── resolution ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_reference_to_a_real_row_resolves_verified(client, open_tro):
    ref = await wr.resolve(f"tro:{open_tro}")
    assert ref is not None and ref.verified
    assert ref.status == "open"
    assert "Chart" in ref.title


@pytest.mark.asyncio
async def test_a_reference_to_a_row_that_does_not_exist_resolves_to_nothing(client):
    """The old behaviour scored this. That is the bug."""
    assert await wr.resolve("tro:99999999") is None


@pytest.mark.asyncio
async def test_a_local_kind_with_a_non_numeric_id_does_not_resolve(client):
    assert await wr.resolve("tro:abc") is None


@pytest.mark.asyncio
async def test_a_git_reference_resolves_but_is_never_marked_verified(client):
    """Nothing in this process can confirm a commit exists, and pretending
    otherwise is how an unverifiable claim earns a verified score."""
    ref = await wr.resolve("commit:deadbeef")
    assert ref is not None
    assert ref.verified is False


# ── transitions ──────────────────────────────────────────────────────────────

async def _status(table, row_id):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT status FROM {table} WHERE id=?", (row_id,))
        return dict(await cur.fetchone())["status"]


@pytest.mark.asyncio
async def test_claiming_in_chat_marks_the_request_matched(client, open_tro, principal_maker):
    """The whole point: a claim in a workspace is a claim in the marketplace."""
    worker = await principal_maker()
    result = await wr.record_link(
        event_id=secrets.token_hex(32), channel_id=None, principal=worker,
        link_type=wr.LINK_CLAIM, raw_work_ref=f"tro:{open_tro}",
    )
    assert result["transitioned"] is True
    assert await _status("tro_requests", open_tro) == "matched"


@pytest.mark.asyncio
async def test_an_artifact_closes_the_work_the_same_principal_claimed(
    client, open_tro, principal_maker
):
    worker = await principal_maker()
    ref = f"tro:{open_tro}"
    await wr.record_link(event_id=secrets.token_hex(32), channel_id=None, principal=worker,
                         link_type=wr.LINK_CLAIM, raw_work_ref=ref)
    result = await wr.record_link(event_id=secrets.token_hex(32), channel_id=None,
                                  principal=worker, link_type=wr.LINK_ARTIFACT, raw_work_ref=ref)
    assert result["transitioned"] is True
    assert await _status("tro_requests", open_tro) == "completed"


@pytest.mark.asyncio
async def test_you_cannot_close_work_someone_else_claimed(client, open_tro, principal_maker):
    """Otherwise posting `artifact tro:123` is a way to bank another agent's
    delivery, and the leaderboard rewards it."""
    worker = await principal_maker()
    thief = await principal_maker()
    ref = f"tro:{open_tro}"
    await wr.record_link(event_id=secrets.token_hex(32), channel_id=None, principal=worker,
                         link_type=wr.LINK_CLAIM, raw_work_ref=ref)
    result = await wr.record_link(event_id=secrets.token_hex(32), channel_id=None,
                                  principal=thief, link_type=wr.LINK_ARTIFACT, raw_work_ref=ref)
    assert result["transitioned"] is False
    assert "claimed by another" in result["note"]
    assert await _status("tro_requests", open_tro) == "matched"


@pytest.mark.asyncio
async def test_a_second_claimant_does_not_steal_an_already_claimed_row(
    client, open_task, principal_maker
):
    """The guard lives in the UPDATE's WHERE clause, so the race resolves in
    the database rather than between two reads."""
    first = await principal_maker()
    second = await principal_maker()
    ref = f"task:{open_task}"
    a = await wr.record_link(event_id=secrets.token_hex(32), channel_id=None, principal=first,
                             link_type=wr.LINK_CLAIM, raw_work_ref=ref)
    b = await wr.record_link(event_id=secrets.token_hex(32), channel_id=None, principal=second,
                             link_type=wr.LINK_CLAIM, raw_work_ref=ref)
    assert a["transitioned"] is True
    assert b["transitioned"] is False
    assert await wr.claim_holder("task", str(open_task)) == first["id"]


@pytest.mark.asyncio
async def test_an_external_reference_is_recorded_but_moves_nothing(client, principal_maker):
    worker = await principal_maker()
    result = await wr.record_link(
        event_id=secrets.token_hex(32), channel_id=None, principal=worker,
        link_type=wr.LINK_ARTIFACT, raw_work_ref="pr:41",
    )
    assert result["verified"] is False
    assert result["transitioned"] is False
    assert "not verified" in result["note"]


@pytest.mark.asyncio
async def test_an_unresolvable_reference_records_no_link_at_all(client, principal_maker):
    worker = await principal_maker()
    assert await wr.record_link(
        event_id=secrets.token_hex(32), channel_id=None, principal=worker,
        link_type=wr.LINK_ARTIFACT, raw_work_ref="whatever bob wanted",
    ) is None


@pytest.mark.asyncio
async def test_recording_the_same_event_twice_is_idempotent(client, open_tro, principal_maker):
    """The indexer can replay the relay. Replaying must not double-count."""
    worker = await principal_maker()
    event_id = secrets.token_hex(32)
    ref = f"tro:{open_tro}"
    await wr.record_link(event_id=event_id, channel_id=None, principal=worker,
                         link_type=wr.LINK_CLAIM, raw_work_ref=ref)
    await wr.record_link(event_id=event_id, channel_id=None, principal=worker,
                         link_type=wr.LINK_CLAIM, raw_work_ref=ref)
    assert len(await wr.links_for_ref("tro", str(open_tro))) == 1


# ── what the leaderboard is allowed to count ─────────────────────────────────

@pytest.mark.asyncio
async def test_only_a_transition_that_happened_counts_as_a_delivery(
    client, open_tro, principal_maker
):
    worker = await principal_maker()
    ref = f"tro:{open_tro}"
    await wr.record_link(event_id=secrets.token_hex(32), channel_id=None, principal=worker,
                         link_type=wr.LINK_CLAIM, raw_work_ref=ref)
    assert await wr.verified_deliveries(worker["id"]) == 0
    await wr.record_link(event_id=secrets.token_hex(32), channel_id=None, principal=worker,
                         link_type=wr.LINK_ARTIFACT, raw_work_ref=ref)
    assert await wr.verified_deliveries(worker["id"]) == 1


@pytest.mark.asyncio
async def test_an_unverifiable_artifact_is_not_a_delivery(client, principal_maker):
    worker = await principal_maker()
    await wr.record_link(event_id=secrets.token_hex(32), channel_id=None, principal=worker,
                         link_type=wr.LINK_ARTIFACT, raw_work_ref="commit:0badc0de")
    assert await wr.verified_deliveries(worker["id"]) == 0


# ── binding ──────────────────────────────────────────────────────────────────

def test_numbered_placeholders_bind_by_position_not_by_count():
    """The first version of this replaced all three with `?` and passed all
    three arguments. Every statement that used only two bound the wrong
    columns, and the two that used three happened to work -- which is how a
    bug like this survives a green run."""
    sql, args = wr._bind("UPDATE t SET who=?2 WHERE id=?3", (7, "alice", 42))
    assert sql == "UPDATE t SET who=? WHERE id=?"
    assert args == ("alice", 42)


def test_a_statement_using_every_argument_still_binds_in_order():
    sql, args = wr._bind("UPDATE t SET a=?1, b=?2 WHERE id=?3", (7, "alice", 42))
    assert args == (7, "alice", 42)


def test_every_configured_statement_binds_without_error():
    """A typo in one of the KINDS statements should fail here, not in
    production the first time somebody claims that kind of work."""
    for spec in wr.KINDS.values():
        for sql in (spec.claim_sql, spec.close_sql):
            if not sql:
                continue
            statement, args = wr._bind(sql, (1, "x", 2))
            assert statement.count("?") == len(args)
