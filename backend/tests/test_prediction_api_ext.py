"""Depth endpoints: calibration history, single-market detail, health, forecast.

Each of these exists because the flat tab could not answer a question:

  - history: "is the gate doing anything?" -> needs a daily series where a day
    with no scored call is `null`, not 0.0
  - market detail: "why this market?" -> needs the decision and the evidence
    that matched it, keyed by (source, market_id) because ids are not unique
    across venues
  - health: "is the feed alive?" -> a collector can ingest for weeks while every
    row is archived, and the totals still look healthy
  - forecast: "what does the substrate expect?" -> Mycelium's own predictions
    table, never a forecast invented here
"""
import time

import pytest

from backend.db import get_db

PREFIX = "test-deep-"


@pytest.fixture(autouse=True)
async def _clean(client):
    async with get_db() as db:
        await db.execute("DELETE FROM brier_predictions WHERE signal_id LIKE ?", (PREFIX + "%",))
        await db.execute("DELETE FROM prediction_signals WHERE id LIKE ?", (PREFIX + "%",))
        await db.execute("DELETE FROM context_signals")
        await db.commit()
    yield


async def _headers(fresh_agent):
    agent = await fresh_agent()
    return {"X-Agent-Key": agent["api_key"]}


async def _signal(sid, *, source="kalshi", market_id="KX-DEEP", question="Will CPI print above 3%?",
                  end_offset=86400, processed=0):
    end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + end_offset))
    async with get_db() as db:
        await db.execute(
            """INSERT OR REPLACE INTO prediction_signals
               (id, source, market_id, question, yes_price, no_price, volume_24h,
                end_date, category, raw_json, ingested_ts, processed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, source, market_id, question, 0.6, 0.4, 1000.0, end, "", "{}",
             int(time.time()), processed),
        )
        await db.commit()


async def _decision(sid, *, verdict="skip", confidence=0.3, abstained=1, outcome=None,
                    scored=0, ts_offset=0, market_id="KX-DEEP"):
    async with get_db() as db:
        await db.execute(
            """INSERT INTO brier_predictions
               (signal_id, verdict, confidence, predicted_ts, outcome, scored,
                abstained, direction, conviction, should_trade, market_id, reasoning)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, verdict, confidence, int(time.time()) + ts_offset, outcome, scored,
             abstained, "pass", "none", 0, market_id, "test"),
        )
        await db.commit()


# ── calibration history ─────────────────────────────────────────────────────

async def test_history_is_vacuous_when_nothing_scored(client, fresh_agent):
    h = await _headers(fresh_agent)
    await _signal(PREFIX + "h1")
    await _decision(PREFIX + "h1", verdict="skip", abstained=1)

    r = await client.get("/api/trading/prediction/calibration/history?window=7d", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["vacuous"] is True
    assert d["totals"]["scored"] == 0
    assert d["totals"]["brier"] is None, "no scored calls must not read as 0.000"
    assert d["totals"]["recorded"] == 1
    assert d["totals"]["abstained"] == 1
    assert len(d["series"]) == 7


async def test_history_computes_brier_on_scored_directional_calls(client, fresh_agent):
    h = await _headers(fresh_agent)
    await _signal(PREFIX + "h2")
    # verdict=yes at 0.8 confidence, resolved YES -> (0.8 - 1.0)^2 = 0.04
    await _decision(PREFIX + "h2", verdict="yes", confidence=0.8, abstained=0,
                    outcome="yes", scored=1)

    r = await client.get("/api/trading/prediction/calibration/history?window=7d", headers=h)
    d = r.json()
    assert d["vacuous"] is False
    assert d["totals"]["scored"] == 1
    assert d["totals"]["brier"] == pytest.approx(0.04, abs=1e-6)


async def test_history_reads_a_no_verdict_as_the_complement(client, fresh_agent):
    h = await _headers(fresh_agent)
    await _signal(PREFIX + "h3")
    # verdict=no at 0.8 -> predicts 0.2 for YES; outcome YES -> (0.2-1.0)^2 = 0.64
    await _decision(PREFIX + "h3", verdict="no", confidence=0.8, abstained=0,
                    outcome="yes", scored=1)

    d = (await client.get("/api/trading/prediction/calibration/history?window=7d", headers=h)).json()
    assert d["totals"]["brier"] == pytest.approx(0.64, abs=1e-6)


async def test_history_rejects_an_unknown_window(client, fresh_agent):
    h = await _headers(fresh_agent)
    r = await client.get("/api/trading/prediction/calibration/history?window=1y", headers=h)
    assert r.status_code == 422


# ── market detail ───────────────────────────────────────────────────────────

async def test_market_detail_returns_signal_decision_and_venue_url(client, fresh_agent):
    h = await _headers(fresh_agent)
    await _signal(PREFIX + "m1", source="kalshi", market_id="KX-CPI-26", question="Will CPI exceed 3%?")
    await _decision(PREFIX + "m1", verdict="skip", market_id="KX-CPI-26")

    r = await client.get("/api/trading/prediction/markets/kalshi/KX-CPI-26", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["market"]["market_id"] == "KX-CPI-26"
    assert d["decision"]["verdict"] == "skip"
    assert d["venue_url"].startswith("https://kalshi.com/markets/")
    assert isinstance(d["context"], list)


async def test_market_detail_is_keyed_by_source_not_id_alone(client, fresh_agent):
    """Two venues can hold the same string; the composite key must not blur them."""
    h = await _headers(fresh_agent)
    sid = PREFIX + "m2"
    await _signal(sid, source="kalshi", market_id="SAME-ID", question="Kalshi one")
    await _signal(sid + "b", source="polymarket", market_id="SAME-ID", question="Polymarket one")

    k = (await client.get("/api/trading/prediction/markets/kalshi/SAME-ID", headers=h)).json()
    p = (await client.get("/api/trading/prediction/markets/polymarket/SAME-ID", headers=h)).json()
    assert k["market"]["question"] == "Kalshi one"
    assert p["market"]["question"] == "Polymarket one"


async def test_market_detail_404s_on_unknown_market(client, fresh_agent):
    h = await _headers(fresh_agent)
    r = await client.get("/api/trading/prediction/markets/kalshi/NOPE", headers=h)
    assert r.status_code == 404


async def test_market_detail_attaches_matched_context(client, fresh_agent):
    h = await _headers(fresh_agent)
    await _signal(PREFIX + "m3", market_id="KX-CPI-3", question="Will CPI inflation exceed 3%?")
    async with get_db() as db:
        await db.execute(
            """INSERT INTO context_signals
               (source, market_id, signal_type, observed_ts, confidence, direction, payload_json)
               VALUES ('fred','CPIAUCSL','macro',?,0.9,'bearish','{"label": "CPI inflation print"}')""",
            (int(time.time()),),
        )
        await db.commit()

    d = (await client.get("/api/trading/prediction/markets/kalshi/KX-CPI-3", headers=h)).json()
    assert d["context"], "a CPI question should match the CPI print"
    assert d["context"][0]["source"] == "fred"


# ── health ──────────────────────────────────────────────────────────────────

async def test_health_flags_a_feed_with_no_open_markets(client, fresh_agent):
    h = await _headers(fresh_agent)
    await _signal(PREFIX + "s1", source="polymarket", end_offset=-86400)  # archived

    d = (await client.get("/api/trading/prediction/health", headers=h)).json()
    row = next(r for r in d["sources"] if r["source"] == "polymarket")
    assert row["open_markets"] == 0
    assert row["status"] == "stale", "ingesting archived-only inventory is the failure being caught"
    assert row["open_ratio"] == 0


async def test_health_says_ok_for_a_live_feed_with_open_markets(client, fresh_agent):
    h = await _headers(fresh_agent)
    await _signal(PREFIX + "s2", source="kalshi", end_offset=86400)

    d = (await client.get("/api/trading/prediction/health", headers=h)).json()
    row = next(r for r in d["sources"] if r["source"] == "kalshi")
    assert row["status"] == "ok"


# ── decisions filters ──────────────────────────────────────────────────────

async def test_decisions_can_filter_to_abstentions_only(client, fresh_agent):
    h = await _headers(fresh_agent)
    await _signal(PREFIX + "d1")
    await _decision(PREFIX + "d1", verdict="skip", abstained=1)
    await _signal(PREFIX + "d2")
    await _decision(PREFIX + "d2", verdict="yes", confidence=0.9, abstained=0)

    d = (await client.get("/api/trading/prediction/decisions?abstained=true", headers=h)).json()
    assert [r["verdict"] for r in d["decisions"]] == ["skip"]

    d2 = (await client.get("/api/trading/prediction/decisions?abstained=false", headers=h)).json()
    assert [r["verdict"] for r in d2["decisions"]] == ["yes"]


async def test_decisions_filters_by_source_and_confidence(client, fresh_agent):
    h = await _headers(fresh_agent)
    await _signal(PREFIX + "d3", source="kalshi")
    await _decision(PREFIX + "d3", verdict="yes", confidence=0.9, abstained=0)
    await _signal(PREFIX + "d4", source="polymarket")
    await _decision(PREFIX + "d4", verdict="yes", confidence=0.5, abstained=0)

    d = (await client.get("/api/trading/prediction/decisions?source=kalshi&min_confidence=0.7",
                          headers=h)).json()
    assert d["count"] == 1
    assert d["decisions"][0]["source"] == "kalshi"


# ── forecast ────────────────────────────────────────────────────────────────

async def test_forecast_is_honest_when_the_gateway_is_unreachable(client, fresh_agent, monkeypatch):
    h = await _headers(fresh_agent)
    monkeypatch.setenv("MYCELIUM_URL", "http://127.0.0.1:9")  # discard port

    r = await client.get("/api/trading/prediction/forecast", headers=h)
    assert r.status_code == 200, "an unreachable substrate must not break the tab"
    d = r.json()
    assert d["available"] is False
    assert d["predictions"] == []
    assert "unreachable" in d["reason"]


async def test_forecast_requires_a_key(client):
    r = await client.get("/api/trading/prediction/forecast")
    assert r.status_code == 401
