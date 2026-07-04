"""Wallet watchlist tests — persisted tracked-wallet CRUD + bounded-concurrency
refresh. Network (address_lookup) is always mocked so these are deterministic
and offline-safe.
"""
import pytest

from backend import market_sources as ms


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


@pytest.mark.asyncio
async def test_add_watchlist_wallet_requires_auth(client):
    r = await client.post("/api/intel/watchlist", json={"chain": "solana", "address": "Addr1"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_add_watchlist_wallet_rejects_bad_chain(client, registered_agent):
    r = await client.post(
        "/api/intel/watchlist",
        headers=_h(registered_agent),
        json={"chain": "ethereum", "address": "0xdead"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_add_and_list_watchlist_wallet(client, fresh_agent):
    agent = await fresh_agent()
    r = await client.post(
        "/api/intel/watchlist",
        headers=_h(agent),
        json={"chain": "solana", "address": "SolWatch1111111111111111111111111111111111", "label": "test whale"},
    )
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["chain"] == "solana"
    assert row["label"] == "test whale"

    r = await client.get("/api/intel/watchlist", headers=_h(agent))
    assert r.status_code == 200, r.text
    data = r.json()
    assert any(w["address"] == "SolWatch1111111111111111111111111111111111" for w in data["wallets"])


@pytest.mark.asyncio
async def test_add_watchlist_wallet_dedupes_and_updates_label(client, fresh_agent):
    agent = await fresh_agent()
    address = "DedupeWallet222222222222222222222222222222"
    r1 = await client.post(
        "/api/intel/watchlist", headers=_h(agent),
        json={"chain": "solana", "address": address, "label": "first label"},
    )
    assert r1.status_code == 200
    r2 = await client.post(
        "/api/intel/watchlist", headers=_h(agent),
        json={"chain": "solana", "address": address, "label": "updated label"},
    )
    assert r2.status_code == 200
    assert r2.json()["label"] == "updated label"
    assert r2.json()["id"] == r1.json()["id"]

    r = await client.get("/api/intel/watchlist", headers=_h(agent))
    matches = [w for w in r.json()["wallets"] if w["address"] == address]
    assert len(matches) == 1


@pytest.mark.asyncio
async def test_delete_watchlist_wallet(client, fresh_agent):
    agent = await fresh_agent()
    add = await client.post(
        "/api/intel/watchlist", headers=_h(agent),
        json={"chain": "bitcoin", "address": "bc1qDeleteMe"},
    )
    wallet_id = add.json()["id"]

    r = await client.delete(f"/api/intel/watchlist/{wallet_id}", headers=_h(agent))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "deleted"

    r = await client.get("/api/intel/watchlist", headers=_h(agent))
    assert not any(w["id"] == wallet_id for w in r.json()["wallets"])


@pytest.mark.asyncio
async def test_delete_watchlist_wallet_404(client, registered_agent):
    r = await client.delete("/api/intel/watchlist/999999999", headers=_h(registered_agent))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_watchlist_refresh_flags_whale_activity(client, fresh_agent, monkeypatch):
    agent = await fresh_agent()
    await client.post(
        "/api/intel/watchlist", headers=_h(agent),
        json={"chain": "solana", "address": "WhaleWallet333333333333333333333333333333", "label": "whale"},
    )
    await client.post(
        "/api/intel/watchlist", headers=_h(agent),
        json={"chain": "solana", "address": "QuietWallet4444444444444444444444444444444", "label": "quiet"},
    )

    async def fake_address_lookup(chain, address):
        if address == "WhaleWallet333333333333333333333333333333":
            amount = 750.0  # above the 500 SOL watchlist whale threshold
        else:
            amount = 2.0
        return {
            "chain": "solana", "address": address, "supported": True, "source": "solana-rpc",
            "balance": {"amount": 1000.0, "unit": "SOL"},
            "tx_count": 1,
            "transactions": [{"txid": "sig1", "direction": "in", "amount": amount, "counterparties": []}],
        }

    monkeypatch.setattr(ms, "address_lookup", fake_address_lookup)

    r = await client.get("/api/intel/watchlist/refresh", headers=_h(agent))
    assert r.status_code == 200, r.text
    data = r.json()
    by_address = {w["address"]: w for w in data["wallets"]}
    assert by_address["WhaleWallet333333333333333333333333333333"]["whale_activity"] is True
    assert by_address["QuietWallet4444444444444444444444444444444"]["whale_activity"] is False


@pytest.mark.asyncio
async def test_refresh_watchlist_returns_empty_for_empty_input():
    # Unit-level check on the market_sources helper directly (the watchlist
    # table is shared across the whole test session, so an HTTP-level empty
    # assertion would be order-dependent on other tests' inserted rows).
    result = await ms.refresh_watchlist([])
    assert result == []
