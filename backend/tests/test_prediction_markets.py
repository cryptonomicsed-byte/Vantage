"""Settlement parsing for prediction_markets: the venue vocabularies, pinned.

These are pure-function tests plus one HTTP-shape test with `_get_json`
monkeypatched, so nothing here touches the network. The cases that matter are
the two that were wrong in production:

  - gamma's condition_ids filter needs `closed=true`; without it a settled
    market answers [] and every Polymarket prediction stays unscored;
  - Kalshi's `closed` means trading ended, not settled. Reading it as settled
    would report an open question as decided.
"""
import pytest

from backend import prediction_markets as pm


# ── Kalshi ───────────────────────────────────────────────────────────────────

def test_kalshi_finalized_yes():
    assert pm.kalshi_resolution({"market": {"status": "finalized", "result": "yes"}}) == "yes"


def test_kalshi_finalized_no():
    assert pm.kalshi_resolution({"market": {"status": "finalized", "result": "no"}}) == "no"


def test_kalshi_finalized_without_result_is_unresolved():
    assert pm.kalshi_resolution({"market": {"status": "finalized", "result": ""}}) == "unresolved"


def test_kalshi_closed_is_not_settled():
    """`closed` on Kalshi means trading ended — the question is still open."""
    assert pm.kalshi_resolution({"market": {"status": "closed", "result": ""}}) == "unresolved"
    assert pm.kalshi_resolution({"market": {"status": "closed", "result": "yes"}}) == "unresolved"


def test_kalshi_void():
    assert pm.kalshi_resolution({"market": {"status": "cancelled", "result": ""}}) == "void"


def test_kalshi_unknown_status_is_none():
    assert pm.kalshi_resolution({"market": {"status": "wat", "result": "yes"}}) is None


def test_kalshi_accepts_bare_market_dict():
    assert pm.kalshi_resolution({"status": "settled", "result": "no"}) == "no"


def test_kalshi_rejects_non_dict():
    assert pm.kalshi_resolution(None) is None
    assert pm.kalshi_resolution([{"status": "finalized"}]) is None


# ── Polymarket ───────────────────────────────────────────────────────────────

def test_polymarket_closed_at_one_is_yes():
    m = {"closed": True, "outcomePrices": '["1", "0"]'}
    assert pm.polymarket_resolution(m) == "yes"


def test_polymarket_closed_at_zero_is_no():
    m = {"closed": True, "outcomePrices": '["0", "1"]'}
    assert pm.polymarket_resolution(m) == "no"


def test_polymarket_closed_at_half_is_unresolved():
    m = {"closed": True, "outcomePrices": '["0.5", "0.5"]'}
    assert pm.polymarket_resolution(m) == "unresolved"


def test_polymarket_open_is_unresolved():
    assert pm.polymarket_resolution({"closed": False, "outcomePrices": '["0.6", "0.4"]'}) == "unresolved"


def test_polymarket_cancelled_is_void():
    assert pm.polymarket_resolution({"umaResolutionStatus": "cancelled", "closed": True}) == "void"


# ── resolution() URL and empty-reply shape ───────────────────────────────────

async def test_polymarket_url_asks_for_closed_markets(monkeypatch):
    seen = {}

    async def fake_get(url):
        seen["url"] = url
        return [{"closed": True, "outcomePrices": '["1", "0"]'}]

    monkeypatch.setattr(pm, "_get_json", fake_get)
    assert await pm.resolution("polymarket", "0xabc") == "yes"
    assert "closed=true" in seen["url"], "gamma returns [] without this filter"


async def test_polymarket_empty_list_is_unresolved_not_unknown(monkeypatch):
    async def fake_get(url):
        return []

    monkeypatch.setattr(pm, "_get_json", fake_get)
    assert await pm.resolution("polymarket", "0xabc") == "unresolved"


async def test_venue_unreachable_is_none(monkeypatch):
    async def fake_get(url):
        return None

    monkeypatch.setattr(pm, "_get_json", fake_get)
    assert await pm.resolution("polymarket", "0xabc") is None


async def test_kalshi_route_reaches_the_kalshi_reader(monkeypatch):
    async def fake_get(url):
        assert "kalshi" in url
        return {"market": {"status": "finalized", "result": "no"}}

    monkeypatch.setattr(pm, "_get_json", fake_get)
    assert await pm.resolution("kalshi", "KXTEST-1") == "no"
