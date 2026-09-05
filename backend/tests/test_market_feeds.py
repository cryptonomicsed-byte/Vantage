"""Hyperliquid derivations and prediction-market settlement parsing.

Neither module is allowed to reach the network here; every upstream reply is a
fixture. That is not only test hygiene — the environment these were written in
blocks both venues, so a fixture is the *only* thing either parser has ever
seen. The shapes come from published documentation, and these tests pin our
reading of them so a live probe later shows up as a failing test rather than a
silently wrong signal.

The cases that get the most attention are the ones where returning a plausible
value would be worse than returning nothing:

  * a position with no liquidation price is not "maximally close" to one;
  * a market closed at 0.5 has not settled on an outcome;
  * a settlement word we cannot map must not be passed through to a falsifier.
"""
import pytest

from backend import hyperliquid as hl
from backend import prediction_markets as pm


# ── Hyperliquid: liquidation distance ────────────────────────────────────────

def test_liquidation_distance_is_a_fraction_of_mark():
    d = hl.liquidation_distance({"liquidationPx": "95.0"}, mark=100.0)
    assert d == pytest.approx(0.05)


def test_liquidation_distance_is_none_without_a_liquidation_price():
    """An unlevered position cannot be liquidated. Reporting 0.0 would rank it
    as maximally urgent, which is exactly backwards."""
    assert hl.liquidation_distance({}, mark=100.0) is None
    assert hl.liquidation_distance({"liquidationPx": None}, mark=100.0) is None


def test_liquidation_distance_is_none_without_a_mark():
    assert hl.liquidation_distance({"liquidationPx": "95.0"}, mark=0.0) is None


def test_liquidation_distance_is_side_agnostic():
    """A short liquidating above and a long liquidating below are equally close."""
    below = hl.liquidation_distance({"liquidationPx": "95.0"}, mark=100.0)
    above = hl.liquidation_distance({"liquidationPx": "105.0"}, mark=100.0)
    assert below == above == pytest.approx(0.05)


# ── Hyperliquid: cascade direction ───────────────────────────────────────────

def _p(side, distance):
    return {"side": side, "distance": distance, "coin": "BTC", "value_usd": 1e6}


def test_cascade_points_down_when_longs_are_nearest():
    """Longs liquidating sell into the market."""
    assert hl.cascade_direction([_p("long", 0.001), _p("short", 0.04)]) == "down"


def test_cascade_points_up_when_shorts_are_nearest():
    assert hl.cascade_direction([_p("short", 0.001), _p("long", 0.04)]) == "up"


def test_cascade_is_none_when_both_sides_are_equally_close():
    """Fuel on both sides is not a direction. Naming one would be a guess
    dressed as a fact."""
    assert hl.cascade_direction([_p("long", 0.010), _p("short", 0.0105)]) is None


def test_cascade_is_none_with_nothing_near():
    assert hl.cascade_direction([]) is None


def test_cascade_handles_a_single_side():
    assert hl.cascade_direction([_p("long", 0.01)]) == "down"
    assert hl.cascade_direction([_p("short", 0.01)]) == "up"


# ── Hyperliquid: funding ─────────────────────────────────────────────────────

def test_normal_funding_is_not_a_signal():
    """Emitting 'funding is normal' as a signal would drown the pool."""
    assert hl.funding_signal(0.0001) is None
    assert hl.funding_signal(0.0) is None
    assert hl.funding_signal(None) is None


def test_positive_funding_marks_longs_crowded():
    s = hl.funding_signal(0.002)
    assert s["crowded_side"] == "long"
    assert s["direction"] == "down"


def test_negative_funding_marks_shorts_crowded():
    s = hl.funding_signal(-0.002)
    assert s["crowded_side"] == "short"
    assert s["direction"] == "up"


def test_funding_conviction_saturates():
    """One outlier must not dominate the aggregate."""
    assert hl.funding_signal(0.002)["conviction"] == pytest.approx(1.0)
    assert hl.funding_signal(50.0)["conviction"] == 1.0


# ── Hyperliquid: funding_and_oi parsing ──────────────────────────────────────

@pytest.mark.asyncio
async def test_funding_and_oi_zips_universe_to_contexts(monkeypatch):
    async def fake_info(body):
        return [
            {"universe": [{"name": "BTC"}, {"name": "ETH"}]},
            [
                {"funding": "0.0001", "openInterest": "1200.5", "markPx": "64000.0"},
                {"funding": "-0.0002", "openInterest": "900.0", "markPx": "3100.0"},
            ],
        ]
    monkeypatch.setattr(hl, "_info", fake_info)

    out = await hl.funding_and_oi()
    assert out["BTC"]["mark"] == 64000.0
    assert out["ETH"]["funding"] == -0.0002


@pytest.mark.asyncio
async def test_funding_and_oi_is_empty_on_an_unexpected_shape(monkeypatch):
    """A venue changing its payload must yield no signal, not a wrong one."""
    for bad in (None, {}, [], [{"universe": []}]):
        async def fake_info(body, _b=bad):
            return _b
        monkeypatch.setattr(hl, "_info", fake_info)
        assert await hl.funding_and_oi() == {}


# ── Limitless settlement ─────────────────────────────────────────────────────

def test_limitless_yes_and_no():
    assert pm.limitless_resolution({"status": "resolved", "winningOutcomeIndex": 0}) == "yes"
    assert pm.limitless_resolution({"status": "resolved", "winningOutcomeIndex": 1}) == "no"


def test_limitless_open_market_is_unresolved():
    assert pm.limitless_resolution({"status": "open"}) == "unresolved"


def test_limitless_cancelled_is_void():
    assert pm.limitless_resolution({"status": "cancelled"}) == "void"


def test_limitless_resolved_without_a_winner_is_unresolved():
    """Settled-but-not-propagated is still no answer, not a coin flip."""
    assert pm.limitless_resolution({"status": "resolved"}) == "unresolved"


def test_limitless_unknown_status_returns_none():
    """The most important case: an unmapped word must never be passed through
    to the falsifier, which would read it as a mismatch and refute the claim."""
    assert pm.limitless_resolution({"status": "quantum-superposition"}) is None
    assert pm.limitless_resolution({}) is None
    assert pm.limitless_resolution(None) is None


# ── Polymarket settlement ────────────────────────────────────────────────────

def test_polymarket_yes_and_no_from_outcome_prices():
    assert pm.polymarket_resolution({"closed": True, "outcomePrices": ["1", "0"]}) == "yes"
    assert pm.polymarket_resolution({"closed": True, "outcomePrices": ["0", "1"]}) == "no"


def test_polymarket_accepts_json_encoded_prices():
    """Gamma returns this list encoded inside a string more often than not."""
    assert pm.polymarket_resolution({"closed": True, "outcomePrices": '["1", "0"]'}) == "yes"


def test_polymarket_open_market_is_unresolved():
    assert pm.polymarket_resolution({"closed": False}) == "unresolved"


def test_polymarket_cancelled_is_void():
    assert pm.polymarket_resolution({"umaResolutionStatus": "cancelled"}) == "void"


def test_polymarket_ambiguous_close_is_not_an_outcome():
    """A market closed at 0.5 has not settled. Reporting the nearer side would
    invent a settlement the venue never made."""
    assert pm.polymarket_resolution({"closed": True, "outcomePrices": ["0.5", "0.5"]}) == "unresolved"
    assert pm.polymarket_resolution({"closed": True, "outcomePrices": ["0.7", "0.3"]}) == "unresolved"


def test_polymarket_malformed_prices_return_none():
    assert pm.polymarket_resolution({"closed": True, "outcomePrices": "not json"}) is None
    assert pm.polymarket_resolution({"closed": True, "outcomePrices": ["x", "y"]}) is None
    assert pm.polymarket_resolution({"closed": True, "outcomePrices": ["1"]}) is None


# ── The observation handed to a probe ────────────────────────────────────────

@pytest.mark.asyncio
async def test_observation_is_omitted_when_resolution_is_unknown(monkeypatch):
    """The contract with the falsifier: an ungathered observation makes it
    abstain. Substituting a guess would convert our ignorance into a verdict."""
    async def none(venue, market_id):
        return None
    monkeypatch.setattr(pm, "resolution", none)
    assert await pm.observation_for("limitless", "m1") == {}


@pytest.mark.asyncio
async def test_observation_carries_the_settlement_when_known(monkeypatch):
    async def yes(venue, market_id):
        return "yes"
    monkeypatch.setattr(pm, "resolution", yes)
    assert await pm.observation_for("limitless", "m1") == {"market:resolution": "yes"}


@pytest.mark.asyncio
async def test_unknown_venue_gathers_nothing(monkeypatch):
    assert await pm.resolution("not-a-venue", "m1") is None


@pytest.mark.asyncio
async def test_venue_outage_gathers_nothing(monkeypatch):
    async def down(url):
        return None
    monkeypatch.setattr(pm, "_get_json", down)
    assert await pm.resolution("limitless", "m1") is None
