"""execute-live / quick-trade chain-mismatch — real production bug.

The Terminal's wallet picker lists every wallet regardless of chain
(Solana/Ethereum/Sui/Hyperliquid, no filter), but quick_trade used to
hardcode chain="solana" on every order no matter which wallet was actually
selected, and execute_live_order only ever validated order["chain"], never
the wallet's own chain. Result: picking a funded Ethereum/Sui/Hyperliquid
wallet and clicking Buy silently tried to reinterpret that wallet's real
private key as Solana key material — any 32-byte key decodes as *some*
valid-looking ed25519 Solana keypair via Keypair.from_seed, just the wrong
one, holding 0 SOL, completely disconnected from the wallet the user
actually funded. User-reported symptom: "I chose the wallet with funds but
it's not working."
"""
import pytest

from backend.crypto_utils import encrypt_key_for_agent
from backend.db import get_db


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


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
async def test_quick_trade_rejects_ethereum_wallet_instead_of_misreading_its_key(client, fresh_agent):
    """The exact user-reported bug: an Ethereum wallet with real funds is
    selected, quick-trade must reject it clearly instead of silently
    treating its private key as Solana key material."""
    agent, api_key_hash = await _agent_with_id(fresh_agent)
    h = _h(agent)
    agent_for_crypto = {**agent, "api_key": api_key_hash}

    # A real-shaped 32-byte Ethereum private key (also 32 bytes — the same
    # length Keypair.from_seed silently accepts for Solana).
    fake_eth_key_hex = "22" * 32
    encrypted = encrypt_key_for_agent(fake_eth_key_hex, agent_for_crypto)
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO trading_wallets (agent_id, label, chain, address, encrypted_private_key) "
            "VALUES (?,?,?,?,?)",
            (agent["id"], "Base/ETH Main", "ethereum", "0xDeadBeef00000000000000000000000000000000", encrypted),
        )
        wallet_id = cur.lastrowid
        await db.commit()

    r = await client.post(
        "/api/trading/quick-trade",
        headers=h,
        json={"mint": "So11111111111111111111111111111111111111112",
              "side": "buy", "wallet_id": wallet_id, "quantity": 0.5,
              "trigger_reason": "manual_terminal"},
    )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    error_text = str(detail["error"] if isinstance(detail, dict) else detail).lower()
    # quick_trade now correctly tags the order chain="ethereum" (the
    # wallet's real chain, not a hardcoded "solana"), so execute_live_order's
    # own pre-existing order.chain guard fires first and rejects clearly —
    # exactly as intended; the point is it's rejected at all, with a real
    # reason, instead of silently misreading the Ethereum key as Solana.
    assert "solana" in error_text

    # The order itself must record the real mismatch, not "solana" —
    # otherwise the stored order still lies about what was actually tried.
    order_id = detail["order_id"] if isinstance(detail, dict) else None
    if order_id:
        async with get_db() as db:
            row = await (await db.execute(
                "SELECT chain, status FROM trading_orders WHERE id=?", (order_id,)
            )).fetchone()
        assert row[0] == "ethereum"
        assert row[1] == "failed"


@pytest.mark.asyncio
async def test_execute_live_rejects_non_solana_wallet_even_if_order_says_solana(client, fresh_agent):
    """Defense in depth: even if some other caller creates an order that
    (incorrectly) claims chain='solana' while pointing at a non-Solana
    wallet, execute_live_order must reject it at the wallet check, not
    just trust order.chain."""
    agent, api_key_hash = await _agent_with_id(fresh_agent)
    h = _h(agent)
    agent_for_crypto = {**agent, "api_key": api_key_hash}

    fake_sui_key_hex = "33" * 32
    encrypted = encrypt_key_for_agent(fake_sui_key_hex, agent_for_crypto)
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO trading_wallets (agent_id, label, chain, address, encrypted_private_key) "
            "VALUES (?,?,?,?,?)",
            (agent["id"], "Sui Main", "sui", "0xSuiAddr00000000000000000000000000000000000000000000000000000",
             encrypted),
        )
        wallet_id = cur.lastrowid
        await db.commit()

    r = await client.post(
        "/api/trading/orders",
        headers=h,
        json={"symbol": "So11111111111111111111111111111111111111112",
              "side": "buy", "chain": "solana",  # deliberately mislabeled
              "quantity": 0.5, "price": 150.0, "order_type": "market",
              "wallet_id": wallet_id, "trigger_reason": "unit-test"},
    )
    assert r.status_code == 200, r.text
    order_id = r.json()["id"]

    r2 = await client.post(f"/api/trading/orders/{order_id}/execute-live", headers=h)
    assert r2.status_code == 422, r2.text
    assert "sui" in r2.json()["detail"].lower()
