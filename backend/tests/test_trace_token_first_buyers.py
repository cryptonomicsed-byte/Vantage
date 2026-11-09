"""Market Intel trace-token first buyers: stored origin trace first, then the
quick walk, else queue the mint (ahead of the runner backlog) and say so --
found live: every trace showed 0 first buyers because only the quick walk
(Helius-dependent) was asked, never the 108 stored origin traces."""
import pytest

from backend import runner_trace as rt
from backend.db import get_db
from backend.routers import pumpfun as pf


async def _no_walk(mint, *a, **k):
    return {"buyers": []}


@pytest.mark.asyncio
async def test_stored_trace_wins_with_funder_and_labels(monkeypatch):
    monkeypatch.setattr("backend.early_buyer.backtrack_early_buyers", _no_walk)
    async with get_db() as db:
        await rt._ensure(db)
        await db.execute("INSERT OR REPLACE INTO runner_trace_queue (mint, enqueued_at, status, attempts, source, "
                         "reached_origin, buyers) VALUES ('TTSTORED', 5, 'done', 1, 'rpc', 1, 2)")
        for rank, w in ((1, "W1"), (2, "W2")):
            await db.execute("INSERT OR REPLACE INTO runner_early_buyers (mint, rank, wallet, entry_ts, sol, tokens, "
                             "signature, funded_by, is_major, source) VALUES ('TTSTORED', ?, ?, 1762600000, 1.5, 1, 's', "
                             "'FUNDER', 0, 'rpc')", (rank, w))
        await db.commit()
    rows, st = await pf._first_buyers_with_status("TTSTORED")
    assert [r["wallet"] for r in rows] == ["W1", "W2"] and rows[0]["rank"] == 1
    assert rows[0]["sol_spent"] == 1.5 and rows[0]["funded_by"] == "FUNDER"
    assert "labels" in rows[0] and "funder_labels" in rows[0]
    assert st["from"] == "stored_trace" and st["reached_origin"] == 1


@pytest.mark.asyncio
async def test_quick_walk_used_when_no_stored_trace(monkeypatch):
    async def walk(mint, *a, **k):
        return {"buyers": [{"wallet_address": "Q2", "entry_rank": 2, "sol_spent": 0.5},
                           {"wallet_address": "Q1", "entry_rank": 1, "sol_spent": 2.0, "is_major": True}]}
    monkeypatch.setattr("backend.early_buyer.backtrack_early_buyers", walk)
    rows, st = await pf._first_buyers_with_status("TTQUICK")
    assert [r["wallet"] for r in rows] == ["Q1", "Q2"] and st["from"] == "quick_walk"


@pytest.mark.asyncio
async def test_nothing_found_queues_ahead_of_backlog_and_says_so(monkeypatch):
    monkeypatch.setattr("backend.early_buyer.backtrack_early_buyers", _no_walk)
    rows, st = await pf._first_buyers_with_status("TTQUEUE")
    assert rows == [] and st["from"] == "queued" and st["status"] == "pending"
    async with get_db() as db:
        q = await (await db.execute("SELECT status, enqueued_at FROM runner_trace_queue WHERE mint='TTQUEUE'")).fetchone()
    assert q == ("pending", 1)
    # asking again doesn't re-queue; it reports the existing job
    rows, st = await pf._first_buyers_with_status("TTQUEUE")
    assert st["from"] == "queued" and st["status"] == "pending"
