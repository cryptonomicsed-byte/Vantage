"""Prediction tab API: auth, shapes, filters, and the two display rules.

The tab's contract is small but specific, and two of these tests exist because
the alternative shipped once already:

  - an unkeyed read returns 401, and the UI must show that as an auth failure
    rather than an empty feed;
  - a venue that publishes 0.0/0.0 has published no price. Rendering that as
    0% claims certainty the venue never stated, so the API nulls it.
"""
import time

import pytest

from backend import brier_calibration_gate as gate
from backend.db import get_db

PREFIX = "test-pm-"


@pytest.fixture(autouse=True)
async def _clean(client):
    """Start each test from empty prediction tables (the shared fixture DB)."""
    async with get_db() as db:
        await db.execute("DELETE FROM brier_predictions WHERE signal_id LIKE ?", (PREFIX + "%",))
        await db.execute("DELETE FROM prediction_signals WHERE id LIKE ?", (PREFIX + "%",))
        await db.commit()
    yield


async def _signal(sid, *, source="kalshi", market_id="KX-TEST", question="q",
                  yes=0.6, no=0.4, volume=1000.0, end_offset=86400):
    end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + end_offset))
    async with get_db() as db:
        await db.execute(
            """INSERT OR REPLACE INTO prediction_signals
               (id, source, market_id, question, yes_price, no_price, volume_24h,
                end_date, category, raw_json, ingested_ts, processed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,0)""",
            (sid, source, market_id, question, yes, no, volume, end, "", "{}", int(time.time())),
        )
        await db.commit()


async def _headers(fresh_agent):
    agent = await fresh_agent()
    return {"X-Agent-Key": agent["api_key"]}


# ── auth ─────────────────────────────────────────────────────────────────────

async def test_overview_requires_agent_key(client):
    r = await client.get("/api/trading/prediction/overview")
    assert r.status_code == 401


async def test_all_read_endpoints_reject_an_invalid_key(client):
    for path in ("overview", "markets", "decisions", "calibration", "sources"):
        r = await client.get(f"/api/trading/prediction/{path}", headers={"X-Agent-Key": "nope"})
        assert r.status_code == 401, path


# ── shapes ───────────────────────────────────────────────────────────────────

async def test_overview_shape(client, fresh_agent):
    h = await _headers(fresh_agent)
    r = await client.get("/api/trading/prediction/overview", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    for key in ("signals", "decisions", "calibration", "markets"):
        assert key in d
    for key in ("total", "last_24h", "unprocessed", "by_source"):
        assert key in d["signals"]
    for key in ("total", "abstentions", "directional", "verdicts"):
        assert key in d["decisions"]
    for key in ("recorded", "scored", "pending", "vacuous", "gated", "brier_score"):
        assert key in d["calibration"]


async def test_markets_nulls_a_venue_that_published_no_price(client, fresh_agent):
    await _signal(PREFIX + "noprice", market_id="KX-NOPRICE", yes=0.0, no=0.0)
    h = await _headers(fresh_agent)
    r = await client.get("/api/trading/prediction/markets", headers=h, params={"limit": 50})
    row = next(m for m in r.json()["markets"] if m["market_id"] == "KX-NOPRICE")
    assert row["implied_yes"] is None and row["implied_no"] is None


async def test_markets_keeps_a_real_price(client, fresh_agent):
    await _signal(PREFIX + "priced", market_id="KX-PRICED", yes=0.62, no=0.38)
    h = await _headers(fresh_agent)
    r = await client.get("/api/trading/prediction/markets", headers=h, params={"limit": 50})
    row = next(m for m in r.json()["markets"] if m["market_id"] == "KX-PRICED")
    assert row["implied_yes"] == 0.62


async def test_markets_dedupes_by_market_id(client, fresh_agent):
    await _signal(PREFIX + "dup1", market_id="KX-DUP")
    await _signal(PREFIX + "dup2", market_id="KX-DUP", question="q again")
    h = await _headers(fresh_agent)
    r = await client.get("/api/trading/prediction/markets", headers=h, params={"limit": 100})
    rows = [m for m in r.json()["markets"] if m["market_id"] == "KX-DUP"]
    assert len(rows) == 1, "the collectors re-ingest; the feed must not repeat a market"


async def test_open_only_hides_closed_markets(client, fresh_agent):
    await _signal(PREFIX + "closed", market_id="KX-CLOSED", end_offset=-3600)
    await _signal(PREFIX + "open", market_id="KX-OPEN", end_offset=86400)
    h = await _headers(fresh_agent)
    r = await client.get(
        "/api/trading/prediction/markets", headers=h,
        params={"limit": 100, "open_only": "true"},
    )
    ids = {m["market_id"] for m in r.json()["markets"]}
    assert "KX-OPEN" in ids and "KX-CLOSED" not in ids


async def test_decided_only_excludes_undecided(client, fresh_agent):
    await _signal(PREFIX + "undecided", market_id="KX-UNDECIDED")
    await _signal(PREFIX + "decided", market_id="KX-DECIDED")
    await gate.record_prediction(PREFIX + "decided", "skip", 0.8, market_id="KX-DECIDED")

    h = await _headers(fresh_agent)
    r = await client.get(
        "/api/trading/prediction/markets", headers=h,
        params={"limit": 100, "decided_only": "true"},
    )
    ids = {m["market_id"] for m in r.json()["markets"]}
    assert "KX-DECIDED" in ids and "KX-UNDECIDED" not in ids


# ── decisions and calibration ───────────────────────────────────────────────

async def test_decisions_include_abstentions(client, fresh_agent):
    await _signal(PREFIX + "skip", market_id="KX-SKIP", question="will it rain")
    await gate.record_prediction(PREFIX + "skip", "skip", 0.91, direction="pass", conviction="none")

    h = await _headers(fresh_agent)
    r = await client.get("/api/trading/prediction/decisions", headers=h, params={"limit": 50})
    row = next(d for d in r.json()["decisions"] if d["signal_id"] == PREFIX + "skip")
    assert row["verdict"] == "skip"
    assert row["abstained"] == 1
    assert row["question"] == "will it rain"


async def test_decisions_verdict_filter(client, fresh_agent):
    await _signal(PREFIX + "a", market_id="KX-A")
    await _signal(PREFIX + "b", market_id="KX-B")
    await gate.record_prediction(PREFIX + "a", "yes", 0.8)
    await gate.record_prediction(PREFIX + "b", "skip", 0.8)

    h = await _headers(fresh_agent)
    r = await client.get(
        "/api/trading/prediction/decisions", headers=h,
        params={"limit": 50, "verdict": "yes"},
    )
    verdicts = {d["verdict"] for d in r.json()["decisions"]}
    assert verdicts == {"yes"}


async def test_calibration_calls_an_empty_window_vacuous(client, fresh_agent):
    h = await _headers(fresh_agent)
    r = await client.get("/api/trading/prediction/calibration", headers=h)
    d = r.json()
    assert "stats" in d and "abstention_audit" in d
    # Whatever else is in the shared test DB, the gate must not claim
    # calibration it does not have.
    if d["stats"]["scored"] == 0:
        assert d["stats"]["vacuous"] is True
        assert d["stats"]["gated"] is False


async def test_sources_reports_open_markets_and_resolvability(client, fresh_agent):
    await _signal(PREFIX + "k", source="kalshi", market_id="KX-K", end_offset=3600)
    await _signal(PREFIX + "m", source="manifold", market_id="MAN-M", end_offset=3600)
    await _signal(PREFIX + "old", source="manifold", market_id="MAN-OLD", end_offset=-3600)

    h = await _headers(fresh_agent)
    r = await client.get("/api/trading/prediction/sources", headers=h)
    d = r.json()
    rows = {s["source"]: s for s in d["sources"]}
    assert rows["kalshi"]["open_markets"] >= 1
    assert rows["kalshi"]["resolvable"] is True
    assert "manifold" in rows and rows["manifold"]["resolvable"] is False
    assert "kalshi" in d["resolvable_venues"]
