"""Tests for backend/dex_metrics_client.py -- real InsightX DEX Metrics
(clusters/bundlers) client. No live network calls: httpx.AsyncClient.get is
monkeypatched, same convention as test_insightx_client.py/
test_nansen_client.py. The X-API-Key header, endpoint paths, and response
shapes asserted here were verified against the REAL live API 2026-08-29
(see dex_metrics_client.py's module docstring) before being encoded as
fixtures.
"""
import pytest

from backend import dex_metrics_client as dm
from backend.market_sources import _cache as _market_cache


class _FakeResponse:
    def __init__(self, status_code, json_body=None, text=""):
        self.status_code = status_code
        self._json = json_body
        self.text = text
        self.headers = {}

    def json(self):
        return self._json


class _FakeAsyncClient:
    def __init__(self, calls, response_or_fn):
        self._calls = calls
        self._response_or_fn = response_or_fn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        self._calls.append({"url": url, "headers": headers})
        if callable(self._response_or_fn):
            return self._response_or_fn(url)
        return self._response_or_fn


@pytest.fixture(autouse=True)
def _clear_state(monkeypatch):
    _market_cache.clear()
    dm._dex_metrics_call_times.clear()
    monkeypatch.setenv("INSIGHTX_API_KEY", "test-insightx-key-not-real")
    yield
    _market_cache.clear()
    dm._dex_metrics_call_times.clear()


@pytest.mark.asyncio
async def test_no_api_key_returns_none_without_any_network_call(monkeypatch):
    monkeypatch.delenv("INSIGHTX_API_KEY", raising=False)
    calls = []

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, {}))

    monkeypatch.setattr(dm.httpx, "AsyncClient", fake_ctor)
    result = await dm.clusters_for_token("SomeMint")
    assert result is None
    assert calls == []


@pytest.mark.asyncio
async def test_empty_token_address_returns_none_without_network_call(monkeypatch):
    calls = []

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, {}))

    monkeypatch.setattr(dm.httpx, "AsyncClient", fake_ctor)
    result = await dm.bundlers_for_token("")
    assert result is None
    assert calls == []


@pytest.mark.asyncio
async def test_clusters_real_request_shape_and_path(monkeypatch):
    calls = []
    body = {"total_cluster_pct": 0.0, "clusters": []}

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, body))

    monkeypatch.setattr(dm.httpx, "AsyncClient", fake_ctor)
    result = await dm.clusters_for_token("aPWhpYZVQ9ZArvUar2oVP3zbVRYrfDz9ETXVu9dpump")

    assert len(calls) == 1
    assert calls[0]["url"] == (
        "https://api.insightx.network/dex-metrics/v1/sol/"
        "aPWhpYZVQ9ZArvUar2oVP3zbVRYrfDz9ETXVu9dpump/clusters"
    )
    assert calls[0]["headers"]["X-API-Key"] == "test-insightx-key-not-real"
    assert result == body


@pytest.mark.asyncio
async def test_bundlers_real_request_shape_and_path(monkeypatch):
    calls = []
    body = {
        "total_bundlers_pct": 4.4784312805233444e-07,
        "bundlers": [
            {"address": "AddrA", "balance": 4.46, "percentage": 4.47e-07, "reasons": ["same_slot_as_pool"], "slot": 442702276},
        ],
    }

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, body))

    monkeypatch.setattr(dm.httpx, "AsyncClient", fake_ctor)
    result = await dm.bundlers_for_token("aPWhpYZVQ9ZArvUar2oVP3zbVRYrfDz9ETXVu9dpump")

    assert calls[0]["url"] == (
        "https://api.insightx.network/dex-metrics/v1/sol/"
        "aPWhpYZVQ9ZArvUar2oVP3zbVRYrfDz9ETXVu9dpump/bundlers"
    )
    assert result == body


@pytest.mark.asyncio
async def test_rate_limit_429_returns_none_not_raises(monkeypatch):
    calls = []

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(429, None, "rate limited"))

    monkeypatch.setattr(dm.httpx, "AsyncClient", fake_ctor)
    result = await dm.clusters_for_token("SomeMint")
    assert result is None


@pytest.mark.asyncio
async def test_malformed_non_dict_response_fails_soft(monkeypatch):
    calls = []

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, ["not", "a", "dict"]))

    monkeypatch.setattr(dm.httpx, "AsyncClient", fake_ctor)
    result = await dm.clusters_for_token("SomeMint")
    assert result is None


@pytest.mark.asyncio
async def test_result_cached_second_call_makes_no_new_request(monkeypatch):
    calls = []
    body = {"total_cluster_pct": 0.0, "clusters": []}

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, body))

    monkeypatch.setattr(dm.httpx, "AsyncClient", fake_ctor)
    await dm.clusters_for_token("MintA")
    await dm.clusters_for_token("MintA")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_clusters_and_bundlers_have_independent_cache_keys(monkeypatch):
    # Real bug class this guards against: if clusters/bundlers accidentally
    # shared a cache key (e.g. keyed only by token address, not by which
    # endpoint), a clusters call would wrongly serve a bundlers response
    # or vice versa.
    calls = []

    def responder(url):
        if url.endswith("/clusters"):
            return _FakeResponse(200, {"total_cluster_pct": 1.0, "clusters": []})
        return _FakeResponse(200, {"total_bundlers_pct": 2.0, "bundlers": []})

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, responder)

    monkeypatch.setattr(dm.httpx, "AsyncClient", fake_ctor)
    c = await dm.clusters_for_token("MintA")
    b = await dm.bundlers_for_token("MintA")
    assert len(calls) == 2
    assert "total_cluster_pct" in c
    assert "total_bundlers_pct" in b


@pytest.mark.asyncio
async def test_per_minute_budget_blocks_further_new_calls(monkeypatch):
    calls = []
    body = {"total_cluster_pct": 0.0, "clusters": []}

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, body))

    monkeypatch.setattr(dm.httpx, "AsyncClient", fake_ctor)
    monkeypatch.setattr(dm, "_DEX_METRICS_CALLS_PER_MINUTE_CAP", 2)

    r1 = await dm.clusters_for_token("MintA")
    r2 = await dm.clusters_for_token("MintB")
    r3 = await dm.clusters_for_token("MintC")

    assert r1 == body
    assert r2 == body
    assert r3 is None
    assert len(calls) == 2


# ── manipulation_signal ────────────────────────────────────────────────

def test_manipulation_signal_no_data_when_both_none():
    sig = dm.manipulation_signal(None, None)
    assert sig == {"has_data": False, "cluster_pct": None, "bundler_pct": None, "flagged": False}


def test_manipulation_signal_real_clean_token_not_flagged():
    # Real confirmed-live shape: a genuinely clean, actively-traded
    # pump.fun token with a tiny residual bundler_pct (4.48e-7%) --
    # detection noise, not manipulation.
    clusters = {"total_cluster_pct": 0.0, "clusters": []}
    bundlers = {"total_bundlers_pct": 4.4784312805233444e-07, "bundlers": [{"address": "A"}]}
    sig = dm.manipulation_signal(clusters, bundlers)
    assert sig["has_data"] is True
    assert sig["flagged"] is False


def test_manipulation_signal_flags_above_threshold():
    clusters = {"total_cluster_pct": 12.5, "clusters": [{"members": ["A", "B"]}]}
    sig = dm.manipulation_signal(clusters, None)
    assert sig["flagged"] is True
    assert sig["cluster_pct"] == 12.5


def test_manipulation_signal_bundler_above_threshold_also_flags():
    bundlers = {"total_bundlers_pct": 8.0, "bundlers": [{"address": "A"}]}
    sig = dm.manipulation_signal(None, bundlers)
    assert sig["flagged"] is True
    assert sig["bundler_pct"] == 8.0


def test_manipulation_signal_exactly_at_threshold_not_flagged():
    clusters = {"total_cluster_pct": 5.0, "clusters": []}
    sig = dm.manipulation_signal(clusters, None)
    assert sig["flagged"] is False  # strictly greater-than, not >=
