"""Regression tests for the receipt outbox and crash-recovery behaviour.

Phase-3 introduced a transactional outbox pattern for execute_live_order:
  1. A 'pending' ARP receipt is written BEFORE the irreversible on-chain call.
  2. After a successful broadcast the receipt is updated to 'success'.
  3. After any failure path the receipt is updated to 'failed'.

These tests verify:
  - _emit_trade_receipt returns a usable receipt_id
  - _update_receipt_outcome transitions outcome correctly
  - A pending row left by a simulated crash is detectable (reconciliation surface)
  - fill attestation requires X-Vantage-Tool-Key (Phase-4 gate)
"""
import json
import time

import aiosqlite
import pytest
import pytest_asyncio

from backend.db import DB_PATH, get_db

pytestmark = pytest.mark.asyncio

TOOL_TRADING_KEY = "test-tool-key-trading-not-a-real-secret"  # set in conftest.py


# ── helpers ───────────────────────────────────────────────────────────────────

async def _get_receipt_row(receipt_id: str) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM arp_receipts WHERE receipt_id = ?", (receipt_id,)
        )).fetchone()
    return dict(row) if row else None


async def _count_pending_receipts() -> int:
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT COUNT(*) FROM arp_receipts WHERE outcome = 'pending'"
        )).fetchone()
    return row[0] if row else 0


# ── _emit_trade_receipt unit tests ────────────────────────────────────────────

async def test_emit_returns_receipt_id(client):
    """_emit_trade_receipt must return a non-empty receipt_id."""
    from backend.routers.trading import _emit_trade_receipt

    order = {
        "id": 99001,
        "side": "buy",
        "quantity": 1.0,
        "price": 100.0,
        "chain": "solana",
        "order_type": "market",
        "symbol": "SOL",
        "strategy_id": None,
    }
    receipt_id = await _emit_trade_receipt(order, agent_id=1, outcome="pending")
    assert receipt_id is not None, "_emit_trade_receipt returned None"
    assert len(receipt_id) > 10, f"receipt_id looks invalid: {receipt_id!r}"


async def test_emit_writes_row_to_db(client):
    from backend.routers.trading import _emit_trade_receipt

    order = {
        "id": 99002, "side": "sell", "quantity": 0.5, "price": 200.0,
        "chain": "solana", "order_type": "limit", "symbol": "SOL", "strategy_id": None,
    }
    receipt_id = await _emit_trade_receipt(order, agent_id=2, outcome="pending")
    assert receipt_id is not None

    row = await _get_receipt_row(receipt_id)
    assert row is not None, "No DB row found for emitted receipt"
    assert row["outcome"] == "pending"
    assert row["agent_id"] == "2"


async def test_emit_is_idempotent_on_duplicate(client):
    """Second emit with same order/agent produces a different receipt_id
    (new UUID each time) but does not raise or corrupt existing rows."""
    from backend.routers.trading import _emit_trade_receipt

    order = {
        "id": 99003, "side": "buy", "quantity": 1.0, "price": 50.0,
        "chain": "solana", "order_type": "market", "symbol": "SOL", "strategy_id": None,
    }
    id1 = await _emit_trade_receipt(order, agent_id=3, outcome="pending")
    id2 = await _emit_trade_receipt(order, agent_id=3, outcome="pending")
    # Each call generates a new UUID, so IDs should differ
    assert id1 != id2 or (id1 is None and id2 is None)
    # Both rows (or at least the first) should be in the DB
    if id1:
        row = await _get_receipt_row(id1)
        assert row is not None


# ── _update_receipt_outcome unit tests ────────────────────────────────────────

async def test_update_transitions_pending_to_success(client):
    from backend.routers.trading import _emit_trade_receipt, _update_receipt_outcome

    order = {
        "id": 99010, "side": "buy", "quantity": 1.0, "price": 100.0,
        "chain": "solana", "order_type": "market", "symbol": "SOL", "strategy_id": None,
    }
    receipt_id = await _emit_trade_receipt(order, agent_id=10, outcome="pending")
    assert receipt_id is not None

    await _update_receipt_outcome(receipt_id, "success", tx_hash="abc123deadbeef")

    row = await _get_receipt_row(receipt_id)
    assert row["outcome"] == "success", f"Expected 'success', got {row['outcome']!r}"


async def test_update_merges_tx_hash_into_raw_json(client):
    from backend.routers.trading import _emit_trade_receipt, _update_receipt_outcome

    order = {
        "id": 99011, "side": "buy", "quantity": 1.0, "price": 100.0,
        "chain": "solana", "order_type": "market", "symbol": "SOL", "strategy_id": None,
    }
    receipt_id = await _emit_trade_receipt(order, agent_id=11, outcome="pending")
    assert receipt_id is not None

    tx = "txhash_merge_test_xyz"
    await _update_receipt_outcome(receipt_id, "success", tx_hash=tx)

    row = await _get_receipt_row(receipt_id)
    raw = json.loads(row["raw_json"])
    action = raw.get("action", {})
    assert action.get("tx_hash") == tx, (
        f"tx_hash not merged into raw_json action: {action}"
    )


async def test_update_transitions_pending_to_failed(client):
    from backend.routers.trading import _emit_trade_receipt, _update_receipt_outcome

    order = {
        "id": 99012, "side": "sell", "quantity": 0.1, "price": 50.0,
        "chain": "solana", "order_type": "market", "symbol": "SOL", "strategy_id": None,
    }
    receipt_id = await _emit_trade_receipt(order, agent_id=12, outcome="pending")
    assert receipt_id is not None

    await _update_receipt_outcome(receipt_id, "failed")

    row = await _get_receipt_row(receipt_id)
    assert row["outcome"] == "failed"


async def test_update_with_none_receipt_id_is_noop(client):
    """A None receipt_id (emit failure) must not raise — fail-open."""
    from backend.routers.trading import _update_receipt_outcome
    # Must not raise
    await _update_receipt_outcome(None, "success", tx_hash="irrelevant")


# ── crash-recovery surface ────────────────────────────────────────────────────

async def test_pending_receipts_are_detectable(client):
    """After a simulated crash (pending row never finalised), the row must be
    queryable by a reconciliation scan.  This test verifies the surface exists;
    a production reconciliation daemon would read this table and re-verify each
    pending row against the chain."""
    from backend.routers.trading import _emit_trade_receipt

    before = await _count_pending_receipts()

    order = {
        "id": 99020, "side": "buy", "quantity": 2.0, "price": 100.0,
        "chain": "solana", "order_type": "market", "symbol": "SOL", "strategy_id": None,
    }
    receipt_id = await _emit_trade_receipt(order, agent_id=20, outcome="pending")
    assert receipt_id is not None

    after = await _count_pending_receipts()
    assert after == before + 1, (
        f"Pending receipt count did not increase: before={before} after={after}"
    )

    # Confirm the row is fetchable by a reconciliation query
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT receipt_id, agent_id, outcome, accepted_at FROM arp_receipts "
            "WHERE outcome = 'pending' ORDER BY accepted_at DESC LIMIT 50"
        )).fetchall()
    pending_ids = [r["receipt_id"] for r in rows]
    assert receipt_id in pending_ids, "Pending receipt not found in reconciliation query"


async def test_finalised_receipts_are_excluded_from_pending_scan(client):
    from backend.routers.trading import _emit_trade_receipt, _update_receipt_outcome

    order = {
        "id": 99021, "side": "buy", "quantity": 1.0, "price": 100.0,
        "chain": "solana", "order_type": "market", "symbol": "SOL", "strategy_id": None,
    }
    receipt_id = await _emit_trade_receipt(order, agent_id=21, outcome="pending")
    await _update_receipt_outcome(receipt_id, "success", tx_hash="finalized_tx")

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT receipt_id FROM arp_receipts WHERE outcome = 'pending'"
        )).fetchall()
    pending_ids = {r["receipt_id"] for r in rows}
    assert receipt_id not in pending_ids, (
        "Finalised receipt still appears in pending scan"
    )


# ── fill attestation gate (Phase-4) ──────────────────────────────────────────

async def test_fill_attestation_without_tool_key_is_rejected(client, fresh_agent):
    """PATCH /orders/{id} with status=filled + real tx_hash requires
    X-Vantage-Tool-Key.  Without it the response must be 403."""
    agent = await fresh_agent()
    headers = {"X-Agent-Key": agent["api_key"]}

    # Create a pending order
    r = await client.post(
        "/api/trading/wallets",
        json={"label": "test-wallet", "chain": "solana", "address": "So1111111111111111111111111111111111111112"},
        headers=headers,
    )
    # Wallet creation may succeed or fail depending on schema; we only need an order_id
    # so try to create an order directly (wallet_id=1 fallback is acceptable for this test)
    r = await client.post(
        "/api/trading/orders",
        json={
            "symbol": "SOL", "side": "buy", "chain": "solana",
            "quantity": 0.1, "order_type": "market", "wallet_id": 1,
            "trigger_reason": "test",
        },
        headers=headers,
    )
    if r.status_code not in (200, 201):
        pytest.skip(f"Could not create order for fill-attestation test: {r.text}")
    order_id = r.json()["id"]

    # Attempt fill attestation without tool key
    r = await client.patch(
        f"/api/trading/orders/{order_id}",
        json={"status": "filled", "tx_hash": "real_chain_tx_abc123", "avg_fill_price": 100.0},
        headers=headers,  # no X-Vantage-Tool-Key
    )
    assert r.status_code == 403, (
        f"Expected 403 without tool key, got {r.status_code}: {r.text}"
    )


async def test_fill_attestation_with_tool_key_is_accepted(client, fresh_agent):
    agent = await fresh_agent()
    agent_headers = {"X-Agent-Key": agent["api_key"]}

    r = await client.post(
        "/api/trading/orders",
        json={
            "symbol": "SOL", "side": "buy", "chain": "solana",
            "quantity": 0.1, "order_type": "market", "wallet_id": 1,
            "trigger_reason": "test",
        },
        headers=agent_headers,
    )
    if r.status_code not in (200, 201):
        pytest.skip(f"Could not create order: {r.text}")
    order_id = r.json()["id"]

    fill_headers = {**agent_headers, "X-Vantage-Tool-Key": TOOL_TRADING_KEY}
    r = await client.patch(
        f"/api/trading/orders/{order_id}",
        json={"status": "filled", "tx_hash": "real_chain_tx_def456", "avg_fill_price": 100.0},
        headers=fill_headers,
    )
    assert r.status_code == 200, (
        f"Expected 200 with valid tool key, got {r.status_code}: {r.text}"
    )


async def test_paper_fill_does_not_require_tool_key(client, fresh_agent):
    """Paper fills (tx_hash starts with 'paper:') bypass the attestation gate
    and must work with agent auth alone."""
    agent = await fresh_agent()
    agent_headers = {"X-Agent-Key": agent["api_key"]}

    r = await client.post(
        "/api/trading/orders",
        json={
            "symbol": "SOL", "side": "buy", "chain": "solana",
            "quantity": 0.1, "order_type": "market", "wallet_id": 1,
            "trigger_reason": "test",
        },
        headers=agent_headers,
    )
    if r.status_code not in (200, 201):
        pytest.skip(f"Could not create order: {r.text}")
    order_id = r.json()["id"]

    # POST /paper-fill uses agent auth only (separate endpoint)
    r = await client.post(
        f"/api/trading/orders/{order_id}/paper-fill",
        headers=agent_headers,
    )
    # 200 = success, 404 = no live price (acceptable in test env), 422 = no price+no limit
    assert r.status_code in (200, 404, 422), (
        f"Paper fill should not require tool key; got {r.status_code}: {r.text}"
    )
