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


def test_changed_updated_at_alone_is_not_re_emitted(sent):
    # Real bug regression: refresh_source_performance()'s own SQL bumps
    # updated_at on every full recompute even when n_trades/wins/
    # avg_pnl_pct are unchanged (confirmed live 2026-08-30) -- dedup must
    # key on the real VALUES, not this always-changing timestamp.
    mb.emit_source_performance_traces([_row(updated_at="2026-08-29 20:00:00")])
    emitted_second = mb.emit_source_performance_traces([_row(updated_at="2026-08-29 20:10:00")])
    assert emitted_second == 0
    assert len(sent) == 1


def test_changed_avg_pnl_pct_is_re_emitted_even_with_same_updated_at(sent):
    mb.emit_source_performance_traces([_row(avg_pnl_pct=3.5, updated_at="t1")])
    emitted_second = mb.emit_source_performance_traces([_row(avg_pnl_pct=4.0, updated_at="t1")])
    assert emitted_second == 1
    assert len(sent) == 2


def test_changed_n_trades_is_re_emitted(sent):
    mb.emit_source_performance_traces([_row(n_trades=10, wins=7, updated_at="t1")])
    emitted_second = mb.emit_source_performance_traces([_row(n_trades=11, wins=7, updated_at="t1")])
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


# ── emit_aggregate_score_trace ──────────────────────────────────────────

def _ranked(address="ADDR1", symbol="FOO", total_score=0.75, components=None):
    return [{"address": address, "symbol": symbol, "total_score": total_score,
             "components": components or {"platform_breadth": {"score": 1.0}}}]


def test_aggregate_score_emits_the_real_winner(sent):
    assert mb.emit_aggregate_score_trace(_ranked(), disqualified=[{"a": 1}]) is True
    assert len(sent) == 1
    body = sent[0]["body"]
    assert body["agent"] == "aggregate_score"
    assert body["kind"] == "observation"
    assert body["action"] == "aggregate_winner"
    assert body["target"] == "ADDR1"
    assert body["payload"]["symbol"] == "FOO"
    assert body["payload"]["total_score"] == 0.75
    assert body["payload"]["disqualified_count"] == 1


def test_aggregate_score_empty_ranked_is_a_noop(sent):
    assert mb.emit_aggregate_score_trace([], []) is False
    assert sent == []


def test_aggregate_score_dedups_on_unchanged_score(sent):
    ranked = _ranked(total_score=0.75)
    assert mb.emit_aggregate_score_trace(ranked, []) is True
    assert mb.emit_aggregate_score_trace(ranked, []) is False
    assert len(sent) == 1


def test_aggregate_score_re_emits_on_changed_score(sent):
    mb.emit_aggregate_score_trace(_ranked(total_score=0.75), [])
    assert mb.emit_aggregate_score_trace(_ranked(total_score=0.90), []) is True
    assert len(sent) == 2


def test_aggregate_score_re_emits_on_changed_winner_same_score(sent):
    mb.emit_aggregate_score_trace(_ranked(address="ADDR1", total_score=0.75), [])
    assert mb.emit_aggregate_score_trace(_ranked(address="ADDR2", total_score=0.75), []) is True
    assert len(sent) == 2


# ── emit_platform_leader_traces ─────────────────────────────────────────

def _leader(platform="GeckoTerminal", address="A1", available=True, **extra):
    d = {"platform": platform, "address": address, "available": available, "symbol": "X"}
    d.update(extra)
    return d


def test_platform_leaders_emits_one_per_available_platform(sent):
    leaders = [_leader("GeckoTerminal", "A1"), _leader("DexScreener", "A2")]
    assert mb.emit_platform_leader_traces(leaders) == 2
    assert len(sent) == 2


def test_platform_leaders_skips_unavailable_platforms(sent):
    leaders = [_leader("GeckoTerminal", "A1"), {"platform": "DexScreener", "available": False}]
    assert mb.emit_platform_leader_traces(leaders) == 1
    assert len(sent) == 1


def test_platform_leaders_skips_missing_address(sent):
    leaders = [{"platform": "GeckoTerminal", "available": True, "address": None}]
    assert mb.emit_platform_leader_traces(leaders) == 0
    assert sent == []


def test_platform_leaders_dedups_unchanged_pick(sent):
    leaders = [_leader("GeckoTerminal", "A1")]
    mb.emit_platform_leader_traces(leaders)
    assert mb.emit_platform_leader_traces(leaders) == 0
    assert len(sent) == 1


def test_platform_leaders_re_emits_when_platforms_pick_rotates(sent):
    mb.emit_platform_leader_traces([_leader("GeckoTerminal", "A1")])
    assert mb.emit_platform_leader_traces([_leader("GeckoTerminal", "A2")]) == 1
    assert len(sent) == 2


def test_platform_leaders_dedup_scoped_per_platform(sent):
    mb.emit_platform_leader_traces([_leader("GeckoTerminal", "A1")])
    # Same address, different platform -- must NOT be treated as already-seen.
    assert mb.emit_platform_leader_traces([_leader("DexScreener", "A1")]) == 1
    assert len(sent) == 2


# ── emit_narrative_theme_trace ──────────────────────────────────────────

def test_narrative_theme_emits_real_heat(sent):
    assert mb.emit_narrative_theme_trace("kw_moon", "Moon", 3, ["m1", "m2", "m3"]) is True
    body = sent[0]["body"]
    assert body["agent"] == "narrative_detection"
    assert body["action"] == "narrative_theme_heat"
    assert body["target"] == "kw_moon"
    assert body["payload"] == {"label": "Moon", "heat_score": 3, "sample_mints": ["m1", "m2", "m3"]}


def test_narrative_theme_sample_mints_capped_at_10(sent):
    mints = [f"m{i}" for i in range(25)]
    mb.emit_narrative_theme_trace("kw_moon", "Moon", 25, mints)
    assert len(sent[0]["body"]["payload"]["sample_mints"]) == 10


def test_narrative_theme_dedups_on_unchanged_heat(sent):
    mb.emit_narrative_theme_trace("kw_moon", "Moon", 3, ["m1"])
    assert mb.emit_narrative_theme_trace("kw_moon", "Moon", 3, ["m1"]) is False
    assert len(sent) == 1


def test_narrative_theme_re_emits_on_changed_heat(sent):
    mb.emit_narrative_theme_trace("kw_moon", "Moon", 3, ["m1"])
    assert mb.emit_narrative_theme_trace("kw_moon", "Moon", 5, ["m1", "m2"]) is True
    assert len(sent) == 2


# ── post_observation (shared primitive) ─────────────────────────────────

def test_post_observation_real_shape(sent):
    ok = mb.post_observation(agent="pine_signal", session="pine-1", action="pine_signal_triggered",
                              target="BTC", payload={"side": "BUY"})
    assert ok is True
    body = sent[0]["body"]
    assert body == {
        "agent": "pine_signal", "session": "pine-1", "kind": "observation",
        "action": "pine_signal_triggered", "target": "BTC", "outcome": "info",
        "payload": {"side": "BUY"},
    }


def test_post_observation_fail_soft_on_unreachable_gateway(monkeypatch):
    import urllib.error

    def boom(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(mb.urllib.request, "urlopen", boom)
    assert mb.post_observation(agent="a", session="s", action="act", target="t", payload={}) is False


# ── emit_wallet_reputation_traces ───────────────────────────────────────

def _wallet_row(address="W1", score=45.2, **extra):
    d = {"wallet_address": address, "chain": "solana", "copy_trade_score": score}
    d.update(extra)
    return d


def test_wallet_reputation_emits_top_n_only(sent):
    rows = [_wallet_row("W1", 45.2), _wallet_row("W2", 10.0), _wallet_row("W3", 99.0)]
    assert mb.emit_wallet_reputation_traces(rows, top_n=2) == 2
    targets = {c["body"]["target"] for c in sent}
    assert targets == {"W1", "W3"}  # W2 (lowest) excluded by top_n=2


def test_wallet_reputation_skips_zero_or_missing_score(sent):
    rows = [_wallet_row("W1", 0), {"wallet_address": "W2"}, _wallet_row("W3", 5.0)]
    assert mb.emit_wallet_reputation_traces(rows) == 1
    assert sent[0]["body"]["target"] == "W3"


def test_wallet_reputation_dedups_on_unchanged_score(sent):
    rows = [_wallet_row("W1", 45.2)]
    mb.emit_wallet_reputation_traces(rows)
    assert mb.emit_wallet_reputation_traces(rows) == 0
    assert len(sent) == 1


def test_wallet_reputation_re_emits_on_changed_score(sent):
    mb.emit_wallet_reputation_traces([_wallet_row("W1", 45.2)])
    assert mb.emit_wallet_reputation_traces([_wallet_row("W1", 60.0)]) == 1
    assert len(sent) == 2


def test_wallet_reputation_real_payload_shape(sent):
    rows = [_wallet_row("W1", 45.2, display_name="@foo (twitter)", name_source="social_claim",
                         first_buyer_count=3, currently_hot_tokens=2, reasoning="first buyer on 3 token(s)")]
    mb.emit_wallet_reputation_traces(rows)
    body = sent[0]["body"]
    assert body["agent"] == "wallet_learner"
    assert body["action"] == "wallet_reputation"
    assert body["payload"]["display_name"] == "@foo (twitter)"
    assert body["payload"]["copy_trade_score"] == 45.2
    assert body["payload"]["reasoning"] == "first buyer on 3 token(s)"


# ── emit_verified_call_trace ────────────────────────────────────────────

def _verified_call(wallet="W1", **extra):
    d = {"platform": "twitter", "username": "foo", "wallet_address": wallet, "mint": "M1",
         "symbol": "FOO", "entry_price_usd": 0.001, "entry_tx_signature": "sig123",
         "current_price_usd": 0.002, "pct_change": 100.0}
    d.update(extra)
    return d


def test_verified_call_emits_real_shape(sent):
    assert mb.emit_verified_call_trace(_verified_call()) is True
    body = sent[0]["body"]
    assert body["agent"] == "social_tracker"
    assert body["action"] == "verified_social_call"
    assert body["target"] == "W1"
    assert body["payload"]["pct_change"] == 100.0
    assert body["payload"]["entry_tx_signature"] == "sig123"


def test_verified_call_missing_wallet_is_a_noop(sent):
    assert mb.emit_verified_call_trace({"platform": "twitter"}) is False
    assert sent == []


def test_verified_call_has_no_dedup_every_real_call_is_a_new_fact(sent):
    # Unlike the other emitters, repeated calls with identical content are
    # NOT deduped here -- the caller (social_tracker.py) only ever invokes
    # this once per genuinely new verified_calls row (guarded upstream by
    # that table's own ON CONFLICT...DO NOTHING), so every invocation
    # already represents a real, new fact.
    vc = _verified_call()
    mb.emit_verified_call_trace(vc)
    mb.emit_verified_call_trace(vc)
    assert len(sent) == 2
