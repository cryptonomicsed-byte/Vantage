"""Signal-ingestion hardening.

conviction is a 0-1 confidence and >0.7 auto-creates a real order, but it was
read with a bare float() and no range check. Several daemons in this repo use
0-5, 0-7 or 0-8 scales, so a scale mismatch didn't read as a bug — it read as
maximum confidence on every signal. These cover the contract being enforced at
the edge instead.
"""
import os

import pytest

from backend.routers.trading import _validated_conviction, _validated_quantity
from fastapi import HTTPException


def _tool_headers():
    return {
        "X-Vantage-Tool": "trading",
        "X-Vantage-Tool-Key": os.environ.get("VANTAGE_TOOL_TRADING", ""),
    }


# ── conviction ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [0, 0.0, 0.5, 0.7, 0.71, 1, 1.0, "0.42"])
def test_in_range_conviction_is_accepted(value):
    assert 0.0 <= _validated_conviction({"conviction": value}) <= 1.0


@pytest.mark.parametrize("value", [1.01, 3.0, 5.0, 7.0, 8.0, 10, 100])
def test_unnormalised_scales_are_rejected(value):
    """0-5/0-7/0-8 scales all clear the 0.7 auto-execute threshold trivially."""
    with pytest.raises(HTTPException) as exc:
        _validated_conviction({"conviction": value})
    assert exc.value.status_code == 422
    assert "between 0 and 1" in str(exc.value.detail)


def test_negative_conviction_is_rejected():
    with pytest.raises(HTTPException) as exc:
        _validated_conviction({"conviction": -0.5})
    assert exc.value.status_code == 422


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_conviction_is_rejected(value):
    """NaN silently fails every comparison, so it would slip past a naive
    range check and land in the order's trigger_reason."""
    with pytest.raises(HTTPException) as exc:
        _validated_conviction({"conviction": value})
    assert exc.value.status_code == 422


@pytest.mark.parametrize("value", ["high", None, {}, []])
def test_non_numeric_conviction_is_rejected(value):
    with pytest.raises(HTTPException) as exc:
        _validated_conviction({"conviction": value})
    assert exc.value.status_code == 422


def test_confidence_is_accepted_as_an_alias():
    assert _validated_conviction({"confidence": 0.8}) == 0.8


def test_missing_conviction_defaults_to_zero_not_execution():
    assert _validated_conviction({}) == 0.0


# ── quantity ─────────────────────────────────────────────────────────────────

def test_quantity_defaults_when_absent():
    assert _validated_quantity({}) == 0.1


@pytest.mark.parametrize("value", [0, -1, -0.001])
def test_non_positive_quantity_is_rejected(value):
    """A negative size would reach risk enforcement and the order row as a
    real quantity."""
    with pytest.raises(HTTPException) as exc:
        _validated_quantity({"quantity": value})
    assert exc.value.status_code == 422


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_non_finite_quantity_is_rejected(value):
    with pytest.raises(HTTPException) as exc:
        _validated_quantity({"quantity": value})
    assert exc.value.status_code == 422


def test_valid_quantity_passes():
    assert _validated_quantity({"quantity": 2.5}) == 2.5


# ── endpoint level ───────────────────────────────────────────────────────────

async def test_ingest_rejects_an_unnormalised_signal(client):
    """End to end: the shape several daemons currently send is refused rather
    than becoming an order."""
    r = await client.post(
        "/api/trading/signals/ingest",
        headers=_tool_headers(),
        json={"symbol": "SOL", "direction": "BUY", "conviction": 7.0,
              "source": "freqtrade", "agent_id": 1},
    )
    # 401 when no tool key is configured in this environment; 422 when it is.
    # Either way it is not a 200 that quietly created an order.
    assert r.status_code in (401, 422)
    if r.status_code == 422:
        assert "between 0 and 1" in r.text


# ── voice exposure ───────────────────────────────────────────────────────────

def test_trading_tools_need_the_destructive_opt_in(app):
    """Placing an order is a POST, so a method-only gate would have let a
    spoken sentence trade while stopping a harmless DELETE."""
    from backend import voice_tools
    # Keyed by (method, path): GET and POST share /api/trading/orders, and it
    # is only the POST that places anything.
    tools = {(t["method"], t["path"]): t for t in voice_tools.select_tools(app, ["tag:trading"])}

    checked = 0
    for method, path in (("POST", "/api/trading/orders"), ("POST", "/api/trading/wallets"),
                         ("POST", "/api/trading/wallets/generate")):
        tool = tools.get((method, path))
        if tool is None:
            continue
        checked += 1
        assert voice_tools.is_destructive(tool), f"{method} {path} should require the destructive opt-in"
    assert checked, "expected at least one mutating trading route in the catalog"


def test_reading_trading_state_is_not_gated(app):
    from backend import voice_tools
    for tool in voice_tools.select_tools(app, ["tag:trading"]):
        if tool["method"] == "GET":
            assert not voice_tools.is_destructive(tool), tool["path"]
