"""P1-06: enrich_from_loom is scoped to the token and fail-open.

Acceptance: the decision is requested with symbol and mint; loom_conviction
is set only when Loom answers scoped=true; a scoped=false, a non-200, or an
exception leaves the signal unchanged (fail-open). Two tokens with different
symbols never share a generic consensus.
"""
import pytest

from backend import signal_fusion as sf

_CAPTURED = {}


def _signal(**kw):
    s = {"symbol": "TOK", "mint": "MINT123", "conviction": 0.5}
    s.update(kw)
    return s


class _Resp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data or {}

    def json(self):
        return self._data


def _patch_client(monkeypatch, status=200, data=None, exc=None):
    class _FakeClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            _CAPTURED["url"], _CAPTURED["params"] = url, params
            if exc:
                raise exc
            return _Resp(status, data)

    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)


async def test_scoped_true_sets_conviction_and_direction(monkeypatch):
    _patch_client(monkeypatch, data={"scoped": True, "consensus": {"direction": "BUY", "conviction": 7}})
    out = await sf.enrich_from_loom(_signal())
    assert _CAPTURED["params"] == {"symbol": "TOK", "mint": "MINT123"}
    assert out["loom_conviction"] == 0.7 and out["loom_direction"] == "up"


async def test_scoped_false_is_fail_open(monkeypatch):
    _patch_client(monkeypatch, data={"scoped": False, "consensus": {"direction": "BUY", "conviction": 9}})
    out = await sf.enrich_from_loom(_signal())
    assert "loom_conviction" not in out and "loom_direction" not in out
    assert out["conviction"] == 0.5  # untouched


async def test_two_tokens_are_not_conflated(monkeypatch):
    """Different symbols request different decisions; the mint travels too."""
    _patch_client(monkeypatch, data={"scoped": True, "consensus": {"direction": "BUY", "conviction": 3}})
    await sf.enrich_from_loom(_signal(symbol="AAA", mint="M1"))
    first = dict(_CAPTURED["params"])
    await sf.enrich_from_loom(_signal(symbol="BBB", mint="M2"))
    assert first == {"symbol": "AAA", "mint": "M1"}
    assert _CAPTURED["params"] == {"symbol": "BBB", "mint": "M2"}


async def test_non_200_is_fail_open(monkeypatch):
    _patch_client(monkeypatch, status=500)
    assert await sf.enrich_from_loom(_signal()) == _signal()


async def test_exception_is_fail_open(monkeypatch):
    _patch_client(monkeypatch, exc=ConnectionError("down"))
    assert await sf.enrich_from_loom(_signal()) == _signal()


def test_loom_base_is_env_overridable(monkeypatch):
    import importlib
    monkeypatch.setenv("LOOM_BASE", "http://loom:9999")
    importlib.reload(sf)
    assert sf.LOOM_BASE == "http://loom:9999"
    monkeypatch.delenv("LOOM_BASE")
    importlib.reload(sf)
    assert sf.LOOM_BASE == "http://localhost:8889"
