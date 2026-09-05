"""Pine compile gate — the verdict/availability distinction, and the route wiring.

The point of these tests is the thing that is easy to get wrong: a broken script
and an unreachable compiler must not produce the same outcome. One is a verdict
that rejects the script; the other is silence that lets it through.

No test here touches the network. The compiler is stubbed at `pine_validate._post`
so the shape of a real reply is asserted rather than assumed.
"""
import pytest
import pytest_asyncio

from backend import pine_validate as pv
from backend.routers import pine
from backend import market_sources as ms


# --- pine-facade reply fixtures, in the shape the endpoint documents ---------

CLEAN = {"success": True, "result": {"functions2": []}}

BROKEN = {
    "success": True,
    "result": {
        "errors2": [
            {
                "message": "Undeclared identifier 'clse'",
                "start": {"line": 3, "column": 13},
                "end": {"line": 3, "column": 17},
            }
        ]
    },
}

BROKEN_MANY = {
    "success": True,
    "result": {
        "errors2": [
            {"message": f"problem {i}", "start": {"line": i, "column": 1}}
            for i in range(1, 8)
        ]
    },
}


def _stub_facade(monkeypatch, *, payload=None, status=200, raise_exc=None, count=None):
    """Stand in for the Pine compiler at `pine_validate._post`.

    Patching `_post` rather than `httpx.AsyncClient` is the point: the route
    module shares the httpx module object, so patching the class would conflate
    the compiler with the Pine sandbox.
    """
    class FakeResp:
        status_code = status
        def json(self):
            if count is not None:
                count["n"] += 1
            return payload

    async def fake_post(code):
        if raise_exc is not None:
            raise raise_exc
        return FakeResp()

    monkeypatch.setattr(pv, "_post", fake_post)
    monkeypatch.setattr(pv, "ENABLED", True)


@pytest_asyncio.fixture(autouse=True)
async def _init_pine(client):
    # ASGITransport doesn't run the app lifespan, so create the table here.
    await pine.init_pine_db()


@pytest.fixture(autouse=True)
def _clear_cache():
    pv._CACHE.clear()
    yield
    pv._CACHE.clear()


# --- the verdict / availability distinction ---------------------------------

@pytest.mark.asyncio
async def test_clean_script_is_valid(monkeypatch):
    _stub_facade(monkeypatch, payload=CLEAN)
    v = await pv.validate_pine("plot(close)")
    assert v["status"] == "ok"
    assert v["valid"] is True
    assert v["errors"] == []


@pytest.mark.asyncio
async def test_broken_script_reports_line_and_column(monkeypatch):
    _stub_facade(monkeypatch, payload=BROKEN)
    v = await pv.validate_pine("//@version=5\nindicator('x')\nplot(ta.sma(clse, 20))")
    assert v["status"] == "ok"
    assert v["valid"] is False
    assert v["errors"][0]["line"] == 3
    assert v["errors"][0]["column"] == 13
    assert "clse" in v["errors"][0]["message"]


@pytest.mark.asyncio
async def test_unreachable_compiler_is_not_a_failing_script(monkeypatch):
    """The distinction the whole module exists for."""
    import httpx
    _stub_facade(monkeypatch, raise_exc=httpx.ConnectError("no route"))
    v = await pv.validate_pine("plot(close)")
    assert v["status"] == "unavailable"
    # Critically: it does NOT claim the script is invalid.
    assert "valid" not in v


@pytest.mark.asyncio
async def test_non_200_is_unavailable_not_invalid(monkeypatch):
    _stub_facade(monkeypatch, payload=None, status=503)
    v = await pv.validate_pine("plot(close)")
    assert v["status"] == "unavailable"
    assert "valid" not in v


@pytest.mark.asyncio
async def test_success_false_is_unavailable(monkeypatch):
    """The compiler declining to answer is an absent verdict, not a bad script."""
    _stub_facade(monkeypatch, payload={"success": False, "error": "bad request"})
    v = await pv.validate_pine("plot(close)")
    assert v["status"] == "unavailable"


@pytest.mark.asyncio
async def test_non_json_reply_is_unavailable(monkeypatch):
    class FakeResp:
        status_code = 200
        def json(self):
            raise ValueError("not json")

    async def fake_post(code):
        return FakeResp()

    monkeypatch.setattr(pv, "_post", fake_post)
    monkeypatch.setattr(pv, "ENABLED", True)
    v = await pv.validate_pine("plot(close)")
    assert v["status"] == "unavailable"


# --- annotation and summary --------------------------------------------------

def test_annotate_shows_the_offending_line():
    code = "//@version=5\nindicator('x')\nplot(ta.sma(clse, 20))"
    out = pv.annotate(code, [{"line": 3, "message": "Undeclared identifier 'clse'"}])
    assert "plot(ta.sma(clse, 20))" in out
    assert "^--" in out
    assert "Undeclared identifier" in out


def test_annotate_survives_an_out_of_range_line():
    """A compiler pointing past the end of the source must not crash the gate."""
    out = pv.annotate("plot(close)", [{"line": 99, "message": "impossible"}])
    assert "?" in out
    assert "impossible" in out


def test_summarize_truncates_and_counts():
    v = {"errors": [{"line": i, "message": f"problem {i}"} for i in range(1, 8)]}
    s = pv.summarize(v, limit=3)
    assert "problem 1" in s and "problem 3" in s
    assert "problem 4" not in s
    assert "+4 more" in s


# --- caching -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_identical_source_is_answered_from_cache(monkeypatch):
    calls = {"n": 0}
    _stub_facade(monkeypatch, payload=CLEAN, count=calls)
    await pv.validate_pine("plot(close)")
    await pv.validate_pine("plot(close)")
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_unavailability_is_never_cached(monkeypatch):
    """A transient outage must not be remembered as this script's verdict."""
    import httpx
    _stub_facade(monkeypatch, raise_exc=httpx.ConnectError("down"))
    await pv.validate_pine("plot(close)")
    assert pv._CACHE == {}

    # Compiler comes back; the next call gets a real verdict.
    _stub_facade(monkeypatch, payload=CLEAN)
    v = await pv.validate_pine("plot(close)")
    assert v["status"] == "ok" and v["valid"] is True


@pytest.mark.asyncio
async def test_different_scripts_do_not_share_a_cache_entry(monkeypatch):
    _stub_facade(monkeypatch, payload=CLEAN)
    await pv.validate_pine("plot(close)")
    _stub_facade(monkeypatch, payload=BROKEN)
    v = await pv.validate_pine("plot(clse)")
    assert v["valid"] is False


@pytest.mark.asyncio
async def test_disabled_returns_unavailable(monkeypatch):
    monkeypatch.setattr(pv, "ENABLED", False)
    v = await pv.validate_pine("anything")
    assert v["status"] == "unavailable"
    assert "disabled" in v["reason"]


# --- route wiring ------------------------------------------------------------

def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


@pytest.mark.asyncio
async def test_run_rejects_a_script_that_does_not_compile(client, monkeypatch, fresh_agent):
    a = await fresh_agent()
    _stub_facade(monkeypatch, payload=BROKEN)

    r = await client.post("/api/pine/run", headers=_h(a),
                          json={"script": "plot(ta.sma(clse, 20))", "symbol": "BTC"})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "Pine Script does not compile"
    assert detail["errors"][0]["line"] == 3


@pytest.mark.asyncio
async def test_compile_gate_runs_before_governance(client, monkeypatch, fresh_agent):
    """Ordering matters: the cheap deterministic gate should short-circuit the
    governance call, not run after it."""
    a = await fresh_agent()
    reviewed = {"called": False}

    async def review(script, agent):
        reviewed["called"] = True
        return {"block": False, "reason": ""}

    monkeypatch.setattr(pine, "_review", review)
    _stub_facade(monkeypatch, payload=BROKEN)

    r = await client.post("/api/pine/run", headers=_h(a),
                          json={"script": "plot(clse)", "symbol": "BTC"})
    assert r.status_code == 422
    assert reviewed["called"] is False


@pytest.mark.asyncio
async def test_run_proceeds_when_the_compiler_is_unreachable(client, monkeypatch, fresh_agent):
    """An outage must not start rejecting every agent's work."""
    import httpx
    a = await fresh_agent()
    _stub_facade(monkeypatch, raise_exc=httpx.ConnectError("down"))

    async def no_review(script, agent):
        return {"block": False, "reason": ""}
    async def candles(symbol, interval, limit):
        return [{"time": i, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
                for i in range(30)]
    monkeypatch.setattr(pine, "_review", no_review)
    monkeypatch.setattr(ms, "ohlc", candles)

    class SandboxResp:
        status_code = 200
        headers = {"content-type": "application/json"}
        def json(self): return {"plots": {}, "alerts": []}

    class SandboxClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None): return SandboxResp()

    monkeypatch.setattr(pine.httpx, "AsyncClient", SandboxClient)

    r = await client.post("/api/pine/run", headers=_h(a),
                          json={"script": "plot(close)", "symbol": "BTC"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_saving_a_broken_script_is_refused(client, monkeypatch, fresh_agent):
    """Persisting and later sharing a script that cannot compile is worse than
    running one, because a guild inherits it."""
    a = await fresh_agent()
    _stub_facade(monkeypatch, payload=BROKEN)

    r = await client.post("/api/pine/indicators", headers=_h(a),
                          json={"name": "Broken", "script": "plot(clse)"})
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_valid_script_still_saves(client, monkeypatch, fresh_agent):
    a = await fresh_agent()
    _stub_facade(monkeypatch, payload=CLEAN)

    r = await client.post("/api/pine/indicators", headers=_h(a),
                          json={"name": "Good", "script": 'plot(ta.ema(close, 20), "EMA")'})
    assert r.status_code == 200, r.text
