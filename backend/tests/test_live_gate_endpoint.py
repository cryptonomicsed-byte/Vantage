"""The global live switch, exercised through the real endpoint.

Unit tests cover the predicate; this covers the wiring. The switch sits after
order/wallet validation (a malformed order must still say what is wrong with it)
and immediately before the key is decrypted, so:

  * switch off  -> 422 naming VANTAGE_LIVE_TRADING, order left pending, no
                   decryption attempted;
  * switch on   -> the request gets past the switch and stops at the undecryptable
                   dummy key instead.

Neither case signs anything: the wallet below carries a deliberately invalid
ciphertext, so there is nothing to sign with even if the code went looking.
"""
import aiosqlite
import pytest

from backend import market_sources as ms
from backend import trading_mode as tm
from backend.db import DB_PATH


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


async def _wallet_with_bad_key(client, agent) -> int:
    """A wallet row that passes every validation and cannot be decrypted.

    execute-live validates wallet presence and chain before the mode gate, so
    the gate can only be reached with a wallet that looks usable. The ciphertext
    is junk on purpose: nothing here must ever reach a signature.
    """
    me = await client.get("/api/trading/wallets", headers=_h(agent))
    payload = me.json()
    wallets = payload["wallets"] if isinstance(payload, dict) else payload
    for w in wallets:
        if w.get("chain") == "solana":
            return w["id"]

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id FROM agents WHERE name = ?", (agent["name"],))
        row = await cur.fetchone()
        agent_id = row[0]
        cur = await db.execute(
            """INSERT INTO trading_wallets
               (agent_id, label, chain, address, encrypted_private_key)
               VALUES (?,?,?,?,?)""",
            (agent_id, f"gate-test-{agent['name']}", "solana",
             "11111111111111111111111111111111", "not-a-real-ciphertext"),
        )
        await db.commit()
        return cur.lastrowid


async def _pending_order(client, agent, wallet_id) -> int:
    r = await client.post(
        "/api/trading/orders",
        headers=_h(agent),
        json={"symbol": "SOL", "side": "buy", "chain": "solana",
              "wallet_id": wallet_id, "quantity": 0.1, "price": 100.0,
              "order_type": "limit", "trigger_reason": "live-gate-test"},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_execute_live_refused_when_global_switch_off(client, registered_agent, monkeypatch):
    monkeypatch.delenv(tm.GLOBAL_LIVE_ENV, raising=False)
    wallet_id = await _wallet_with_bad_key(client, registered_agent)
    order_id = await _pending_order(client, registered_agent, wallet_id)

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
    monkeypatch.setattr("backend.trading_mode._db_live_flag", lambda: True)  # Layer 0 ceiling + switch
    wallet_id = await _wallet_with_bad_key(client, registered_agent)
    order_id = await _pending_order(client, registered_agent, wallet_id)

    r = await client.post(f"/api/trading/orders/{order_id}/execute-live",
                          headers=_h(registered_agent))

    # Past the mode gate: the failure is now the undecryptable key, not the switch.
    assert tm.GLOBAL_LIVE_ENV not in r.text, (
        f"global switch still blocking with it enabled: {r.text}")
    assert r.status_code >= 400
    assert "decrypt" in r.text.lower() or "signing key" in r.text.lower(), r.text
