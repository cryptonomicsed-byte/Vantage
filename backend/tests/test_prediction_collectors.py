"""Collector parsing rules, pinned.

Both collectors used to mint a fresh uuid per row and insert with INSERT OR
IGNORE, which never matched anything: the same handful of markets was re-inserted
every poll (92,000 polymarket rows across 947 markets, 89,700 of them already
past their end date). These tests pin the three fixes -- open-only filtering,
upsert identity, and probability scaling -- without touching the network.
"""
from datetime import datetime, timedelta, timezone

import pytest

from daemons.polymarket_collector_daemon import is_open, parse_market
from daemons.kalshi_collector_daemon import _prob

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
FUTURE = (NOW + timedelta(days=10)).isoformat().replace("+00:00", "Z")
PAST = (NOW - timedelta(days=10)).isoformat().replace("+00:00", "Z")


def _market(**over):
    base = {
        "conditionId": "0xabc",
        "question": "Will it rain in NYC on Friday?",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.62", "0.38"]',
        "volume24hr": "1234.5",
        "endDate": FUTURE,
        "closed": False,
        "archived": False,
        "active": True,
        "slug": "will-it-rain-nyc",
    }
    base.update(over)
    return base


# ── open filtering ──────────────────────────────────────────────────────────

def test_future_open_market_is_open():
    assert is_open(_market(), NOW) is True


def test_closed_market_is_not_open():
    assert is_open(_market(closed=True), NOW) is False


def test_archived_market_is_not_open():
    assert is_open(_market(archived=True), NOW) is False


def test_past_end_date_is_not_open():
    """The bug in one line: 89,700 rows with an end_date already gone."""
    assert is_open(_market(endDate=PAST), NOW) is False


def test_missing_end_date_is_not_open():
    assert is_open(_market(endDate=None), NOW) is False


def test_inactive_market_is_not_open():
    assert is_open(_market(active=False), NOW) is False


# ── parsing ─────────────────────────────────────────────────────────────────

def test_parse_maps_outcomes_to_prices_by_name_not_position():
    """gamma can order outcomes either way; position-based reading inverted it."""
    m = _market(outcomes='["No", "Yes"]', outcomePrices='["0.38", "0.62"]')
    parsed = parse_market(m)
    assert parsed["yes_price"] == pytest.approx(0.62)
    assert parsed["no_price"] == pytest.approx(0.38)


def test_parse_keeps_market_id_and_question():
    parsed = parse_market(_market())
    assert parsed["market_id"] == "0xabc"
    assert parsed["question"].startswith("Will it rain")
    assert parsed["slug"] == "will-it-rain-nyc"


def test_parse_drops_rows_without_id_or_question():
    assert parse_market(_market(conditionId=None, id=None)) is None
    assert parse_market(_market(question="")) is None


def test_parse_tolerates_list_payloads():
    m = _market(outcomes=["Yes", "No"], outcomePrices=[0.7, 0.3])
    parsed = parse_market(m)
    assert parsed["yes_price"] == pytest.approx(0.7)


# ── kalshi price scaling ────────────────────────────────────────────────────

def test_kalshi_cents_become_probabilities():
    assert _prob(62) == pytest.approx(0.62)
    assert _prob(0) == pytest.approx(0.0)


def test_kalshi_dollar_fields_pass_through():
    assert _prob(0.62) == pytest.approx(0.62)
    assert _prob("0.05") == pytest.approx(0.05)


def test_kalshi_missing_price_is_none_not_zero():
    """0.0 renders as a 0% market; None renders as '—'. Not the same claim."""
    assert _prob(None) is None
    assert _prob("") is None
    assert _prob("not-a-number") is None
