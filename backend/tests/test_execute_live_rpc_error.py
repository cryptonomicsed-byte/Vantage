"""execute-live's balance check silently treated an RPC failure as a real
zero balance — the actual root cause behind a live user report.

(bal_r.json().get("result") or {}).get("value") or 0 cannot distinguish
"Helius returned {value: 0}" from "Helius returned {error: rate limited}"
— both produce 0. Confirmed happening live with the production API key
during this session's own diagnostics ("max usage reached"), and the user
hit exactly this: a wallet holding real SOL (0.1209 SOL, verified via the
public Solana RPC) was rejected with "wallet balance 0.000000 SOL" by
Vantage's own balance check, which was actually just relaying a Helius
rate-limit error as if it meant "empty wallet."
"""
import pytest

from backend.crypto_utils import encrypt_key_for_agent
from backend.db import get_db


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


class _RateLimitedResponse:
    def json(self):
        return {"jsonrpc": "2.0", "error": {"code": -32429, "message": "max usage reached"}}


class _RateLimitedHeliusClient:
    """Every Helius call fails with the exact real-world rate-limit error —
    the balance check must surface this as a 502, never as "0 SOL"."""

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, **kw):
        return _RateLimitedResponse()


@pytest.fixture(autouse=True)
def _rate_limited_helius(monkeypatch):
    from backend.routers import trading as trading_mod
    monkeypatch.setattr(trading_mod.httpx, "AsyncClient", _RateLimitedHeliusClient)


async def _agent_with_id(fresh_agent):
    import hashlib
    agent = await fresh_agent()
    api_key_hash = hashlib.sha256(agent["api_key"].encode()).hexdigest()
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT id FROM agents WHERE api_key=?", (api_key_hash,)
        )).fetchone()
        agent["id"] = row[0]
    return agent, api_key_hash


@pytest.mark.asyncio
async def test_helius_rate_limit_surfaces_as_502_not_fake_zero_balance(client, fresh_agent):
    agent, api_key_hash = await _agent_with_id(fresh_agent)
    h = _h(agent)
    agent_for_crypto = {**agent, "api_key": api_key_hash}

    fake_seed_hex = "44" * 32
    encrypted = encrypt_key_for_agent(fake_seed_hex, agent_for_crypto)
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO trading_wallets (agent_id, label, chain, address, encrypted_private_key) "
            "VALUES (?,?,?,?,?)",
            (agent["id"], "real-funded-wallet", "solana", "SomeRealFundedAddress11111111111111111111", encrypted),
        )
        wallet_id = cur.lastrowid
        await db.commit()

    r = await client.post(
        "/api/trading/orders",
        headers=h,
        json={"symbol": "So11111111111111111111111111111111111111112",
              "side": "buy", "chain": "solana", "quantity": 0.05,
              "price": 150.0, "order_type": "market", "trigger_reason": "unit-test",
              "wallet_id": wallet_id},
    )
    order_id = r.json()["id"]

    r2 = await client.post(f"/api/trading/orders/{order_id}/execute-live", headers=h)
    # Must be a 502 (upstream RPC failure) — never a 422 claiming 0 SOL,
    # which would falsely blame the wallet for Helius being unavailable.
    assert r2.status_code == 502, r2.text
    assert "max usage reached" in r2.json()["detail"]

    # And the order must NOT be marked failed for a balance reason — the
    # RPC error path doesn't touch order status at all, so a genuinely
    # transient rate-limit doesn't permanently poison a real order.
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT status FROM trading_orders WHERE id=?", (order_id,)
        )).fetchone()
    assert row[0] == "pending"
