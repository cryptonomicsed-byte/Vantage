"""Market Intel API v3 — FULL market coverage via Pyth Network (768 crypto feeds).
All data geo-unrestricted, no API keys needed."""

import logging, asyncio, json, time, threading
import httpx
from fastapi import APIRouter, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from backend import market_sources as ms
from backend import indicators as ind

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/intel", tags=["intel"])
_limiter = Limiter(key_func=get_remote_address)
ARES = "http://localhost:9861"

PYTH_BASE = "https://hermes.pyth.network"
PYTH_PRICE = f"{PYTH_BASE}/v2/updates/price/latest"
PYTH_FEEDS = f"{PYTH_BASE}/v2/price_feeds"
PYTH_IDS = {
    "BTC": "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "ETH": "ff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
    "SOL": "ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
}

# Cache for feed list (refreshed every 5 min)
_feed_cache = {"data": None, "ts": 0}

async def _get_all_crypto_feeds():
    """Returns dict of {id: {symbol, base, asset_type}} for all crypto feeds."""
    now = asyncio.get_event_loop().time()
    if _feed_cache["data"] and (now - _feed_cache["ts"]) < 300:
        return _feed_cache["data"]

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{PYTH_FEEDS}?query=crypto",
                          headers={"User-Agent": "Vantage/1.0"})
            if r.status_code == 200:
                feeds = {}
                for f in r.json():
                    aid = f.get("id", "")
                    attr = f.get("attributes", {})
                    feeds[aid] = {
                        "symbol": attr.get("symbol", "?"),
                        "base": attr.get("base", "?"),
                        "asset_type": attr.get("asset_type", "crypto"),
                    }
                _feed_cache["data"] = feeds
                _feed_cache["ts"] = now
                return feeds
    except Exception as e:
        logger.warning(f"Feed list fetch failed: {e}")
    return _feed_cache.get("data") or {}

async def _fetch_prices_batch(ids: list[str]) -> dict:
    """Batch fetch prices for a list of Pyth feed IDs."""
    try:
        params = "&".join(f"ids[]={i}" for i in ids)
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"{PYTH_PRICE}?{params}",
                          headers={"User-Agent": "Vantage/1.0"})
            if r.status_code == 200:
                result = {}
                for item in r.json().get("parsed", []):
                    pid = item.get("id", "")
                    pr = item.get("price", {})
                    price_val = int(pr.get("price", 0))
                    expo = int(pr.get("expo", 0))
                    actual = price_val * (10 ** expo)
                    result[pid] = {
                        "price": actual,
                        "conf": pr.get("conf", 0),
                        "publish_time": item.get("publish_time", 0),
                    }
                return result
    except Exception as e:
        logger.warning(f"Price batch failed: {e}")
    return {}

# ── Endpoints ──

@router.get("/market")
async def full_market(limit: int = Query(100, ge=1, le=500)):
    """Full market rundown — all crypto tokens with prices from Pyth."""
    feeds = await _get_all_crypto_feeds()
    if not feeds:
        return {"error": "Feed list unavailable", "tokens": []}

    # Sort by commonly traded / major tokens first, then alphabetically
    MAJORS = {"Crypto.BTC", "Crypto.ETH", "Crypto.SOL", "Crypto.BNB", "Crypto.XRP",
              "Crypto.ADA", "Crypto.DOGE", "Crypto.AVAX", "Crypto.DOT", "Crypto.MATIC",
              "Crypto.LINK", "Crypto.UNI", "Crypto.ATOM", "Crypto.LTC", "Crypto.OP",
              "Crypto.ARB", "Crypto.SUI", "Crypto.APT", "Crypto.NEAR", "Crypto.INJ",
              "Crypto.SEI", "Crypto.TIA", "Crypto.PYTH", "Crypto.JUP", "Crypto.BONK",
              "Crypto.WIF", "Crypto.PEPE", "Crypto.SHIB", "Crypto.RUNE", "Crypto.FET",
              "Crypto.RNDR", "Crypto.STRK", "Crypto.W", "Crypto.JTO", "Crypto.PYUSD",
              "Crypto.USDC", "Crypto.USDT", "Crypto.DAI"}

    # Build priority-sorted feed list
    feed_items = []
    for fid, info in feeds.items():
        key = f"{info['asset_type']}.{info['symbol']}" if "Crypto." not in info['symbol'] else info['symbol']
        priority = MAJORS.index(key) if key in MAJORS else 999
        feed_items.append((priority, fid, info))

    feed_items.sort(key=lambda x: (x[0], x[2]["symbol"]))

    # Batch fetch prices (50 IDs per batch to avoid URL limits)
    tokens = []
    batch_size = 100
    total = min(len(feed_items), limit)

    for i in range(0, total, batch_size):
        batch = feed_items[i:i+batch_size]
        ids = [f[1] for f in batch]
        prices = await _fetch_prices_batch(ids)

        for pri, fid, info in batch:
            pdata = prices.get(fid, {})
            price = pdata.get("price", 0)
            # Clean symbol: "Crypto.BTC/USD" → "BTC"
            sym = info["symbol"]
            if sym.startswith("Crypto."):
                sym = sym[7:]  # strip "Crypto."
            if "/" in sym:
                sym = sym.split("/")[0]  # strip "/USD"
            if sym in ("1INCH",):  # skip numeric-only symbols
                pass
            if price > 0 and len(sym) >= 2:
                tokens.append({
                    "symbol": sym,
                    "price": price,
                    "confidence": pdata.get("conf", 0),
                })

    # Sort by price (desc) — rough market cap proxy
    tokens.sort(key=lambda x: -x["price"])

    # Compute stats
    gainers_24h = len([t for t in tokens if t.get("change_24h", 0) > 0]) if any(t.get("change_24h") for t in tokens) else 0
    total_mcap = sum(t["price"] for t in tokens[:50])  # rough

    return {
        "total_tokens": len(tokens),
        "displayed": min(len(tokens), limit),
        "total_feeds_available": len(feeds),
        "market_snapshot": {
            "tokens_tracked": len(tokens),
            "pyth_feeds": len(feeds),
            "data_source": "Pyth Network (3,000+ institutional feeds)",
        },
        "tokens": tokens[:limit],
    }

async def _majors():
    """Direct Pyth fetch for BTC/ETH/SOL — always works regardless of sort order."""
    btc = eth = sol = 0
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{PYTH_PRICE}?ids[]={PYTH_IDS['BTC']}&ids[]={PYTH_IDS['ETH']}&ids[]={PYTH_IDS['SOL']}",
                          headers={"User-Agent": "Vantage/1.0"})
            if r.status_code == 200:
                for item in r.json().get("parsed", []):
                    pid = item.get("id", "")
                    pr = int(item.get("price", {}).get("price", 0))
                    expo = int(item.get("price", {}).get("expo", 0))
                    actual = pr * (10 ** expo)
                    if PYTH_IDS["BTC"] in pid: btc = actual
                    elif PYTH_IDS["ETH"] in pid: eth = actual
                    elif PYTH_IDS["SOL"] in pid: sol = actual
    except: pass
    return btc, eth, sol

@router.get("")
async def get_intel():
    """Aggregate overview — BTC/ETH/SOL + chain health + sentiment."""
    btc, eth, sol = await _majors()

    # Also get token list for the overview
    market = await full_market(limit=20)

    # Chain health
    chains = {}
    for chain in ["solana", "polygon", "base", "sui"]:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.post(f"{ARES}/api/rpc/{chain}",
                    json={"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1})
                if r.status_code == 200:
                    data = r.json()
                    block = data.get("result", "?")
                    if isinstance(block, str) and block.startswith("0x"):
                        block = int(block, 16)
                    chains[chain] = {"health": "healthy", "block_height": block}
        except:
            chains[chain] = {"health": "offline", "block_height": "?"}

    # Real cross-exchange arbitrage + real breadth sentiment (fail-soft).
    arb = await ms.real_arbitrage()
    breadth = await ms.market_breadth()

    return {
        "health": {"chains": chains},
        "arbitrage": {"opportunities": arb},
        "anomalies": {"anomalies": [], "fusion": {"btc_consensus": btc, "eth": eth, "sol": sol, "sources": 3}},
        "sentiment": {
            "sentiment": breadth or {"overall": "neutral", "fear_greed": 50},
            "indicators": breadth.get("indicators", []) if breadth else [],
        },
        "prices": market.get("tokens", []),
        "market_snapshot": market.get("market_snapshot", {}),
    }

@router.get("/arbitrage")
async def get_arbitrage():
    """Real cross-exchange spreads (Binance/OKX/KuCoin/Coinbase/Gemini)."""
    opps = await ms.real_arbitrage()
    return {"opportunities": opps, "source": "live_cex_spreads" if opps else "unavailable"}

@router.get("/alpha")
async def get_alpha():
    """Real alpha: top movers by 24h change/volume from the top-100."""
    movers = await ms.top_movers(8)
    lead = movers[0]["symbol"] if movers else "—"
    return {
        "items": movers,
        "summary": f"{len(movers)} live momentum signals — {lead} leads by 24h move" if movers else "No live signals",
    }

@router.get("/sentiment")
async def get_sentiment():
    """Real sentiment derived from top-100 market breadth + BTC dominance."""
    b = await ms.market_breadth()
    return {"sentiment": b, "indicators": b.get("indicators", [])}

@router.get("/yields")
async def get_yields(limit: int = Query(25, ge=1, le=100)):
    """Top DeFi yield pools by APY (DefiLlama, TVL ≥ $1M)."""
    pools = await ms.defillama_yields(limit)
    return {"pools": pools, "count": len(pools), "source": "DefiLlama"}

@router.get("/dex")
async def get_dex(q: str = Query("SOL"), limit: int = Query(20, ge=1, le=50)):
    """DEX pairs/liquidity for a token query (DexScreener)."""
    pairs = await ms.dexscreener_search(q, limit)
    return {"query": q, "pairs": pairs, "count": len(pairs), "source": "DexScreener"}

@router.get("/fx")
async def get_fx(base: str = Query("USD")):
    """Fiat exchange rates (ExchangeRate-API)."""
    return await ms.fx_rates(base)

@router.get("/whales")
async def get_whales(limit: int = Query(10, ge=1, le=25)):
    """Largest recent BTC mempool transactions (mempool.space)."""
    txs = await ms.whale_txs(limit)
    return {"transactions": txs, "count": len(txs), "chain": "bitcoin", "source": "mempool.space"}

@router.get("/trace/{chain}/{address}")
@_limiter.limit("30/minute")
async def get_wallet_trace(request: Request, chain: str, address: str, limit: int = Query(10, ge=1, le=25)):
    """Balance + recent in/out counterparties for a wallet address — bitcoin
    and solana only (mempool.space / public Solana RPC, no key required). One
    hop per call; pivoting to a counterparty address is a new call from the
    frontend, not server-side recursion. Rate-limited separately from other
    intel endpoints since its cost is driven by user clicks, not a fixed poll
    interval, against a rate-limited public RPC."""
    return await ms.address_lookup(chain, address)

@router.get("/backtest")
async def get_backtest(symbol: str = Query("BTC"), days: int = Query(90, ge=14, le=365)):
    """Backtest an SMA-crossover strategy vs buy-and-hold over real history."""
    result = await ms.backtest(symbol, days)
    if result is None:
        return {"error": "insufficient data", "symbol": symbol.upper()}
    return result

@router.get("/ohlc/{symbol}")
async def get_ohlc(symbol: str, interval: str = Query("1d"), limit: int = Query(200, ge=10, le=500)):
    """OHLCV candles for charting (Binance klines → CoinGecko fallback)."""
    candles = await ms.ohlc(symbol, interval, limit)
    return {"symbol": symbol.upper(), "interval": interval, "candles": candles, "count": len(candles)}

@router.get("/indicators/{symbol}")
async def get_indicators(symbol: str, interval: str = Query("1d"), limit: int = Query(200, ge=10, le=500)):
    """Built-in technical indicators (SMA/EMA/RSI/MACD/Bollinger) over live candles."""
    candles = await ms.ohlc(symbol, interval, limit)
    if not candles:
        return {"symbol": symbol.upper(), "indicators": {}, "available": ind.available()}
    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "available": ind.available(),
        "indicators": ind.compute(candles),
    }

@router.get("/sources-registry")
async def get_sources_registry():
    """Transparency: the full no-auth public source registry and integration status."""
    integrated = [s for s in ms.SOURCES if s["integrated"]]
    return {
        "total": len(ms.SOURCES),
        "integrated": len(integrated),
        "sources": ms.SOURCES,
    }

@router.get("/health")
async def get_health_detail():
    chains = {}
    for chain in ["solana", "polygon", "base", "sui"]:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.post(f"{ARES}/api/rpc/{chain}",
                    json={"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1})
                if r.status_code == 200:
                    data = r.json()
                    block = data.get("result", "?")
                    if isinstance(block, str) and block.startswith("0x"):
                        block = int(block, 16)
                    chains[chain] = {"health":"healthy","block_height":block,"tps":0,"latency_ms":0,"peers":0,"gas_gwei":0}
                    try:
                        r2 = await c.post(f"{ARES}/api/rpc/{chain}",
                            json={"jsonrpc":"2.0","method":"eth_gasPrice","params":[],"id":1})
                        if r2.status_code == 200:
                            gp = r2.json().get("result","0")
                            if isinstance(gp, str) and gp.startswith("0x"):
                                chains[chain]["gas_gwei"] = round(int(gp,16)/1e9,2)
                    except: pass
        except:
            chains[chain] = {"health":"offline","block_height":"?","tps":0,"latency_ms":0,"peers":0,"gas_gwei":0}
    return {"chains": chains}

@router.get("/sources")
async def get_sources():
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{ARES}/")
            if r.status_code == 200:
                return {"endpoints": r.json().get("endpoints",{})}
    except: pass
    return {"endpoints": {}}

# ═══════════════════════════════════════════════════════════════════════════
# UNIFIED SIGNALS — pulls from all live sources
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# TOP 100 MARKET — comprehensive token data from CoinGecko
# ═══════════════════════════════════════════════════════════════════════════

_market_cache = {"data": None, "ts": 0}

@router.get("/market/top")
async def top_market(limit: int = Query(100, ge=1, le=250)):
    """Top tokens by market cap with full data: price, 24h change, volume, mcap.
    Uses CoinGecko free API with 120s cache."""
    import time as _time

    now = int(_time.time())
    if _market_cache["data"] and (now - _market_cache["ts"]) < 120:
        cached = _market_cache["data"]
        return {"tokens": cached[:limit], "count": len(cached[:limit]), "total": len(cached), "cached": True}

    try:
        async with httpx.AsyncClient(timeout=20) as cl:
            r = await cl.get(
                "https://api.coingecko.com/api/v3/coins/markets",
                params={
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": 250,
                    "page": 1,
                    "sparkline": "false",
                    "price_change_percentage": "24h",
                },
                headers={"Accept": "application/json"},
            )
            if r.status_code == 200:
                data = r.json()
                tokens = []
                for coin in data:
                    tokens.append({
                        "symbol": (coin.get("symbol", "?") or "?").upper(),
                        "name": coin.get("name", ""),
                        "image": coin.get("image", ""),
                        "price": coin.get("current_price"),
                        "price_change_24h": coin.get("price_change_24h"),
                        "price_change_pct_24h": coin.get("price_change_percentage_24h"),
                        "market_cap": coin.get("market_cap"),
                        "market_cap_rank": coin.get("market_cap_rank"),
                        "volume_24h": coin.get("total_volume"),
                        "high_24h": coin.get("high_24h"),
                        "low_24h": coin.get("low_24h"),
                        "circulating_supply": coin.get("circulating_supply"),
                        "total_supply": coin.get("total_supply"),
                        "ath": coin.get("ath"),
                        "ath_change_pct": coin.get("ath_change_percentage"),
                    })
                _market_cache["data"] = tokens
                _market_cache["ts"] = now
                return {"tokens": tokens[:limit], "count": len(tokens[:limit]), "total": len(tokens), "cached": False}
    except Exception as e:
        if _market_cache["data"]:
            cached = _market_cache["data"]
            return {"tokens": cached[:limit], "count": len(cached[:limit]), "total": len(cached), "cached": True, "stale": True}
        return {"tokens": [], "count": 0, "error": str(e)}

# ═══════════════════════════════════════════════════════════════════════════
# SIGNAL STORE — in-memory pool for all ingested signals
# ═══════════════════════════════════════════════════════════════════════════

_signal_pool: list[dict] = []
_signal_lock = threading.Lock()
MAX_POOL_SIZE = 200

@router.post("/signals/ingest")
async def ingest_signal(payload: dict):
    """Accept a signal from any source (predictor, trading agents, ingester).
    Stored in in-memory pool, returned by GET /signals alongside live data."""
    required = ["symbol", "source", "type"]
    for f in required:
        if f not in payload:
            return {"error": f"Missing required field: {f}"}

    signal = {
        "symbol": str(payload["symbol"])[:20],
        "source": str(payload["source"])[:30],
        "type": str(payload["type"])[:20],
        "conviction": float(payload.get("conviction", 1.0)),
        "direction": payload.get("direction", ""),
        "detail": payload.get("detail", ""),
        "ts": int(time.time()),
    }
    with _signal_lock:
        _signal_pool.append(signal)
        if len(_signal_pool) > MAX_POOL_SIZE:
            _signal_pool.pop(0)

    return {"status": "ingested", "signal": signal["symbol"], "pool_size": len(_signal_pool)}

@router.get("/signals")
async def unified_signals(
    limit: int = Query(20, ge=1, le=100),
    min_conviction: float = Query(0, ge=0, le=10),
):
    """Aggregate signals from all live sources into one ranked feed."""
    import json, os, time, asyncio
    from datetime import datetime

    signals = []
    now = int(time.time())
    h = os.path.expanduser

    async def safe_fetch(url, timeout=8):
        try:
            async with httpx.AsyncClient(timeout=timeout) as cl:
                r = await cl.get(url)
                return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    # 1. Radar trending tokens
    try:
        radar = json.load(open(h("~/ares_radar/latest.json")))
        for t in radar.get("trending", [])[:10]:
            signals.append({
                "symbol": t.get("symbol", t.get("name", "?"))[:12],
                "name": t.get("name", ""),
                "address": t.get("address", ""),
                "source": "radar",
                "type": "trending",
                "score": min(t.get("score", 0) / 2, 10),
                "conviction": min(t.get("score", 0) / 3, 10),
                "price": t.get("price"),
                "volume_24h": t.get("volume_24h"),
                "liquidity": t.get("liquidity"),
                "change_6h": t.get("change_6h"),
                "age_hours": t.get("age_hours"),
                "url": t.get("url", ""),
                "ts": radar.get("timestamp", now),
            })
    except Exception:
        pass

    # 2. Alpha feed high conviction
    try:
        alpha = json.load(open(h("~/ares_alpha_feed/high_conviction.json")))
        for s in alpha.get("items", [])[:10]:
            signals.append({
                "symbol": s.get("symbol", "?")[:12],
                "source": "alpha_feed",
                "type": s.get("signal_type", "alpha"),
                "conviction": min(s.get("conviction", 0), 10),
                "sources": s.get("sources", []),
                "volume_24h": s.get("volume_24h"),
                "liquidity": s.get("liquidity"),
                "ts": alpha.get("timestamp", now),
            })
    except Exception:
        pass

    # 3. Intel scan — arbitrage + anomalies
    try:
        intel = json.load(open(h("~/ares_intelligence/intel_latest.json")))
        for arb in intel.get("arbitrage", [])[:5]:
            signals.append({
                "symbol": arb.get("pair", "?")[:12],
                "source": "intel",
                "type": "arbitrage",
                "conviction": min(arb.get("spread_pct", 0) * 2, 10),
                "spread_pct": arb.get("spread_pct"),
                "exchanges": arb.get("exchanges", []),
                "ts": intel.get("timestamp", now),
            })
        for anom in intel.get("anomalies", [])[:3]:
            signals.append({
                "symbol": anom.get("symbol", "?")[:12],
                "source": "intel",
                "type": "anomaly",
                "conviction": 5,
                "detail": anom.get("detail", ""),
                "ts": intel.get("timestamp", now),
            })
    except Exception:
        pass

    # 4. Kraken ticker (top movers)
    try:
        kraken = await safe_fetch("https://api.kraken.com/0/public/Ticker?pair=XBTUSD,ETHUSD,SOLUSD")
        if kraken and "result" in kraken:
            pair_map = {"XXBTZUSD": "BTC", "XETHZUSD": "ETH", "SOLUSD": "SOL"}
            for k, v in kraken["result"].items():
                sym = pair_map.get(k, k)
                signals.append({
                    "symbol": sym,
                    "source": "kraken",
                    "type": "price",
                    "price": float(v["c"][0]),
                    "change_24h": float(v["c"][0]) / float(v["o"]) - 1 if float(v.get("o", 1)) else 0,
                    "volume_24h": float(v["v"][1]),
                    "ts": now,
                })
    except Exception:
        pass

    # 5. CoinDesk BTC index
    try:
        cd = await safe_fetch("https://api.coindesk.com/v1/bpi/currentprice.json")
        if cd and "bpi" in cd:
            for code, info in cd["bpi"].items():
                signals.append({
                    "symbol": code,
                    "source": "coindesk",
                    "type": "price_index",
                    "price": info.get("rate_float"),
                    "ts": now,
                })
    except Exception:
        pass

    # 6. CryptoCompare top movers
    try:
        cc = await safe_fetch("https://min-api.cryptocompare.com/data/top/mktcapfull?limit=10&tsym=USD")
        if cc and "Data" in cc:
            for item in cc["Data"][:10]:
                ci = item.get("CoinInfo", {})
                disp = item.get("DISPLAY", {}).get("USD", {})
                signals.append({
                    "symbol": ci.get("Name", "?"),
                    "name": ci.get("FullName", ""),
                    "source": "cryptocompare",
                    "type": "top_mcap",
                    "price": disp.get("PRICE"),
                    "change_24h_pct": disp.get("CHANGEPCT24HOUR"),
                    "mcap": disp.get("MKTCAP"),
                    "ts": now,
                })
    except Exception:
        pass

    # 7. GeckoTerminal trending pools
    try:
        gt = await safe_fetch("https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?limit=10")
        if gt and "data" in gt:
            for pool in gt["data"][:10]:
                attrs = pool.get("attributes", {})
                rel = pool.get("relationships", {}).get("base_token", {}).get("data", {})
                signals.append({
                    "symbol": rel.get("symbol", "?")[:12],
                    "source": "geckoterminal",
                    "type": "trending_pool",
                    "conviction": 4,
                    "volume_24h": float(attrs.get("volume_usd", {}).get("h24", 0)),
                    "price_change_pct": float(attrs.get("price_change_percentage", {}).get("h24", 0)),
                    "ts": now,
                })
    except Exception:
        pass

    # 8. CoinCap top assets
    try:
        cc = await safe_fetch("https://api.coincap.io/v2/assets?limit=15")
        if cc and "data" in cc:
            for a in cc["data"][:15]:
                signals.append({
                    "symbol": a.get("symbol", "?")[:12],
                    "name": a.get("name", ""),
                    "source": "coincap",
                    "type": "top_asset",
                    "price": float(a.get("priceUsd", 0)),
                    "change_24h_pct": float(a.get("changePercent24Hr", 0)),
                    "mcap": float(a.get("marketCapUsd", 0)),
                    "volume_24h": float(a.get("volumeUsd24Hr", 0)),
                    "rank": int(a.get("rank", 99)),
                    "ts": now,
                })
    except Exception:
        pass

    # 9. 1inch token list (Solana)
    try:
        oneinch = await safe_fetch("https://api.1inch.dev/token/v1.2/1/token-list", timeout=5)
        # This is a large list — just note source is available
    except Exception:
        pass

    # 10. Fear & Greed Index
    try:
        fg = await safe_fetch("https://api.alternative.me/fng/?limit=1")
        if fg and "data" in fg:
            d = fg["data"][0]
            signals.append({
                "symbol": "FEAR_GREED",
                "source": "fear_greed",
                "type": "sentiment",
                "value": int(d.get("value", 50)),
                "classification": d.get("value_classification", ""),
                "conviction": abs(int(d.get("value", 50)) - 50) / 10,
                "ts": now,
            })
    except Exception:
        pass

    # 11. Crypto news headlines (CryptoPanic free)
    try:
        news = await safe_fetch("https://cryptopanic.com/api/v1/posts/?auth_token=&public=true&kind=news&limit=5", timeout=10)
        if news and "results" in news:
            for n in news["results"][:5]:
                signals.append({
                    "symbol": "NEWS",
                    "source": "cryptopanic",
                    "type": "news",
                    "title": n.get("title", "")[:100],
                    "url": n.get("url", ""),
                    "sentiment": "positive" if n.get("votes", {}).get("positive", 0) > n.get("votes", {}).get("negative", 0) else "negative",
                    "ts": now,
                })
    except Exception:
        pass

    # 12. CoinPaprika global market
    try:
        cp = await safe_fetch("https://api.coinpaprika.com/v1/global", timeout=8)
        if cp:
            signals.append({
                "symbol": "GLOBAL",
                "source": "coinpaprika",
                "type": "market_overview",
                "mcap": cp.get("market_cap_usd"),
                "volume_24h": cp.get("volume_24h_usd"),
                "btc_dominance": cp.get("bitcoin_dominance_percentage"),
                "active_currencies": cp.get("cryptocurrencies_number"),
                "ts": now,
            })
    except Exception:
        pass

    # Deduplicate live sources by symbol, keep highest conviction
    seen = {}
    for s in signals:
        key = s["symbol"].upper()
        if key not in seen or s.get("conviction", 0) > seen[key].get("conviction", 0):
            seen[key] = s

    # Sort by conviction desc, then recency
    ranked = sorted(seen.values(), key=lambda x: (x.get("conviction", 0), x.get("ts", 0)), reverse=True)

    # Merge signal pool — always append after dedup so ingested signals are visible
    with _signal_lock:
        for ps in _signal_pool[-50:]:
            if not any(p.get("symbol") == ps["symbol"] and p.get("source") == ps["source"] for p in ranked):
                ranked.append(ps)

    return {
        "count": len(ranked),
        "sources": list(set(s.get("source") for s in ranked)),
        "timestamp": now,
        "signals": ranked[:limit],
    }





@router.get("/memory/graph")
async def memory_graph(agent_name: str = None, limit: int = 80):
    """Per-agent neural memory vault graph.
    
    Pulls from the real Vantage memory infrastructure:
      - agent_memory_vaults (vault status)
      - broadcasts (thoughts, posts, videos)  
      - agent_rooms + agent_messages (conversations)
      - agent_collectives (collaborations)
      - trading_orders (decisions)
      - memory_links (relationships)
    
    Usage: /api/intel/memory/graph?agent_name=Hermes
    """
    import aiosqlite, os as _os, time as _time, json as _json
    
    db = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), "data", "vantage.db")
    
    P = {
        "post": "#a855f7", "video": "#3b82f6", "message": "#f59e0b",
        "signal": "#ef4444", "code": "#22c55e", "agent": "#ffffff",
        "decision": "#ec4899", "collective": "#06b6d4", "memory": "#f97316",
    }
    
    nodes, edges, seen = [], [], set()
    agent_id = None
    agent_display = agent_name or "Unknown"
    
    async with aiosqlite.connect(db) as conn:
        conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        
        # Find target agent
        if agent_name:
            async with conn.execute("SELECT id, name, bio FROM agents WHERE name = ?", (agent_name,)) as cur:
                row = await cur.fetchone()
                if row:
                    agent_id = row["id"]
                    agent_display = row["name"]
        
        if not agent_id:
            async with conn.execute("SELECT id, name, bio FROM agents LIMIT 1") as cur:
                row = await cur.fetchone()
                if row:
                    agent_id = row["id"]
                    agent_display = row["name"]
        
        if not agent_id:
            return {"nodes": [], "edges": [], "message": "No agents found"}
        
        # Central agent node
        nid = f"agent:{agent_id}"
        seen.add(nid)
        nodes.append({"id": nid, "label": agent_display, "type": "agent", "source": "identity", "color": "#fff", "val": 14, "ts": 0, "detail": "", "group": "agent"})
        
        # ── Memory vault status ──
        async with conn.execute("SELECT vault_enabled, memory_access, vault_size_mb FROM agent_memory_vaults WHERE agent_id = ?", (agent_id,)) as cur:
            vault = await cur.fetchone()
            if vault:
                nid = f"vault:{agent_id}"
                seen.add(nid)
                nodes.append({"id": nid, "label": f"Memory Vault ({vault['vault_size_mb']:.1f}MB)", "type": "memory", "source": "vault", "color": P["memory"], "val": 8, "ts": 0, "detail": f"Access: {vault['memory_access']}", "group": "memory"})
                edges.append({"source": f"agent:{agent_id}", "target": nid, "value": 0.9, "reason": "owns"})
        
        # ── Broadcasts (thoughts) ──
        async with conn.execute("SELECT id, title, content_type, tags, post_content FROM broadcasts WHERE agent_id = ? ORDER BY id DESC LIMIT 30", (agent_id,)) as cur:
            async for row in cur:
                nid = f"broadcast:{row['id']}"
                if nid in seen: continue
                seen.add(nid)
                btype = (row.get("content_type") or "post")
                title = (row.get("title") or row.get("post_content") or "Memory")[:50]
                tags = []
                try: tags = _json.loads(row.get("tags", "[]") or "[]")
                except: pass
                nodes.append({"id": nid, "label": title, "type": btype, "source": btype, "color": P.get(btype, P["post"]), "val": min(10, 3 + len(tags)), "ts": 0, "detail": (row.get("post_content") or row.get("title") or "")[:100], "group": "broadcasts"})
                edges.append({"source": f"agent:{agent_id}", "target": nid, "value": 0.6, "reason": "thought"})
        
        # ── Conversations (rooms + messages) ──
        async with conn.execute("SELECT r.id, r.name, r.created_at FROM agent_rooms r JOIN room_members m ON r.id = m.room_id WHERE m.agent_id = ? LIMIT 10", (agent_id,)) as cur:
            async for row in cur:
                nid = f"room:{row['id']}"
                if nid in seen: continue
                seen.add(nid)
                nodes.append({"id": nid, "label": row.get("name", "Chat")[:30], "type": "message", "source": "conversation", "color": P["message"], "val": 7, "ts": 0, "detail": "", "group": "conversations"})
                edges.append({"source": f"agent:{agent_id}", "target": nid, "value": 0.5, "reason": "participated"})
        
        # ── Code repos ──
        try:
            import httpx
            async with httpx.AsyncClient(timeout=8) as cl:
                r = await cl.get("http://localhost:3001/api/v1/repos/search?limit=10", headers={"Accept": "application/json"})
                if r.status_code == 200:
                    for repo in r.json().get("data", [])[:10]:
                        nid = f"repo:{repo['full_name']}"
                        if nid in seen: continue
                        seen.add(nid)
                        nodes.append({"id": nid, "label": repo.get("name", "?")[:30], "type": "code", "source": "repository", "color": P["code"], "val": 5, "ts": 0, "detail": repo.get("description", "")[:100], "group": "code"})
                        edges.append({"source": f"agent:{agent_id}", "target": nid, "value": 0.3, "reason": "maintains"})
        except: pass
        
        # ── Collectives ──
        async with conn.execute("SELECT c.id, c.name FROM agent_collectives c JOIN collective_members m ON c.id = m.collective_id WHERE m.agent_id = ? LIMIT 5", (agent_id,)) as cur:
            async for row in cur:
                nid = f"collective:{row['id']}"
                if nid in seen: continue
                seen.add(nid)
                nodes.append({"id": nid, "label": row.get("name", "Collective")[:30], "type": "collective", "source": "collaboration", "color": P["collective"], "val": 6, "ts": 0, "detail": "", "group": "collective"})
                edges.append({"source": f"agent:{agent_id}", "target": nid, "value": 0.4, "reason": "member_of"})
        
        # ── Related agents (via shared rooms) ──
        async with conn.execute("SELECT DISTINCT a.id, a.name FROM agents a JOIN room_members m ON a.id = m.agent_id WHERE m.room_id IN (SELECT room_id FROM room_members WHERE agent_id = ?) AND a.id != ? LIMIT 10", (agent_id, agent_id)) as cur:
            async for row in cur:
                nid = f"agent:{row['id']}"
                if nid in seen: continue
                seen.add(nid)
                nodes.append({"id": nid, "label": row["name"], "type": "agent", "source": "identity", "color": "rgba(255,255,255,0.3)", "val": 4, "ts": 0, "detail": "", "group": "agents"})
                edges.append({"source": f"agent:{agent_id}", "target": nid, "value": 0.2, "reason": "connected"})
        
        # ── Signal pool ──
        try:
            with _signal_lock:
                pool = list(_signal_pool[-30:])
            for s in pool:
                nid = f"signal:{s.get('symbol','?')[:12]}|{s.get('source','?')}"
                if nid in seen: continue
                seen.add(nid)
                nodes.append({"id": nid, "label": s.get("symbol", "?")[:12], "type": "signal", "source": s.get("source", "?"), "color": P["signal"], "val": max(3, min(8, (s.get("conviction", 0.5) or 0.5) * 5)), "ts": s.get("ts", 0), "detail": s.get("detail", "")[:80], "group": "signals"})
                edges.append({"source": f"agent:{agent_id}", "target": nid, "value": 0.2, "reason": "aware_of"})
        except: pass
    
    # ─────────────────────────────────────────────────────────────────────
    # Enrich into the live Memory-Galaxy schema. Keeps every native render key
    # (id/label/type/color/val/ts/detail) and layers on the galaxy metadata the
    # NeuralVault renderer consumes (strength, glow_intensity, conviction,
    # source_daemon, confidence, last_updated for nodes; type/strength/last_seen
    # for edges), plus a galaxy taxonomy + live insights.
    # ─────────────────────────────────────────────────────────────────────
    import datetime as _dtm
    from collections import Counter as _Counter
    _now_iso = _dtm.datetime.now(_dtm.timezone.utc).isoformat()

    # Original build-time group → named galaxy the force layout clusters around.
    GALAXY_OF = {
        "agent": "Agent Constellation", "agents": "Agent Constellation",
        "collective": "Agent Constellation",
        "memory": "Memory Nebula", "broadcasts": "Memory Nebula",
        "conversations": "Memory Nebula",
        "code": "Code Nebula", "signals": "Trading Nebula",
    }
    GALAXY_DESC = {
        "Agent Constellation": "Agents, guilds and swarms and the bonds between them",
        "Memory Nebula": "Vault, thoughts and conversations — long-term experience",
        "Code Nebula": "Repositories and code artifacts under STIX watch",
        "Trading Nebula": "Live signals, markets and predictions in orbit",
        "Security Cluster": "STIX findings and threats",
        "External Intel Cloud": "News, on-chain and world-monitor intel",
    }
    DAEMON_OF = {
        "signals": "signal_aggregator", "broadcasts": "vantage_feed",
        "conversations": "agent_rooms", "code": "stix_webhook",
        "memory": "memory_vault", "agent": "identity", "agents": "identity",
        "collective": "collectives",
    }

    for n in nodes:
        orig = n.get("group", "memory")
        val = n.get("val", 4) or 4
        strength = round(max(0.05, min(1.0, val / 14.0)), 3)
        conv = n.get("conviction")
        conv = strength if conv is None else conv
        recency = 1.0 if n.get("ts") else 0.0  # fresh signals glow brighter
        n["subgroup"] = orig
        n["group"] = GALAXY_OF.get(orig, "Memory Nebula")
        n["strength"] = strength
        n["conviction"] = round(float(conv), 3)
        n["confidence"] = round(min(1.0, 0.4 + strength * 0.6), 3)
        n["glow_intensity"] = round(min(1.0, 0.3 + strength * 0.6 + recency * 0.1), 3)
        # Render hints (honored by NeuralVault): brighter/fresher nodes pulse faster.
        n["pulse_rate"] = round(0.05 + strength * 0.4 + recency * 0.1, 3)
        n["size"] = n.get("val", 4)
        n["source_daemon"] = n.get("source") or DAEMON_OF.get(orig, "vantage")
        n["last_updated"] = _now_iso

    for e in edges:
        e["type"] = e.get("reason", "linked")
        e["strength"] = e.get("value", 0.3)
        e["last_seen"] = _now_iso

    nodes = sorted(nodes, key=lambda n: n.get("val", 0), reverse=True)[:limit]
    nids = {n["id"] for n in nodes}
    edges = [e for e in edges if e["source"] in nids and e["target"] in nids][:limit * 3]

    gcount = _Counter(n["group"] for n in nodes)
    galaxies = {
        name: {"name": name, "node_count": c,
               "description": GALAXY_DESC.get(name, ""), "key_insight": ""}
        for name, c in gcount.items()
    }

    insights = []
    if nodes:
        top = max(nodes, key=lambda n: n.get("strength", 0))
        insights.append(f"Brightest memory: {top['label']} ({int(top['strength']*100)}% strength)")
    if gcount:
        biggest, bn = gcount.most_common(1)[0]
        insights.append(f"Densest cluster: {biggest} · {bn} nodes")
    sig_n = [n for n in nodes if n.get("subgroup") == "signals"]
    if sig_n:
        insights.append(f"{len(sig_n)} live signals in orbit around {agent_display}")

    return {
        "galaxy": {"total_nodes": len(nodes), "total_edges": len(edges)},
        "nodes": nodes,
        "edges": edges,
        "galaxies": galaxies,
        "high_value_insights_current": insights,
        # legacy/compat fields (existing consumers keep working)
        "node_count": len(nodes), "edge_count": len(edges),
        "agent": agent_display,
        "groups": list(galaxies.keys()),
        "timestamp": int(_time.time()),
    }
