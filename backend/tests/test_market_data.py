"""Market-data layer tests — direct no-auth sources + live-valued positions.

Network is always mocked so these are deterministic and offline-safe.
"""
import pytest

from backend import market_sources as ms


# ── market_sources unit tests (no app needed) ─────────────────────────────────────

def test_sources_registry_complete():
    # The full no-auth API catalog is surfaced for transparency.
    assert len(ms.SOURCES) >= 34
    integrated = [s for s in ms.SOURCES if s["integrated"]]
    assert any(s["name"] == "Pyth Network" for s in integrated)
    assert any(s["name"] == "CoinGecko" for s in integrated)
    # Every entry is well-formed.
    for s in ms.SOURCES:
        assert {"name", "category", "url", "integrated"} <= set(s)


@pytest.mark.asyncio
async def test_exchange_spreads_computes_real_spread(monkeypatch):
    # Mock each venue fetcher with distinct prices → a real, ranked spread.
    monkeypatch.setattr(ms, "_binance", lambda s: _ret(100.0))
    monkeypatch.setattr(ms, "_okx", lambda s: _ret(101.0))
    monkeypatch.setattr(ms, "_kucoin", lambda s: _ret(99.0))   # cheapest → buy venue
    monkeypatch.setattr(ms, "_coinbase", lambda s: _ret(102.0))  # priciest → sell venue
    monkeypatch.setattr(ms, "_gemini", lambda s: _ret(None))
    ms._cache.clear()

    out = await ms.exchange_spreads("BTC")
    assert out["buy_venue"] == "kucoin"
    assert out["sell_venue"] == "coinbase"
    assert out["spread_pct"] == pytest.approx((102 - 99) / 99 * 100, abs=0.01)
    assert len(out["venues"]) == 4  # gemini returned None → excluded


@pytest.mark.asyncio
async def test_resolve_price_prefers_pyth_then_caches(monkeypatch):
    calls = {"pyth": 0, "cg": 0}

    async def fake_pyth(syms):
        calls["pyth"] += 1
        return {"BTC": 64000.0}

    async def fake_cg(sym):
        calls["cg"] += 1
        return 1.23

    monkeypatch.setattr(ms, "_pyth_prices", fake_pyth)
    monkeypatch.setattr(ms, "_coingecko_price", fake_cg)
    ms._cache.clear()

    p1 = await ms.resolve_price("BTC")
    p2 = await ms.resolve_price("BTC")  # served from cache
    assert p1 == 64000.0 and p2 == 64000.0
    assert calls["pyth"] == 1  # second call hit the cache
    assert calls["cg"] == 0    # CoinGecko fallback not needed for a Pyth major


@pytest.mark.asyncio
async def test_resolve_price_falls_back_to_coingecko(monkeypatch):
    async def empty_pyth(syms):
        return {}

    async def fake_cg(sym):
        return 7.77

    monkeypatch.setattr(ms, "_pyth_prices", empty_pyth)
    monkeypatch.setattr(ms, "_coingecko_price", fake_cg)
    ms._cache.clear()

    assert await ms.resolve_price("RNDR") == 7.77


def _ret(v):
    async def _coro(*a, **k):
        return v
    return _coro(0)


@pytest.mark.asyncio
async def test_backtest_computes_real_returns(monkeypatch):
    # A clean uptrend: buy-and-hold is strongly positive; the SMA strategy trades.
    prices = [[i, 100 + i] for i in range(60)]  # 100 → 159

    async def fake_get(url, timeout=8):
        return {"prices": prices}

    monkeypatch.setattr(ms, "_get_json", fake_get)
    ms._cache.clear()

    out = await ms.backtest("BTC", days=60, fast=5, slow=15)
    assert out is not None
    assert out["buy_hold_return_pct"] == pytest.approx((159 / 100 - 1) * 100, abs=0.1)
    assert out["strategy"].startswith("SMA")
    assert out["data_points"] == 60
    assert isinstance(out["trades"], int)


@pytest.mark.asyncio
async def test_backtest_insufficient_data(monkeypatch):
    async def fake_get(url, timeout=8):
        return {"prices": [[0, 100], [1, 101]]}  # too few points
    monkeypatch.setattr(ms, "_get_json", fake_get)
    ms._cache.clear()
    assert await ms.backtest("BTC", days=60, slow=30) is None


@pytest.mark.asyncio
async def test_defillama_yields_filters_and_sorts(monkeypatch):
    async def fake_get(url, timeout=12):
        return {"data": [
            {"symbol": "USDC", "project": "aave", "chain": "Ethereum", "apy": 4.0, "tvlUsd": 5_000_000, "stablecoin": True},
            {"symbol": "SOL", "project": "marinade", "chain": "Solana", "apy": 9.0, "tvlUsd": 2_000_000},
            {"symbol": "TINY", "project": "x", "chain": "Base", "apy": 999.0, "tvlUsd": 100},  # below TVL floor
        ]}
    monkeypatch.setattr(ms, "_get_json", fake_get)
    ms._cache.clear()
    rows = await ms.defillama_yields(limit=10)
    assert [r["pool"] for r in rows] == ["SOL", "USDC"]  # TVL-floored, APY-sorted
    assert all(r["tvl_usd"] >= 1_000_000 for r in rows)


@pytest.mark.asyncio
async def test_btc_address_lookup_annotates_in_out_counterparties(monkeypatch):
    tx_in = {
        "txid": "tx1hash",
        "status": {"confirmed": True, "block_time": 1700000000},
        "fee": 500,
        "vin": [{"prevout": {"scriptpubkey_address": "addrB", "value": 100500000}}],
        "vout": [{"scriptpubkey_address": "addrA", "value": 100000000}],
    }
    tx_out = {
        "txid": "tx2hash",
        "status": {"confirmed": True, "block_time": 1700000100},
        "fee": 1000000,
        "vin": [{"prevout": {"scriptpubkey_address": "addrA", "value": 100000000}}],
        "vout": [
            {"scriptpubkey_address": "addrC", "value": 40000000},
            {"scriptpubkey_address": "addrA", "value": 59000000},  # change back to self
        ],
    }

    async def fake_get(url, timeout=8):
        if url.endswith("/txs"):
            return [tx_in, tx_out]
        return {"chain_stats": {"funded_txo_sum": 100000000, "spent_txo_sum": 41000000, "tx_count": 2}}

    monkeypatch.setattr(ms, "_get_json", fake_get)
    ms._cache.clear()

    out = await ms.address_lookup("bitcoin", "addrA")
    assert out["supported"] is True
    assert out["chain"] == "bitcoin"
    assert out["balance"] == {"amount": 0.59, "unit": "BTC"}
    assert out["tx_count"] == 2
    txs = out["transactions"]
    assert txs[0]["direction"] == "in" and txs[0]["amount"] == pytest.approx(1.0)
    assert txs[0]["counterparties"] == [{"address": "addrB", "role": "sender", "amount": pytest.approx(1.005)}]
    assert txs[1]["direction"] == "out" and txs[1]["amount"] == pytest.approx(0.41)
    # The change output back to addrA itself must not appear as a counterparty.
    assert txs[1]["counterparties"] == [{"address": "addrC", "role": "recipient", "amount": pytest.approx(0.4)}]


@pytest.mark.asyncio
async def test_btc_lookup_chain_alias_and_cache_reuse(monkeypatch):
    calls = {"n": 0}

    async def fake_get(url, timeout=8):
        calls["n"] += 1
        if url.endswith("/txs"):
            return []
        return {"chain_stats": {"funded_txo_sum": 100, "spent_txo_sum": 0, "tx_count": 0}}

    monkeypatch.setattr(ms, "_get_json", fake_get)
    ms._cache.clear()

    first = await ms.address_lookup("btc", "addrX")   # alias for "bitcoin"
    second = await ms.address_lookup("bitcoin", "addrX")
    assert first == second
    assert calls["n"] == 2  # only the first lookup hit the network; second served from cache


@pytest.mark.asyncio
async def test_sol_address_lookup_annotates_native_transfer(monkeypatch):
    async def fake_rpc(method, params, timeout=10):
        if method == "getBalance":
            return {"value": 2_000_000_000}
        if method == "getSignaturesForAddress":
            return [{"signature": "sig1", "blockTime": 1700000000, "confirmationStatus": "finalized"}]
        if method == "getTransaction":
            return {
                "transaction": {"message": {"accountKeys": [{"pubkey": "solA"}, {"pubkey": "solB"}]}},
                "meta": {"preBalances": [3_000_000_000, 500_000_000], "postBalances": [2_000_000_000, 1_500_000_000], "fee": 5000},
            }
        return None

    monkeypatch.setattr(ms, "_sol_rpc", fake_rpc)
    ms._cache.clear()

    out = await ms.address_lookup("solana", "solA")
    assert out["supported"] is True
    assert out["balance"] == {"amount": 2.0, "unit": "SOL"}
    tx = out["transactions"][0]
    assert tx["direction"] == "out" and tx["amount"] == pytest.approx(1.0)
    assert tx["counterparties"] == [{"address": "solB", "role": "recipient", "amount": pytest.approx(1.0)}]


@pytest.mark.asyncio
async def test_address_lookup_unsupported_chain_fails_soft():
    ms._cache.clear()
    out = await ms.address_lookup("ethereum", "0xdead")
    assert out == {
        "chain": "ethereum", "address": "0xdead", "supported": False,
        "reason": "Chain 'ethereum' not supported for live trace yet.", "transactions": [],
    }


@pytest.mark.asyncio
async def test_wallet_trace_endpoint_returns_lookup(client, monkeypatch):
    async def fake_lookup(chain, address):
        return {"chain": chain, "address": address, "supported": True, "source": "mempool.space",
                "balance": {"amount": 1.0, "unit": "BTC"}, "tx_count": 0, "transactions": []}
    monkeypatch.setattr(ms, "address_lookup", fake_lookup)
    r = await client.get("/api/intel/trace/bitcoin/addrA")
    assert r.status_code == 200, r.text
    assert r.json()["balance"] == {"amount": 1.0, "unit": "BTC"}


@pytest.mark.asyncio
async def test_debate_endpoints_removed(client):
    assert (await client.get("/api/intel/debate")).status_code == 404
    assert (await client.get("/api/debate")).status_code == 404


# ── /api/trading/positions integration (live-valued) ──────────────────────────────

def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


# Isolated agents come from the conftest `fresh_agent` fixture (direct DB insert,
# no rate limit) since the session agent is shared and would contaminate totals.


@pytest.mark.asyncio
async def test_positions_live_valuation(client, registered_agent, monkeypatch):
    # Deterministic, mutable "live" price shared by paper-fill and positions valuation.
    holder = {"p": 100.0}

    async def fake_resolve(symbol):
        return holder["p"]

    monkeypatch.setattr(ms, "resolve_price", fake_resolve)

    # Log a BUY limit order and paper-fill it at 100.
    r = await client.post("/api/trading/orders", headers=_h(registered_agent), json={
        "symbol": "BTC", "side": "buy", "chain": "bitcoin",
        "quantity": 2, "price": 100.0, "order_type": "limit",
    })
    assert r.status_code == 200, r.text
    oid = r.json()["id"]
    rf = await client.post(f"/api/trading/orders/{oid}/paper-fill", headers=_h(registered_agent))
    assert rf.status_code == 200, rf.text

    # Price moves up to 150 → +50% unrealized on a 2-unit position.
    holder["p"] = 150.0
    rp = await client.get("/api/trading/positions", headers=_h(registered_agent))
    assert rp.status_code == 200, rp.text
    body = rp.json()
    btc = next(p for p in body["positions"] if p["symbol"] == "BTC")
    assert btc["net_quantity"] == 2
    assert btc["avg_cost"] == pytest.approx(100.0, abs=0.01)
    assert btc["live_price"] == 150.0
    assert btc["market_value_usd"] == pytest.approx(300.0, abs=0.01)
    assert btc["unrealized_pnl_usd"] == pytest.approx(100.0, abs=0.01)
    assert btc["unrealized_pnl_pct"] == pytest.approx(50.0, abs=0.1)


@pytest.mark.asyncio
async def test_positions_requires_agent_key(client):
    r = await client.get("/api/trading/positions")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_portfolio_realized_and_unrealized_pnl(client, fresh_agent, monkeypatch):
    """Avg-cost book: buy 2@100, buy 2@200 (avg 150), sell 2@300 (realized +300),
    then value the remaining 2 at 300 (unrealized +300)."""
    holder = {"p": 100.0}

    async def fake_resolve(symbol):
        return holder["p"]

    monkeypatch.setattr(ms, "resolve_price", fake_resolve)
    h = _h(await fresh_agent())

    async def fill(side, qty):
        r = await client.post("/api/trading/orders", headers=h, json={
            "symbol": "ETH", "side": side, "chain": "base", "quantity": qty, "order_type": "market"})
        oid = r.json()["id"]
        rf = await client.post(f"/api/trading/orders/{oid}/paper-fill", headers=h)
        assert rf.status_code == 200, rf.text

    holder["p"] = 100.0; await fill("buy", 2)
    holder["p"] = 200.0; await fill("buy", 2)
    holder["p"] = 300.0; await fill("sell", 2)

    holder["p"] = 300.0
    r = await client.get("/api/trading/portfolio", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    eth = next(p for p in body["positions"] if p["symbol"] == "ETH")
    assert eth["net_quantity"] == pytest.approx(2.0, abs=1e-6)
    assert eth["avg_cost"] == pytest.approx(150.0, abs=0.01)
    assert eth["realized_pnl_usd"] == pytest.approx(300.0, abs=0.5)
    assert eth["unrealized_pnl_usd"] == pytest.approx(300.0, abs=0.5)
    assert body["total_realized_pnl_usd"] == pytest.approx(300.0, abs=0.5)
    assert body["total_pnl_usd"] == pytest.approx(600.0, abs=1.0)


@pytest.mark.asyncio
async def test_auto_snapshot_writes_equity(client, fresh_agent, monkeypatch):
    async def fake_resolve(symbol):
        return 50.0
    monkeypatch.setattr(ms, "resolve_price", fake_resolve)
    h = _h(await fresh_agent())
    r = await client.post("/api/trading/orders", headers=h, json={
        "symbol": "SOL", "side": "buy", "chain": "solana", "quantity": 4, "order_type": "market"})
    oid = r.json()["id"]
    await client.post(f"/api/trading/orders/{oid}/paper-fill", headers=h)

    rs = await client.post("/api/trading/snapshot/auto", headers=h)
    assert rs.status_code == 200, rs.text
    assert rs.json()["portfolio_value_usd"] == pytest.approx(200.0, abs=1.0)  # 4 * 50
    # The equity curve now has a real data point.
    rd = await client.get("/api/trading/performance/daily?days=7", headers=h)
    assert any(abs(row["portfolio_value_usd"] - 200.0) < 1.0 for row in rd.json())
