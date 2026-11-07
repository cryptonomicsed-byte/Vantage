"""The global live switch, exercised through the real endpoint.

Unit tests cover the predicate; this covers the wiring: POST /orders/{id}/execute-live
must refuse *before* it decrypts a key or signs anything when VANTAGE_LIVE_TRADING
is unset, and must get past that gate when it is set.

The second half never signs: the order has no wallet, so with the gate open the
request fails at "No wallet/private key available". That difference in failure is
the proof the mode gate flipped — no signature was attempted in either case.
"""
import pytest

from backend import market_sources as ms
from backend import trading_mode as tm


@pytest.fixture(autouse=True)
def _fixed_quote(monkeypatch):
    """Deterministic reference price: the order endpoint rejects a limit price
    more than 50% away from the live quote, and a real quote would make this
    test depend on the network and on SOL's price today."""
    async def _price(symbol):
        return 100.0
    monkeypatch.setattr(ms, "resolve_price", _price)


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


async def _pending_order(client, agent) -> int:
    r = await client.post(
        "/api/trading/orders",
        headers=_h(agent),
        json={"symbol": "SOL", "side": "buy", "chain": "solana",
              "quantity": 0.1, "price": 100.0, "order_type": "limit",
              "trigger_reason": "live-gate-test"},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_execute_live_refused_when_global_switch_off(client, registered_agent, monkeypatch):
    monkeypatch.delenv(tm.GLOBAL_LIVE_ENV, raising=False)
    order_id = await _pending_order(client, registered_agent)

    r = await client.post(f"/api/trading/orders/{order_id}/execute-live",
                          headers=_h(registered_agent))

    assert r.status_code == 422, r.text
    body = r.text
    assert tm.GLOBAL_LIVE_ENV in body, f"refusal does not name the switch: {body}"
    assert "Paper trading is unaffected" in body

    # The order must still be pending: a process-level mode is not an order defect.
    listing = await client.get("/api/trading/orders", headers=_h(registered_agent))
    payload = listing.json()
    orders = payload["orders"] if isinstance(payload, dict) else payload
    row = next(o for o in orders if o["id"] == order_id)
    assert row["status"] == "pending", row


@pytest.mark.asyncio
async def test_execute_live_gets_past_the_switch_when_enabled(client, registered_agent, monkeypatch):
    monkeypatch.setenv(tm.GLOBAL_LIVE_ENV, "1")
    order_id = await _pending_order(client, registered_agent)

    r = await client.post(f"/api/trading/orders/{order_id}/execute-live",
                          headers=_h(registered_agent))

    # Different failure: past the mode gate, stopped by the missing key. If the
    # switch were still blocking, this would be the 422 naming VANTAGE_LIVE_TRADING.
    assert tm.GLOBAL_LIVE_ENV not in r.text, (
        f"global switch still blocking with it enabled: {r.text}")
    assert "wallet" in r.text.lower() or "private key" in r.text.lower(), r.text
