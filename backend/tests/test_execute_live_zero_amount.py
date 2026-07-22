"""execute-live's balance-capping zero-amount path — real production bug fix.

Every trading wallet was hitting amount_lamports = min(requested,
max(0, sol_balance - fee_buffer)) = 0 (confirmed live: every wallet on the
VPS currently holds 0 SOL). Two real defects: the error message didn't say
*why* it hit zero, and the order was never marked failed — it stayed
'pending' forever with zero persisted trace. This test proves both fixes:
a diagnostic reason string, and the order row actually getting updated.
"""
import httpx
import pytest

from backend import routers as _routers_pkg  # noqa: F401 — ensures package import works
from backend.crypto_utils import encrypt_key_for_agent
from backend.db import get_db


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


class _FakeHeliusResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient inside execute_live_order — returns a
    zero SOL balance for getBalance, regardless of the request body, so the
    balance-capping path is exercised deterministically and offline."""

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, **kw):
        method = (json or {}).get("method", "")
        if method == "getBalance":
            return _FakeHeliusResponse({"result": {"value": 0}})
        return _FakeHeliusResponse({"result": {"value": []}})


@pytest.fixture(autouse=True)
def _fake_helius(monkeypatch):
    from backend.routers import trading as trading_mod
    monkeypatch.setattr(trading_mod.httpx, "AsyncClient", _FakeAsyncClient)


@pytest.mark.asyncio
async def test_execute_live_buy_marks_order_failed_with_diagnostic_reason(client, fresh_agent):
    agent = await fresh_agent()
    h = _h(agent)

    import hashlib
    api_key_hash = hashlib.sha256(agent["api_key"].encode()).hexdigest()
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT id FROM agents WHERE api_key=?", (api_key_hash,)
        )).fetchone()
        agent["id"] = row[0]

    # crypto_utils derives the encryption key from agents.api_key, which is
    # the SHA-256 *hash* (see identity.py's /register — the column never
    # holds the raw key), and get_agent()'s dependency returns that same
    # hash as agent["api_key"]. encrypt_key_for_agent/decrypt_key_for_agent
    # must both see the hash, not the raw X-Agent-Key header value, or
    # decryption fails with "wrong agent for this wallet" even for the
    # correct agent.
    agent_for_crypto = {**agent, "api_key": api_key_hash}

    # A real-shaped 32-byte hex seed, not a live key — this test never
    # reaches real signing/broadcast, it's rejected by balance-capping first.
    fake_seed_hex = "11" * 32
    encrypted = encrypt_key_for_agent(fake_seed_hex, agent_for_crypto)
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO trading_wallets (agent_id, label, chain, address, encrypted_private_key) "
            "VALUES (?,?,?,?,?)",
            (agent["id"], "zero-balance-test", "solana", "TestWalletAddr111111111111111111111111111", encrypted),
        )
        wallet_id = cur.lastrowid
        await db.commit()

    r = await client.post(
        "/api/trading/orders",
        headers=h,
        json={"symbol": "So11111111111111111111111111111111111111112",
              "side": "buy", "chain": "solana", "quantity": 1.5,
              "price": 150.0, "order_type": "market", "trigger_reason": "unit-test",
              "wallet_id": wallet_id},
    )
    assert r.status_code == 200, r.text
    order_id = r.json()["id"]

    r2 = await client.post(f"/api/trading/orders/{order_id}/execute-live", headers=h)
    assert r2.status_code == 422, r2.text
    detail = r2.json()["detail"]
    assert "fee buffer" in detail  # diagnostic reason, not the old generic message
    assert "0.000000 SOL" in detail

    # The real fix: the order must not be left stuck in 'pending' limbo.
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT status, error FROM trading_orders WHERE id=?", (order_id,)
        )).fetchone()
    assert row[0] == "failed"
    assert "fee buffer" in row[1]
