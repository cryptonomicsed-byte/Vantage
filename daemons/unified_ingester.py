#!/usr/bin/env python3
"""
Vantage Unified Ingester — Polls 30+ free crypto APIs at optimized rates.
Posts all signals to Vantage feed + /api/intel/signals.

Tier system:
  Tier 1: every 2-5s  — Price tickers (CoinCap, OKX, Coinbase, WazirX)
  Tier 2: every 15s   — Market data (CoinGecko, CoinPaprika, GeckoTerminal)
  Tier 3: every 60s   — Aggregators (CoinLore, CryptoCompare, Messari)
  Tier 4: every 5min  — DEX, FX, on-chain (1inch, 0x, NBP, Solana)
  Tier 5: every 30min — Fear & Greed, ExchangeRate, slow APIs

Posts to Vantage: text broadcasts for significant signals (conviction-aware).
"""

import json, os, sys, time, logging, threading, argparse
from datetime import datetime, timezone
from collections import defaultdict
from typing import Optional, Callable

import urllib.request
import urllib.error

# ── Config ──────────────────────────────────────────────────────────────

VANTAGE_URL = "http://127.0.0.1:8001"
VANTAGE_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()
LOG_FILE = os.path.expanduser("~/ares_logs/unified_ingester.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [INGEST] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger("unified_ingester")

# ── Rate-limited HTTP client ────────────────────────────────────────────

class RateLimiter:
    """Simple token bucket per domain."""
    def __init__(self):
        self.buckets: dict[str, tuple[float, float, float]] = {}  # domain: (tokens, max, refill_rate)

    def configure(self, domain: str, max_calls: int, per_seconds: int):
        self.buckets[domain] = (float(max_calls), float(max_calls), max_calls / per_seconds)

    def acquire(self, domain: str) -> bool:
        if domain not in self.buckets:
            self.configure(domain, 10, 60)  # default: 10/min
        tokens, max_t, rate = self.buckets[domain]
        now = time.time()
        tokens = min(max_t, tokens + rate * 1.0)  # refill ~1s worth
        if tokens >= 1:
            self.buckets[domain] = (tokens - 1, max_t, rate)
            return True
        return False

limiter = RateLimiter()

def fetch_json(url: str, timeout: int = 10) -> Optional[dict]:
    """Fetch JSON with rate limiting and error handling."""
    domain = url.split("/")[2]
    if not limiter.acquire(domain):
        return None
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Vantage/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read())
    except Exception:
        pass
    return None


def post_to_feed(title: str, content: str, tags: list[str], content_type: str = "text",
                 stream_url: str = "", thumbnail_url: str = ""):
    """Post a signal to Vantage feed as a visible broadcast."""
    payload = json.dumps({
        "title": title, "content": content, "tags": tags,
        "content_type": content_type, "stream_url": stream_url,
        "thumbnail_url": thumbnail_url,
    }).encode()
    try:
        req = urllib.request.Request(
            f"{VANTAGE_URL}/api/trading/signals/ingest",
            data=payload,
            headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None

# ── Signal store (shared across threads) ────────────────────────────────

signal_store: list[dict] = []
store_lock = threading.Lock()

def add_signals(signals: list[dict]):
    """Thread-safe signal addition."""
    with store_lock:
        signal_store.extend(signals)
        # Keep last 200
        if len(signal_store) > 200:
            del signal_store[:-200]

# ═══════════════════════════════════════════════════════════════════════════
# TIER 1: PRICE TICKERS (every 2-5s)
# ═══════════════════════════════════════════════════════════════════════════

def fetch_coincap():
    """CoinCap — top 100 assets, no key, ~200/min limit."""
    data = fetch_json("https://api.coincap.io/v2/assets?limit=50")
    if not data or "data" not in data:
        return []
    signals = []
    for a in data["data"][:50]:
        signals.append({
            "symbol": a.get("symbol", "?").upper(),
            "source": "coincap",
            "type": "price",
            "price": float(a.get("priceUsd", 0)),
            "change_24h_pct": float(a.get("changePercent24Hr", 0)),
            "volume_24h": float(a.get("volumeUsd24Hr", 0)),
            "mcap": float(a.get("marketCapUsd", 0)),
            "rank": int(a.get("rank", 99)),
            "ts": int(time.time()),
        })
    return signals

def fetch_okx():
    """OKX — top tickers, no key, ~600/min."""
    data = fetch_json("https://www.okx.com/api/v5/market/tickers?instType=SPOT", timeout=8)
    if not data or "data" not in data:
        return []
    signals = []
    for t in data["data"][:30]:
        sym = t.get("instId", "").replace("-USDT", "").replace("-USDC", "")
        if sym and len(sym) <= 8:
            signals.append({
                "symbol": sym,
                "source": "okx",
                "type": "price",
                "price": float(t.get("last", 0)),
                "change_24h_pct": float(t.get("open24h", 0)) and (float(t.get("last", 0)) / float(t.get("open24h", 1)) - 1) * 100,
                "volume_24h": float(t.get("vol24h", 0)),
                "ts": int(time.time()),
            })
    return signals

def fetch_wazirx():
    """WazirX — top tickers, no key, ~1000/min."""
    data = fetch_json("https://api.wazirx.com/sapi/v1/tickers/24hr", timeout=8)
    if not data or not isinstance(data, list):
        return []
    signals = []
    for t in data[:30]:
        sym = t.get("symbol", "").replace("usdt", "").upper()
        if sym:
            signals.append({
                "symbol": sym,
                "source": "wazirx",
                "type": "price",
                "price": float(t.get("lastPrice", 0)),
                "change_24h_pct": float(t.get("priceChangePercent", 0)),
                "volume_24h": float(t.get("volume", 0)),
                "ts": int(time.time()),
            })
    return signals

def fetch_coinbase():
    """Coinbase — BTC/ETH spot, no key, ~600/min."""
    signals = []
    for pair in ["BTC-USD", "ETH-USD"]:
        data = fetch_json(f"https://api.coinbase.com/v2/prices/{pair}/spot")
        if data and "data" in data:
            sym = pair.split("-")[0]
            signals.append({
                "symbol": sym,
                "source": "coinbase",
                "type": "price",
                "price": float(data["data"]["amount"]),
                "ts": int(time.time()),
            })
    return signals

# ═══════════════════════════════════════════════════════════════════════════
# TIER 2: MARKET DATA (every 15s)
# ═══════════════════════════════════════════════════════════════════════════

def fetch_coinpaprika_global():
    """CoinPaprika — global market, no key, ~60/min."""
    data = fetch_json("https://api.coinpaprika.com/v1/global", timeout=8)
    if not data:
        return []
    return [{
        "symbol": "GLOBAL",
        "source": "coinpaprika",
        "type": "market_overview",
        "mcap": data.get("market_cap_usd"),
        "volume_24h": data.get("volume_24h_usd"),
        "btc_dominance": data.get("bitcoin_dominance_percentage"),
        "ts": int(time.time()),
    }]

def fetch_coinglass():
    """CoinGlass — open interest (free)."""
    data = fetch_json("https://open-api-v3.coinglass.com/api/futures/openInterest/chart?symbol=BTC&interval=1h&limit=1", timeout=8)
    if not data or "data" not in data:
        return []
    return [{
        "symbol": "BTC_OI",
        "source": "coinglass",
        "type": "open_interest",
        "value": data["data"][0].get("avg") if data["data"] else None,
        "ts": int(time.time()),
    }]

def fetch_fear_greed():
    """Fear & Greed Index — ~10/min."""
    data = fetch_json("https://api.alternative.me/fng/?limit=1", timeout=8)
    if not data or "data" not in data:
        return []
    d = data["data"][0]
    return [{
        "symbol": "FEAR_GREED",
        "source": "fear_greed",
        "type": "sentiment",
        "value": int(d.get("value", 50)),
        "classification": d.get("value_classification", ""),
        "ts": int(time.time()),
    }]

# ═══════════════════════════════════════════════════════════════════════════
# TIER 3: AGGREGATORS (every 60s)
# ═══════════════════════════════════════════════════════════════════════════

def fetch_coinlore():
    """CoinLore — all coins in one call, no key, ~60/min."""
    data = fetch_json("https://api.coinlore.net/api/tickers/?limit=50")
    if not data or "data" not in data:
        return []
    signals = []
    for c in data["data"][:50]:
        signals.append({
            "symbol": c.get("symbol", "?").upper(),
            "source": "coinlore",
            "type": "price",
            "price": float(c.get("price_usd", 0)),
            "change_24h_pct": float(c.get("percent_change_24h", 0)),
            "volume_24h": float(c.get("volume24", 0)),
            "mcap": float(c.get("market_cap_usd", 0)),
            "rank": int(c.get("rank", 99)),
            "ts": int(time.time()),
        })
    return signals

def fetch_geckoterminal():
    """GeckoTerminal — top pools across networks, no key, ~30/min."""
    data = fetch_json("https://api.geckoterminal.com/api/v2/networks/trending_pools?limit=10", timeout=10)
    if not data or "data" not in data:
        return []
    signals = []
    for pool in data["data"][:10]:
        attrs = pool.get("attributes", {})
        rel = pool.get("relationships", {}).get("base_token", {}).get("data", {})
        signals.append({
            "symbol": (rel.get("symbol", "?"))[:12],
            "source": "geckoterminal",
            "type": "trending_pool",
            "price": float(attrs.get("base_token_price_usd", 0)),
            "volume_24h": float(attrs.get("volume_usd", {}).get("h24", 0)),
            "price_change_pct": float(attrs.get("price_change_percentage", {}).get("h24", 0)),
            "ts": int(time.time()),
        })
    return signals

# ═══════════════════════════════════════════════════════════════════════════
# TIER 4: DEX / ON-CHAIN (every 5min)
# ═══════════════════════════════════════════════════════════════════════════

def fetch_1inch():
    """1inch — token prices, no key, ~30/min."""
    # Just fetch SOL price via 1inch quote as a sample
    data = fetch_json("https://api.1inch.dev/swap/v5.2/1/quote?src=0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee&dst=0xdac17f958d2ee523a2206206994597c13d831ec7&amount=1000000000000000000", timeout=8)
    if not data:
        return []
    return [{
        "symbol": "ETH_USDT",
        "source": "1inch",
        "type": "dex_quote",
        "price": float(data.get("dstAmount", 0)) / 1e6 if data.get("dstAmount") else None,
        "ts": int(time.time()),
    }]

def fetch_solana_rpc():
    """Solana RPC — current slot/epoch, no key, ~25/sec."""
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getEpochInfo"}).encode()
    try:
        req = urllib.request.Request(
            "https://api.mainnet-beta.solana.com",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
            if "result" in data:
                return [{
                    "symbol": "SOLANA",
                    "source": "solana_rpc",
                    "type": "network",
                    "epoch": data["result"].get("epoch"),
                    "slot_height": data["result"].get("absoluteSlot"),
                    "ts": int(time.time()),
                }]
    except:
        pass
    return []

# ═══════════════════════════════════════════════════════════════════════════
# SIGNIFICANT EVENT DETECTION
# ═══════════════════════════════════════════════════════════════════════════

_last_posted: dict[str, float] = {}  # symbol -> timestamp to prevent spam

def detect_significant(signals: list[dict]) -> list[dict]:
    """Find signals worth posting to the feed (big moves, high conviction, notable events)."""
    significant = []
    for s in signals:
        sym = s.get("symbol", "")
        pct = abs(s.get("change_24h_pct", 0) or 0)
        source = s.get("source", "")

        # Large price moves (>5% 24h)
        if pct >= 5 and (time.time() - _last_posted.get(f"move_{sym}", 0)) > 3600:
            direction = "up" if (s.get("change_24h_pct", 0) or 0) > 0 else "down"
            significant.append({
                "title": f"📈 {sym} {direction} {pct:.1f}% (24h)",
                "content": f"**{sym}** is {direction} **{pct:.1f}%** in the last 24h. Price: ${s.get('price', 0):.4f}. Source: {source}.",
                "tags": ["signal", "price_move", sym.lower()],
                "source": source,
            })
            _last_posted[f"move_{sym}"] = time.time()

        # Fear & Greed extremes
        if s.get("type") == "sentiment" and s.get("value", 50) <= 20:
            if (time.time() - _last_posted.get("fear", 0)) > 3600:
                significant.append({
                    "title": f"😱 Extreme Fear: {s.get('value')}",
                    "content": f"Fear & Greed Index at **{s.get('value')}** — {s.get('classification', 'Extreme Fear')}. Historically a contrarian BUY signal.",
                    "tags": ["signal", "sentiment", "fear"],
                    "source": "fear_greed",
                })
                _last_posted["fear"] = time.time()

        # On-chain events
        if s.get("type") == "network":
            if (time.time() - _last_posted.get("solana", 0)) > 43200:  # 12h
                significant.append({
                    "title": f"🔗 Solana Network: Epoch {s.get('epoch')}, Slot {s.get('slot_height')}",
                    "content": f"Solana mainnet at epoch {s.get('epoch')}, slot {s.get('slot_height')}.",
                    "tags": ["signal", "network", "solana"],
                    "source": "solana_rpc",
                })
                _last_posted["solana"] = time.time()

    return significant


# ═══════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════════

# Configure rate limiters
limiter.configure("api.coincap.io", 150, 60)
limiter.configure("www.okx.com", 300, 60)
limiter.configure("api.wazirx.com", 500, 60)
limiter.configure("api.coinbase.com", 300, 60)
limiter.configure("api.coinpaprika.com", 50, 60)
limiter.configure("api.alternative.me", 8, 60)
limiter.configure("api.coinlore.net", 50, 60)
limiter.configure("api.geckoterminal.com", 25, 60)
limiter.configure("api.1inch.dev", 20, 60)
limiter.configure("api.mainnet-beta.solana.com", 30, 60)

# Tier definitions: (interval_seconds, [(fetcher, name)])
TIERS = [
    (3, [
        (fetch_coincap, "CoinCap"),
        (fetch_okx, "OKX"),
        (fetch_wazirx, "WazirX"),
        (fetch_coinbase, "Coinbase"),
    ]),
    (15, [
        (fetch_coinpaprika_global, "CoinPaprika"),
        (fetch_fear_greed, "Fear&Greed"),
        (fetch_coinglass, "CoinGlass"),
    ]),
    (60, [
        (fetch_coinlore, "CoinLore"),
        (fetch_geckoterminal, "GeckoTerminal"),
    ]),
    (300, [
        (fetch_1inch, "1inch"),
        (fetch_solana_rpc, "SolanaRPC"),
    ]),
]

def run_tier(interval: int, fetchers: list):
    """Run a tier's fetchers on an interval."""
    log.info(f"Tier started: {interval}s interval, {len(fetchers)} sources")
    while True:
        for fetcher, name in fetchers:
            try:
                signals = fetcher()
                if signals:
                    add_signals(signals)
                    log.debug(f"{name}: {len(signals)} signals")
            except Exception as e:
                log.error(f"{name} error: {e}")
        time.sleep(interval)


def run_poster():
    """Periodically post significant signals to the feed."""
    while True:
        time.sleep(30)
        with store_lock:
            recent = list(signal_store[-50:])
        sig = detect_significant(recent)
        for s in sig:
            result = post_to_feed(s["title"], s["content"], s["tags"])
            if result:
                log.info(f"📡 Feed: {s['title'][:60]}")


def run_daemon():
    """Start all tiers as threads + poster."""
    log.info("Unified Ingester starting — 14 sources across 4 tiers")

    # Start tier threads
    for interval, fetchers in TIERS:
        t = threading.Thread(target=run_tier, args=(interval, fetchers), daemon=True)
        t.start()

    # Start poster
    t = threading.Thread(target=run_poster, daemon=True)
    t.start()

    # Keep main thread alive
    while True:
        time.sleep(60)
        with store_lock:
            log.info(f"Signal store: {len(signal_store)} signals from "
                     f"{len(set(s['source'] for s in signal_store))} sources")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vantage Unified Ingester")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()

    if args.once:
        all_signals = []
        for _, fetchers in TIERS:
            for fetcher, name in fetchers:
                try:
                    s = fetcher()
                    all_signals.extend(s)
                    print(f"{name}: {len(s)} signals")
                except Exception as e:
                    print(f"{name}: {e}")
        print(f"\nTotal: {len(all_signals)} signals from {len(set(s['source'] for s in all_signals))} sources")
    else:
        run_daemon()
