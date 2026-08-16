"""Voice session persistence schema — the tables that make a voice conversation
a durable object instead of the in-process dict voice_session.py keeps today."""
import uuid

import aiosqlite
import pytest
import pytest_asyncio

from backend.db import DB_PATH, init_agents_db


@pytest_asyncio.fixture(scope="module")
async def voice_db():
    """Schema-only boot. Deliberately does not use the app-wide `client` fixture:
    these tables are created by init_agents_db(), so the test should fail when
    that boot path stops creating them, not when an unrelated import breaks."""
    await init_agents_db()


@pytest_asyncio.fixture
async def new_agent(voice_db):
    """Factory inserting an isolated agent row; returns its id."""
    async def _make() -> int:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "INSERT INTO agents (name, api_key, bio) VALUES (?,?,?)",
                (f"VoiceTestAgent_{uuid.uuid4().hex[:12]}", uuid.uuid4().hex, "voice schema test"),
            )
            await db.commit()
            return cur.lastrowid

    return _make


async def _new_session(agent_id: int, **overrides) -> str:
    fields = {
        "id": f"vsess_{uuid.uuid4().hex}",
        "agent_id": agent_id,
        "engine": "gemini_live",
        "framework": "native",
        "ws_token_hash": uuid.uuid4().hex,
        **overrides,
    }
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"INSERT INTO voice_sessions ({cols}) VALUES ({marks})", tuple(fields.values()))
        await db.commit()
    return fields["id"]


@pytest.mark.parametrize(
    "table",
    ["voice_sessions", "voice_session_turns", "voice_session_tool_calls", "voice_session_turns_fts"],
)
async def test_voice_tables_created_on_boot(voice_db, table):
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT name FROM sqlite_master WHERE name=?", (table,)
        )).fetchone()
    assert row is not None, f"{table} should be created by init_agents_db()"


async def test_session_defaults_are_applied(new_agent):
    session_id = await _new_session(await new_agent())

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM voice_sessions WHERE id=?", (session_id,)
        )).fetchone()

    assert row["status"] == "active"
    assert row["ttl_seconds"] == 1800
    assert row["started_at"] and row["last_activity_at"]
    assert row["stopped_at"] is None


async def test_turns_are_ordered_and_sequence_is_unique_per_session(new_agent):
    agent_id = await new_agent()
    session_id = await _new_session(agent_id)

    async with aiosqlite.connect(DB_PATH) as db:
        for seq, (role, text) in enumerate(
            [("user", "what is my balance"), ("assistant", "checking now"), ("tool", "")], start=1
        ):
            await db.execute(
                "INSERT INTO voice_session_turns (id, session_id, agent_id, role, content_text, sequence_num)"
                " VALUES (?,?,?,?,?,?)",
                (f"vturn_{uuid.uuid4().hex}", session_id, agent_id, role, text, seq),
            )
        await db.commit()

        rows = await (await db.execute(
            "SELECT role FROM voice_session_turns WHERE session_id=? ORDER BY sequence_num",
            (session_id,),
        )).fetchall()
        assert [r[0] for r in rows] == ["user", "assistant", "tool"]

        # A replayed frame must not be able to fork the transcript at a sequence
        # number that is already written.
        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                "INSERT INTO voice_session_turns (id, session_id, agent_id, role, sequence_num)"
                " VALUES (?,?,?,?,?)",
                (f"vturn_{uuid.uuid4().hex}", session_id, agent_id, "user", 1),
            )


async def test_turns_are_full_text_searchable(new_agent):
    agent_id = await new_agent()
    session_id = await _new_session(agent_id)
    turn_id = f"vturn_{uuid.uuid4().hex}"
    said = "rebalance the treasury into stablecoins"

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO voice_session_turns (id, session_id, agent_id, role, content_text, sequence_num)"
            " VALUES (?,?,?,?,?,?)",
            (turn_id, session_id, agent_id, "user", said, 1),
        )
        await db.execute(
            "INSERT INTO voice_session_turns_fts (session_id, agent_id, turn_id, role, content_text)"
            " VALUES (?,?,?,?,?)",
            (session_id, agent_id, turn_id, "user", said),
        )
        await db.commit()

        # porter stemming: "treasuries" is not the stored surface form
        rows = await (await db.execute(
            "SELECT turn_id FROM voice_session_turns_fts WHERE content_text MATCH ? AND session_id=?",
            ("treasuries", session_id),
        )).fetchall()

    assert [r[0] for r in rows] == [turn_id]


async def test_tool_calls_record_errors_and_timing(new_agent):
    agent_id = await new_agent()
    session_id = await _new_session(agent_id)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO voice_session_tool_calls"
            " (id, session_id, agent_id, tool_name, tool_source, arguments_json, result_json, is_error, duration_ms)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                f"vtc_{uuid.uuid4().hex}", session_id, agent_id,
                "vantage__api_agents_me_wallets_get", "vantage_mcp",
                '{"network":"solana"}', '{"error":"rate limited"}', 1, 412,
            ),
        )
        await db.commit()

        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM voice_session_tool_calls WHERE session_id=?", (session_id,)
        )).fetchone()

    assert row["is_error"] == 1
    assert row["duration_ms"] == 412
    assert row["tool_source"] == "vantage_mcp"
    assert row["created_at"]


async def test_session_is_scoped_to_its_agent(new_agent):
    """Two agents' sessions must not collide in the per-agent active lookup."""
    mine_id = await new_agent()
    theirs_id = await new_agent()

    my_session = await _new_session(mine_id)
    await _new_session(theirs_id)

    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute(
            "SELECT id FROM voice_sessions WHERE agent_id=? AND status='active'", (mine_id,)
        )).fetchall()

    assert [r[0] for r in rows] == [my_session]


async def test_a_locked_database_does_not_lose_a_turn(new_agent, monkeypatch):
    """Under concurrent writers SQLite raises "database is locked". That used to
    escape append_turn's retry loop and be swallowed by the relay's broad
    except, dropping the turn with only a log line — in the feature whose whole
    point is that transcripts are durable."""
    import aiosqlite as _aio
    from backend import voice_session_store as store

    agent_id = await new_agent()
    session_id = await _new_session(agent_id)

    real_get_db = store.get_db
    calls = {"n": 0}

    def flaky_get_db():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _aio.OperationalError("database is locked")
        return real_get_db()

    monkeypatch.setattr(store, "get_db", flaky_get_db)
    result = await store.append_turn(session_id, agent_id, "user", content_text="survived the lock")

    assert result["sequence_num"] == 1
    assert calls["n"] >= 2, "expected a retry after the lock error"

    monkeypatch.undo()
    turns = await store.get_transcript(session_id)
    assert [t["content_text"] for t in turns] == ["survived the lock"]


async def test_a_non_lock_operational_error_still_raises(new_agent, monkeypatch):
    """Only lock contention is retried; a real schema error must surface."""
    import aiosqlite as _aio
    from backend import voice_session_store as store

    agent_id = await new_agent()
    session_id = await _new_session(agent_id)

    def broken_get_db():
        raise _aio.OperationalError("no such table: voice_session_turns")

    monkeypatch.setattr(store, "get_db", broken_get_db)
    with pytest.raises(_aio.OperationalError, match="no such table"):
        await store.append_turn(session_id, agent_id, "user", content_text="x")
