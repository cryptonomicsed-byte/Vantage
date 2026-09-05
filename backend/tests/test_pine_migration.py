"""Migration audit — does it turn "migrate everything" into an ordered queue?

The behaviour under test is mostly about not inventing work: a sidecar outage
must not look like a coverage gap, and a script that is simply broken must not
be counted toward the list of functions to implement.
"""
import pytest
import pytest_asyncio

import aiosqlite

from backend import pine_migration as pm
from backend import pine_validate as pv
from backend.routers import pine
from backend.db import DB_PATH


@pytest_asyncio.fixture
async def indicators(client):
    """A table with a known mix of runnable, blocked and broken scripts."""
    await pine.init_pine_db()
    rows = [
        (1, "Runnable EMA", "indicator('a')\nplot(ta.ema(close, 20))"),
        (1, "Needs percentrank", "indicator('b')\nplot(ta.percentrank(close, 20))"),
        (2, "Also needs percentrank", "indicator('c')\nplot(ta.percentrank(high, 5))"),
        (2, "Needs supertrend2", "indicator('d')\nplot(ta.supertrend2(close))"),
        (3, "Just broken", "indicator('e')\nplot(ta.sma(close, 20)"),
    ]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pine_indicators")
        for agent_id, name, script in rows:
            await db.execute(
                "INSERT INTO pine_indicators (agent_id, name, script) VALUES (?,?,?)",
                (agent_id, name, script))
        await db.commit()
    yield
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pine_indicators")
        await db.commit()


def _verdicts(monkeypatch, mapping):
    """Stand in for the native validator, keyed by a substring of the script."""
    async def fake(code):
        for needle, verdict in mapping.items():
            if needle in code:
                return verdict
        return {"status": "ok", "valid": True, "errors": []}
    monkeypatch.setattr(pv, "validate_native", fake)


def _blocked(message, line=2, source="x"):
    return {"status": "ok", "valid": False,
            "errors": [{"line": line, "message": message, "source": source}]}


# --- missing_symbol ---------------------------------------------------------

def test_missing_symbol_reads_an_unknown_function():
    assert pm.missing_symbol("Unknown function: ta.percentrank") == "ta.percentrank"


def test_missing_symbol_reads_an_unknown_identifier():
    assert pm.missing_symbol("Unknown identifier: barstate") == "barstate"


def test_missing_symbol_is_none_for_a_real_script_error():
    """The separation that keeps the queue honest: a syntax error is not a
    function someone needs to implement."""
    assert pm.missing_symbol("Unbalanced plot()") is None
    assert pm.missing_symbol("Expected ) after args") is None
    assert pm.missing_symbol("") is None


# --- the audit --------------------------------------------------------------

@pytest.mark.asyncio
async def test_ranks_missing_functions_by_how_many_scripts_they_unblock(monkeypatch, indicators):
    """The output the whole module exists to produce."""
    _verdicts(monkeypatch, {
        "percentrank": _blocked("Unknown function: ta.percentrank"),
        "supertrend2": _blocked("Unknown function: ta.supertrend2"),
        "indicator('e')": _blocked("Unbalanced plot()"),
    })
    report = await pm.audit_saved_indicators()

    top = report["blocking_functions"][0]
    assert top["symbol"] == "ta.percentrank"
    assert top["blocks"] == 2, report["blocking_functions"]
    assert len(top["example_ids"]) == 2


@pytest.mark.asyncio
async def test_counts_runnable_and_blocked(monkeypatch, indicators):
    _verdicts(monkeypatch, {
        "percentrank": _blocked("Unknown function: ta.percentrank"),
        "supertrend2": _blocked("Unknown function: ta.supertrend2"),
        "indicator('e')": _blocked("Unbalanced plot()"),
    })
    s = (await pm.audit_saved_indicators())["summary"]
    assert s["total"] == 5
    assert s["runnable"] == 1
    assert s["blocked"] == 4


@pytest.mark.asyncio
async def test_a_broken_script_is_not_counted_as_a_coverage_gap(monkeypatch, indicators):
    """Otherwise a typo sends someone off to implement a function that does not
    exist."""
    _verdicts(monkeypatch, {
        "percentrank": _blocked("Unknown function: ta.percentrank"),
        "supertrend2": _blocked("Unknown function: ta.supertrend2"),
        "indicator('e')": _blocked("Unbalanced plot()"),
    })
    report = await pm.audit_saved_indicators()
    assert report["summary"]["blocked_by_script_errors"] == 1
    symbols = [r["symbol"] for r in report["blocking_functions"]]
    assert "Unbalanced" not in " ".join(symbols)


@pytest.mark.asyncio
async def test_a_sidecar_outage_is_not_reported_as_blocked(monkeypatch, indicators):
    """An outage must not invent migration work."""
    async def down(code):
        return {"status": "unavailable", "reason": "ConnectError"}
    monkeypatch.setattr(pv, "validate_native", down)

    report = await pm.audit_saved_indicators()
    assert report["summary"]["no_verdict"] == 5
    assert report["summary"]["blocked"] == 0
    assert report["blocking_functions"] == []


@pytest.mark.asyncio
async def test_blocked_entries_carry_enough_to_act_on(monkeypatch, indicators):
    _verdicts(monkeypatch, {
        "percentrank": _blocked("Unknown function: ta.percentrank", line=2,
                                source="plot(ta.percentrank(close, 20))"),
    })
    report = await pm.audit_saved_indicators()
    hit = next(b for b in report["blocked"] if b["missing"] == "ta.percentrank")
    assert hit["line"] == 2
    assert "percentrank" in hit["source"]
    assert hit["name"]
    assert hit["agent_id"]


@pytest.mark.asyncio
async def test_an_empty_table_is_a_clean_report(monkeypatch, client):
    await pine.init_pine_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pine_indicators")
        await db.commit()
    report = await pm.audit_saved_indicators()
    assert report["summary"]["total"] == 0
    assert report["blocking_functions"] == []


# --- rendering --------------------------------------------------------------

def test_report_leads_with_the_ranking():
    text = pm.format_report({
        "summary": {"total": 5, "runnable": 1, "blocked": 3, "no_verdict": 1,
                    "blocked_by_script_errors": 0},
        "blocking_functions": [{"symbol": "ta.percentrank", "blocks": 2, "example_ids": [1]}],
        "blocked": [],
        "no_verdict": [{"id": 9, "name": "x", "reason": "down"}],
    })
    assert "ta.percentrank" in text
    assert "sidecar" in text  # tells the reader why one could not be checked


def test_report_names_scripts_broken_on_their_own_terms():
    text = pm.format_report({
        "summary": {"total": 1, "runnable": 0, "blocked": 1, "no_verdict": 0,
                    "blocked_by_script_errors": 1},
        "blocking_functions": [],
        "blocked": [{"id": 3, "name": "Bad", "line": 2, "message": "Unbalanced plot()",
                     "missing": None}],
        "no_verdict": [],
    })
    assert "not coverage" in text
    assert "Unbalanced plot()" in text
