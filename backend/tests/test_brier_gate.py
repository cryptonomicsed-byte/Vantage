"""brier_calibration_gate: abstentions recorded, excluded from Brier, and resolved.

The behaviour under test is the fix for a gate that could never close:
brier_predictions was empty because only "yes"/"no" above threshold were
stored, while the live decision path returns "skip" by default. Every one of
these tests fails against the old module.
"""
import time

import pytest
import pytest_asyncio

from backend import brier_calibration_gate as gate
from backend.db import get_db


@pytest_asyncio.fixture(autouse=True)
async def _schema(client):
    """Start every test from an empty pair of tables.

    `client` is the session-scoped fixture in conftest: requesting it is what
    guarantees the schema exists (it runs init_all_databases() once), so this
    module does not carry its own copy of the boot sequence.
    """
    async with get_db() as db:
        await db.execute("DELETE FROM brier_predictions")
        await db.execute("DELETE FROM prediction_signals")
        await db.commit()
    yield


async def _row(signal_id: str) -> dict:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM brier_predictions WHERE signal_id = ?", (signal_id,)
        )
        cols = [d[0] for d in (cur.description or [])]
        row = await cur.fetchone()
        return dict(zip(cols, row)) if row else {}


async def _mark_scored(signal_id: str, outcome: str) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE brier_predictions SET outcome=?, scored=1 WHERE signal_id=?",
            (outcome, signal_id),
        )
        await db.commit()


async def _signal(signal_id: str, source: str, market_id: str) -> None:
    async with get_db() as db:
        await db.execute(
            """INSERT OR REPLACE INTO prediction_signals
               (id, source, market_id, question, yes_price, no_price, volume_24h,
                end_date, category, raw_json, ingested_ts, processed)
               VALUES (?, ?, ?, 'q', 0.5, 0.5, 1000, '', '', '{}', ?, 0)""",
            (signal_id, source, market_id, int(time.time())),
        )
        await db.commit()


# ── recording ────────────────────────────────────────────────────────────────

async def test_skip_is_recorded_as_abstention():
    await gate.record_prediction("s-skip", "skip", 0.9, direction="pass")
    row = await _row("s-skip")
    assert row, "a skip must still be recorded"
    assert row["verdict"] == "skip"
    assert row["abstained"] == 1
    assert row["scored"] == 0


async def test_directional_call_is_not_an_abstention():
    await gate.record_prediction("s-yes", "yes", 0.8)
    row = await _row("s-yes")
    assert row["abstained"] == 0
    assert row["scored"] == 0


async def test_record_carries_the_reasoning_fields():
    await gate.record_prediction(
        "s-ctx", "no", 0.7, direction="down", conviction="high",
        should_trade=True, market_id="0xabc", reasoning="why",
    )
    row = await _row("s-ctx")
    assert row["direction"] == "down"
    assert row["conviction"] == "high"
    assert row["should_trade"] == 1.0
    assert row["market_id"] == "0xabc"
    assert row["reasoning"] == "why"


# ── scoring ──────────────────────────────────────────────────────────────────

async def test_brier_excludes_abstentions(monkeypatch):
    await gate.record_prediction("d1", "yes", 0.9)
    await gate.record_prediction("a1", "skip", 0.95)
    await _mark_scored("d1", "yes")
    await _mark_scored("a1", "yes")

    seen = {}

    async def fake_brier(predictions, outcomes):
        seen["preds"] = list(predictions)
        seen["outcomes"] = list(outcomes)
        return {"brier_score": 0.0}

    monkeypatch.setattr(gate, "_julia_brier", fake_brier)
    await gate.compute_rolling_brier()
    assert seen["preds"] == [0.9], "only the directional call may be scored"


async def test_brier_excludes_voids(monkeypatch):
    await gate.record_prediction("v1", "yes", 0.6)
    await _mark_scored("v1", "void")
    called = {"n": 0}

    async def fake_brier(predictions, outcomes):
        called["n"] += 1
        return {"brier_score": 0.0}

    monkeypatch.setattr(gate, "_julia_brier", fake_brier)
    score = await gate.compute_rolling_brier()
    assert score == 0.0
    assert called["n"] == 0, "a void settlement has no correctness to score"


async def test_empty_window_is_reported_as_vacuous_not_perfect():
    stats = await gate.brier_stats()
    assert stats["scored"] == 0
    assert stats["vacuous"] is True
    assert stats["gated"] is False


async def test_gate_closes_when_rolling_brier_exceeds_threshold(monkeypatch):
    await gate.record_prediction("g1", "yes", 0.9)
    await _mark_scored("g1", "no")  # confidently wrong

    async def fake_brier(predictions, outcomes):
        return {"brier_score": 0.9}

    monkeypatch.setattr(gate, "_julia_brier", fake_brier)
    gated, score = await gate.should_gate()
    assert gated is True
    assert score == 0.9

    stats = await gate.brier_stats()
    assert stats["vacuous"] is False
    assert stats["gated"] is True


async def test_record_outcome_refuses_unresolved():
    await gate.record_prediction("u1", "yes", 0.8)
    assert await gate.record_outcome("u1", "unresolved") is False
    row = await _row("u1")
    assert row["scored"] == 0
    assert row["outcome"] is None


# ── resolution wiring ────────────────────────────────────────────────────────

async def test_resolver_records_yes_on_settlement(monkeypatch):
    await _signal("r1", "polymarket", "0xabc")
    await gate.record_prediction("r1", "yes", 0.8, market_id="0xabc")

    from backend import prediction_markets

    async def fake_resolution(venue, market_id):
        assert venue == "polymarket"
        assert market_id == "0xabc"
        return "yes"

    monkeypatch.setattr(prediction_markets, "resolution", fake_resolution)
    counters = await gate.resolve_pending_outcomes()
    assert counters["resolved"] == 1
    row = await _row("r1")
    assert row["outcome"] == "yes"
    assert row["scored"] == 1


async def test_resolver_marks_void_without_scoring(monkeypatch):
    await _signal("r2", "polymarket", "0xdef")
    await gate.record_prediction("r2", "no", 0.7, market_id="0xdef")

    from backend import prediction_markets

    async def fake_resolution(venue, market_id):
        return "void"

    monkeypatch.setattr(prediction_markets, "resolution", fake_resolution)
    counters = await gate.resolve_pending_outcomes()
    assert counters["void"] == 1
    row = await _row("r2")
    assert row["outcome"] == "void"
    assert row["scored"] == 1


async def test_resolver_leaves_unparsed_settlement_pending(monkeypatch):
    await _signal("r3", "polymarket", "0x999")
    await gate.record_prediction("r3", "yes", 0.6, market_id="0x999")

    from backend import prediction_markets

    async def fake_resolution(venue, market_id):
        return None  # venue unreachable / payload unrecognized

    monkeypatch.setattr(prediction_markets, "resolution", fake_resolution)
    counters = await gate.resolve_pending_outcomes()
    assert counters["pending"] == 1
    row = await _row("r3")
    assert row["scored"] == 0, "a missing answer must not become a settlement"


async def test_resolver_ignores_venues_without_a_reader():
    await _signal("k1", "kalshi", "KX-1")
    await gate.record_prediction("k1", "yes", 0.8, market_id="KX-1")
    counters = await gate.resolve_pending_outcomes()
    assert counters["checked"] == 0
    row = await _row("k1")
    assert row["scored"] == 0


async def test_resolver_can_score_an_abstention_without_counting_it(monkeypatch):
    """Abstentions resolve too -- that is what makes skipping auditable."""
    await _signal("a2", "polymarket", "0x111")
    await gate.record_prediction("a2", "skip", 0.9, market_id="0x111")

    from backend import prediction_markets

    async def fake_resolution(venue, market_id):
        return "yes"

    monkeypatch.setattr(prediction_markets, "resolution", fake_resolution)
    counters = await gate.resolve_pending_outcomes()
    assert counters["resolved"] == 1

    row = await _row("a2")
    assert row["outcome"] == "yes"
    assert row["abstained"] == 1

    stats = await gate.brier_stats()
    assert stats["abstentions_resolved"] == 1
    assert stats["scored"] == 0, "the abstention still contributes nothing to Brier"
