"""Tests for backend/mycelium_bridge.py -- real per-source-performance
observation traces into Mycelium's substrate. No live network calls:
urllib.request.urlopen is monkeypatched, same convention as
test_daemon_signals.py. The trace body shape (agent/session/kind/action/
target/outcome/payload) and the gateway's real POST /api/trace endpoint
were verified against the REAL live gateway 2026-08-29 (see
mycelium_bridge.py's module docstring).
"""
import json

import pytest

from backend import mycelium_bridge as mb


@pytest.fixture(autouse=True)
def _clear_dedup_state():
    mb._last_emitted.clear()
    yield
    mb._last_emitted.clear()


@pytest.fixture
def sent(monkeypatch):
    """Capture the request instead of making it, same shape as
    test_daemon_signals.py's own `sent` fixture."""
    calls = []

    class _Response:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        calls.append({
            "url": request.full_url,
            "headers": {k.lower(): v for k, v in request.headers.items()},
            "body": json.loads(request.data.decode()),
        })
        return _Response()

    monkeypatch.setattr(mb.urllib.request, "urlopen", fake_urlopen)
    return calls


def _row(source="strategy:9", window="1h", n_trades=10, wins=7, avg_pnl_pct=3.5, updated_at="2026-08-29 20:00:00"):
    return {"source": source, "window": window, "n_trades": n_trades, "wins": wins,
            "avg_pnl_pct": avg_pnl_pct, "updated_at": updated_at}


def test_real_trace_shape_matches_the_gateways_contract(sent):
    emitted = mb.emit_source_performance_traces([_row()])
    assert emitted == 1
    assert len(sent) == 1
    call = sent[0]
    assert call["url"] == "http://127.0.0.1:8811/api/trace"
    assert call["headers"]["content-type"] == "application/json"
    body = call["body"]
    assert body["agent"] == "trade_outcome_learner"
    assert body["kind"] == "observation"
    assert body["action"] == "source_performance"
    assert body["target"] == "strategy:9"
    assert body["outcome"] == "success"
    assert body["payload"] == {
        "window": "1h", "n_trades": 10, "wins": 7, "win_rate": 0.7,
        "avg_pnl_pct": 3.5, "updated_at": "2026-08-29 20:00:00",
    }


def test_multiple_rows_each_get_their_own_trace(sent):
    rows = [_row(source="strategy:9", window="1h"), _row(source="strategy:9", window="24h"), _row(source="manual_ui", window="1h")]
    emitted = mb.emit_source_performance_traces(rows)
    assert emitted == 3
    assert len(sent) == 3


def test_row_missing_source_or_window_is_skipped_without_a_network_call(sent):
    rows = [{"source": None, "window": "1h", "n_trades": 1, "wins": 1, "avg_pnl_pct": 1.0, "updated_at": "t1"},
            {"source": "strategy:9", "window": None, "n_trades": 1, "wins": 1, "avg_pnl_pct": 1.0, "updated_at": "t1"}]
    emitted = mb.emit_source_performance_traces(rows)
    assert emitted == 0
    assert sent == []


def test_zero_trades_gives_none_win_rate_not_a_zero_division(sent):
    emitted = mb.emit_source_performance_traces([_row(n_trades=0, wins=0, updated_at="t1")])
    assert emitted == 1
    assert sent[0]["body"]["payload"]["win_rate"] is None


# ── Dedup ────────────────────────────────────────────────────────────────

def test_unchanged_updated_at_is_not_re_emitted(sent):
    row = _row(updated_at="2026-08-29 20:00:00")
    mb.emit_source_performance_traces([row])
    emitted_second = mb.emit_source_performance_traces([row])
    assert emitted_second == 0
    assert len(sent) == 1  # only the first call actually hit the network


def test_changed_updated_at_is_re_emitted(sent):
    mb.emit_source_performance_traces([_row(updated_at="2026-08-29 20:00:00")])
    emitted_second = mb.emit_source_performance_traces([_row(updated_at="2026-08-29 20:10:00")])
    assert emitted_second == 1
    assert len(sent) == 2


def test_dedup_is_scoped_per_source_and_window(sent):
    mb.emit_source_performance_traces([_row(source="strategy:9", window="1h", updated_at="t1")])
    # Same source, different window -- must NOT be treated as already-seen.
    emitted = mb.emit_source_performance_traces([_row(source="strategy:9", window="24h", updated_at="t1")])
    assert emitted == 1
    assert len(sent) == 2


# ── Fail-soft ────────────────────────────────────────────────────────────

def test_unreachable_gateway_returns_zero_not_raises(monkeypatch):
    import urllib.error

    def boom(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(mb.urllib.request, "urlopen", boom)
    emitted = mb.emit_source_performance_traces([_row()])
    assert emitted == 0


def test_non_2xx_status_does_not_count_as_emitted_or_update_dedup_state(monkeypatch):
    class _Response:
        status = 500

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(mb.urllib.request, "urlopen", lambda request, timeout=None: _Response())
    emitted = mb.emit_source_performance_traces([_row(updated_at="t1")])
    assert emitted == 0
    assert mb._last_emitted == {}


def test_empty_rows_list_returns_zero_without_any_network_call(sent):
    emitted = mb.emit_source_performance_traces([])
    assert emitted == 0
    assert sent == []
