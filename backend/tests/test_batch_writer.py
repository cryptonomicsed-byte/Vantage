"""Regression tests for _BatchWriter's flush-failure handling.

Guards the real data-loss bug: the old _flush() cleared `self._pending`
BEFORE the try/except, so any transient "database is locked" (measured 39×/2h
live) silently DROPPED the whole batch of view_events/activity-log rows.
The fix swaps the list out and re-queues the failed batch at the front on
error so it retries on the next flush cycle instead of vanishing.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest

from backend import utils


class _FakeDB:
    def __init__(self, fail: bool):
        self._fail = fail
        self.committed = 0

    async def execute(self, sql, params):
        if self._fail:
            raise Exception("database is locked")

    async def commit(self):
        self.committed += 1


@pytest.mark.asyncio
async def test_failed_flush_requeues_batch_instead_of_dropping(monkeypatch):
    """A 'database is locked' flush must NOT lose the rows — they retry."""
    writer = utils._BatchWriter(flush_interval=60.0, max_pending=1000)

    # First flush fails.
    fail_state = {"fail": True}
    calls = {"n": 0}

    @asynccontextmanager
    async def fake_get_db():
        calls["n"] += 1
        yield _FakeDB(fail_state["fail"])

    monkeypatch.setattr(utils, "get_db", fake_get_db)

    await writer.add("INSERT INTO view_events ...", (1,))
    await writer.add("INSERT INTO view_events ...", (2,))
    assert len(writer._pending) == 2

    # Force a flush (interval is huge, so add() won't auto-flush).
    await writer._flush()

    # The failed batch must be back in _pending (re-queued), not dropped.
    assert len(writer._pending) == 2, "failed flush dropped the batch"

    # Now the DB recovers; the SAME rows must flush successfully.
    fail_state["fail"] = False
    await writer._flush()
    assert len(writer._pending) == 0
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_requeue_preserves_order_with_new_rows(monkeypatch):
    """Re-queued rows land at the FRONT, before rows added during the failure."""
    writer = utils._BatchWriter(flush_interval=60.0, max_pending=1000)

    fail_state = {"fail": True}

    @asynccontextmanager
    async def fake_get_db():
        yield _FakeDB(fail_state["fail"])

    monkeypatch.setattr(utils, "get_db", fake_get_db)

    # Two rows, then flush fails and re-queues them.
    await writer.add("INSERT ...", ("A",))
    await writer.add("INSERT ...", ("B",))
    await writer._flush()
    assert len(writer._pending) == 2

    # A third row is added while the batch is still "failed".
    await writer.add("INSERT ...", ("C",))

    # Re-queued (A, B) must precede the new (C).
    assert writer._pending[0][1] == ("A",)
    assert writer._pending[1][1] == ("B",)
    assert writer._pending[2][1] == ("C",)


@pytest.mark.asyncio
async def test_successful_flush_commits_and_clears(monkeypatch):
    """Normal path: flush commits and leaves _pending empty."""
    writer = utils._BatchWriter(flush_interval=60.0, max_pending=1000)
    fake = _FakeDB(fail=False)

    @asynccontextmanager
    async def fake_get_db():
        yield fake

    monkeypatch.setattr(utils, "get_db", fake_get_db)

    await writer.add("INSERT ...", (1,))
    await writer._flush()
    assert len(writer._pending) == 0
    assert fake.committed == 1


