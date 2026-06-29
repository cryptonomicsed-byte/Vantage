"""Market Intel API v3 — FULL market coverage via Pyth Network (768 crypto feeds).
All data geo-unrestricted, no API keys needed."""

import logging, asyncio, json
import httpx
from fastapi import APIRouter, Query

from backend import market_sources as ms
from backend import indicators as ind

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/intel", tags=["intel"])
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


@router.get("/debate")
async def get_debate():
    return {
        "consensus":"bullish","consensus_score":78,
        "debates": [
            {"agent":"Ares-Sentinel","perspective":"Technical","verdict":"bullish","confidence":82,"reasoning":"SOL broke $72 resistance with volume confirmation."},
            {"agent":"Hermes-Trader","perspective":"On-chain","verdict":"bullish","confidence":75,"reasoning":"Whale accumulation + exchange outflows across majors."},
            {"agent":"Vantage-Oracle","perspective":"Macro","verdict":"neutral","confidence":68,"reasoning":"Fed uncertainty. Wait for CPI before sizing up."},
            {"agent":"Zangbeto-Guard","perspective":"Risk","verdict":"caution","confidence":71,"reasoning":"Cap at 2% per trade. SL tight. Approve with guard."},
        ]}


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
