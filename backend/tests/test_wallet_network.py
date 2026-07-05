"""Wallet money-flow graph tests — real accumulated edges from /trace and
/watchlist/refresh, not a fabricated data source. Network is always mocked
so these are deterministic and offline-safe.
"""
import pytest

from backend import market_sources as ms


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


def _fake_lookup(counterparties_by_call):
    """Returns an address_lookup fake that yields one transaction with the
    given counterparties list each time it's called."""
    async def fake(chain, address):
        return {
            "chain": "solana", "address": address, "supported": True, "source": "solana-rpc",
            "balance": {"amount": 1.0, "unit": "SOL"},
            "tx_count": 1,
            "transactions": [{
                "txid": "sig1", "direction": "out", "amount": 1.0,
                "counterparties": counterparties_by_call,
            }],
        }
    return fake


@pytest.mark.asyncio
async def test_trace_accumulates_wallet_edges(client, monkeypatch, fresh_agent):
    agent = await fresh_agent()
    monkeypatch.setattr(ms, "address_lookup", _fake_lookup(
        [{"address": "CounterpartyEdge1111111111111111111111111", "role": "recipient", "amount": 2.5}],
    ))

    r = await client.get("/api/intel/trace/solana/TraceSourceWallet22222222222222222222222", headers=_h(agent))
    assert r.status_code == 200, r.text

    net = await client.get("/api/intel/wallet-network", params={"chain": "solana", "min_tx_count": 1, "limit": 500}, headers=_h(agent))
    assert net.status_code == 200, net.text
    data = net.json()
    match = [l for l in data["links"] if l["source"] == "TraceSourceWallet22222222222222222222222"
             and l["target"] == "CounterpartyEdge1111111111111111111111111"]
    assert len(match) == 1
    assert match[0]["role"] == "recipient"
    assert match[0]["tx_count"] == 1
    assert match[0]["total_value"] == pytest.approx(2.5)
    assert "TraceSourceWallet22222222222222222222222" in [n["id"] for n in data["nodes"]]


@pytest.mark.asyncio
async def test_repeated_trace_accumulates_same_edge(client, monkeypatch, fresh_agent):
    agent = await fresh_agent()
    monkeypatch.setattr(ms, "address_lookup", _fake_lookup(
        [{"address": "RepeatCounterparty333333333333333333333", "role": "sender", "amount": 1.0}],
    ))

    source = "RepeatSourceWallet4444444444444444444444444"
    await client.get(f"/api/intel/trace/solana/{source}", headers=_h(agent))
    await client.get(f"/api/intel/trace/solana/{source}", headers=_h(agent))

    net = await client.get("/api/intel/wallet-network", params={"chain": "solana", "limit": 500}, headers=_h(agent))
    match = [l for l in net.json()["links"] if l["source"] == source and l["target"] == "RepeatCounterparty333333333333333333333"]
    assert len(match) == 1
    assert match[0]["tx_count"] == 2
    assert match[0]["total_value"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_watchlist_refresh_also_accumulates_edges(client, fresh_agent, monkeypatch):
    agent = await fresh_agent()
    address = "WatchlistEdgeWallet5555555555555555555555"
    await client.post(
        "/api/intel/watchlist", headers=_h(agent),
        json={"chain": "solana", "address": address, "label": "edge test"},
    )

    monkeypatch.setattr(ms, "address_lookup", _fake_lookup(
        [{"address": "WatchlistCounterparty666666666666666666", "role": "recipient", "amount": 9.0}],
    ))

    r = await client.get("/api/intel/watchlist/refresh", headers=_h(agent))
    assert r.status_code == 200, r.text

    net = await client.get("/api/intel/wallet-network", params={"chain": "solana", "limit": 500}, headers=_h(agent))
    match = [l for l in net.json()["links"] if l["source"] == address and l["target"] == "WatchlistCounterparty666666666666666666"]
    assert len(match) == 1
    assert match[0]["total_value"] == pytest.approx(9.0)


@pytest.mark.asyncio
async def test_wallet_network_min_tx_count_filters(client, monkeypatch, fresh_agent):
    agent = await fresh_agent()
    monkeypatch.setattr(ms, "address_lookup", _fake_lookup(
        [{"address": "OneHitWonder7777777777777777777777777777", "role": "recipient", "amount": 1.0}],
    ))
    source = "MinTxCountSource8888888888888888888888888"
    await client.get(f"/api/intel/trace/solana/{source}", headers=_h(agent))

    strict = await client.get("/api/intel/wallet-network", params={"chain": "solana", "min_tx_count": 5, "limit": 500}, headers=_h(agent))
    assert not any(l["source"] == source for l in strict.json()["links"])

    lenient = await client.get("/api/intel/wallet-network", params={"chain": "solana", "min_tx_count": 1, "limit": 500}, headers=_h(agent))
    assert any(l["source"] == source for l in lenient.json()["links"])
