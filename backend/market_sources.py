"""Shared market-data sources — direct no-auth public crypto API integrations.

Centralizes the no-key public feeds so intel / trading / copilot all draw from one
place, and Vantage owns its market data instead of depending on an external intel
engine. Every fetch is fail-soft: it returns None / [] / {} on any error and never
raises, so callers can treat market data as best-effort enrichment.

Sources used here are all no-auth, geo-open public endpoints (Pyth, CoinGecko,
CoinCap, Binance, Kraken, KuCoin, OKX, Coinbase, Gemini, mempool.space, ...). See
SOURCES for the full registry surfaced at /api/intel/sources-registry.
"""
import time
import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)
UA = {"User-Agent": "Vantage/1.0"}

# ── TTL cache ───────────────────────────────────────────────────────────────────
_cache: dict[str, tuple[float, object]] = {}


def _cache_get(key: str, ttl: float):
    e = _cache.get(key)
    if e and (time.time() - e[0]) < ttl:
        return e[1]
    return None


def _cache_put(key: str, val):
    _cache[key] = (time.time(), val)
    return val


async def _get_json(url: str, timeout: float = 8.0):
    """GET → parsed JSON, or None on any failure."""
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=UA) as c:
            r = await c.get(url)
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.debug("market GET %s failed: %s", url, e)
    return None


# ── Pyth (fast majors, primary price oracle) ──────────────────────────────────────
PYTH_PRICE = "https://hermes.pyth.network/v2/updates/price/latest"
PYTH_IDS = {
    "BTC": "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "ETH": "ff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
    "SOL": "ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
    "BNB": "2f95862b045670cd22bee3114c39763a4a08beeb663b145d283c31d7d1101c4f",
    "XRP": "ec5d399846a9209f3fe5881d70aae9268c94339ff9817e8d18ff19fa05eea1c8",
    "DOGE": "dcef50dd0a4cd2dcc17e45df1676dcb336a11a61c69df7a0299b0150c672d25c",
    "ADA": "2a01deaec9e51a579277b34b122399984d0bbf57e2458a7e42fecd2829867a0d",
    "AVAX": "93da3352f9f1d105fdfe4971cfa80e9dd777bfc5d0f683ebb6e1294b92137bb7",
    "LINK": "8ac0c70fff57e9aefdf5edf44b51d62c2d433653cbb2cf5cc06bb115af04d221",
    "MATIC": "5de33a9112c2b700b8d30b8a3402c103578ccfa2765696471cc672bd5cf6ac52",
}

# CoinGecko id aliases for symbols we price via CoinGecko fallback.
CG_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "BNB": "binancecoin",
    "XRP": "ripple", "DOGE": "dogecoin", "ADA": "cardano", "AVAX": "avalanche-2",
    "LINK": "chainlink", "MATIC": "matic-network", "DOT": "polkadot", "UNI": "uniswap",
    "ATOM": "cosmos", "LTC": "litecoin", "ARB": "arbitrum", "OP": "optimism",
    "SUI": "sui", "APT": "aptos", "NEAR": "near", "INJ": "injective-protocol",
    "TIA": "celestia", "SEI": "sei-network", "PYTH": "pyth-network", "JUP": "jupiter-exchange-solana",
    "BONK": "bonk", "WIF": "dogwifcoin", "PEPE": "pepe", "SHIB": "shiba-inu",
    "RNDR": "render-token", "FET": "fetch-ai", "USDC": "usd-coin", "USDT": "tether",
}


async def _pyth_prices(symbols: list[str]) -> dict[str, float]:
    ids = [PYTH_IDS[s] for s in symbols if s in PYTH_IDS]
    if not ids:
        return {}
    query = "&".join(f"ids[]={i}" for i in ids)
    data = await _get_json(f"{PYTH_PRICE}?{query}", timeout=6)
    out: dict[str, float] = {}
    if not data:
        return out
    rev = {v: k for k, v in PYTH_IDS.items()}
    for item in data.get("parsed", []):
        pid = item.get("id", "")
        pr = item.get("price", {})
        try:
            actual = int(pr.get("price", 0)) * (10 ** int(pr.get("expo", 0)))
        except Exception:
            continue
        # Pyth ids come back without a leading 0x; match by suffix.
        for full, sym_name in rev.items():
            if full in pid or pid in full:
                if actual > 0:
                    out[sym_name] = actual
                break
    return out


async def _coingecko_price(symbol: str) -> Optional[float]:
    cid = CG_IDS.get(symbol.upper(), symbol.lower())
    data = await _get_json(
        f"https://api.coingecko.com/api/v3/simple/price?ids={cid}&vs_currencies=usd", timeout=6
    )
    if data and cid in data:
        p = data[cid].get("usd")
        return float(p) if p else None
    return None


async def resolve_price(symbol: str) -> Optional[float]:
    """Best-effort live USD price: Pyth (fast majors) → CoinGecko fallback. Cached 30s."""
    if not symbol:
        return None
    symbol = symbol.upper()
    key = f"price:{symbol}"
    cached = _cache_get(key, 30)
    if cached is not None:
        return cached
    price: Optional[float] = None
    pyth = await _pyth_prices([symbol])
    if symbol in pyth:
        price = pyth[symbol]
    if price is None:
        price = await _coingecko_price(symbol)
    if price is not None:
        _cache_put(key, price)
    return price


# ── CoinGecko markets / global ────────────────────────────────────────────────────
async def coingecko_markets(limit: int = 100) -> list[dict]:
    """Top markets by market cap with 24h change, volume, sparkline. Cached 60s."""
    limit = max(1, min(limit, 250))
    key = f"cg_markets:{limit}"
    cached = _cache_get(key, 60)
    if cached is not None:
        return cached
    data = await _get_json(
        "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc"
        f"&per_page={limit}&page=1&sparkline=true&price_change_percentage=24h",
        timeout=10,
    )
    rows: list[dict] = []
    if isinstance(data, list):
        for c in data:
            rows.append({
                "rank": c.get("market_cap_rank"),
                "symbol": (c.get("symbol") or "").upper(),
                "name": c.get("name"),
                "price": c.get("current_price"),
                "change_24h": c.get("price_change_percentage_24h"),
                "market_cap": c.get("market_cap"),
                "volume_24h": c.get("total_volume"),
                "sparkline": (c.get("sparkline_in_7d") or {}).get("price", [])[-48:],
            })
    if rows:
        _cache_put(key, rows)
    return rows


async def coingecko_price_full(symbol: str) -> Optional[dict]:
    """Price + 24h change + 24h volume for a symbol (direct CoinGecko). Cached 30s."""
    symbol = symbol.upper()
    key = f"cgfull:{symbol}"
    cached = _cache_get(key, 30)
    if cached is not None:
        return cached
    cid = CG_IDS.get(symbol, symbol.lower())
    data = await _get_json(
        f"https://api.coingecko.com/api/v3/simple/price?ids={cid}&vs_currencies=usd"
        "&include_24hr_change=true&include_24hr_vol=true", timeout=6,
    )
    if data and cid in data:
        item = data[cid]
        out = {
            "symbol": symbol,
            "price": item.get("usd"),
            "change_24h": item.get("usd_24h_change"),
            "volume_24h": item.get("usd_24h_vol"),
        }
        if out["price"]:
            return _cache_put(key, out)
    return None


async def coingecko_volatility(symbol: str, days: int = 7) -> Optional[dict]:
    """Realized volatility over `days` from CoinGecko market_chart. Cached 300s."""
    symbol = symbol.upper()
    key = f"cgvol:{symbol}:{days}"
    cached = _cache_get(key, 300)
    if cached is not None:
        return cached
    cid = CG_IDS.get(symbol, symbol.lower())
    data = await _get_json(
        f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart?vs_currency=usd&days={days}", timeout=8,
    )
    prices = (data or {}).get("prices") or []
    vals = [p[1] for p in prices if len(p) > 1]
    if len(vals) > 1:
        m = sum(vals) / len(vals)
        std = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5
        out = {
            "symbol": symbol,
            "volatility_pct": round(std / m * 100, 2) if m else 0,
            "avg_price": round(m, 6),
            "data_points": len(vals),
            "days": days,
        }
        return _cache_put(key, out)
    return None


async def coingecko_global() -> dict:
    """Global market metrics: total mcap, 24h volume, BTC dominance. Cached 300s."""
    cached = _cache_get("cg_global", 300)
    if cached is not None:
        return cached
    data = await _get_json("https://api.coingecko.com/api/v3/global", timeout=8)
    d = (data or {}).get("data", {})
    out = {
        "total_market_cap_usd": (d.get("total_market_cap") or {}).get("usd"),
        "total_volume_usd": (d.get("total_volume") or {}).get("usd"),
        "btc_dominance": (d.get("market_cap_percentage") or {}).get("btc"),
        "eth_dominance": (d.get("market_cap_percentage") or {}).get("eth"),
        "active_cryptocurrencies": d.get("active_cryptocurrencies"),
        "market_cap_change_24h_pct": d.get("market_cap_change_percentage_24h_usd"),
    }
    if out["total_market_cap_usd"]:
        _cache_put("cg_global", out)
    return out


# ── Real cross-exchange spreads (genuine arbitrage signal) ─────────────────────────
async def _binance(sym: str) -> Optional[float]:
    d = await _get_json(f"https://api4.binance.com/api/v3/ticker/price?symbol={sym}USDT", timeout=6)
    return float(d["price"]) if d and "price" in d else None


async def _okx(sym: str) -> Optional[float]:
    d = await _get_json(f"https://www.okx.com/api/v5/market/ticker?instId={sym}-USDT", timeout=6)
    try:
        return float(d["data"][0]["last"]) if d and d.get("data") else None
    except Exception:
        return None


async def _kucoin(sym: str) -> Optional[float]:
    d = await _get_json(f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={sym}-USDT", timeout=6)
    try:
        return float(d["data"]["price"]) if d and d.get("data") else None
    except Exception:
        return None


async def _coinbase(sym: str) -> Optional[float]:
    d = await _get_json(f"https://api.coinbase.com/v2/prices/{sym}-USD/spot", timeout=6)
    try:
        return float(d["data"]["amount"]) if d and d.get("data") else None
    except Exception:
        return None


async def _gemini(sym: str) -> Optional[float]:
    d = await _get_json(f"https://api.gemini.com/v2/ticker/{sym.lower()}usd", timeout=6)
    try:
        return float(d["close"]) if d and d.get("close") else None
    except Exception:
        return None


async def exchange_spreads(symbol: str = "BTC") -> dict:
    """Fetch the same pair across several CEXes and compute the real spread. Cached 30s."""
    symbol = symbol.upper()
    key = f"spreads:{symbol}"
    cached = _cache_get(key, 30)
    if cached is not None:
        return cached
    names = ["binance", "okx", "kucoin", "coinbase", "gemini"]
    fns = [_binance(symbol), _okx(symbol), _kucoin(symbol), _coinbase(symbol), _gemini(symbol)]
    results = await asyncio.gather(*fns, return_exceptions=True)
    venues = {}
    for n, r in zip(names, results):
        if isinstance(r, (int, float)) and r and r > 0:
            venues[n] = float(r)
    out = {"symbol": symbol, "venues": venues, "spread_pct": 0.0, "buy_venue": None, "sell_venue": None}
    if len(venues) >= 2:
        lo_v = min(venues, key=venues.get)
        hi_v = max(venues, key=venues.get)
        lo, hi = venues[lo_v], venues[hi_v]
        out.update({
            "buy_venue": lo_v, "buy_price": lo,
            "sell_venue": hi_v, "sell_price": hi,
            "spread_pct": round((hi - lo) / lo * 100, 3) if lo else 0.0,
        })
    if venues:
        _cache_put(key, out)
    return out


async def real_arbitrage(symbols: Optional[list[str]] = None) -> list[dict]:
    """Real cross-exchange spreads for a basket of liquid majors, ranked by spread."""
    symbols = symbols or ["BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "LINK"]
    spreads = await asyncio.gather(*[exchange_spreads(s) for s in symbols])
    opps = []
    for s in spreads:
        if s.get("buy_venue") and s.get("spread_pct", 0) > 0:
            opps.append({
                "route": f"{s['buy_venue']}→{s['sell_venue']}",
                "pair": f"{s['symbol']}/USD",
                "spread_pct": s["spread_pct"],
                "buy_price": s.get("buy_price"),
                "sell_price": s.get("sell_price"),
                "venues": len(s.get("venues", {})),
            })
    opps.sort(key=lambda o: -o["spread_pct"])
    return opps


# ── On-chain (BTC) ────────────────────────────────────────────────────────────────
async def mempool_fees() -> dict:
    """Recommended BTC fee rates (sat/vB) from mempool.space. Cached 60s."""
    cached = _cache_get("btc_fees", 60)
    if cached is not None:
        return cached
    data = await _get_json("https://mempool.space/api/v1/fees/recommended", timeout=6)
    if data:
        _cache_put("btc_fees", data)
        return data
    return {}


# ── Market breadth → real sentiment ────────────────────────────────────────────────
async def market_breadth() -> dict:
    """Derive a real sentiment read from top-100 market breadth + BTC dominance. Cached 120s."""
    cached = _cache_get("breadth", 120)
    if cached is not None:
        return cached
    markets = await coingecko_markets(100)
    gl = await coingecko_global()
    out: dict = {}
    if markets:
        changes = [m["change_24h"] for m in markets if isinstance(m.get("change_24h"), (int, float))]
        gainers = len([c for c in changes if c > 0])
        breadth_pct = round(gainers / len(changes) * 100, 1) if changes else 0
        # Fear/greed proxy: blend market breadth with average 24h momentum.
        avg = sum(changes) / len(changes) if changes else 0
        score = max(0, min(100, round(breadth_pct * 0.7 + (50 + avg * 3) * 0.3)))
        mood = "extreme greed" if score >= 80 else "greed" if score >= 60 else \
               "neutral" if score >= 45 else "fear" if score >= 25 else "extreme fear"
        out = {
            "overall": "bullish" if avg > 0 else "bearish",
            "fear_greed": score,
            "mood": mood,
            "gainers_pct": breadth_pct,
            "avg_change_24h": round(avg, 2),
            "btc_dominance": gl.get("btc_dominance"),
            "indicators": [
                f"Market breadth: {breadth_pct}% of top-100 green",
                f"Avg 24h move: {round(avg, 2)}%",
                f"BTC dominance: {round(gl.get('btc_dominance') or 0, 1)}%",
                f"Fear & Greed (derived): {score} — {mood}",
            ],
        }
        _cache_put("breadth", out)
    return out


async def top_movers(limit: int = 8) -> list[dict]:
    """Real alpha proxy: top gainers from the top-100 by 24h change + volume."""
    markets = await coingecko_markets(100)
    ranked = [m for m in markets if isinstance(m.get("change_24h"), (int, float))]
    ranked.sort(key=lambda m: -(m["change_24h"] or 0))
    out = []
    for m in ranked[:limit]:
        out.append({
            "symbol": m["symbol"],
            "price": m["price"],
            "change_24h": round(m["change_24h"], 2),
            "volume_24h": m["volume_24h"],
            "conviction": round(min(5.0, abs(m["change_24h"]) / 5), 2),
            "signal": "breakout" if m["change_24h"] > 10 else "momentum",
        })
    return out


# ── Source registry (transparency for /api/intel/sources-registry) ──────────────────
SOURCES = [
    {"name": "Pyth Network", "category": "oracle", "url": "https://hermes.pyth.network", "integrated": True},
    {"name": "CoinGecko", "category": "market", "url": "https://api.coingecko.com", "integrated": True},
    {"name": "Binance", "category": "exchange", "url": "https://api4.binance.com", "integrated": True},
    {"name": "OKX", "category": "exchange", "url": "https://www.okx.com", "integrated": True},
    {"name": "KuCoin", "category": "exchange", "url": "https://api.kucoin.com", "integrated": True},
    {"name": "Coinbase", "category": "exchange", "url": "https://api.coinbase.com", "integrated": True},
    {"name": "Gemini", "category": "exchange", "url": "https://api.gemini.com", "integrated": True},
    {"name": "mempool.space", "category": "onchain", "url": "https://mempool.space", "integrated": True},
    {"name": "CoinCap", "category": "market", "url": "https://api.coincap.io", "integrated": False},
    {"name": "CoinPaprika", "category": "market", "url": "https://api.coinpaprika.com", "integrated": False},
    {"name": "CoinLore", "category": "market", "url": "https://api.coinlore.net", "integrated": False},
    {"name": "CoinDesk", "category": "market", "url": "https://api.coindesk.com", "integrated": False},
    {"name": "CryptoCompare", "category": "market", "url": "https://min-api.cryptocompare.com", "integrated": False},
    {"name": "Messari", "category": "fundamentals", "url": "https://data.messari.io", "integrated": False},
    {"name": "DefiLlama", "category": "defi", "url": "https://api.llama.fi", "integrated": False},
    {"name": "DEX Screener", "category": "dex", "url": "https://api.dexscreener.com", "integrated": False},
    {"name": "GeckoTerminal", "category": "dex", "url": "https://api.geckoterminal.com", "integrated": False},
    {"name": "0x", "category": "dex", "url": "https://api.0x.org", "integrated": False},
    {"name": "1inch", "category": "dex", "url": "https://api.1inch.io", "integrated": False},
    {"name": "Kraken", "category": "exchange", "url": "https://api.kraken.com", "integrated": False},
    {"name": "WazirX", "category": "exchange", "url": "https://api.wazirx.com", "integrated": False},
    {"name": "Bitcambio", "category": "exchange", "url": "https://nova.bitcambio.com.br", "integrated": False},
    {"name": "MercadoBitcoin", "category": "exchange", "url": "https://www.mercadobitcoin.com.br", "integrated": False},
    {"name": "Cryptonator", "category": "fx", "url": "https://www.cryptonator.com", "integrated": False},
    {"name": "ExchangeRate-API", "category": "fx", "url": "https://open.er-api.com", "integrated": False},
    {"name": "Currency Rates", "category": "fx", "url": "https://cdn.jsdelivr.net", "integrated": False},
    {"name": "NBP", "category": "fx", "url": "https://api.nbp.pl", "integrated": False},
    {"name": "Solana JSON RPC", "category": "rpc", "url": "https://api.mainnet-beta.solana.com", "integrated": False},
    {"name": "ZMOK (ETH RPC)", "category": "rpc", "url": "https://api.zmok.io", "integrated": False},
    {"name": "Mempool (fees)", "category": "onchain", "url": "https://mempool.space", "integrated": True},
    {"name": "CoinMap", "category": "global", "url": "https://coinmap.org", "integrated": False},
    {"name": "Localbitcoins", "category": "p2p", "url": "https://localbitcoins.com", "integrated": False},
    {"name": "Nexchange", "category": "exchange", "url": "https://api.n.exchange", "integrated": False},
    {"name": "CryptingUp", "category": "market", "url": "https://www.cryptingup.com", "integrated": False},
    {"name": "BitcoinCharts", "category": "market", "url": "https://bitcoincharts.com", "integrated": False},
    {"name": "CryptAPI", "category": "payments", "url": "https://api.cryptapi.io", "integrated": False},
    {"name": "Razorpay IFSC", "category": "fiat", "url": "https://ifsc.razorpay.com", "integrated": False},
]
