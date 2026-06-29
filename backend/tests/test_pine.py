"""Pine indicator API — persistence, sharing, governance, auth.

The sandbox execution itself is covered by pine-runtime/test.js; here we cover
the Vantage surface (save/list/share, governance block, no-data, auth) with the
sidecar not required.
"""
import pytest
import pytest_asyncio

from backend.routers import pine
from backend import market_sources as ms


@pytest_asyncio.fixture(autouse=True)
async def _init_pine(client):
    # ASGITransport doesn't run the app lifespan, so create the table here.
    await pine.init_pine_db()


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


@pytest.mark.asyncio
async def test_save_list_share_roundtrip(client, fresh_agent):
    a = await fresh_agent()
    b = await fresh_agent()

    # Save under agent a.
    r = await client.post("/api/pine/indicators", headers=_h(a),
                          json={"name": "My EMA", "script": 'plot(ta.ema(close, 20), "EMA")'})
    assert r.status_code == 200, r.text
    iid = r.json()["id"]

    # a sees it; b does not (not shared yet).
    la = (await client.get("/api/pine/indicators", headers=_h(a))).json()
    assert any(x["id"] == iid for x in la)
    lb = (await client.get("/api/pine/indicators", headers=_h(b))).json()
    assert not any(x["id"] == iid for x in lb)

    # Share → b now sees it.
    rs = await client.post(f"/api/pine/indicators/{iid}/share", headers=_h(a), json={"guild_slug": "quants"})
    assert rs.status_code == 200, rs.text
    lb2 = (await client.get("/api/pine/indicators", headers=_h(b))).json()
    assert any(x["id"] == iid and x["shared"] == 1 for x in lb2)


@pytest.mark.asyncio
async def test_run_blocked_by_governance(client, monkeypatch, fresh_agent):
    a = await fresh_agent()

    async def block(script, agent):
        return {"block": True, "reason": "policy: no leverage indicators"}
    monkeypatch.setattr(pine, "_review", block)

    r = await client.post("/api/pine/run", headers=_h(a),
                          json={"script": "plot(close)", "symbol": "BTC"})
    assert r.status_code == 403
    assert "governance" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_run_no_candles_404(client, monkeypatch, fresh_agent):
    a = await fresh_agent()

    async def no_review(script, agent):
        return {"block": False, "reason": ""}
    async def empty(symbol, interval, limit):
        return []
    monkeypatch.setattr(pine, "_review", no_review)
    monkeypatch.setattr(ms, "ohlc", empty)

    r = await client.post("/api/pine/run", headers=_h(a),
                          json={"script": 'plot(ta.ema(close,20))', "symbol": "NOPE"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_run_proxies_sandbox(client, monkeypatch, fresh_agent):
    """Happy path with the sidecar mocked — verifies candles are fetched and the
    sandbox response is returned to the caller."""
    a = await fresh_agent()

    async def no_review(script, agent):
        return {"block": False, "reason": ""}
    async def candles(symbol, interval, limit):
        return [{"time": i, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(30)]
    monkeypatch.setattr(pine, "_review", no_review)
    monkeypatch.setattr(ms, "ohlc", candles)

    class FakeResp:
        status_code = 200
        headers = {"content-type": "application/json"}
        def json(self): return {"plots": {"EMA": [{"time": 0, "value": 1.0}]}, "alerts": []}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None): return FakeResp()

    monkeypatch.setattr(pine.httpx, "AsyncClient", FakeClient)

    r = await client.post("/api/pine/run", headers=_h(a),
                          json={"script": 'plot(ta.ema(close,20), "EMA")', "symbol": "BTC"})
    assert r.status_code == 200, r.text
    assert r.json()["plots"]["EMA"][0]["value"] == 1.0


@pytest.mark.asyncio
async def test_pine_requires_agent_key(client):
    assert (await client.post("/api/pine/run", json={"script": "plot(close)"})).status_code == 401
    assert (await client.get("/api/pine/indicators")).status_code == 401
