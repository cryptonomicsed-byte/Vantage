"""The shared daemon signal client.

The property under test is the one that made this worth extracting: fixing the
daemons' auth must not, by itself, switch on auto-trading. Eighteen daemons
posted X-Agent-Key to endpoints that require system-tool auth, so every call
401'd and the pipeline sat dead. Several of those daemons also used 0-5/0-7
conviction scales, which the 0-1 contract reads as "maximum confidence" -- so
a header-only repair would have put the whole fleet above the 0.7
auto-execution threshold on essentially every signal.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from daemons import vantage_signals as vs  # noqa: E402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("VANTAGE_DAEMON_AUTO_EXECUTE", "VANTAGE_TOOL_KEY",
                "VANTAGE_TOOL_INTEL_KEY", "VANTAGE_TOOL_TRADING_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def sent(monkeypatch):
    """Capture the request instead of making it."""
    calls = []

    class _Response:
        def read(self):
            return b'{"status": "ingested"}'

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

    monkeypatch.setattr(vs.urllib.request, "urlopen", fake_urlopen)
    return calls


# ── conviction normalisation ────────────────────────────────────────────

@pytest.mark.parametrize("raw, scale, expected", [
    (0.42, 1.0, 0.42),
    (7.0, 7.0, 1.0),
    (3.5, 7.0, 0.5),      # a 0-7 mid becomes a 0-1 mid, not a clamp to 1.0
    (2.0, 5.0, 0.4),
    (-1.0, 1.0, 0.0),
    (99.0, 1.0, 1.0),
])
def test_conviction_is_normalised_not_clamped(raw, scale, expected):
    assert vs.normalise_conviction(raw, scale) == pytest.approx(expected)


def test_a_midrange_score_on_a_wide_scale_stays_below_the_execution_threshold():
    """The bug this whole module exists to prevent: freqtrade_bridge scored
    conviction on 0-7. Clamping 3.5 would yield 1.0 -- auto-execution on a
    thoroughly average signal. Scaling yields 0.5, which executes nothing."""
    assert vs.normalise_conviction(3.5, 7.0) < vs.AUTO_EXECUTION_THRESHOLD


@pytest.mark.parametrize("junk", [None, "abc", float("nan"), float("inf"), object()])
def test_junk_conviction_becomes_zero_rather_than_raising(junk):
    """A bad reading from one of thirty upstream APIs must not kill the loop,
    and 0.0 is the value that causes nothing to happen."""
    assert vs.normalise_conviction(junk) == 0.0


def test_a_nonpositive_scale_is_a_programming_error():
    with pytest.raises(ValueError):
        vs.normalise_conviction(1.0, 0.0)


# ── auth ────────────────────────────────────────────────────────────────

def test_ingest_uses_system_tool_auth_not_an_agent_key(sent, monkeypatch):
    monkeypatch.setenv("VANTAGE_TOOL_INTEL_KEY", "tool-secret")
    vs.post_signal("SOL", "test_daemon")

    headers = sent[0]["headers"]
    assert headers["x-vantage-tool"] == "intel"
    assert headers["x-vantage-tool-key"] == "tool-secret"
    assert "x-agent-key" not in headers


def test_tool_key_falls_back_to_the_shared_variable(monkeypatch):
    monkeypatch.setenv("VANTAGE_TOOL_KEY", "shared")
    assert vs.headers_for("intel")["X-Vantage-Tool-Key"] == "shared"


# ── routing ─────────────────────────────────────────────────────────────

def test_signals_go_to_the_non_executing_intel_pool_by_default(sent):
    vs.post_signal("SOL", "test_daemon", conviction=0.95, direction="BUY")
    assert sent[0]["url"].endswith(vs.INTEL_INGEST)


def test_execute_is_ignored_unless_the_operator_enabled_it(sent):
    """Restoring auth on a box that has run these daemons for months must not
    start placing orders. The signal is still delivered -- to intel."""
    vs.post_signal("SOL", "test_daemon", conviction=0.95, direction="BUY",
                   execute=True, agent_id=1)

    assert len(sent) == 1, "the signal should be delivered, not dropped"
    assert sent[0]["url"].endswith(vs.INTEL_INGEST)


def test_execute_reaches_the_trading_endpoint_once_enabled(sent, monkeypatch):
    monkeypatch.setenv("VANTAGE_DAEMON_AUTO_EXECUTE", "1")
    vs.post_signal("SOL", "test_daemon", conviction=0.95, direction="BUY",
                   execute=True, agent_id=7, chain="solana")

    assert sent[0]["url"].endswith(vs.TRADING_INGEST)
    assert sent[0]["headers"]["x-vantage-tool"] == "trading"
    assert sent[0]["body"]["agent_id"] == 7


def test_trading_post_without_an_agent_id_is_refused(sent, monkeypatch):
    """Guessing an agent id would place someone else's order."""
    monkeypatch.setenv("VANTAGE_DAEMON_AUTO_EXECUTE", "1")
    assert vs.post_signal("SOL", "d", direction="BUY", execute=True) is None
    assert sent == []


def test_an_unnormalised_scale_cannot_reach_the_trading_endpoint_above_threshold(sent, monkeypatch):
    monkeypatch.setenv("VANTAGE_DAEMON_AUTO_EXECUTE", "1")
    vs.post_signal("SOL", "freqtrade", conviction=3.5, scale=7.0,
                   direction="BUY", execute=True, agent_id=1)
    assert sent[0]["body"]["conviction"] == pytest.approx(0.5)


# ── payload shape ───────────────────────────────────────────────────────

def test_intel_payload_carries_the_fields_that_endpoint_requires(sent):
    vs.post_signal("BTC", "scanner", type_="threat", detail="d", mint="So111")
    body = sent[0]["body"]
    assert {"symbol", "source", "type"} <= set(body)
    assert body["mint"] == "So111"


def test_mint_is_omitted_when_unknown(sent):
    vs.post_signal("BTC", "scanner")
    assert "mint" not in sent[0]["body"]


# ── failure handling ────────────────────────────────────────────────────

def test_a_401_is_logged_and_returns_none_rather_than_killing_the_loop(monkeypatch, caplog):
    def boom(request, timeout=None):
        raise vs.urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(vs.urllib.request, "urlopen", boom)
    with caplog.at_level("WARNING"):
        assert vs.post_signal("SOL", "test_daemon") is None
    assert "401" in caplog.text


def test_a_transport_error_returns_none(monkeypatch):
    def boom(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(vs.urllib.request, "urlopen", boom)
    assert vs.post_signal("SOL", "test_daemon") is None
