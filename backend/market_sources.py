"""Shared market-data sources — direct no-auth public crypto API integrations.

Centralizes the no-key public feeds so intel / trading / copilot all draw from one
place, and Vantage owns its market data instead of depending on an external intel
engine. Every fetch is fail-soft: it returns None / [] / {} on any error and never
raises, so callers can treat market data as best-effort enrichment.

Sources used here are all no-auth, geo-open public endpoints (Pyth, CoinGecko,
CoinCap, Binance, Kraken, KuCoin, OKX, Coinbase, Gemini, mempool.space, ...). See
SOURCES for the full registry surfaced at /api/intel/sources-registry.
"""
import re
import time
import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)
UA = {"User-Agent": "Vantage/1.0"}
# Browser-ish UA for RSS/news hosts that bot-block the default UA.
NEWS_UA = {"User-Agent": "Mozilla/5.0 (compatible; VantageBot/1.0)"}

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


# ── OHLC candles (Binance klines → CoinGecko OHLC fallback) ─────────────────────────
# Binance interval → CoinGecko /ohlc `days` (CoinGecko picks granularity from days).
_CG_OHLC_DAYS = {"1h": 1, "4h": 7, "1d": 30, "1w": 365}


_KRAKEN_INTERVAL_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}
_KRAKEN_SYMBOL = {"BTC": "XBT"}  # Kraken's legacy XBT ticker for Bitcoin


async def ohlc(symbol: str, interval: str = "1d", limit: int = 200) -> list[dict]:
    """OHLCV candles for a symbol. Kraken first (Binance hard-geoblocks this
    deployment's IP with a 451 — see ops notes; Kraken has no such restriction
    and needs no auth), Binance second in case Kraken lacks a pair, CoinGecko
    /ohlc last (no volume, and rate-limited on the free tier so it's a last
    resort, not a primary path). Returns [{time, open, high, low, close,
    volume}], time in unix seconds ascending. Cached 60s."""
    symbol = symbol.upper()
    interval = interval if interval in ("1m", "5m", "15m", "1h", "4h", "1d", "1w") else "1d"
    limit = max(10, min(limit, 500))
    key = f"ohlc:{symbol}:{interval}:{limit}"
    cached = _cache_get(key, 60)
    if cached is not None:
        return cached

    candles: list[dict] = []

    # Kraken: {"result": {"<PAIR>": [[time, open, high, low, close, vwap, volume, count], ...]}}
    kraken_pair = f"{_KRAKEN_SYMBOL.get(symbol, symbol)}USD"
    kr = await _get_json(
        f"https://api.kraken.com/0/public/OHLC?pair={kraken_pair}&interval={_KRAKEN_INTERVAL_MIN[interval]}",
        timeout=8,
    )
    if isinstance(kr, dict) and not kr.get("error") and isinstance(kr.get("result"), dict):
        rows = next((v for k, v in kr["result"].items() if k != "last"), None)
        if isinstance(rows, list):
            for r in rows[-limit:]:
                try:
                    candles.append({
                        "time": int(r[0]),
                        "open": float(r[1]), "high": float(r[2]),
                        "low": float(r[3]), "close": float(r[4]),
                        "volume": float(r[6]),
                    })
                except (ValueError, IndexError, TypeError):
                    continue

    # Fallback: Binance klines (true OHLCV) — [openTime(ms), open, high, low, close, volume, ...]
    if not candles:
        data = await _get_json(
            f"https://api4.binance.com/api/v3/klines?symbol={symbol}USDT&interval={interval}&limit={limit}",
            timeout=8,
        )
        if isinstance(data, list) and data:
            for k in data:
                try:
                    candles.append({
                        "time": int(k[0]) // 1000,
                        "open": float(k[1]), "high": float(k[2]),
                        "low": float(k[3]), "close": float(k[4]),
                        "volume": float(k[5]),
                    })
                except (ValueError, IndexError, TypeError):
                    continue

    # Last resort: CoinGecko /ohlc (no volume).
    if not candles:
        cid = CG_IDS.get(symbol, symbol.lower())
        days = _CG_OHLC_DAYS.get(interval, 30)
        cg = await _get_json(
            f"https://api.coingecko.com/api/v3/coins/{cid}/ohlc?vs_currency=usd&days={days}", timeout=10,
        )
        if isinstance(cg, list):
            for c in cg[-limit:]:
                try:
                    candles.append({
                        "time": int(c[0]) // 1000,
                        "open": float(c[1]), "high": float(c[2]),
                        "low": float(c[3]), "close": float(c[4]),
                        "volume": 0.0,
                    })
                except (ValueError, IndexError, TypeError):
                    continue

    if candles:
        _cache_put(key, candles)
    return candles


# ── DeFi yields (DefiLlama) ─────────────────────────────────────────────────────────
async def defillama_yields(limit: int = 25, min_tvl: float = 1_000_000) -> list[dict]:
    """Top yield pools by APY with a TVL floor, from DefiLlama. Cached 300s."""
    key = f"yields:{limit}:{int(min_tvl)}"
    cached = _cache_get(key, 300)
    if cached is not None:
        return cached
    data = await _get_json("https://yields.llama.fi/pools", timeout=12)
    pools = (data or {}).get("data") or []
    rows = []
    for p in pools:
        tvl = p.get("tvlUsd") or 0
        apy = p.get("apy")
        if tvl >= min_tvl and isinstance(apy, (int, float)):
            rows.append({
                "pool": p.get("symbol"),
                "project": p.get("project"),
                "chain": p.get("chain"),
                "apy": round(apy, 2),
                "tvl_usd": round(tvl, 0),
                "stablecoin": bool(p.get("stablecoin")),
            })
    rows.sort(key=lambda r: -(r["apy"] or 0))
    rows = rows[:limit]
    if rows:
        _cache_put(key, rows)
    return rows


# ── DEX pairs / liquidity (DexScreener) ─────────────────────────────────────────────
async def dexscreener_search(query: str, limit: int = 20) -> list[dict]:
    """Search DEX pairs by token/symbol; returns price, liquidity, 24h volume. Cached 60s."""
    q = (query or "").strip()
    if not q:
        return []
    key = f"dex:{q.lower()}:{limit}"
    cached = _cache_get(key, 60)
    if cached is not None:
        return cached
    data = await _get_json(f"https://api.dexscreener.com/latest/dex/search?q={q}", timeout=10)
    pairs = (data or {}).get("pairs") or []
    rows = []
    for p in pairs[: limit * 2]:
        rows.append({
            "pair": f"{(p.get('baseToken') or {}).get('symbol','?')}/{(p.get('quoteToken') or {}).get('symbol','?')}",
            "dex": p.get("dexId"),
            "chain": p.get("chainId"),
            "price_usd": float(p["priceUsd"]) if p.get("priceUsd") else None,
            "liquidity_usd": (p.get("liquidity") or {}).get("usd"),
            "volume_24h": (p.get("volume") or {}).get("h24"),
            "change_24h": (p.get("priceChange") or {}).get("h24"),
        })
    rows.sort(key=lambda r: -((r.get("liquidity_usd") or 0)))
    rows = rows[:limit]
    if rows:
        _cache_put(key, rows)
    return rows


# ── DEX new/trending pools (GeckoTerminal) — broader token coverage ────────────────
# CoinGecko/Pyth-backed market lists are all ranked by market cap, which structurally
# excludes brand-new/low-cap tokens. This surfaces the actual pool-level feed instead —
# every currently-trading pair on a chain, not just the ones that made a top-N ranking.
async def dex_new_pools(network: str = "solana", kind: str = "trending", limit: int = 30) -> list[dict]:
    """Pool-level DEX feed: 'trending' (established momentum) or 'new' (just launched).
    Cached 30s."""
    network = (network or "solana").strip().lower()
    kind = "new" if kind == "new" else "trending"
    key = f"dexpools:{network}:{kind}:{limit}"
    cached = _cache_get(key, 30)
    if cached is not None:
        return cached
    endpoint = "new_pools" if kind == "new" else "trending_pools"
    data = await _get_json(
        f"https://api.geckoterminal.com/api/v2/networks/{network}/{endpoint}?page=1", timeout=10
    )
    pools = (data or {}).get("data") or []
    rows = []
    for p in pools[:limit]:
        a = p.get("attributes") or {}
        name = a.get("name") or "? / ?"
        base_sym, _, quote_sym = name.partition(" / ")
        vol = a.get("volume_usd") or {}
        chg = a.get("price_change_percentage") or {}
        txns = (a.get("transactions") or {}).get("h24") or {}
        try:
            price_usd = float(a["base_token_price_usd"]) if a.get("base_token_price_usd") else None
        except (TypeError, ValueError):
            price_usd = None
        rows.append({
            "pair": f"{base_sym.strip()}/{quote_sym.strip() or '?'}",
            "pool_address": a.get("address"),
            "network": network,
            "price_usd": price_usd,
            "liquidity_usd": float(a["reserve_in_usd"]) if a.get("reserve_in_usd") else None,
            "volume_24h": float(vol["h24"]) if vol.get("h24") else 0.0,
            "change_24h_pct": float(chg["h24"]) if chg.get("h24") else None,
            "buys_24h": txns.get("buys"),
            "sells_24h": txns.get("sells"),
            "created_at": a.get("pool_created_at"),
        })
    if rows:
        _cache_put(key, rows)
    return rows


# ── FX rates (ExchangeRate-API) ─────────────────────────────────────────────────────
async def fx_rates(base: str = "USD") -> dict:
    """Fiat exchange rates for a base currency. Cached 1h."""
    base = base.upper()
    key = f"fx:{base}"
    cached = _cache_get(key, 3600)
    if cached is not None:
        return cached
    data = await _get_json(f"https://open.er-api.com/v6/latest/{base}", timeout=8)
    rates = (data or {}).get("rates") or {}
    if rates:
        out = {"base": base, "rates": rates, "updated": (data or {}).get("time_last_update_utc")}
        return _cache_put(key, out)
    return {"base": base, "rates": {}}


# ── On-chain whale activity (mempool.space, BTC) ────────────────────────────────────
async def whale_txs(limit: int = 10, min_value_btc: Optional[float] = None) -> list[dict]:
    """Largest recent BTC mempool transactions by value (whale activity), optionally
    filtered to only transactions >= min_value_btc. The raw mempool.space fetch is
    cached 30s unfiltered/untruncated; filtering and limiting happen fresh on every
    call so different callers can use different thresholds against the same fetch."""
    cached = _cache_get("whales:raw", 30)
    if cached is not None:
        rows = cached
    else:
        data = await _get_json("https://mempool.space/api/mempool/recent", timeout=8)
        txs = data if isinstance(data, list) else []
        rows = []
        for t in txs:
            sats = t.get("value") or 0
            rows.append({
                "txid": (t.get("txid") or "")[:16] + "…",
                "value_btc": round(sats / 1e8, 4),
                "fee_sat": t.get("fee"),
                "size_vb": t.get("vsize"),
            })
        rows.sort(key=lambda r: -(r["value_btc"] or 0))
        if rows:
            _cache_put("whales:raw", rows)
    if min_value_btc is not None:
        rows = [r for r in rows if (r["value_btc"] or 0) >= min_value_btc]
    return rows[:limit]


# ── Wallet fund-flow trace (bitcoin: mempool.space; solana: public JSON RPC) ─────────
# v1 scope only: bitcoin + solana-native (no SPL tokens, no EVM chains — neither
# mempool.space nor a free no-key RPC gives a comparably good tx-history API for
# those). One hop per call — the frontend pivots to a counterparty by calling this
# again with that address, there is no server-side recursion.
async def _btc_address_lookup(address: str) -> Optional[dict]:
    summary = await _get_json(f"https://mempool.space/api/address/{address}", timeout=8)
    if not summary:
        return None
    txs = await _get_json(f"https://mempool.space/api/address/{address}/txs", timeout=10) or []
    cs = summary.get("chain_stats", {})
    balance_sats = cs.get("funded_txo_sum", 0) - cs.get("spent_txo_sum", 0)
    rows = []
    for t in txs[:25]:
        vin = t.get("vin", [])
        vout = t.get("vout", [])
        sent = sum(
            (v.get("prevout") or {}).get("value", 0) for v in vin
            if (v.get("prevout") or {}).get("scriptpubkey_address") == address
        )
        received = sum(v.get("value", 0) for v in vout if v.get("scriptpubkey_address") == address)
        net = received - sent
        counterparties = []
        if net < 0:
            for v in vout:
                a = v.get("scriptpubkey_address")
                if a and a != address:
                    counterparties.append({"address": a, "role": "recipient", "amount": round(v.get("value", 0) / 1e8, 8)})
        else:
            for v in vin:
                prevout = v.get("prevout") or {}
                a = prevout.get("scriptpubkey_address")
                if a and a != address:
                    counterparties.append({"address": a, "role": "sender", "amount": round(prevout.get("value", 0) / 1e8, 8)})
        rows.append({
            "txid": t.get("txid"),
            "timestamp": (t.get("status") or {}).get("block_time"),
            "confirmed": (t.get("status") or {}).get("confirmed", False),
            "direction": "out" if net < 0 else "in",
            "amount": round(abs(net) / 1e8, 8),
            "fee": round(t.get("fee", 0) / 1e8, 8),
            "counterparties": counterparties[:10],
        })
    return {
        "balance": {"amount": round(balance_sats / 1e8, 8), "unit": "BTC"},
        "tx_count": cs.get("tx_count", 0),
        "transactions": rows,
    }


_SOL_RPC_URL = "https://api.mainnet-beta.solana.com"


async def _sol_rpc(method: str, params: list, timeout: float = 10) -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=UA) as c:
            r = await c.post(_SOL_RPC_URL, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            if r.status_code == 200:
                return r.json().get("result")
    except Exception as e:
        logger.debug("solana RPC %s failed: %s", method, e)
    return None


def _sol_token_transfers(p: dict, address: str) -> list[dict]:
    """SPL token deltas for `address` within an already-parsed transaction `p`,
    via pre/post *token*-balance deltas (meta.preTokenBalances/postTokenBalances —
    already present in the jsonParsed getTransaction response _sol_address_lookup
    fetches, at zero extra RPC cost; just unused until now). Counterparties are
    other token accounts with an opposite-signed delta for the same mint."""
    try:
        pre_tok = p["meta"].get("preTokenBalances") or []
        post_tok = p["meta"].get("postTokenBalances") or []
    except (KeyError, TypeError):
        return []
    if not pre_tok and not post_tok:
        return []

    pre_by_idx = {b["accountIndex"]: b for b in pre_tok}
    post_by_idx = {b["accountIndex"]: b for b in post_tok}

    def ui_amount(b: Optional[dict]) -> float:
        return ((b or {}).get("uiTokenAmount") or {}).get("uiAmount") or 0.0

    # Per-mint delta for every token account touched in this tx, keyed by owner.
    deltas_by_mint: dict[str, dict[str, float]] = {}
    for idx in set(pre_by_idx) | set(post_by_idx):
        b = post_by_idx.get(idx) or pre_by_idx.get(idx)
        mint, owner = b.get("mint"), b.get("owner")
        if not mint or not owner:
            continue
        delta = ui_amount(post_by_idx.get(idx)) - ui_amount(pre_by_idx.get(idx))
        if delta:
            deltas_by_mint.setdefault(mint, {})[owner] = deltas_by_mint.get(mint, {}).get(owner, 0) + delta

    transfers = []
    for mint, by_owner in deltas_by_mint.items():
        own_delta = by_owner.get(address)
        if not own_delta:
            continue
        counterparties = [
            {"address": owner, "role": "recipient" if own_delta < 0 else "sender", "amount": round(abs(d), 9)}
            for owner, d in by_owner.items()
            if owner != address and d != 0 and (d > 0) != (own_delta > 0)
        ]
        transfers.append({
            "mint": mint,
            "direction": "out" if own_delta < 0 else "in",
            "amount": round(abs(own_delta), 9),
            "counterparties": sorted(counterparties, key=lambda c: -c["amount"])[:10],
        })
    return transfers


async def _sol_address_lookup(address: str, limit: int = 15) -> Optional[dict]:
    """Native-SOL balance + recent transactions with counterparties, via pre/post
    account-balance deltas, plus SPL token transfers (see _sol_token_transfers)."""
    bal = await _sol_rpc("getBalance", [address])
    if bal is None:
        return None
    sigs = await _sol_rpc("getSignaturesForAddress", [address, {"limit": limit}]) or []
    sem = asyncio.Semaphore(3)

    async def fetch(sig: str):
        async with sem:
            return await _sol_rpc(
                "getTransaction",
                [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                timeout=12,
            )

    parsed = await asyncio.gather(*[fetch(s["signature"]) for s in sigs])
    rows = []
    for s, p in zip(sigs, parsed):
        if not p:
            continue
        try:
            keys = [k.get("pubkey") for k in p["transaction"]["message"]["accountKeys"]]
            pre, post = p["meta"]["preBalances"], p["meta"]["postBalances"]
        except (KeyError, TypeError):
            continue
        if address not in keys:
            continue
        i = keys.index(address)
        delta = (post[i] - pre[i]) / 1e9
        counterparties = []
        for j, k in enumerate(keys):
            if k == address:
                continue
            d2 = (post[j] - pre[j]) / 1e9
            if d2 != 0 and (d2 > 0) != (delta > 0):
                counterparties.append({"address": k, "role": "recipient" if delta < 0 else "sender", "amount": round(abs(d2), 9)})
        rows.append({
            "txid": s["signature"],
            "timestamp": s.get("blockTime"),
            "confirmed": s.get("confirmationStatus") == "finalized",
            "direction": "out" if delta < 0 else "in",
            "amount": round(abs(delta), 9),
            "fee": round(p["meta"].get("fee", 0) / 1e9, 9),
            "counterparties": sorted(counterparties, key=lambda c: -c["amount"])[:10],
            "token_transfers": _sol_token_transfers(p, address),
        })
    return {
        "balance": {"amount": round(bal["value"] / 1e9, 9), "unit": "SOL"},
        "tx_count": len(sigs),
        "transactions": rows,
    }


_CHAIN_ALIASES = {"btc": "bitcoin", "bitcoin": "bitcoin", "sol": "solana", "solana": "solana"}


async def address_lookup(chain: str, address: str) -> dict:
    """Balance + annotated in/out transactions for an address — bitcoin/solana
    only. Fails soft (`supported: False`) for anything else, never raises."""
    canon = _CHAIN_ALIASES.get((chain or "").lower())
    key = f"trace:{canon}:{address}"
    cached = _cache_get(key, 20)
    if cached is not None:
        return cached
    if canon == "bitcoin":
        data = await _btc_address_lookup(address)
        source = "mempool.space"
    elif canon == "solana":
        data = await _sol_address_lookup(address)
        source = "solana-rpc"
    else:
        return {
            "chain": chain, "address": address, "supported": False,
            "reason": f"Chain '{chain}' not supported for live trace yet.", "transactions": [],
        }
    if data is None:
        return {
            "chain": canon, "address": address, "supported": True,
            "reason": "Address lookup failed or address has no history.", "transactions": [],
        }
    out = {"chain": canon, "address": address, "supported": True, "source": source, **data}
    return _cache_put(key, out)


# ── Wallet watchlist refresh (bounded concurrency over address_lookup) ──────────────
# No free no-key "mempool by value" endpoint exists for Solana the way
# mempool.space's /mempool/recent does for Bitcoin, so there's no equivalent whale
# feed to poll. Instead, whale detection for tracked wallets rides on top of
# address_lookup's own transaction deltas: a large balance move on a wallet you're
# already watching *is* the whale signal, for either chain.
_WATCHLIST_WHALE_THRESHOLD = {"bitcoin": 10.0, "solana": 500.0}


async def refresh_watchlist(wallets: list[dict]) -> list[dict]:
    """Re-run address_lookup for every tracked wallet with bounded concurrency
    (address_lookup's own 20s cache means repeat refreshes inside that window are
    free). Each row is annotated with whale_activity: True if any recent
    transaction's amount is at or above that chain's whale threshold."""
    sem = asyncio.Semaphore(3)

    async def one(w: dict) -> dict:
        async with sem:
            data = await address_lookup(w["chain"], w["address"])
        txs = data.get("transactions") or []
        threshold = _WATCHLIST_WHALE_THRESHOLD.get(data.get("chain"))
        whale_activity = threshold is not None and any((t.get("amount") or 0) >= threshold for t in txs)
        return {
            "id": w["id"],
            "chain": w["chain"],
            "address": w["address"],
            "label": w["label"],
            "address_type": w.get("address_type", "wallet"),
            "notes": w.get("notes", ""),
            "supported": data.get("supported", False),
            "balance": data.get("balance"),
            "tx_count": data.get("tx_count"),
            "recent_transactions": txs,
            "whale_activity": whale_activity,
        }

    return list(await asyncio.gather(*[one(w) for w in wallets]))


# ── CryptoCompare social stats ───────────────────────────────────────────────────
async def cryptocompare_social(symbol: str) -> Optional[dict]:
    """Social stats from CryptoCompare (top posts, social volume). Cached 120s."""
    symbol = symbol.upper()
    key = f"cc_social:{symbol}"
    cached = _cache_get(key, 120)
    if cached is not None:
        return cached
    data = await _get_json(
        f"https://min-api.cryptocompare.com/data/social/coin/latest?coinId={CG_IDS.get(symbol, symbol.lower())}",
        timeout=8,
    )
    if data and data.get("Data"):
        out = {
            "symbol": symbol,
            "reddit_posts": data["Data"].get("Reddit", {}).get("posts_per_hour", 0),
            "twitter_followers": data["Data"].get("Twitter", {}).get("followers", 0),
            "social_score": data["Data"].get("General", {}).get("social_grade", 0),
        }
        return _cache_put(key, out)
    return None


# ── Crypto news headlines (multi-outlet RSS, no key) ───────────────────────────────
_NEWS_FEEDS = [
    ("cryptopanic", "https://cryptopanic.com/news/rss/"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("decrypt", "https://decrypt.co/feed"),
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
]
# Cheap lexical sentiment — enough to tag a headline green/red/grey.
_POS_WORDS = {"surge", "soar", "rally", "gain", "bullish", "breakout", "record", "high",
              "adopt", "approve", "partnership", "upgrade", "boost", "jump", "rise", "wins", "buy"}
_NEG_WORDS = {"crash", "plunge", "drop", "fall", "bearish", "hack", "exploit", "scam", "rug",
              "lawsuit", "ban", "sell-off", "dump", "collapse", "warning", "fear", "liquidat", "down"}
# Common tickers to link a headline back to a symbol.
_NEWS_TICKERS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "MATIC",
                 "DOT", "SUI", "APT", "ARB", "OP", "PEPE", "BONK", "WIF", "JUP", "TIA"]
_NAME_TO_TICKER = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "ripple": "XRP",
                   "dogecoin": "DOGE", "cardano": "ADA", "avalanche": "AVAX", "chainlink": "LINK"}


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"').strip()


def _headline_sentiment(text: str) -> tuple[str, float]:
    t = text.lower()
    pos = sum(1 for w in _POS_WORDS if w in t)
    neg = sum(1 for w in _NEG_WORDS if w in t)
    if pos == neg:
        return "neutral", 0.5
    if pos > neg:
        return "positive", min(0.95, 0.55 + 0.15 * (pos - neg))
    return "negative", min(0.95, 0.55 + 0.15 * (neg - pos))


def _headline_symbols(text: str) -> list[str]:
    found: list[str] = []
    up = f" {text.upper()} "
    for tk in _NEWS_TICKERS:
        if f" {tk} " in up or f"{tk}/" in up or f"${tk}" in up:
            found.append(tk)
    low = text.lower()
    for name, tk in _NAME_TO_TICKER.items():
        if name in low and tk not in found:
            found.append(tk)
    return found[:4]


async def _get_text(url: str, timeout: float = 10.0) -> Optional[str]:
    """GET → raw text (follows redirects), or None on any failure."""
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=NEWS_UA, follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code == 200:
                return r.text
    except Exception as e:
        logger.debug("news GET %s failed: %s", url, e)
    return None


async def crypto_news(limit: int = 40) -> list[dict]:
    """Latest crypto headlines aggregated from several public RSS feeds
    (CryptoPanic, CoinTelegraph, Decrypt, CoinDesk — no keys). Each item is
    lexically sentiment-tagged and linked to any tickers it mentions. Deduped
    by title, newest first. Cached 120s."""
    cached = _cache_get("news:all", 120)
    if cached is not None:
        return cached[:limit]

    feeds = await asyncio.gather(*[_get_text(url) for _, url in _NEWS_FEEDS], return_exceptions=True)
    items: list[dict] = []
    seen: set[str] = set()
    for (source, _url), body in zip(_NEWS_FEEDS, feeds):
        if not isinstance(body, str):
            continue
        for raw in re.findall(r"<item>(.*?)</item>", body, re.DOTALL | re.IGNORECASE)[:25]:
            tm = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", raw, re.DOTALL | re.IGNORECASE)
            lm = re.search(r"<link>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", raw, re.DOTALL | re.IGNORECASE)
            dm = re.search(r"<pubDate>(.*?)</pubDate>", raw, re.DOTALL | re.IGNORECASE)
            if not tm:
                continue
            title = _strip_html(tm.group(1))
            if not title or title.lower() in seen:
                continue
            seen.add(title.lower())
            sentiment, confidence = _headline_sentiment(title)
            items.append({
                "title": title,
                "source": source,
                "url": _strip_html(lm.group(1)) if lm else "",
                "sentiment": sentiment,
                "confidence": confidence,
                "symbols": _headline_symbols(title),
                "timestamp": _strip_html(dm.group(1)) if dm else "",
            })
    # Newest first when pubDate parses; otherwise keep feed order (already recent).
    def _ts(it):
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(it["timestamp"]).timestamp()
        except Exception:
            return 0
    items.sort(key=_ts, reverse=True)
    if items:
        _cache_put("news:all", items)
    return items[:limit]


# ── Messari asset profile (no-key public fields only) ──────────────────────────────
async def messari_profile(symbol: str) -> Optional[dict]:
    """Public asset profile from Messari (no API key required for basic fields).
    Cached 300s. Falls back gracefully if rate-limited."""
    symbol = symbol.upper()
    key = f"messari:{symbol}"
    cached = _cache_get(key, 300)
    if cached is not None:
        return cached
    data = await _get_json(
        f"https://data.messari.io/api/v1/assets/{symbol.lower()}/profile",
        timeout=8,
    )
    if data and data.get("status", {}).get("elapsed") is not None:
        d = data.get("data", {})
        out = {
            "symbol": symbol,
            "name": data.get("name", d.get("name", "")),
            "description": (d.get("profile") or {}).get("general", {}).get("overview", {}).get("project_details", ""),
            "category": d.get("profile", {}).get("general", {}).get("overview", {}).get("category", ""),
            "sector": d.get("profile", {}).get("general", {}).get("overview", {}).get("sector", ""),
            "tagline": d.get("profile", {}).get("general", {}).get("overview", {}).get("tagline", ""),
        }
        if out["symbol"]:
            return _cache_put(key, out)
    return None


# ── 0x swap/liquidity quotes ────────────────────────────────────────────────────
async def zerox_liquidity(symbol: str) -> Optional[dict]:
    """DEX swap quote + liquidity depth from 0x API. Cached 30s."""
    symbol = symbol.upper()
    key = f"0xliq:{symbol}"
    cached = _cache_get(key, 30)
    if cached is not None:
        return cached
    # 0x requires a token address — map common symbols
    TOKEN_MAP = {
        "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "DAI": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
    }
    buy_token = TOKEN_MAP.get(symbol)
    if not buy_token:
        return None
    data = await _get_json(
        f"https://api.0x.org/swap/v1/quote?buyToken={buy_token}"
        f"&sellToken={TOKEN_MAP['USDC']}&sellAmount=1000000",
        timeout=8,
    )
    if data and data.get("price"):
        out = {
            "symbol": symbol,
            "price": float(data["price"]),
            "liquidity_available": float(data.get("buyAmount", 0)) / 1e18,
            "protocol_fee": float(data.get("protocolFee", 0)),
        }
        return _cache_put(key, out)
    return None


# ── BitcoinCharts historical data ────────────────────────────────────────────────
async def bitcoincharts_history(symbol: str = "BTC", days: int = 365) -> Optional[list]:
    """Historical BTC price data from BitcoinCharts. Cached 600s."""
    if symbol.upper() != "BTC":
        return None
    key = f"btcharts:{days}"
    cached = _cache_get(key, 600)
    if cached is not None:
        return cached
    data = await _get_json(
        f"https://api.bitcoincharts.com/v1/markets.json",
        timeout=10,
    )
    if isinstance(data, list) and data:
        # Return top exchanges with price data
        out = []
        for m in data[:20]:
            if m.get("close"):
                out.append({
                    "exchange": m.get("symbol", "?"),
                    "currency": m.get("currency", "USD"),
                    "close": float(m["close"]),
                    "volume": float(m.get("volume", 0)),
                })
        if out:
            return _cache_put(key, out)
    return None


# ── Cryptonator multi-exchange rates ─────────────────────────────────────────────
async def cryptonator_rates(symbol: str = "BTC") -> Optional[dict]:
    """Cross-exchange pricing from Cryptonator. Cached 60s."""
    symbol = symbol.upper()
    key = f"cryptonator:{symbol}"
    cached = _cache_get(key, 60)
    if cached is not None:
        return cached
    data = await _get_json(
        f"https://api.cryptonator.com/api/ticker/{symbol.lower()}-usd",
        timeout=8,
    )
    if data and data.get("ticker"):
        t = data["ticker"]
        out = {
            "symbol": t.get("base", symbol),
            "price": float(t.get("price", 0)),
            "volume": float(t.get("volume", 0)),
            "change": float(t.get("change", 0)),
        }
        return _cache_put(key, out)
    return None


# ── Nexchange cross-exchange pricing ─────────────────────────────────────────────
async def nexchange_rates() -> Optional[list]:
    """Exchange rates from Nexchange. Cached 300s."""
    key = "nexchange"
    cached = _cache_get(key, 300)
    if cached is not None:
        return cached
    data = await _get_json("https://api.n.exchange/en/api/v1/price/", timeout=8)
    if isinstance(data, list) and data:
        out = []
        for p in data[:50]:
            out.append({
                "pair": f"{p.get('name', {}).get('base', '?')}/{p.get('name', {}).get('quote', '?')}",
                "price": float(p.get("ticker", {}).get("price", 0)),
                "change_24h": float(p.get("ticker", {}).get("change", 0)),
            })
        if out:
            return _cache_put(key, out)
    return None


# ── CoinMap adoption data ────────────────────────────────────────────────────────
async def coinmap_adoption() -> Optional[list]:
    """Crypto merchant/ATM locations from CoinMap. Cached 3600s (1hr)."""
    key = "coinmap"
    cached = _cache_get(key, 3600)
    if cached is not None:
        return cached
    data = await _get_json("https://coinmap.org/api/v1/venues/", timeout=10)
    if data and data.get("venues"):
        venues = data["venues"][:50]
        out = []
        for v in venues:
            out.append({
                "name": v.get("name", ""),
                "lat": float(v.get("lat", 0)),
                "lon": float(v.get("lon", 0)),
                "category": v.get("category", ""),
            })
        return _cache_put(key, out)
    return None


# ── Binance 24hr ticker (all pairs) ──────────────────────────────────────────────
async def binance_ticker_all() -> Optional[list]:
    """24hr ticker for all Binance pairs. Cached 30s."""
    key = "bin_ticker"
    cached = _cache_get(key, 30)
    if cached is not None:
        return cached
    data = await _get_json("https://api4.binance.com/api/v3/ticker/24hr", timeout=10)
    if isinstance(data, list):
        out = []
        for t in data[:100]:
            sym = t.get("symbol", "")
            if sym.endswith("USDT"):
                out.append({
                    "symbol": sym[:-4],
                    "price": float(t.get("lastPrice", 0)),
                    "change_24h": float(t.get("priceChangePercent", 0)),
                    "volume_24h": float(t.get("volume", 0)),
                    "high_24h": float(t.get("highPrice", 0)),
                    "low_24h": float(t.get("lowPrice", 0)),
                    "count": int(t.get("count", 0)),
                })
        if out:
            return _cache_put(key, out)
    return None


# ── Real backtest (SMA crossover vs buy-and-hold over CoinGecko history) ─────────────
async def backtest(symbol: str, days: int = 90, fast: int = 10, slow: int = 30) -> Optional[dict]:
    """Backtest a fast/slow SMA-crossover strategy against buy-and-hold over real
    daily closes. Returns total returns, trade count, win rate. Cached 600s."""
    symbol = symbol.upper()
    key = f"bt:{symbol}:{days}:{fast}:{slow}"
    cached = _cache_get(key, 600)
    if cached is not None:
        return cached
    cid = CG_IDS.get(symbol, symbol.lower())
    data = await _get_json(
        f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart?vs_currency=usd&days={days}&interval=daily",
        timeout=10,
    )
    prices = [p[1] for p in (data or {}).get("prices", []) if len(p) > 1]
    if len(prices) < slow + 2:
        return None

    def sma(arr, n, i):
        return sum(arr[i - n:i]) / n

    position = 0
    entry = 0.0
    rets: list[float] = []
    for i in range(slow, len(prices)):
        f, s = sma(prices, fast, i), sma(prices, slow, i)
        if f > s and position == 0:
            position, entry = 1, prices[i]
        elif f < s and position == 1:
            rets.append(prices[i] / entry - 1)
            position = 0
    if position == 1:
        rets.append(prices[-1] / entry - 1)

    strat = 1.0
    for r in rets:
        strat *= (1 + r)
    strat_return = (strat - 1) * 100
    bh_return = (prices[-1] / prices[0] - 1) * 100
    wins = len([r for r in rets if r > 0])
    out = {
        "symbol": symbol,
        "days": days,
        "strategy": f"SMA {fast}/{slow} crossover",
        "strategy_return_pct": round(strat_return, 2),
        "buy_hold_return_pct": round(bh_return, 2),
        "trades": len(rets),
        "win_rate_pct": round(wins / len(rets) * 100, 1) if rets else 0,
        "beat_buy_hold": strat_return > bh_return,
        "data_points": len(prices),
    }
    return _cache_put(key, out)


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
    {"name": "CoinCap", "category": "market", "url": "https://api.coincap.io", "integrated": True},
    {"name": "CoinPaprika", "category": "market", "url": "https://api.coinpaprika.com", "integrated": True},
    {"name": "CoinLore", "category": "market", "url": "https://api.coinlore.net", "integrated": False},
    {"name": "CoinDesk", "category": "market", "url": "https://api.coindesk.com", "integrated": True},
    {"name": "CryptoCompare", "category": "market", "url": "https://min-api.cryptocompare.com", "integrated": True},
    {"name": "Messari", "category": "fundamentals", "url": "https://data.messari.io", "integrated": True},
    {"name": "DefiLlama", "category": "defi", "url": "https://yields.llama.fi", "integrated": True},
    {"name": "DEX Screener", "category": "dex", "url": "https://api.dexscreener.com", "integrated": True},
    {"name": "GeckoTerminal", "category": "dex", "url": "https://api.geckoterminal.com", "integrated": True},
    {"name": "0x", "category": "dex", "url": "https://api.0x.org", "integrated": True},
    {"name": "1inch", "category": "dex", "url": "https://api.1inch.io", "integrated": False},
    {"name": "Kraken", "category": "exchange", "url": "https://api.kraken.com", "integrated": True},
    {"name": "WazirX", "category": "exchange", "url": "https://api.wazirx.com", "integrated": False},
    {"name": "Bitcambio", "category": "exchange", "url": "https://nova.bitcambio.com.br", "integrated": False},
    {"name": "MercadoBitcoin", "category": "exchange", "url": "https://www.mercadobitcoin.com.br", "integrated": False},
    {"name": "Cryptonator", "category": "fx", "url": "https://www.cryptonator.com", "integrated": True},
    {"name": "ExchangeRate-API", "category": "fx", "url": "https://open.er-api.com", "integrated": True},
    {"name": "Currency Rates", "category": "fx", "url": "https://cdn.jsdelivr.net", "integrated": False},
    {"name": "NBP", "category": "fx", "url": "https://api.nbp.pl", "integrated": False},
    {"name": "Solana JSON RPC", "category": "rpc", "url": "https://api.mainnet-beta.solana.com", "integrated": True},
    {"name": "ZMOK (ETH RPC)", "category": "rpc", "url": "https://api.zmok.io", "integrated": False},
    {"name": "Mempool (fees)", "category": "onchain", "url": "https://mempool.space", "integrated": True},
    {"name": "CoinMap", "category": "global", "url": "https://coinmap.org", "integrated": True},
    {"name": "Localbitcoins", "category": "p2p", "url": "https://localbitcoins.com", "integrated": False},
    {"name": "Nexchange", "category": "exchange", "url": "https://api.n.exchange", "integrated": True},
    {"name": "CryptingUp", "category": "market", "url": "https://www.cryptingup.com", "integrated": False},
    {"name": "BitcoinCharts", "category": "market", "url": "https://bitcoincharts.com", "integrated": False},
    {"name": "CryptAPI", "category": "payments", "url": "https://api.cryptapi.io", "integrated": False},
    {"name": "Razorpay IFSC", "category": "fiat", "url": "https://ifsc.razorpay.com", "integrated": False},
]
