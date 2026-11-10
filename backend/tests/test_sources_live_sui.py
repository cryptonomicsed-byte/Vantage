"""Measured per-host source stats + Sui GraphQL health fallback."""
import httpx
import pytest

from backend import market_sources as ms
from backend.routers import intel


def test_record_counts_ok_errors_and_rate_limits(monkeypatch):
    monkeypatch.setattr(ms, "_source_stats", {})
    ms._record("https://api.example.com/a?x=1", 200, 100.0)
    ms._record("https://api.example.com/b", 429, 50.0, "slow down")
    ms._record("https://api.example.com/c", None, 0.0, "ConnectError: boom")
    [h] = ms.source_stats()
    assert h["host"] == "api.example.com" and h["calls"] == 3 and h["ok"] == 1
    assert h["errors"] == 2 and h["rate_limited"] == 1 and h["avg_ms"] == 50
    assert h["last_error"].startswith("ConnectError") and h["ok_rate"] == round(1 / 3, 3)


@pytest.mark.asyncio
async def test_get_json_records(monkeypatch):
    monkeypatch.setattr(ms, "_source_stats", {})

    def handler(req):
        return httpx.Response(401, text="unauthorized")

    real = httpx.AsyncClient
    monkeypatch.setattr(ms.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    assert await ms._get_json("https://hermes.pyth.network/v2/updates/price/latest", retries=1) is None
    [h] = ms.source_stats()
    assert h["host"] == "hermes.pyth.network" and h["last_status"] == 401 and h["last_error"] == "unauthorized"


@pytest.mark.asyncio
async def test_sui_falls_back_to_graphql():
    def handler(req):
        if req.url.path.endswith("/api/rpc/sui"):
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                             "error": {"code": -32601, "message": "Method not found"}})
        if "graphql" in str(req.url):
            return httpx.Response(200, json={"data": {"checkpoint": {"sequenceNumber": 343778162}}})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        out = await intel._probe_chain(c, "sui")
    assert out["block_height"] == 343778162 and out["health"] == "healthy" and out["source"] == "graphql"
