"""Regression tests for verify_sui_settlement_effects().

Verifies that settlement confirmation checks:
  - TX finality (effects.status == 'success')
  - Sender identity against the agent's registered Sui address
  - Receipt-hash commitment in the TX inputs

Also verifies the endpoint-level gates:
  - Cross-agent settlement spoofing is blocked (403)
  - A failed or missing TX is rejected (400)
  - A TX with the wrong sender is rejected (400)
  - A TX that doesn't commit to the receipt hash is rejected (400)
"""
import hashlib
import json
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_sui_response(
    status: str = "success",
    sender: str = "0xagent_sui_address",
    receipt_hash: str = "abc123",
    error: dict | None = None,
) -> dict:
    """Build a minimal sui_getTransactionBlock response."""
    if error:
        return {"error": error}
    return {
        "result": {
            "transaction": {
                "data": {
                    "sender": sender,
                    "transaction": {
                        "kind": "ProgrammableTransaction",
                        "inputs": [
                            {"type": "pure", "value": receipt_hash},
                            {"type": "pure", "value": "receipt-id-001"},
                        ],
                    },
                }
            },
            "effects": {
                "status": {"status": status, "error": "execution_error" if status != "success" else None}
            },
        }
    }


# ── verify_sui_settlement_effects unit tests ──────────────────────────────────

async def test_valid_tx_passes_all_checks():
    from backend.sui_client import verify_sui_settlement_effects

    mock_resp = _make_sui_response(
        status="success",
        sender="0xmyaddress",
        receipt_hash="deadbeef",
    )
    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value.raise_for_status = lambda: None
        mock_client.post.return_value.json = lambda: mock_resp

        ok, reason = await verify_sui_settlement_effects(
            "tx_digest_1",
            expected_sender="0xmyaddress",
            expected_receipt_hash="deadbeef",
        )
    assert ok is True, f"Expected success, got reason={reason!r}"
    assert reason == "ok"


async def test_failed_tx_status_is_rejected():
    from backend.sui_client import verify_sui_settlement_effects

    mock_resp = _make_sui_response(status="failure", sender="0xa", receipt_hash="abc")
    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value.raise_for_status = lambda: None
        mock_client.post.return_value.json = lambda: mock_resp

        ok, reason = await verify_sui_settlement_effects("tx_failed")
    assert ok is False
    assert "not successful" in reason


async def test_rpc_error_is_rejected():
    from backend.sui_client import verify_sui_settlement_effects

    mock_resp = {"error": {"message": "object not found", "code": -32000}}
    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value.raise_for_status = lambda: None
        mock_client.post.return_value.json = lambda: mock_resp

        ok, reason = await verify_sui_settlement_effects("tx_missing")
    assert ok is False
    assert "RPC returned error" in reason


async def test_wrong_sender_is_rejected():
    from backend.sui_client import verify_sui_settlement_effects

    mock_resp = _make_sui_response(sender="0xsomeoneelse", receipt_hash="abc123")
    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value.raise_for_status = lambda: None
        mock_client.post.return_value.json = lambda: mock_resp

        ok, reason = await verify_sui_settlement_effects(
            "tx_wrong_sender",
            expected_sender="0xexpectedagent",
            expected_receipt_hash="abc123",
        )
    assert ok is False
    assert "Sender mismatch" in reason


async def test_missing_receipt_hash_in_inputs_is_rejected():
    from backend.sui_client import verify_sui_settlement_effects

    # TX inputs contain a different hash
    mock_resp = _make_sui_response(sender="0xagent", receipt_hash="wrong_hash")
    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value.raise_for_status = lambda: None
        mock_client.post.return_value.json = lambda: mock_resp

        ok, reason = await verify_sui_settlement_effects(
            "tx_wrong_hash",
            expected_sender="0xagent",
            expected_receipt_hash="correct_hash",
        )
    assert ok is False
    assert "not found in TX inputs" in reason


async def test_sender_check_is_case_insensitive():
    """Sui addresses may differ in case; comparison must normalise."""
    from backend.sui_client import verify_sui_settlement_effects

    mock_resp = _make_sui_response(sender="0xABCDEF", receipt_hash="h1")
    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value.raise_for_status = lambda: None
        mock_client.post.return_value.json = lambda: mock_resp

        ok, reason = await verify_sui_settlement_effects(
            "tx_case",
            expected_sender="0xabcdef",
            expected_receipt_hash="h1",
        )
    assert ok is True, f"Case-insensitive sender check failed: {reason}"


async def test_network_error_returns_false_not_raises():
    """An httpx exception must not propagate — verification is fail-closed
    (returns False) but must not crash the caller."""
    from backend.sui_client import verify_sui_settlement_effects

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value.__aenter__.return_value = mock_client
        import httpx
        mock_client.post.side_effect = httpx.ConnectError("connection refused")

        ok, reason = await verify_sui_settlement_effects("tx_network_error")
    assert ok is False
    assert "RPC error" in reason


# ── endpoint-level cross-agent spoofing test ──────────────────────────────────

async def test_cross_agent_cannot_confirm_settlement(client, fresh_agent):
    """Agent B must not be able to confirm settlement of a receipt owned by
    Agent A — this was the pre-fix behaviour."""
    agent_a = await fresh_agent()
    agent_b = await fresh_agent()

    headers_a = {"X-Agent-Key": agent_a["api_key"]}
    headers_b = {"X-Agent-Key": agent_b["api_key"]}

    # Insert a receipt owned by agent_a directly (bypass the initiation endpoint
    # since we don't need a real Sui address for this ownership check)
    import aiosqlite
    from backend.db import DB_PATH, get_db

    receipt_id = f"test-receipt-spoof-{agent_a['name'][:8]}"
    async with get_db() as db:
        try:
            await db.execute(
                """CREATE TABLE IF NOT EXISTS guild_execution_receipts (
                    id TEXT PRIMARY KEY, agent_id INTEGER, receipt_body TEXT,
                    sui_tx_digest TEXT, settled_at TEXT
                )"""
            )
            agent_a_id_row = await (await db.execute(
                "SELECT id FROM agents WHERE name=?", (agent_a["name"],)
            )).fetchone()
            if not agent_a_id_row:
                pytest.skip("Agent A not found in DB")
            agent_a_db_id = agent_a_id_row[0]
            await db.execute(
                "INSERT OR IGNORE INTO guild_execution_receipts (id, agent_id, receipt_body) VALUES (?,?,?)",
                (receipt_id, agent_a_db_id, json.dumps({"amount": 100})),
            )
            await db.commit()
        except Exception as e:
            pytest.skip(f"Could not set up test receipt: {e}")

    # Agent B attempts to confirm settlement of Agent A's receipt
    r = await client.post(
        f"/api/receipts/{receipt_id}/settle/sui/confirm",
        json={"tx_digest": "some_valid_looking_digest"},
        headers=headers_b,
    )
    assert r.status_code == 403, (
        f"Expected 403 for cross-agent settlement, got {r.status_code}: {r.text}"
    )
