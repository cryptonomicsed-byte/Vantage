"""Analytics feed fixes (2026-11-09 audit of every Market Intel tab).

- Health sent eth_blockNumber to Solana and Sui, got a JSON-RPC error, and
  still reported "healthy" with zeros for fields it never measured.
- Backtest read CoinGecko only; its 429s surfaced as "insufficient data".
- Alpha called a 13% drop a "breakout".
- Top Wallets to Copy ranked a 2,237-token launch bot first."""
import pytest

from backend import market_sources as ms
from backend.routers import intel


class _Resp:
    def __init__(self, body, code=200):
        self._b, self.status_code = body, code

    def json(self):
        return self._b


class _Client:
    """Answers like the real RPC proxy: EVM chains know eth_*, Solana and Sui
    reject it with a JSON-RPC error and answer their own method."""
    calls = []

    async def post(self, url, json=None):
        chain, method = url.rsplit("/", 1)[-1], json["method"]
        _Client.calls.append((chain, method))
        if chain in ("polygon", "base"):
            return _Resp({"result": "0x5c8f3a9" if method == "eth_blockNumber" else "0x12a05f200"})
        if chain == "solana":
            return _Resp({"result": 467_500_000} if method == "getSlot" else {"error": {"message": "Method not found"}})
        if chain == "sui":
            return _Resp({"result": "201234567"} if method == "sui_getLatestCheckpointSequenceNumber"
                         else {"error": {"message": "Method not found"}})
        return _Resp({}, 500)


@pytest.mark.asyncio
async def test_each_chain_probed_with_its_own_method():
    _Client.calls.clear()
    out = {ch: await intel._probe_chain(_Client(), ch) for ch in intel._CHAIN_PROBES}
    assert ("solana", "getSlot") in _Client.calls and ("solana", "eth_blockNumber") not in _Client.calls
    assert out["solana"]["block_height"] == 467_500_000 and out["solana"]["health"] == "healthy"
    assert out["sui"]["block_height"] == 201_234_567
    assert out["polygon"]["block_height"] == int("0x5c8f3a9", 16) and out["polygon"]["gas_gwei"] == 5.0
    for v in out.values():                      # never invented
        assert v["tps"] is None and v["peers"] is None and isinstance(v["latency_ms"], int)


@pytest.mark.asyncio
async def test_rpc_error_is_degraded_not_healthy():
    class Err(_Client):
        async def post(self, url, json=None):
            return _Resp({"error": {"message": "Method not found"}})
    v = await intel._probe_chain(Err(), "solana")
    assert v["health"] == "degraded" and v["block_height"] is None and "Method not found" in v["error"]


@pytest.mark.asyncio
async def test_backtest_uses_candles_before_coingecko(monkeypatch):
    ms._CACHE.clear() if hasattr(ms, "_CACHE") else None
    closes = [100 + (i % 7) * 3 + i * 0.5 for i in range(120)]

    async def fake_ohlc(symbol, interval="1d", limit=200):
        return [{"time": i, "close": c} for i, c in enumerate(closes)][-limit:]

    async def no_cg(*a, **k):
        raise AssertionError("CoinGecko must not be needed when candles exist")
    monkeypatch.setattr(ms, "ohlc", fake_ohlc)
    monkeypatch.setattr(ms, "_get_json", no_cg)
    out = await ms.backtest("ZZTEST", 90, 10, 30)
    assert out and out["price_src"] == "ohlc" and out["data_points"] >= 32


@pytest.mark.asyncio
async def test_alpha_drop_is_breakdown(monkeypatch):
    async def mk(n):
        return [{"symbol": "DN", "price": 1, "change_24h": -13.2, "volume_24h": 1},
                {"symbol": "UP", "price": 1, "change_24h": 12.0, "volume_24h": 1},
                {"symbol": "FL", "price": 1, "change_24h": 2.0, "volume_24h": 1}]
    monkeypatch.setattr(ms, "coinpaprika_markets", mk)
    sig = {m["symbol"]: m["signal"] for m in await ms.top_movers(3)}
    assert sig == {"DN": "breakdown", "UP": "breakout", "FL": "momentum"}


def test_mass_deployer_rule_sql():
    from backend.routers import degen
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE wallet_reputation (wallet_address TEXT, copy_trade_score REAL, deployer_count INT, "
              "tokens_tracked INT)")
    c.executemany("INSERT INTO wallet_reputation VALUES (?,?,?,?)", [
        ("LAUNCHBOT", 33555, 2237, 2237),    # deploys everything it touches
        ("TRADER", 120, 0, 40),
        ("BUILDER", 90, 25, 400),           # deployed some, mostly trades
    ])
    got = [r[0] for r in c.execute(f"SELECT wallet_address FROM wallet_reputation WHERE copy_trade_score > 0 "
                                    f"AND NOT {degen._MASS_DEPLOYER_SQL} ORDER BY copy_trade_score DESC")]
    assert got == ["TRADER", "BUILDER"]
