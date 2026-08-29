"""Tests for backend/insightx_client.py -- real InsightX Labels/Scanner
client. No live network calls in this file (httpx.AsyncClient.get is
monkeypatched, same convention as test_nansen_client.py) -- isolates
client-shape logic (auth header, path construction, caching, fail-soft
behavior, the per-minute scanner budget) from actual network I/O. The
X-API-Key header, endpoint paths, and rate-limit response shapes asserted
here were verified against the REAL live API 2026-08-29 (see
insightx_client.py's module docstring) before being encoded as fixtures.
"""
import pytest

from backend import insightx_client
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
    insightx_client._scanner_call_times.clear()
    monkeypatch.setenv("INSIGHTX_API_KEY", "test-insightx-key-not-real")
    yield
    _market_cache.clear()
    insightx_client._scanner_call_times.clear()


# ── Labels ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_api_key_returns_empty_without_any_network_call(monkeypatch):
    monkeypatch.delenv("INSIGHTX_API_KEY", raising=False)
    calls = []

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, []))

    monkeypatch.setattr(insightx_client.httpx, "AsyncClient", fake_ctor)
    result = await insightx_client.labels_for_addresses(["addr1"])
    assert result == {}
    assert calls == []


@pytest.mark.asyncio
async def test_empty_address_list_returns_empty_without_network_call(monkeypatch):
    calls = []

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, []))

    monkeypatch.setattr(insightx_client.httpx, "AsyncClient", fake_ctor)
    result = await insightx_client.labels_for_addresses([])
    assert result == {}
    assert calls == []


@pytest.mark.asyncio
async def test_real_request_shape_x_api_key_header_and_path(monkeypatch):
    calls = []
    body = [{
        "address": "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9",
        "label": "Binance: Hot Wallet",
        "tags": ["exchange"],
        "smart_contract": False,
    }]

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, body))

    monkeypatch.setattr(insightx_client.httpx, "AsyncClient", fake_ctor)
    result = await insightx_client.labels_for_addresses(
        ["5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"]
    )

    assert len(calls) == 1
    assert calls[0]["url"] == (
        "https://api.insightx.network/labels/v1/sol/"
        "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"
    )
    assert calls[0]["headers"]["X-API-Key"] == "test-insightx-key-not-real"
    assert result["5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"] == {
        "label": "Binance: Hot Wallet",
        "tags": ["exchange"],
        "smart_contract": False,
    }


@pytest.mark.asyncio
async def test_unlabeled_address_is_absent_from_result_not_a_falsy_entry(monkeypatch):
    calls = []

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, []))  # confirmed live shape

    monkeypatch.setattr(insightx_client.httpx, "AsyncClient", fake_ctor)
    result = await insightx_client.labels_for_addresses(["some-unlabeled-address"])
    assert result == {}
    assert "some-unlabeled-address" not in result


@pytest.mark.asyncio
async def test_repeated_call_for_same_address_hits_cache_not_network(monkeypatch):
    calls = []
    body = [{"address": "AddrA", "label": "Some Label", "tags": ["dex"], "smart_contract": True}]

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, body))

    monkeypatch.setattr(insightx_client.httpx, "AsyncClient", fake_ctor)
    await insightx_client.labels_for_addresses(["AddrA"])
    await insightx_client.labels_for_addresses(["AddrA"])
    assert len(calls) == 1  # second call served entirely from per-address cache


@pytest.mark.asyncio
async def test_unlabeled_address_also_cached_no_repeat_spend(monkeypatch):
    calls = []

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, []))

    monkeypatch.setattr(insightx_client.httpx, "AsyncClient", fake_ctor)
    await insightx_client.labels_for_addresses(["AddrX"])
    await insightx_client.labels_for_addresses(["AddrX"])
    assert len(calls) == 1  # confirmed-no-label result cached too, not re-fetched


@pytest.mark.asyncio
async def test_overlapping_pools_only_fetch_the_new_addresses(monkeypatch):
    calls = []

    def fake_ctor(*a, **kw):
        def responder(url):
            # Whichever addresses were actually requested this call, echo
            # them back labeled -- proves the second call's batch only
            # contains the genuinely-new address.
            addrs = url.rsplit("/", 1)[-1].split(",")
            return _FakeResponse(200, [
                {"address": a, "label": "L", "tags": [], "smart_contract": False} for a in addrs
            ])
        return _FakeAsyncClient(calls, responder)

    monkeypatch.setattr(insightx_client.httpx, "AsyncClient", fake_ctor)
    await insightx_client.labels_for_addresses(["AddrA", "AddrB"])
    await insightx_client.labels_for_addresses(["AddrB", "AddrC"])

    assert len(calls) == 2
    assert calls[0]["url"].endswith("AddrA,AddrB")
    assert calls[1]["url"].endswith("AddrC")  # AddrB already cached from call 1


@pytest.mark.asyncio
async def test_more_than_100_addresses_chunked_into_multiple_calls(monkeypatch):
    calls = []

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, []))

    monkeypatch.setattr(insightx_client.httpx, "AsyncClient", fake_ctor)
    addrs = [f"addr{i}" for i in range(150)]
    await insightx_client.labels_for_addresses(addrs)
    assert len(calls) == 2  # 100 + 50, real endpoint max per call


@pytest.mark.asyncio
async def test_rate_limit_429_returns_empty_not_raises(monkeypatch):
    calls = []

    def fake_ctor(*a, **kw):
        resp = _FakeResponse(429, None, "rate limited")
        resp.headers = {"Retry-After": "5"}
        return _FakeAsyncClient(calls, resp)

    monkeypatch.setattr(insightx_client.httpx, "AsyncClient", fake_ctor)
    result = await insightx_client.labels_for_addresses(["addr1"])
    assert result == {}


# ── Scanner ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scanner_real_request_shape_and_path(monkeypatch):
    calls = []
    body = {
        "network": {"name": "Solana", "symbol": "SOL"},
        "token": {"address": "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"},
        "results": {
            "generated_at": 1234,
            "simple": {"score": 85, "message": "Low risk", "reasons": []},
            "advanced": {"drainable": False, "renounced": True},
        },
    }

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, body))

    monkeypatch.setattr(insightx_client.httpx, "AsyncClient", fake_ctor)
    result = await insightx_client.scanner_for_token("pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn")

    assert len(calls) == 1
    assert calls[0]["url"] == (
        "https://api.insightx.network/scanner/v1/tokens/sol/"
        "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"
    )
    assert calls[0]["headers"]["X-API-Key"] == "test-insightx-key-not-real"
    assert result == body


@pytest.mark.asyncio
async def test_scanner_risk_flags_extracts_drainable_and_score():
    scan = {
        "results": {
            "simple": {"score": 12, "reasons": []},
            "advanced": {"drainable": True, "renounced": False},
        }
    }
    flags = insightx_client.scanner_risk_flags(scan)
    assert flags == {"has_data": True, "drainable": True, "renounced": False, "score": 12}


@pytest.mark.asyncio
async def test_scanner_risk_flags_token_not_found_is_no_data_not_safe():
    # Real confirmed-live shape: unknown token returns a real 200 with
    # score=0 and a "Token not found" reason, NOT a 404 or an error.
    scan = {
        "results": {
            "simple": {"score": 0, "message": "High risk", "reasons": ["Token not found."]},
            "advanced": {},
        }
    }
    flags = insightx_client.scanner_risk_flags(scan)
    assert flags["has_data"] is False
    assert flags["drainable"] is False  # must NOT be treated as a risk flag


@pytest.mark.asyncio
async def test_scanner_risk_flags_none_input_is_no_data():
    flags = insightx_client.scanner_risk_flags(None)
    assert flags == {"has_data": False, "drainable": False, "renounced": False, "score": None}


@pytest.mark.asyncio
async def test_scanner_empty_token_address_returns_none_without_network_call(monkeypatch):
    calls = []

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, {}))

    monkeypatch.setattr(insightx_client.httpx, "AsyncClient", fake_ctor)
    result = await insightx_client.scanner_for_token("")
    assert result is None
    assert calls == []


@pytest.mark.asyncio
async def test_scanner_result_cached_second_call_makes_no_new_request(monkeypatch):
    calls = []
    body = {"results": {"simple": {"score": 90, "reasons": []}, "advanced": {"drainable": False, "renounced": True}}}

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, body))

    monkeypatch.setattr(insightx_client.httpx, "AsyncClient", fake_ctor)
    await insightx_client.scanner_for_token("MintA")
    await insightx_client.scanner_for_token("MintA")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_scanner_per_minute_budget_blocks_further_new_calls(monkeypatch):
    calls = []
    body = {"results": {"simple": {"score": 50, "reasons": []}, "advanced": {"drainable": False, "renounced": False}}}

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, body))

    monkeypatch.setattr(insightx_client.httpx, "AsyncClient", fake_ctor)
    monkeypatch.setattr(insightx_client, "_SCANNER_CALLS_PER_MINUTE_CAP", 2)

    r1 = await insightx_client.scanner_for_token("MintA")
    r2 = await insightx_client.scanner_for_token("MintB")
    r3 = await insightx_client.scanner_for_token("MintC")  # budget exhausted

    assert r1 == body
    assert r2 == body
    assert r3 is None
    assert len(calls) == 2  # third genuinely-new token never hit the network


@pytest.mark.asyncio
async def test_scanner_budget_does_not_block_cached_lookups(monkeypatch):
    calls = []
    body = {"results": {"simple": {"score": 50, "reasons": []}, "advanced": {"drainable": False, "renounced": False}}}

    def fake_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, body))

    monkeypatch.setattr(insightx_client.httpx, "AsyncClient", fake_ctor)
    monkeypatch.setattr(insightx_client, "_SCANNER_CALLS_PER_MINUTE_CAP", 1)

    await insightx_client.scanner_for_token("MintA")  # uses the only budget slot
    r2 = await insightx_client.scanner_for_token("MintA")  # cached, must not be blocked
    assert r2 == body
    assert len(calls) == 1
