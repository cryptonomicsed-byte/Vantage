"""P4-04: trace-token works with either daemon signature and gets first buyers
from the ordered early-buyer walk."""
import pytest

from backend.routers import pumpfun as pf


def test_old_signature_called_without_symbol():
    seen = {}
    def old(mint):  # repo copy: enrich_token(mint)
        seen["args"] = (mint,)
        return {}
    pf._call_enrich(old, "M", "SYM")
    assert seen["args"] == ("M",)


def test_new_signature_gets_symbol():
    seen = {}
    def new(mint, symbol=""):
        seen["args"] = (mint, symbol)
        return {}
    pf._call_enrich(new, "M", "SYM")
    assert seen["args"] == ("M", "SYM")


@pytest.mark.asyncio
async def test_first_buyers_come_from_backtrack_in_rank_order(monkeypatch):
    import backend.early_buyer as eb

    async def fake(mint, max_buyers=50, dry_run=False):
        return {"buyers": [
            {"wallet_address": "w2", "entry_rank": 2, "sol_spent": 1.5, "funded_by": "f", "is_major": 0},
            {"wallet_address": "w1", "entry_rank": 1, "sol_spent": 4.0, "funded_by": None, "is_major": 1},
            {"wallet_address": "", "entry_rank": 3},
        ]}
    monkeypatch.setattr(eb, "backtrack_early_buyers", fake)
    rows = await pf._first_buyers("M")
    assert [r["wallet"] for r in rows] == ["w1", "w2"]
    assert rows[0] == {"wallet": "w1", "rank": 1, "sol_spent": 4.0, "funded_by": None, "is_major": True}


@pytest.mark.asyncio
async def test_first_buyers_empty_on_failure(monkeypatch):
    import backend.early_buyer as eb

    async def boom(*a, **k):
        raise RuntimeError("rpc down")
    monkeypatch.setattr(eb, "backtrack_early_buyers", boom)
    assert await pf._first_buyers("M") == []
