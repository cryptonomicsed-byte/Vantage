"""Unit tests for the ARP receipt outbox reconciliation daemon.

Tests _scan_once() behaviour directly against a real SQLite DB (same
pattern as test_outbox_reconciliation.py) without needing the ASGI app
client, so they run on any platform including Termux.

Scenarios covered:
  - Fresh pending row (not yet stale) is NOT touched by the scanner
  - Stale pending row with no matching tx_hash → resolved to 'failed'
  - Stale pending row + chain verify succeeds → resolved to 'success'
  - Stale pending row + chain verify fails → resolved to 'failed'
  - Already-finalised rows are never re-processed
  - reconciliation_status endpoint returns correct counts (import-only check)
"""
import asyncio
import json
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from backend.db import DB_PATH, get_db

pytestmark = pytest.mark.asyncio

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS arp_receipts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id    TEXT NOT NULL UNIQUE,
    kind          TEXT NOT NULL DEFAULT 'economic',
    kind_ext      TEXT,
    agent_id      TEXT NOT NULL DEFAULT '1',
    action_kind   TEXT,
    target        TEXT,
    outcome       TEXT NOT NULL DEFAULT 'pending',
    timestamp     INTEGER,
    previous_hash TEXT,
    raw_json      TEXT NOT NULL DEFAULT '{}',
    accepted_at   TEXT NOT NULL
)
"""

_CREATE_ORDERS = """
CREATE TABLE IF NOT EXISTS trading_orders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   TEXT NOT NULL DEFAULT '1',
    order_type TEXT NOT NULL DEFAULT 'market',
    side       TEXT NOT NULL DEFAULT 'buy',
    symbol     TEXT NOT NULL DEFAULT 'SOL/USDC',
    chain      TEXT NOT NULL DEFAULT 'sui',
    tx_hash    TEXT,
    created_at TEXT NOT NULL
)
"""


async def _insert_receipt(receipt_id: str, outcome: str, age_seconds: int) -> None:
    """Insert a receipt row with accepted_at set to now minus age_seconds."""
    accepted = (
        datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    ).isoformat()
    async with get_db() as db:
        await db.execute(_CREATE_TABLE)
        await db.execute(_CREATE_ORDERS)
        await db.execute(
            "INSERT OR IGNORE INTO arp_receipts "
            "(receipt_id, kind, agent_id, outcome, raw_json, accepted_at) "
            "VALUES (?,?,?,?,?,?)",
            (receipt_id, "economic", "1", outcome, "{}", accepted),
        )
        await db.commit()


async def _get_outcome(receipt_id: str) -> str | None:
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT outcome FROM arp_receipts WHERE receipt_id=?", (receipt_id,)
        )).fetchone()
    return row[0] if row else None


# ── fresh rows are left alone ─────────────────────────────────────────────────

async def test_fresh_pending_row_is_not_touched():
    """A pending row written 10 seconds ago is below the 300s staleness
    threshold and must not be touched by the scanner."""
    from backend.receipt_reconciler import _scan_once
    rid = "reconcile-fresh-001"
    await _insert_receipt(rid, "pending", age_seconds=10)

    resolved = await _scan_once()

    outcome = await _get_outcome(rid)
    assert outcome == "pending", "fresh pending row must not be resolved"


# ── stale row without tx_hash → failed ───────────────────────────────────────

async def test_stale_pending_no_tx_hash_resolves_to_failed():
    """Stale pending row with no matching order tx_hash → failed."""
    from backend.receipt_reconciler import _scan_once
    rid = "reconcile-stale-notx-001"
    await _insert_receipt(rid, "pending", age_seconds=400)

    resolved = await _scan_once()

    assert resolved >= 1
    outcome = await _get_outcome(rid)
    assert outcome == "failed", f"expected 'failed', got {outcome!r}"


# ── stale row + chain verify succeeds → success ───────────────────────────────

async def test_stale_pending_chain_ok_resolves_to_success():
    """When a matching order has a tx_hash and the chain verifies it, the
    receipt is promoted to 'success'."""
    from backend.receipt_reconciler import _scan_once, STALENESS_THRESHOLD_SECONDS

    rid = "reconcile-stale-chainok-001"
    accepted = (
        datetime.now(timezone.utc) - timedelta(seconds=STALENESS_THRESHOLD_SECONDS + 60)
    ).isoformat()
    created_at_ts = int(time.time()) - STALENESS_THRESHOLD_SECONDS - 60

    async with get_db() as db:
        await db.execute(_CREATE_TABLE)
        await db.execute(_CREATE_ORDERS)
        await db.execute(
            "INSERT OR IGNORE INTO arp_receipts "
            "(receipt_id, kind, agent_id, outcome, timestamp, raw_json, accepted_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (rid, "economic", "1", "pending", created_at_ts, "{}", accepted),
        )
        await db.execute(
            "INSERT INTO trading_orders "
            "(agent_id, side, symbol, chain, tx_hash, created_at) VALUES (?,?,?,?,?,?)",
            ("1", "buy", "SOL/USDC", "sui", "valid_chain_tx_abc",
             datetime.fromtimestamp(created_at_ts, timezone.utc).isoformat()),
        )
        await db.commit()

    with patch("backend.receipt_reconciler.verify_sui_settlement_effects",
               new=AsyncMock(return_value=(True, "ok"))):
        resolved = await _scan_once()

    outcome = await _get_outcome(rid)
    assert outcome == "success", f"expected 'success', got {outcome!r}"


# ── stale row + chain verify fails → failed ───────────────────────────────────

async def test_stale_pending_chain_fail_resolves_to_failed():
    """When chain verification returns (False, reason), receipt → 'failed'."""
    from backend.receipt_reconciler import _scan_once, STALENESS_THRESHOLD_SECONDS

    rid = "reconcile-stale-chainfail-001"
    accepted = (
        datetime.now(timezone.utc) - timedelta(seconds=STALENESS_THRESHOLD_SECONDS + 60)
    ).isoformat()
    created_at_ts = int(time.time()) - STALENESS_THRESHOLD_SECONDS - 60

    async with get_db() as db:
        await db.execute(_CREATE_TABLE)
        await db.execute(_CREATE_ORDERS)
        await db.execute(
            "INSERT OR IGNORE INTO arp_receipts "
            "(receipt_id, kind, agent_id, outcome, timestamp, raw_json, accepted_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (rid, "economic", "1", "pending", created_at_ts, "{}", accepted),
        )
        await db.execute(
            "INSERT INTO trading_orders "
            "(agent_id, side, symbol, chain, tx_hash, created_at) VALUES (?,?,?,?,?,?)",
            ("1", "buy", "SOL/USDC", "sui", "failed_chain_tx_xyz",
             datetime.fromtimestamp(created_at_ts, timezone.utc).isoformat()),
        )
        await db.commit()

    with patch("backend.receipt_reconciler.verify_sui_settlement_effects",
               new=AsyncMock(return_value=(False, "TX not successful: failure"))):
        resolved = await _scan_once()

    outcome = await _get_outcome(rid)
    assert outcome == "failed", f"expected 'failed', got {outcome!r}"


# ── finalised rows are never re-processed ─────────────────────────────────────

async def test_finalised_rows_not_re_processed():
    """Rows already in 'success' or 'failed' must not be touched."""
    from backend.receipt_reconciler import _scan_once

    rid_ok = "reconcile-final-success-001"
    rid_fail = "reconcile-final-failed-001"
    await _insert_receipt(rid_ok, "success", age_seconds=1000)
    await _insert_receipt(rid_fail, "failed", age_seconds=1000)

    before_ok = await _get_outcome(rid_ok)
    before_fail = await _get_outcome(rid_fail)

    await _scan_once()

    assert await _get_outcome(rid_ok) == before_ok == "success"
    assert await _get_outcome(rid_fail) == before_fail == "failed"


# ── reconciliation_status endpoint imports cleanly ───────────────────────────

async def test_reconciliation_status_endpoint_importable():
    """Smoke-test that the endpoint function exists and is importable."""
    from backend.routers.arp_receipts import reconciliation_status
    assert callable(reconciliation_status)
