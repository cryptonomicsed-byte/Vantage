"""Trading paper-fill tests — simulated (paper) mode for the Portfolio UI.

Paper-fill resolves a live quote via the direct market sources; when none is
available it falls back to the order's own limit price. These tests force the
fallback path (resolve_price → None) so they are deterministic and offline-safe.
"""
import pytest

from backend import market_sources as ms


@pytest.fixture(autouse=True)
def _no_live_quote(monkeypatch):
    """Force the limit-price fallback so paper-fill is deterministic offline."""
    async def _none(symbol):
        return None
    monkeypatch.setattr(ms, "resolve_price", _none)


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


@pytest.mark.asyncio
async def test_paper_fill_marks_order_filled(client, registered_agent):
    # Log a pending order with a limit price (fallback fill price).
    r = await client.post(
        "/api/trading/orders",
        headers=_h(registered_agent),
        json={"symbol": "SOL", "side": "buy", "chain": "solana",
              "quantity": 3, "price": 150.0, "order_type": "limit",
              "trigger_reason": "unit-test"},
    )
    assert r.status_code == 200, r.text
    order_id = r.json()["id"]

    # Paper-fill it.
    r = await client.post(f"/api/trading/orders/{order_id}/paper-fill", headers=_h(registered_agent))
    assert r.status_code == 200, r.text
    filled = r.json()
    assert filled["status"] == "filled"
    assert filled["filled_quantity"] == 3
    assert filled["avg_fill_price"] == 150.0
    assert str(filled["tx_hash"]).startswith("paper:")

    # A simulated journal entry should exist for this order.
    r = await client.get("/api/trading/journal", headers=_h(registered_agent))
    assert r.status_code == 200, r.text
    entries = [e for e in r.json() if e["order_id"] == order_id]
    assert entries, "expected a journal entry for the paper-filled order"
    assert any("simulated" in str(e.get("tags", "")).lower() for e in entries)


@pytest.mark.asyncio
async def test_paper_fill_rejects_non_pending(client, registered_agent):
    r = await client.post(
        "/api/trading/orders",
        headers=_h(registered_agent),
        json={"symbol": "ETH", "side": "buy", "chain": "base",
              "quantity": 1, "price": 3000.0, "order_type": "limit"},
    )
    order_id = r.json()["id"]

    # First fill succeeds.
    r1 = await client.post(f"/api/trading/orders/{order_id}/paper-fill", headers=_h(registered_agent))
    assert r1.status_code == 200, r1.text

    # Second fill: order is no longer pending → 409.
    r2 = await client.post(f"/api/trading/orders/{order_id}/paper-fill", headers=_h(registered_agent))
    assert r2.status_code == 409, r2.text


@pytest.mark.asyncio
async def test_paper_fill_missing_order_404(client, registered_agent):
    r = await client.post("/api/trading/orders/999999/paper-fill", headers=_h(registered_agent))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_paper_fill_requires_agent_key(client):
    r = await client.post("/api/trading/orders/1/paper-fill")
    assert r.status_code == 401


# ── Wallet sync: real balance lookup, not the old no-op timestamp bump ─────────────

@pytest.mark.asyncio
async def test_sync_wallet_populates_real_balance(client, fresh_agent, monkeypatch):
    async def fake_lookup(chain, address):
        assert chain == "bitcoin" and address == "bc1qtest"
        return {"chain": "bitcoin", "address": address, "supported": True, "source": "mempool.space",
                "balance": {"amount": 0.5, "unit": "BTC"}, "tx_count": 3, "transactions": []}

    async def fake_price(symbol):
        assert symbol == "BTC"
        return 60000.0

    monkeypatch.setattr(ms, "address_lookup", fake_lookup)
    monkeypatch.setattr(ms, "resolve_price", fake_price)
    h = _h(await fresh_agent())

    rw = await client.post("/api/trading/wallets", headers=h,
                            json={"label": "btc-1", "chain": "bitcoin", "address": "bc1qtest"})
    assert rw.status_code == 200, rw.text
    wallet_id = rw.json()["id"]

    rs = await client.post(f"/api/trading/wallets/{wallet_id}/sync", headers=h)
    assert rs.status_code == 200, rs.text
    assert rs.json()["status"] == "synced"

    rg = await client.get(f"/api/trading/wallets/{wallet_id}", headers=h)
    assert rg.status_code == 200, rg.text
    balances = rg.json()["balances"]
    assert balances == [{"token": "BTC", "balance": 0.5, "value_usd": 30000.0}]


@pytest.mark.asyncio
async def test_sync_wallet_unsupported_chain_is_a_soft_noop(client, fresh_agent, monkeypatch):
    async def fake_lookup(chain, address):
        return {"chain": chain, "address": address, "supported": False,
                "reason": "unsupported", "transactions": []}
    monkeypatch.setattr(ms, "address_lookup", fake_lookup)
    h = _h(await fresh_agent())

    rw = await client.post("/api/trading/wallets", headers=h,
                            json={"label": "eth-1", "chain": "ethereum", "address": "0xdead"})
    wallet_id = rw.json()["id"]

    rs = await client.post(f"/api/trading/wallets/{wallet_id}/sync", headers=h)
    assert rs.status_code == 200, rs.text
    assert rs.json()["status"] == "chain_unsupported_for_live_sync"

    rg = await client.get(f"/api/trading/wallets/{wallet_id}", headers=h)
    assert rg.json()["balances"] == []


@pytest.mark.asyncio
async def test_sync_wallet_missing_wallet_404(client, registered_agent):
    r = await client.post("/api/trading/wallets/999999/sync", headers=_h(registered_agent))
    assert r.status_code == 404
