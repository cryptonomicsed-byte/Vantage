"""Context signal producers: parsing, storage, matching, and the fusion step.

All parsers are pure functions over captured bodies, so nothing here touches the
network. The shapes are the ones verified live on 2026-11-05.
"""
import time

import pytest

from backend.signal_sources import fred, manifold, open_meteo
from backend.signal_sources import store
from backend.signal_sources.base import signal as make_signal
from backend.db import get_db


# ── FRED ─────────────────────────────────────────────────────────────────────

FRED_CSV = """observation_date,CPIAUCSL
2026-06-01,320.100
2026-07-01,321.400
2026-08-01,.
"""


def test_fred_parser_skips_missing_observations():
    item = fred.parse_csv(FRED_CSV, "CPIAUCSL", "CPI", "up")
    assert item is not None
    assert item["payload"]["value"] == 321.4
    assert item["payload"]["previous"] == 320.1
    assert item["payload"]["observation_date"] == "2026-07-01", "the '.' row must be skipped"
    assert round(item["payload"]["delta"], 3) == 1.3


def test_fred_rising_series_reads_bearish_when_risk_read_is_up():
    item = fred.parse_csv(FRED_CSV, "CPIAUCSL", "CPI", "up")
    assert item["direction"] == "bearish"
    assert item["signal_type"] == "macro"
    assert item["source"] == "fred"


def test_fred_falling_series_reads_bullish():
    body = "observation_date,UNRATE\n2026-06-01,4.4\n2026-07-01,4.1\n"
    item = fred.parse_csv(body, "UNRATE", "Unemployment", "up")
    assert item["direction"] == "bullish"


def test_fred_flat_series_is_neutral_and_less_confident():
    body = "observation_date,X\n2026-06-01,5.0\n2026-07-01,5.0\n"
    item = fred.parse_csv(body, "X", "flat", "up")
    assert item["direction"] == "neutral"
    assert item["confidence"] == 0.6


def test_fred_series_with_no_read_is_a_catalyst():
    body = "observation_date,X\n2026-06-01,1.0\n2026-07-01,2.0\n"
    item = fred.parse_csv(body, "X", "no read", "none")
    assert item["direction"] == "catalyst"


def test_fred_single_observation_has_no_delta():
    body = "observation_date,X\n2026-07-01,7.0\n"
    item = fred.parse_csv(body, "X", "one", "up")
    assert item["payload"]["delta"] is None
    assert item["direction"] == "neutral"


# ── Open-Meteo ───────────────────────────────────────────────────────────────

WEATHER = {
    "latitude": 40.71, "longitude": -74.01,
    "daily": {
        "time": ["2026-11-05", "2026-11-06", "2026-11-07"],
        "temperature_2m_max": [21.0, 38.5, 19.0],
        "temperature_2m_min": [12.0, 24.0, 11.0],
        "precipitation_probability_max": [10, 20, 30],
    },
}


def test_weather_extreme_heat_is_a_catalyst():
    item = open_meteo.parse(WEATHER, "new-york")
    assert item["direction"] == "catalyst"
    assert item["payload"]["peak_c_next_72h"] == 38.5
    assert item["payload"]["city"] == "new-york"
    assert item["signal_type"] == "weather"


def test_weather_benign_forecast_is_neutral():
    calm = {
        "latitude": 1, "longitude": 2,
        "daily": {
            "time": ["2026-11-05"],
            "temperature_2m_max": [20.0],
            "temperature_2m_min": [10.0],
            "precipitation_probability_max": [5],
        },
    }
    item = open_meteo.parse(calm, "london")
    assert item["direction"] == "neutral"


def test_weather_empty_payload_is_none():
    assert open_meteo.parse({}, "london") is None


# ── Manifold ─────────────────────────────────────────────────────────────────

MANIFOLD = [
    {"id": "m1", "outcomeType": "BINARY", "isResolved": False, "probability": 0.82,
     "question": "Will X happen?", "volume24hr": 500.0, "totalLiquidity": 1000.0,
     "closeTime": 1793900000000, "url": "https://manifold.markets/x", "creatorUsername": "a"},
    {"id": "m2", "outcomeType": "BINARY", "isResolved": False, "probability": 0.48,
     "question": "Coin flip?", "volume24hr": 900.0},
    {"id": "m3", "outcomeType": "MULTIPLE_CHOICE", "isResolved": False, "probability": 0.9,
     "question": "Not binary", "volume24hr": 9999.0},
    {"id": "m4", "outcomeType": "BINARY", "isResolved": True, "probability": 0.9,
     "question": "Already resolved", "volume24hr": 9999.0},
]


def test_manifold_keeps_only_open_binary_markets():
    items = manifold.parse(MANIFOLD)
    ids = {i["market_id"] for i in items}
    assert ids == {"m1", "m2"}


def test_manifold_ranks_by_volume():
    items = manifold.parse(MANIFOLD)
    assert items[0]["market_id"] == "m2", "900 > 500"


def test_manifold_direction_and_confidence_bands():
    items = {i["market_id"]: i for i in manifold.parse(MANIFOLD)}
    assert items["m1"]["direction"] == "bullish"
    assert items["m1"]["confidence"] == pytest.approx(0.32, abs=0.001)
    assert items["m2"]["direction"] == "neutral"
    assert items["m2"]["confidence"] < 0.05


def test_manifold_rejects_unknown_direction_words():
    with pytest.raises(ValueError):
        make_signal(source="x", market_id="1", signal_type="community",
                    confidence=0.5, direction="maybe", payload={})


# ── storage and matching ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def _clean(client):
    async with get_db() as db:
        await db.execute("DELETE FROM context_signals")
        await db.commit()
    yield


async def test_upsert_replaces_the_previous_read_for_a_key():
    base = dict(source="fred", market_id="CPIAUCSL", signal_type="macro",
                confidence=0.9, direction="bearish", payload={"label": "CPI"})
    await store.upsert([make_signal(**base)])
    await store.upsert([make_signal(**{**base, "confidence": 0.5, "direction": "bullish",
                                       "payload": {"label": "CPI", "value": 2}})])

    rows = await store.latest()
    assert len(rows) == 1, "one row per (source, market_id)"
    assert rows[0]["confidence"] == 0.5
    assert rows[0]["direction"] == "bullish"
    assert rows[0]["payload"]["value"] == 2


async def test_latest_filters_by_source():
    await store.upsert([
        make_signal(source="fred", market_id="A", signal_type="macro", confidence=0.9,
                    direction="bearish", payload={"label": "CPI"}),
        make_signal(source="manifold", market_id="B", signal_type="community", confidence=0.2,
                    direction="bullish", payload={"question": "Will CPI fall?"}),
    ])
    assert len(await store.latest(source="fred")) == 1
    assert len(await store.latest()) == 2


async def test_matching_finds_shared_subject():
    await store.upsert([make_signal(
        source="fred", market_id="CPIAUCSL", signal_type="macro", confidence=0.9,
        direction="bearish", payload={"label": "CPI all urban consumers inflation"},
    )])
    hits = await store.matching("Will CPI inflation come in above 3%?")
    assert hits and hits[0]["source"] == "fred"
    assert "cpi" in hits[0]["matched_on"]


async def test_matching_returns_nothing_for_unrelated_question():
    await store.upsert([make_signal(
        source="fred", market_id="CPIAUCSL", signal_type="macro", confidence=0.9,
        direction="bearish", payload={"label": "CPI all urban consumers"},
    )])
    assert await store.matching("Will the Lakers win tonight?") == []


# ── fusion ──────────────────────────────────────────────────────────────────

async def test_enrich_from_context_attaches_evidence_and_a_catalyst_wins(monkeypatch):
    from backend import signal_fusion

    await store.upsert([
        make_signal(source="fred", market_id="CPIAUCSL", signal_type="macro", confidence=0.9,
                    direction="bearish", payload={"label": "CPI inflation print"}),
        make_signal(source="open-meteo", market_id="new-york", signal_type="weather",
                    confidence=0.75, direction="catalyst", payload={"city": "New York"}),
    ])

    # Make both match the question deterministically.
    async def fake_matching(question, limit=3, scan=400):
        return [
            {"source": "fred", "signal_type": "macro", "market_id": "CPIAUCSL",
             "direction": "bearish", "confidence": 0.9, "match_score": 2,
             "payload": {"label": "CPI inflation print"}},
            {"source": "open-meteo", "signal_type": "weather", "market_id": "new-york",
             "direction": "catalyst", "confidence": 0.75, "match_score": 1,
             "payload": {"city": "New York"}},
        ]

    monkeypatch.setattr(store, "matching", fake_matching)
    out = await signal_fusion.enrich_from_context({"question": "CPI inflation above 3%?"})
    assert len(out["context"]) == 2
    assert out["context_direction"] == "catalyst"
    assert out["context_confidence"] == pytest.approx(0.825, abs=0.01)


async def test_enrich_from_context_leaves_signal_untouched_without_question():
    from backend import signal_fusion

    sig = {"market_id": "X"}
    assert await signal_fusion.enrich_from_context(sig) == sig
