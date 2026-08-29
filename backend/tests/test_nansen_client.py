"""Tests for backend/nansen_client.py -- real Nansen smart-money holdings
client. No live network calls: httpx.AsyncClient.post is monkeypatched to
return controlled responses, isolating client-shape logic (auth header,
request body, response parsing, fail-soft behavior) from actual network
I/O, same convention as moonshot_client's existing coverage.
"""
import pytest

from backend import nansen_client
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
    def __init__(self, calls, response):
        self._calls = calls
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        self._calls.append({"url": url, "json": json, "headers": headers})
        return self._response


@pytest.fixture(autouse=True)
def _clear_cache_and_key(monkeypatch):
    _market_cache.clear()
    monkeypatch.setenv("NANSEN_API_KEY", "test-nansen-key-not-real")
    yield
    _market_cache.clear()


@pytest.mark.asyncio
async def test_no_api_key_returns_empty_without_any_network_call(monkeypatch):
    monkeypatch.delenv("NANSEN_API_KEY", raising=False)
    calls = []

    def fake_client_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, []))

    monkeypatch.setattr(nansen_client.httpx, "AsyncClient", fake_client_ctor)
    result = await nansen_client.smart_money_holdings("solana")
    assert result == []
    assert calls == []  # fail-soft before ever touching the network


@pytest.mark.asyncio
async def test_real_request_shape_apikey_header_and_chains_body(monkeypatch):
    calls = []
    body = [{
        "token_address": "So11111111111111111111111111111111111111112",
        "token_symbol": "SOL",
        "value_usd": 1_234_567.89,
        "holders_count": 42,
        "balance_24h_percent_change": 3.5,
    }]

    def fake_client_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, body))

    monkeypatch.setattr(nansen_client.httpx, "AsyncClient", fake_client_ctor)
    result = await nansen_client.smart_money_holdings("solana")

    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.nansen.ai/api/v1/smart-money/holdings"
    assert calls[0]["headers"]["apikey"] == "test-nansen-key-not-real"
    assert calls[0]["json"] == {"chains": ["solana"]}

    assert result == [{
        "token_address": "So11111111111111111111111111111111111111112",
        "symbol": "SOL",
        "value_usd": 1_234_567.89,
        "holders_count": 42,
        "balance_change_24h_pct": 3.5,
    }]


@pytest.mark.asyncio
async def test_rate_limit_429_returns_empty_not_raises(monkeypatch):
    calls = []

    def fake_client_ctor(*a, **kw):
        resp = _FakeResponse(429, None, "rate limited")
        resp.headers = {"Retry-After": "5"}
        return _FakeAsyncClient(calls, resp)

    monkeypatch.setattr(nansen_client.httpx, "AsyncClient", fake_client_ctor)
    result = await nansen_client.smart_money_holdings("solana")
    assert result == []


@pytest.mark.asyncio
async def test_malformed_response_fails_soft(monkeypatch):
    calls = []

    def fake_client_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, {"unexpected": "shape"}))

    monkeypatch.setattr(nansen_client.httpx, "AsyncClient", fake_client_ctor)
    result = await nansen_client.smart_money_holdings("solana")
    assert result == []


@pytest.mark.asyncio
async def test_holdings_by_mint_filters_to_requested_mints_only(monkeypatch):
    calls = []
    body = [
        {"token_address": "MintA", "token_symbol": "AAA", "value_usd": 100.0, "holders_count": 1, "balance_24h_percent_change": 0.0},
        {"token_address": "MintB", "token_symbol": "BBB", "value_usd": 200.0, "holders_count": 2, "balance_24h_percent_change": 0.0},
        {"token_address": "MintC", "token_symbol": "CCC", "value_usd": 300.0, "holders_count": 3, "balance_24h_percent_change": 0.0},
    ]

    def fake_client_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, body))

    monkeypatch.setattr(nansen_client.httpx, "AsyncClient", fake_client_ctor)
    result = await nansen_client.smart_money_holdings_by_mint(["MintA", "MintC", "MintZ"])

    assert set(result.keys()) == {"MintA", "MintC"}  # MintB not requested, MintZ has no data
    assert result["MintA"]["value_usd"] == 100.0
    assert result["MintC"]["value_usd"] == 300.0


@pytest.mark.asyncio
async def test_empty_mint_list_returns_empty_without_network_call(monkeypatch):
    calls = []

    def fake_client_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, []))

    monkeypatch.setattr(nansen_client.httpx, "AsyncClient", fake_client_ctor)
    result = await nansen_client.smart_money_holdings_by_mint([])
    assert result == {}
    assert calls == []


@pytest.mark.asyncio
async def test_result_cached_within_ttl_second_call_makes_no_new_request(monkeypatch):
    calls = []
    body = [{"token_address": "MintA", "token_symbol": "AAA", "value_usd": 50.0, "holders_count": 1, "balance_24h_percent_change": 0.0}]

    def fake_client_ctor(*a, **kw):
        return _FakeAsyncClient(calls, _FakeResponse(200, body))

    monkeypatch.setattr(nansen_client.httpx, "AsyncClient", fake_client_ctor)
    await nansen_client.smart_money_holdings("solana")
    await nansen_client.smart_money_holdings("solana")
    assert len(calls) == 1  # second call served from cache, no re-spend of credits
