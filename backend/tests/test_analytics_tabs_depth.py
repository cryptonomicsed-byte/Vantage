"""Analytics tabs audit (2026-11-10): Top 5, Add Tracker, Overview, Trace,
Arbitrage, Alpha, Yields, DEX, All Tokens.

Live findings these pin down:
- Arbitrage compared last-trade prices (not executable), treated 1 USDT as
  1 USD, and Binance answers HTTP 451 from this host on every call.
- /api/intel ran five independent sections one after another (~8.5 s) and
  paid a Pyth 401 round trip twice per call.
- Trace: a public-RPC 429 on getSignaturesForAddress collapsed to "tx_count 0,
  no transactions" for one of Solana's busiest wallets, and was cached.
- All Tokens asked GeckoTerminal for 50 pools and always got 20 (one page).
- Add Tracker accepted anything matching the base58 alphabet + length,
  including Bitcoin legacy addresses, as a Solana wallet.
- Top Wallets to Copy: 12 s per poll; its role counts are 14 days stale.
Network is always mocked.
"""
import asyncio
import time

import pytest
from fastapi import HTTPException

from backend import address_validation as av
from backend import market_sources as ms
from backend.routers import intel


def _q(bid, ask, last=None, fee=0.1, ccy="USD", ts=None, fetched=None):
    return {"bid": bid, "ask": ask, "last": last or (bid + ask) / 2, "quote_ccy": ccy,
            "exchange_ts": ts, "fetched_at": fetched or time.time(),
            "ts_source": "exchange" if ts else "fetch_time", "taker_fee_pct": fee}


# ── Arbitrage ──────────────────────────────────────────────────────────────
def test_arbitrage_buys_at_ask_sells_at_bid_net_of_both_legs():
    now = 1_000_000.0
    xq = {"symbol": "SOL", "missing": {"binance": "HTTP 451 (region-blocked from this host)"},
          "usdt_usd": 0.999, "usdt_usd_measured": True,
          "quotes": {"okx": _q(99.9, 100.0, fee=0.1, ts=now - 2),
                     "kraken": _q(101.0, 101.1, fee=0.4, fetched=now - 5),
                     "gemini": _q(100.5, 100.6, fee=0.4, fetched=now)}}
    o = ms.arbitrage_from_quotes(xq, now)
    assert o["route"] == "okx→kraken" and o["spread_basis"] == "bid_ask"
    assert o["buy_price"] == 100.0 and o["sell_price"] == 101.0          # ask, then bid
    assert o["spread_pct"] == pytest.approx(1.0, abs=1e-3)
    assert o["est_fees_pct"] == pytest.approx(0.5)
    assert o["net_spread_pct"] == pytest.approx(0.5, abs=1e-3)
    assert o["buy_leg"]["quote_age_s"] == 2.0 and o["buy_leg"]["ts_source"] == "exchange"
    assert o["sell_leg"]["quote_age_s"] == 5.0 and o["max_quote_age_s"] == 5.0
    assert o["missing_venues"] == xq["missing"] and o["profitable_after_fees"] is True
    assert {v["venue"] for v in o["venue_quotes"]} == {"okx", "kraken", "gemini"}


def test_arbitrage_negative_when_book_is_tight():
    xq = {"symbol": "BTC", "quotes": {"a": _q(100.0, 100.01), "b": _q(100.0, 100.02)}}
    o = ms.arbitrage_from_quotes(xq)
    assert o["spread_pct"] <= 0 and o["net_spread_pct"] < 0 and o["profitable_after_fees"] is False


def test_arbitrage_falls_back_to_last_and_needs_two_venues():
    assert ms.arbitrage_from_quotes({"symbol": "X", "quotes": {"a": _q(1, 1.1)}}) is None
    xq = {"symbol": "X", "quotes": {"a": {**_q(None, None, last=10.0)}, "b": {**_q(None, None, last=10.5)}}}
    o = ms.arbitrage_from_quotes(xq)
    assert o["spread_basis"] == "last" and o["route"] == "a→b"


@pytest.mark.asyncio
async def test_exchange_quotes_converts_usdt_and_skips_geoblocked_venue(monkeypatch):
    calls = []

    async def fake(url, timeout=5.0, client=None):
        calls.append(url)
        if "binance" in url:
            return None, 451, "HTTP 451"
        if "kraken" in url and "USDTUSD" in url:
            return {"error": [], "result": {"USDTZUSD": {"a": ["0.9990"], "b": ["0.9988"], "c": ["0.9989"]}}}, 200, None
        if "kraken" in url:
            return {"error": [], "result": {"SOLUSD": {"a": ["82.10"], "b": ["82.00"], "c": ["82.05"]}}}, 200, None
        if "okx" in url:
            return {"data": [{"bidPx": "82.0", "askPx": "82.1", "last": "82.05", "ts": "1794328000000"}]}, 200, None
        return None, 500, "HTTP 500"
    monkeypatch.setattr(ms, "_get_json_status", fake)
    ms._cache.clear(); ms._VENUE_DOWN.clear()
    out = await ms.exchange_quotes("SOL")
    assert set(out["quotes"]) == {"okx", "kraken"}
    assert out["usdt_usd"] == pytest.approx(0.9989) and out["usdt_usd_measured"] is True
    assert out["quotes"]["okx"]["bid"] == pytest.approx(82.0 * 0.9989)   # USDT book in USD
    assert out["quotes"]["okx"]["ts_source"] == "exchange"
    assert "451" in out["missing"]["binance"]
    ms._cache.clear()
    calls.clear()
    await ms.exchange_quotes("SOL")
    assert not any("binance" in u for u in calls)      # remembered, not re-hit every 20 s


# ── Alpha ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_alpha_exposes_inputs_source_age_and_thin_volume_flag(monkeypatch):
    async def cp(n):
        return [{"symbol": "RAIN", "name": "Rain", "id": "rain-rain", "rank": 41, "price": 0.0038,
                 "change_24h": -18.0, "volume_24h": 670_000, "market_cap": 2.7e9,
                 "last_updated": "2026-11-10T16:29:13Z"},
                {"symbol": "OK", "name": "Ok", "id": "ok-ok", "rank": 3, "price": 1.0,
                 "change_24h": 4.0, "volume_24h": 5e8, "market_cap": 1e10, "last_updated": None}]
    monkeypatch.setattr(ms, "coinpaprika_markets", cp)
    rows = await ms.top_movers(2)
    r = rows[0]
    assert r["symbol"] == "RAIN" and r["signal"] == "breakdown" and r["conviction"] == 3.6  # unchanged
    assert r["source"] == "coinpaprika" and r["source_id"] == "rain-rain"
    assert r["inputs"]["change_24h"] == -18.0 and "min(5" in r["inputs"]["conviction_formula"]
    assert r["quote_age_s"] is not None and r["flags"] == ["thin volume vs market cap"]
    assert rows[1]["flags"] == [] and rows[1]["quote_age_s"] is None


# ── Yields ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_yields_link_filters_and_sorts(monkeypatch):
    async def fake_get(url, timeout=8.0, retries=2):
        return {"data": [
            {"pool": "p-1", "symbol": "USDC", "project": "aave", "chain": "Ethereum", "apy": 5.0,
             "apyBase": 5.0, "apyReward": None, "tvlUsd": 9e8, "stablecoin": True, "ilRisk": "no",
             "exposure": "single", "apyMean30d": 4.8, "apyPct7D": -0.2},
            {"pool": "p-2", "symbol": "USDC-AERO", "project": "aerodrome", "chain": "Base", "apy": 250.0,
             "apyBase": None, "apyReward": 250.0, "tvlUsd": 4e6, "stablecoin": False, "ilRisk": "yes",
             "rewardTokens": ["aero"]},
            {"pool": "p-3", "symbol": "JUNK", "project": "x", "chain": "Base", "apy": 9e6, "tvlUsd": 5e7,
             "outlier": True}]}
    monkeypatch.setattr(ms, "_get_json", fake_get)
    ms._cache.clear()
    rows = await ms.defillama_yields(10)
    assert [r["pool"] for r in rows] == ["USDC-AERO", "USDC"]           # default unchanged: APY, outlier dropped
    assert rows[0]["url"] == "https://defillama.com/yields/pool/p-2" and rows[0]["reward_share_pct"] == 100.0
    assert rows[1]["apy_mean_30d"] == 4.8 and rows[1]["exposure"] == "single"
    assert [r["pool"] for r in await ms.defillama_yields(10, stablecoin_only=True)] == ["USDC"]
    assert [r["pool"] for r in await ms.defillama_yields(10, chain="base")] == ["USDC-AERO"]
    assert [r["pool"] for r in await ms.defillama_yields(10, sort="tvl")] == ["USDC", "USDC-AERO"]
    assert [r["pool"] for r in await ms.defillama_yields(10, min_apy_base=1)] == ["USDC"]


# ── DEX ────────────────────────────────────────────────────────────────────
_DS_PAIR = {"chainId": "solana", "dexId": "raydium", "url": "https://dexscreener.com/solana/pairA",
            "pairAddress": "PairAddrAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "baseToken": {"address": "MintAAAA", "symbol": "WIF"}, "quoteToken": {"address": "So1", "symbol": "SOL"},
            "priceUsd": "1.23", "priceNative": "0.015", "liquidity": {"usd": 5e6},
            "volume": {"h24": 9e6, "h1": 2e5}, "priceChange": {"h24": -3.0, "h1": 0.5},
            "txns": {"h24": {"buys": 900, "sells": 800}}, "pairCreatedAt": 1700000000000, "fdv": 1.2e9,
            "marketCap": 1.2e9}


@pytest.mark.asyncio
async def test_dex_search_rows_carry_pair_address_age_and_flow(monkeypatch):
    async def fake_get(url, timeout=10):
        return {"pairs": [_DS_PAIR]}
    monkeypatch.setattr(ms, "_get_json", fake_get)
    ms._cache.clear()
    r = (await ms.dexscreener_search("WIF"))[0]
    assert r["pair_address"] == _DS_PAIR["pairAddress"] and r["pair_created_at"] == 1700000000000
    assert (r["buys_24h"], r["sells_24h"]) == (900, 800) and r["url"].endswith("pairA")


@pytest.mark.asyncio
async def test_dex_pair_is_priced_by_pair_address_only(monkeypatch):
    other = {**_DS_PAIR, "pairAddress": "SomeOtherPairBBBBBBBBBBBBBBBBBBBBBBBBBBBBB", "priceUsd": "99"}
    seen = []

    async def fake_get(url, timeout=8):
        seen.append(url)
        return {"pairs": [other, _DS_PAIR]}
    monkeypatch.setattr(ms, "_get_json", fake_get)
    ms._cache.clear()
    row = await ms.dexscreener_pair("solana", _DS_PAIR["pairAddress"])
    assert row["price_usd"] == 1.23 and seen[0].endswith(f"/pairs/solana/{_DS_PAIR['pairAddress']}")
    assert await ms.dexscreener_pair("solana", "../../etc") is None       # never forwarded
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_all_tokens_pages_past_twenty(monkeypatch):
    urls = []

    def page(n, k):
        return [{"attributes": {"name": f"T{n}{i} / SOL", "address": f"pool{n}{i}", "reserve_in_usd": "1000",
                                "fdv_usd": "5000"},
                 "relationships": {"base_token": {"data": {"id": f"solana_mint{n}{i}"}},
                                   "dex": {"data": {"id": "pumpswap"}}}} for i in range(k)]

    async def fake_get(url, timeout=10):
        urls.append(url)
        n = int(url.rsplit("page=", 1)[1])
        return {"data": page(n, 20 if n < 3 else 7)}
    monkeypatch.setattr(ms, "_get_json", fake_get)
    ms._cache.clear()
    rows = await ms.dex_new_pools("solana", "trending", 50)
    assert len(urls) == 3 and len(rows) == 47
    assert rows[0]["rank"] == 1 and rows[-1]["rank"] == 47
    assert rows[0]["dex"] == "pumpswap" and rows[0]["fdv_usd"] == 5000.0 and rows[0]["base_address"] == "mint10"
    urls.clear(); ms._cache.clear()
    await ms.dex_new_pools("solana", "trending", 10)
    assert len(urls) == 1                                                  # small asks stay one call


# ── Trace ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_sol_trace_reports_rpc_failure_instead_of_empty_history(monkeypatch):
    async def fake_rpc(method, params, timeout=10):
        if method == "getBalance":
            return {"value": 9_300_000 * 10**9}
        ms._SOL_RPC_LAST_ERROR[method] = f"{method}: HTTP 429"
        return None
    monkeypatch.setattr(ms, "_sol_rpc", fake_rpc)
    ms._cache.clear()
    out = await ms.address_lookup("solana", "busyWallet")
    assert out["balance"]["amount"] == 9_300_000 and out["tx_count"] is None
    assert "429" in out["rpc_error"] and "unavailable" in out["reason"]
    assert ms._cache_get("trace:solana:busyWallet", 20) is None             # not pinned for the TTL


@pytest.mark.asyncio
async def test_sol_trace_flags_partial_transaction_fetch(monkeypatch):
    async def fake_rpc(method, params, timeout=10):
        if method == "getBalance":
            return {"value": 1}
        if method == "getSignaturesForAddress":
            return [{"signature": "s1"}, {"signature": "s2"}]
        if method == "getTransaction" and params[0] == "s1":
            return {"transaction": {"message": {"accountKeys": [{"pubkey": "w"}, {"pubkey": "x"}]}},
                    "meta": {"preBalances": [2, 0], "postBalances": [1, 1], "fee": 0}}
        return None
    monkeypatch.setattr(ms, "_sol_rpc", fake_rpc)
    ms._cache.clear()
    out = await ms.address_lookup("solana", "w")
    assert out["tx_count"] == 2 and len(out["transactions"]) == 1
    assert out["partial"] is True and out["tx_fetch_failed"] == 1


@pytest.mark.asyncio
async def test_sol_rpc_falls_through_to_next_endpoint(monkeypatch):
    seen = []

    class _R:
        def __init__(self, code, body=None):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            seen.append(url)
            if "gate" in url:
                raise ms.httpx.ConnectError("refused")
            return _R(200, {"result": {"value": 5}})
    monkeypatch.setattr(ms, "_SOL_RPC_URLS", ["http://gate", "https://public"])
    monkeypatch.setattr(ms.httpx, "AsyncClient", _C)
    assert await ms._sol_rpc("getBalance", ["w"]) == {"value": 5}
    assert seen == ["http://gate", "https://public"]


@pytest.mark.asyncio
async def test_trace_labels_counterparties(monkeypatch):
    from backend import wallet_labels as wl
    from backend.db import get_db

    async def fake_lookup(chain, address):
        return {"chain": "solana", "address": address, "supported": True, "source": "solana-rpc",
                "balance": {"amount": 1, "unit": "SOL"}, "tx_count": 1,
                "transactions": [{"direction": "in", "amount": 1,
                                  "counterparties": [{"address": "CPTY_SNIPER_1", "role": "sender", "amount": 1}],
                                  "token_transfers": [{"mint": "m", "counterparties": [
                                      {"address": "CPTY_PLAIN_2", "role": "sender", "amount": 1}]}]}]}
    monkeypatch.setattr(ms, "address_lookup", fake_lookup)
    async with get_db() as db:
        await wl._ensure(db)
        await db.execute("INSERT OR REPLACE INTO wallet_labels (wallet, label, evidence, first_seen, updated_at) "
                         "VALUES ('CPTY_SNIPER_1', 'sniper', '[]', 1, 1)")
        await db.commit()
    labels = await intel._counterparty_labels("solana", "TRACED_W", (await fake_lookup("solana", "TRACED_W"))["transactions"])
    assert labels.get("CPTY_SNIPER_1") == ["sniper"] and "CPTY_PLAIN_2" not in labels


# ── Overview ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_overview_sections_run_concurrently_and_report_failures(monkeypatch):
    async def slow(v, d=0.3):
        await asyncio.sleep(d)
        return v

    async def boom():
        await asyncio.sleep(0.3)
        raise RuntimeError("venue scan exploded")
    monkeypatch.setattr(intel, "_majors", lambda: slow((70000.0, 2000.0, 80.0)))
    monkeypatch.setattr(intel, "full_market", lambda limit=20: slow({"tokens": [{"symbol": "BTC", "price": 1}],
                                                                     "market_snapshot": {"price_source": "coinpaprika"}}))
    monkeypatch.setattr(intel, "_chain_health", lambda: slow({"solana": {"health": "healthy", "block_height": 5}}))
    monkeypatch.setattr(ms, "real_arbitrage", boom)
    monkeypatch.setattr(ms, "market_breadth", lambda: slow({}))
    t0 = time.monotonic()
    out = await intel.get_intel(agent={})
    assert time.monotonic() - t0 < 0.9                                     # concurrent, not 1.5 s serial
    s = out["sections"]
    assert s["arbitrage"]["ok"] is False and "exploded" in s["arbitrage"]["error"]
    assert s["sentiment"]["ok"] is False and s["sentiment"]["error"] == "source returned no data"
    assert s["majors"]["ok"] and s["chain_health"]["ok"]
    assert out["arbitrage"]["opportunities"] == [] and out["anomalies"]["fusion"]["btc_consensus"] == 70000.0


@pytest.mark.asyncio
async def test_pyth_refusal_is_remembered(monkeypatch):
    intel._PYTH_REFUSED.update(until=0.0, status=None)
    intel._note_pyth_status(401)
    assert intel._pyth_refused()

    class NoNet:
        def __init__(self, *a, **k):
            raise AssertionError("Pyth must not be called while it is refusing")
    monkeypatch.setattr(intel.httpx, "AsyncClient", NoNet)
    assert await intel._fetch_prices_batch(["x"]) == {}
    intel._PYTH_REFUSED.update(until=0.0, status=None)


# ── Add Tracker ────────────────────────────────────────────────────────────
_SOL_OK = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
_BTC_LEGACY = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"


def test_address_validation_per_chain():
    assert av.validate_solana(_SOL_OK)["valid"]
    assert not av.validate_solana(_SOL_OK[:-1] + "0")["valid"]                 # 0 is not base58
    assert intel._SOL_ADDR_RE.match(_BTC_LEGACY)                              # the old check passed it...
    assert not av.validate_solana(_BTC_LEGACY)["valid"]                        # ...but it isn't a key
    assert av.validate_bitcoin(_BTC_LEGACY)["valid"]
    assert not av.validate_bitcoin(_BTC_LEGACY[:-1] + "3")["valid"]
    assert av.validate_bitcoin("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")["valid"]
    assert not av.validate_bitcoin("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdx")["valid"]
    assert av.validate_handle("twitter", "@solana_dev")["valid"]
    assert not av.validate_handle("twitter", "a" * 16)["valid"]
    assert not av.validate_handle("telegram", "abc")["valid"]


def test_evm_checksum():
    good = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
    v = av.validate_evm(good)
    assert v["valid"] and v["checksum"] in ("ok", "unverified")
    if v["checksum"] == "ok":
        bad = good.replace("dA6B", "Da6B")                                     # one case flipped
        assert av.validate_evm(bad)["checksum"] == "bad" and not av.validate_evm(bad)["valid"]
    assert av.validate_evm(good.lower())["checksum"] == "absent"
    assert not av.validate_evm("0x123")["valid"]


@pytest.mark.asyncio
async def test_preview_flags_token_mint_and_writes_nothing(monkeypatch):
    async def fake_rpc(method, params, timeout=10):
        assert method == "getAccountInfo"
        return {"value": {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "lamports": 1461600,
                          "executable": False, "data": {"parsed": {"type": "mint", "info": {}}}}}
    monkeypatch.setattr(ms, "_sol_rpc", fake_rpc)
    out = await intel.ingest_preview(intel.IngestRequest(input=_SOL_OK, label="x"), agent={})
    assert out["valid"] and out["account"]["kind"] == "token_mint" and out["can_submit"]
    assert any("token mint" in w for w in out["warnings"])
    assert out["will_track"]["table"] == "tracked_wallets" and out["will_track"]["label"] == "x"


@pytest.mark.asyncio
async def test_preview_and_ingest_reject_bitcoin_as_solana(monkeypatch):
    out = await intel.ingest_preview(intel.IngestRequest(input=_BTC_LEGACY), agent={})
    assert out["valid"] is False and out["can_submit"] is False and "Bitcoin" in out["reason"]
    with pytest.raises(HTTPException) as e:
        await intel.ingest(intel.IngestRequest(input=_BTC_LEGACY), agent={})
    assert e.value.status_code == 422 and "Solana" in str(e.value.detail)


# ── Top 5: why each copy-wallet ranks ──────────────────────────────────────
@pytest.mark.asyncio
async def test_copy_wallets_explain_rank_from_live_roles(monkeypatch):
    from backend.routers import degen

    async def ok(_):
        return {"id": 1}
    monkeypatch.setattr(degen, "get_agent", ok)
    degen._RESP_CACHE.clear()
    async with degen._db() as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS wallet_reputation (
            wallet_address TEXT PRIMARY KEY, chain TEXT DEFAULT 'solana', display_name TEXT DEFAULT '',
            name_source TEXT DEFAULT '', name_confidence REAL DEFAULT 0, copy_trade_score REAL DEFAULT 0,
            first_buyer_count INTEGER DEFAULT 0, top_trader_count INTEGER DEFAULT 0,
            top_holder_count INTEGER DEFAULT 0, deployer_count INTEGER DEFAULT 0,
            tokens_tracked INTEGER DEFAULT 0, currently_hot_tokens INTEGER DEFAULT 0,
            reasoning TEXT DEFAULT '', updated_at TEXT DEFAULT (datetime('now')),
            verified_call_count INTEGER DEFAULT 0, verified_avg_return_pct REAL DEFAULT 0)""")
        cur = await db.execute("PRAGMA table_info(wallet_reputation)")
        have = {r["name"] for r in await cur.fetchall()}
        for col, decl in (("deployer_count", "INT"), ("tokens_tracked", "INT"), ("updated_at", "TEXT"),
                          ("first_buyer_count", "INT"), ("top_trader_count", "INT"), ("top_holder_count", "INT")):
            if col not in have:
                await db.execute(f"ALTER TABLE wallet_reputation ADD COLUMN {col} {decl}")
        await db.execute("""CREATE TABLE IF NOT EXISTS token_wallet_roles (id INTEGER PRIMARY KEY, mint TEXT,
            symbol TEXT, wallet_address TEXT, role TEXT, rank INTEGER, metric REAL, metric_label TEXT,
            discovered_at TEXT DEFAULT (datetime('now')))""")
        await db.execute("DELETE FROM wallet_reputation WHERE wallet_address='WHY_W1'")
        await db.execute("DELETE FROM token_wallet_roles WHERE wallet_address='WHY_W1'")
        await db.execute("INSERT INTO wallet_reputation (wallet_address, copy_trade_score, first_buyer_count, "
                         "top_trader_count, top_holder_count, deployer_count, tokens_tracked, updated_at) "
                         "VALUES ('WHY_W1', 999999, 0, 1, 5, 0, 6, '2026-10-27T22:27:53+00:00')")
        for i in range(3):
            await db.execute("INSERT INTO token_wallet_roles (mint, symbol, wallet_address, role, discovered_at) "
                             "VALUES (?, 'S', 'WHY_W1', 'first_buyer', '2026-11-10 04:36:32')", (f"why_m{i}",))
        await db.commit()
    out = await degen.top_wallets_to_copy(limit=1, x_agent_key="k")
    w = out["wallets"][0]
    assert w["wallet_address"] == "WHY_W1"
    why = w["why"]
    assert why["live_roles"]["first_buyer"] == 3 and why["stored_roles"]["first_buyer"] == 0
    assert why["roles_changed_since_scoring"] is True and why["score_age_h"] > 24
    assert why["roles_last_seen_at"] == "2026-11-10 04:36:32" and why["roles_last_seen_age_h"] is not None
    assert why["win_rate"] is None and why["win_rate_unavailable"]
    assert out["scores_age_h"] is not None
    # second poll inside the TTL is served from cache (was a 12 s query each time)
    monkeypatch.setattr(degen, "_copy_wallet_why", None)                     # would raise if re-run
    assert (await degen.top_wallets_to_copy(limit=1, x_agent_key="k"))["wallets"][0]["wallet_address"] == "WHY_W1"
    degen._RESP_CACHE.clear()
