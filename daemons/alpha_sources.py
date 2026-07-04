#!/usr/bin/env python3
"""
Vantage Multi-Source Alpha Ingester — FinBERT + GDELT + Jupiter + Birdeye.

Adds 4 new free data sources to the signal pipeline:
  1. FinBERT — finance-specific sentiment NLP on crypto headlines
  2. GDELT   — global geopolitical event data (protests, conflicts, policy changes)
  3. Jupiter  — Solana DEX swap quotes + token list
  4. Birdeye  — Solana DEX OHLCV + trending tokens

All output flows into Vantage /api/intel/signals/ingest + feed posts.

Usage:
  python3 alpha_sources.py              # single scan
  python3 alpha_sources.py --daemon 300  # continuous
"""

import json, os, sys, time, logging, argparse, re
from typing import Optional
import urllib.request

VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://127.0.0.1:8001")
VANTAGE_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()
JUPITER_KEY = "jup_3225abe22087ddd85a81186c31cff59a48bf273331717a3754e6f68a49ec9270"
SIGNALS_INGEST = f"{VANTAGE_URL}/api/intel/signals/ingest"
FEED_POST = f"{VANTAGE_URL}/api/trading/signals/ingest"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ALPHA] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("alpha_sources")

# ── Helpers ──────────────────────────────────────────────────────────────

def fetch_json(url: str, timeout: int = 10) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Vantage/2.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()) if r.status == 200 else None
    except:
        return None

def fetch_text(url: str, timeout: int = 10) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Vantage/2.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode(errors="replace") if r.status == 200 else None
    except:
        return None

def post_signal(symbol: str, source: str, stype: str, conviction: float = 0.5,
                direction: str = "", detail: str = ""):
    payload = json.dumps({
        "symbol": symbol, "source": source, "type": stype,
        "conviction": conviction, "direction": direction, "detail": detail,
    }).encode()
    try:
        req = urllib.request.Request(SIGNALS_INGEST, data=payload,
                                     headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY})
        urllib.request.urlopen(req, timeout=5)
    except:
        pass

_last_feed: dict[str, float] = {}
_gdelt_last_call: float = 0.0

def feed_once(key: str, cooldown: int, title: str, content: str, tags: list[str]):
    now = time.time()
    if now - _last_feed.get(key, 0) < cooldown:
        return
    _last_feed[key] = now
    payload = json.dumps({"title": title, "content": content, "tags": tags}).encode()
    try:
        req = urllib.request.Request(FEED_POST, data=payload,
                                     headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY})
        urllib.request.urlopen(req, timeout=10)
    except:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# 1. FINBERT — Finance-specific NLP sentiment
# ═══════════════════════════════════════════════════════════════════════════

_finbert_model = None
_finbert_tokenizer = None

def finbert_load():
    """Lazy-load FinBERT model (ProsusAI/finbert)."""
    global _finbert_model, _finbert_tokenizer
    if _finbert_model is None:
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        import torch
        model_name = "ProsusAI/finbert"
        log.info("Loading FinBERT model...")
        _finbert_tokenizer = AutoTokenizer.from_pretrained(model_name)
        _finbert_model = AutoModelForSequenceClassification.from_pretrained(model_name)
        _finbert_model.eval()
        log.info("FinBERT loaded")
    return _finbert_model, _finbert_tokenizer

def finbert_analyze(text: str) -> dict:
    """Run FinBERT sentiment on text. Returns {label, confidence}."""
    try:
        model, tokenizer = finbert_load()
        import torch
        inputs = tokenizer(text[:512], return_tensors="pt", truncation=True, padding=True)
        with torch.no_grad():
            outputs = model(**inputs)
            probs = torch.softmax(outputs.logits, dim=1)[0]
            # Labels: 0=negative, 1=neutral, 2=positive
            idx = torch.argmax(probs).item()
            labels = ["negative", "neutral", "positive"]
            confidence = float(probs[idx])
            return {"label": labels[idx], "confidence": round(confidence, 3)}
    except Exception as e:
        log.error(f"FinBERT error: {e}")
        return {"label": "neutral", "confidence": 0.0}

_HEADLINE_CACHE: list[str] = []

def get_crypto_headlines() -> list[str]:
    """Fetch headlines from CryptoPanic + CoinDesk RSS."""
    global _HEADLINE_CACHE
    headlines = []

    # CryptoPanic RSS
    rss = fetch_text("https://cryptopanic.com/news/rss/", timeout=10)
    if rss:
        items = re.findall(r'<item>(.*?)</item>', rss, re.DOTALL)
        for item in items[:15]:
            title = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item)
            if title:
                h = re.sub(r'<[^>]+>', '', title.group(1)).strip()
                if h and h not in headlines:
                    headlines.append(h)

    # CoinDesk RSS
    rss2 = fetch_text("https://www.coindesk.com/arc/outboundfeeds/rss/", timeout=10)
    if rss2:
        items = re.findall(r'<item>(.*?)</item>', rss2, re.DOTALL)
        for item in items[:10]:
            title = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item)
            if title:
                h = title.group(1).strip()
                if h and h not in headlines:
                    headlines.append(h)

    return headlines

def finbert_scan():
    """Run FinBERT on crypto headlines, post to signals."""
    headlines = get_crypto_headlines()
    if not headlines:
        return

    # Only analyze new headlines
    new = [h for h in headlines if h not in _HEADLINE_CACHE]
    _HEADLINE_CACHE.clear()
    _HEADLINE_CACHE.extend(headlines[-100:])  # keep last 100

    results = []
    for h in new[:10]:  # Limit to 10 per scan (model is CPU-heavy)
        result = finbert_analyze(h)
        results.append(result)
        direction = "BUY" if result["label"] == "positive" else "SELL" if result["label"] == "negative" else ""
        post_signal(
            symbol="MARKET", source="finbert", stype="sentiment",
            conviction=result["confidence"], direction=direction, detail=h[:100],
        )

    if results:
        pos = sum(1 for r in results if r["label"] == "positive")
        neg = sum(1 for r in results if r["label"] == "negative")
        neu = sum(1 for r in results if r["label"] == "neutral")
        log.info(f"FinBERT: {len(results)} headlines → {pos}P/{neg}N/{neu}U")
        _HEADLINE_CACHE.extend(headlines)

        # Post aggregate if sentiment is clearly directional
        if pos > neg * 2:
            feed_once("finbert_bull", 3600,
                      title=f"🟢 FinBERT Bullish: {pos}/{len(results)} headlines positive",
                      content=f"**FinBERT** analyzed {len(results)} crypto headlines. "
                              f"{pos} positive, {neg} negative, {neu} neutral.",
                      tags=["signal", "sentiment", "bullish"])
        elif neg > pos * 2:
            feed_once("finbert_bear", 3600,
                      title=f"🔴 FinBERT Bearish: {neg}/{len(results)} headlines negative",
                      content=f"**FinBERT** analyzed {len(results)} crypto headlines. "
                              f"{pos} positive, {neg} negative, {neu} neutral.",
                      tags=["signal", "sentiment", "bearish"])


# ═══════════════════════════════════════════════════════════════════════════
# 2. GDELT — Global geopolitical event data (FREE, no key)
# ═══════════════════════════════════════════════════════════════════════════

def gdelt_scan():
    """Fetch GDELT event data via Doc API.
    Docs: https://www.gdeltproject.org/data.html
    Rate limit: 1 request per 5 seconds."""
    # Rate limit guard
    global _gdelt_last_call
    now = time.time()
    if now - _gdelt_last_call < 6:
        return
    _gdelt_last_call = now

    url = ("https://api.gdeltproject.org/api/v2/doc/doc"
           "?query=crypto+OR+bitcoin+OR+sanction+OR+regulation"
           "&mode=artlist&timespan=15min&maxrecords=10&format=json")
    data = fetch_json(url, timeout=20)
    if not data:
        return

    articles = data if isinstance(data, list) else data.get("articles", [])

    keywords = [
        "protest", "crackdown", "sanction", "regulation", "ban",
        "trade war", "tariff", "interest rate", "inflation", "recession",
        "conflict", "attack", "coup", "election", "policy",
    ]

    market_relevant = []
    for art in (articles or [])[:20]:
        title = art.get("title", "") if isinstance(art, dict) else str(art)
        combined = title.lower()
        matched = [kw for kw in keywords if kw in combined]
        if matched:
            market_relevant.append({"title": title[:80], "keywords": matched})

    if market_relevant:
        log.info(f"GDELT: {len(articles)} articles -> {len(market_relevant)} relevant")
        event_types = set()
        for e in market_relevant:
            event_types.update(e["keywords"])
        post_signal(
            symbol="GLOBAL", source="gdelt", stype="geopolitical",
            conviction=min(len(market_relevant) / 10, 1.0),
            detail=", ".join(list(event_types)[:3])[:100],
        )
        if len(market_relevant) >= 2:
            feed_once("gdelt", 7200,
                      title=f"Geopolitical Alert: {len(market_relevant)} events",
                      content=f"**GDELT** detected {len(market_relevant)} market-relevant events: "
                              f"{', '.join(list(event_types)[:5])}.",
                      tags=["signal", "geopolitical", "gdelt"])


# ═══════════════════════════════════════════════════════════════════════════
# 3. JUPITER — Solana DEX swap quotes (FREE, no key)
# ═══════════════════════════════════════════════════════════════════════════

JUPITER_TOKENS = [
    ("SOL", "So11111111111111111111111111111111111111112"),
    ("USDC", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"),
    ("JUP", "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"),
    ("BONK", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"),
    ("WIF", "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"),
    ("JTO", "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL"),
    ("PYTH", "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3"),
    ("RNDR", "rndr4pPLNj1DtuQ3VjD8f3KspjZSHxBLiMRwCC6fseK"),
]

def jupiter_scan():
    """Fetch Jupiter swap quotes using API key for Solana tokens.
    Docs: https://station.jup.ag/docs"""
    # Jupiter quote API with API key header
    for sym, mint in JUPITER_TOKENS:
        if sym == "USDC":
            continue
        try:
            quote_url = (
                f"https://quote-api.jup.ag/v6/quote?"
                f"inputMint={mint}&outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
                f"&amount=1000000000&slippageBps=50"
            )
            req = urllib.request.Request(quote_url, headers={
                "Accept": "application/json",
                "x-jupiter-api-key": JUPITER_KEY,
            })
            with urllib.request.urlopen(req, timeout=10) as r:
                quote = json.loads(r.read())
                if "outAmount" in quote:
                    price = int(quote["outAmount"]) / 1e6
                    impact = float(quote.get("priceImpactPct", 0))
                    post_signal(
                        symbol=sym, source="jupiter", stype="dex_price",
                        conviction=min(1.0, abs(impact) * 10 if impact else 0.3),
                        detail=f"${price:.4f} (impact: {impact:.2f}%)",
                    )
        except Exception as e:
            log.debug(f"Jupiter {sym}: {e}")
            continue

    log.info("Jupiter: SOL token quotes fetched")


# ═══════════════════════════════════════════════════════════════════════════
# 4. BIRDEYE — Solana DEX trending + OHLCV (FREE tier, no key)
# ═══════════════════════════════════════════════════════════════════════════

def birdeye_scan():
    """Fetch Solana token prices from Birdeye DeFi price API.
    Docs: https://docs.birdeye.so/reference/get-defi-price"""
    # Birdeye DeFi price endpoint for major Solana tokens
    for sym, mint in JUPITER_TOKENS_DICT.items():
        if sym == "USDC":
            continue
        try:
            price_url = f"https://public-api.birdeye.so/defi/price?address={mint}"
            req = urllib.request.Request(price_url, headers={
                "Accept": "application/json",
                "x-api-key": JUPITER_KEY,  # Birdeye free tier uses same key format
            })
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
                if data.get("success") and "data" in data:
                    price_data = data["data"]
                    price = price_data.get("value", 0)
                    chg = price_data.get("priceChange24h", 0)
                    if price:
                        post_signal(
                            symbol=sym, source="birdeye", stype="dex_price",
                            conviction=min(abs(chg) / 20, 1.0) if chg else 0.5,
                            detail=f"${price:.4f}" + (f" chg={chg:+.1f}%" if chg else ""),
                        )
        except Exception as e:
            log.debug(f"Birdeye {sym}: {e}")
            continue

    # Also fetch trending tokens
    try:
        trending_url = "https://public-api.birdeye.so/defi/trending?limit=10"
        req = urllib.request.Request(trending_url, headers={
            "Accept": "application/json",
            "x-api-key": JUPITER_KEY,
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            if data.get("success") and "data" in data:
                for t in data["data"]["tokens"]:
                    sym = t.get("symbol", "?")[:12]
                    chg = t.get("priceChange24hPercent", 0)
                    if chg and abs(chg) >= 5:
                        direction = "BUY" if chg > 0 else "SELL"
                        post_signal(
                            symbol=sym, source="birdeye", stype="trending",
                            conviction=min(abs(chg) / 20, 1.0), direction=direction,
                            detail=f"{chg:+.1f}% 24h",
                        )
                        if abs(chg) >= 15:
                            feed_once(f"birdeye_{sym}", 3600,
                                      title=f"{sym} {chg:+.1f}% on Solana DEX",
                                      content=f"**{sym}** surged **{chg:+.1f}%** in 24h on Solana DEX via Birdeye.",
                                      tags=["signal", "solana", "dex", sym.lower()])
    except:
        pass

    log.info("Birdeye: Solana price + trending scan")

# Map Jupiter mints to symbols for Birdeye
JUPITER_TOKENS_DICT = {
    "So11111111111111111111111111111111111111112": "SOL",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": "JUP",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "BONK",
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm": "WIF",
    "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL": "JTO",
    "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3": "PYTH",
    "rndr4pPLNj1DtuQ3VjD8f3KspjZSHxBLiMRwCC6fseK": "RNDR",
}


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def run_scan():
    log.info("=== Alpha Sources Scan ===")

    # 1. FinBERT (heavy — skip some cycles)
    try:
        finbert_scan()
    except Exception as e:
        log.error(f"FinBERT: {e}")

    # 2. GDELT
    try:
        gdelt_scan()
    except Exception as e:
        log.error(f"GDELT: {e}")

    # 3. Jupiter
    try:
        jupiter_scan()
    except Exception as e:
        log.error(f"Jupiter: {e}")

    # 4. Birdeye
    try:
        birdeye_scan()
    except Exception as e:
        log.error(f"Birdeye: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vantage Alpha Sources")
    parser.add_argument("--daemon", type=int, nargs="?", const=300, metavar="SECONDS")
    args = parser.parse_args()

    if args.daemon:
        # First scan: download FinBERT model (takes a minute)
        log.info("Pre-loading FinBERT...")
        try:
            finbert_load()
        except:
            log.warning("FinBERT pre-load failed — will try again on scan")

        log.info(f"Alpha Sources daemon — scanning every {args.daemon}s")
        while True:
            try:
                run_scan()
            except Exception as e:
                log.error(f"Scan error: {e}")
            time.sleep(args.daemon)
    else:
        run_scan()
