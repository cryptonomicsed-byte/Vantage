"""A tool call cancelled mid-flight must still resolve its audit row.

The relay records a tool call before running it, then completes the row after.
On disconnect the pump task is cancelled; asyncio.CancelledError is a
BaseException, so a cancel landing on any await between the two (the
announcement send, the dispatch, the result hand-back) skipped the completion
entirely. The row then claimed the call was still in flight forever -- and
test_voice_relay's tool-call tests flaked on exactly that (`None is not None`
when they closed the socket right after the announcement).

Deterministic: no sockets, no timing. The dispatcher blocks until cancelled.
"""
import asyncio

import pytest

from backend.routers import voice_sessions as vs


class _Event:
    tool_name = "vantage__whoami_get"
    tool_args = {}
    tool_call_id = "fc-1"


class _Engine:
    def __init__(self):
        self.results = []

    async def send_tool_result(self, call_id, name, result):
        self.results.append((call_id, name, result))


class _BlockingDispatcher:
    def __init__(self):
        self.started = asyncio.Event()

    async def execute(self, name, args):
        self.started.set()
        await asyncio.Event().wait()          # never returns; only cancellation ends it


class _OkDispatcher:
    async def execute(self, name, args):
        return {"status": "ok", "data": {"id": 1}}


class _WS:
    async def send_text(self, _):
        pass


@pytest.fixture
def audit(monkeypatch):
    rows = {}

    async def record(session_id, agent_id, tool_name, source, args, turn_id=None):
        rows["vtc_1"] = {"result": None, "is_error": None, "duration_ms": None}
        return "vtc_1"

    async def complete(call_id, result=None, is_error=False, duration_ms=None):
        rows[call_id].update(result=result, is_error=is_error, duration_ms=duration_ms)

    monkeypatch.setattr(vs.store, "record_tool_call", record)
    monkeypatch.setattr(vs.store, "complete_tool_call", complete)
    return rows


async def test_a_cancelled_tool_call_is_resolved_as_interrupted(audit):
    d = _BlockingDispatcher()
    task = asyncio.create_task(vs._handle_tool_call(_WS(), _Engine(), d, "vs_1", 1, _Event()))
    await d.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = audit["vtc_1"]
    assert row["result"] is not None, "audit row left claiming the call is in flight"
    assert row["result"]["status"] == "interrupted"
    assert row["is_error"] is True
    assert row["duration_ms"] is not None


async def test_a_completed_tool_call_records_its_real_result(audit):
    engine = _Engine()
    await vs._handle_tool_call(_WS(), engine, _OkDispatcher(), "vs_1", 1, _Event())
    assert audit["vtc_1"]["result"] == {"status": "ok", "data": {"id": 1}}
    assert audit["vtc_1"]["is_error"] is False
    assert engine.results and engine.results[0][2]["status"] == "ok"
