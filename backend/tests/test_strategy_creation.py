"""The two things the Execution panel cannot work without: a wallet, and a
strategy bound to it.

Both were broken before this file existed, and neither break was theoretical:

  * POST /trading/wallets/generate (system="bip39", the UI default) POSTed to a
    wallet service on 127.0.0.1:8778 that is no longer running, so it answered
    500 "Wallet generation failed" for every caller;
  * POST /trading/strategies took wallet_id from the body but StrategyCreate
    never declared it — AttributeError, 500, for every caller.

The tests also pin the ownership check: a strategy may only bind a wallet the
calling agent owns.
"""
import pytest

from backend import market_sources as ms


@pytest.fixture(autouse=True)
def _fixed_quote(monkeypatch):
    async def _price(symbol):
        return 100.0
    monkeypatch.setattr(ms, "resolve_price", _price)


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


@pytest.mark.asyncio
async def test_generate_wallet_works_in_process(client, registered_agent):
    r = await client.post("/api/trading/wallets/generate",
                          headers=_h(registered_agent),
                          json={"system": "bip39", "chain": "solana",
                                "label": "unit-test-wallet"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["id"]
    assert d["address"], "no address returned"
    assert d["chain"] == "solana"
    # The private key must never be echoed back.
    assert "private_key" not in d and "privateKey" not in d


@pytest.mark.asyncio
async def test_strategy_requires_wallet_id(client, registered_agent):
    r = await client.post("/api/trading/strategies", headers=_h(registered_agent),
                          json={"name": "no-wallet", "strategy_type": "bighit_40_800"})
    assert r.status_code == 422, r.text
    assert "wallet_id" in r.text


@pytest.mark.asyncio
async def test_strategy_binds_to_owned_wallet(client, registered_agent):
    w = await client.post("/api/trading/wallets/generate", headers=_h(registered_agent),
                          json={"system": "bip39", "chain": "solana", "label": "bind-test"})
    assert w.status_code == 200, w.text
    wid = w.json()["id"]

    r = await client.post("/api/trading/strategies", headers=_h(registered_agent),
                          json={"name": "bound", "strategy_type": "bighit_40_800",
                                "wallet_id": wid, "max_position_size_usd": 25,
                                "target_chain": "solana"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["id"]
    # created unarmed: creating a strategy must never start trading
    assert d.get("armed") in (0, False, None)

    listing = await client.get("/api/trading/strategies", headers=_h(registered_agent))
    rows = listing.json()
    rows = rows if isinstance(rows, list) else rows.get("strategies", [])
    row = next(s for s in rows if s["id"] == d["id"])
    assert row["wallet_id"] == wid
    assert not row.get("armed")


@pytest.mark.asyncio
async def test_strategy_cannot_bind_someone_elses_wallet(client, registered_agent, fresh_agent):
    other = await fresh_agent()
    w = await client.post("/api/trading/wallets/generate", headers=_h(other),
                          json={"system": "bip39", "chain": "solana", "label": "theirs"})
    assert w.status_code == 200, w.text
    wid = w.json()["id"]

    r = await client.post("/api/trading/strategies", headers=_h(registered_agent),
                          json={"name": "hijack", "strategy_type": "bighit_40_800",
                                "wallet_id": wid})
    assert r.status_code == 422, r.text
    assert "belonging to this agent" in r.text
