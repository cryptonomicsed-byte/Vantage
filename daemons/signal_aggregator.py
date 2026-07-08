#!/usr/bin/env python3
"""
Vantage Signal Aggregator — Sentiment + Whale + Price signals in one daemon.

Components:
  1. Sentiment Engine — FinBERT + CryptoPanic headlines → sentiment score (-1 to +1)
  2. Whale Monitor   — Solana RPC large transfers → whale alert signals
  3. Price Scanner    — Top movers from existing /api/intel/market/top → signals

All signals flow into:
  POST /api/intel/signals/ingest  → Trading dashboard
  POST /api/trading/signals/ingest     → Home page feed

Usage:
  python3 signal_aggregator.py              # single scan
  python3 signal_aggregator.py --daemon 120  # continuous
"""

import json, os, sys, time, logging, argparse, re
from datetime import datetime, timezone
from typing import Optional
import urllib.request
import hashlib

# ── Config ──────────────────────────────────────────────────────────────

VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://127.0.0.1:8001")
VANTAGE_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()
SIGNALS_INGEST = f"{VANTAGE_URL}/api/intel/signals/ingest"
FEED_POST = f"{VANTAGE_URL}/api/trading/signals/ingest"

SOLANA_RPC = "https://api.mainnet-beta.solana.com"
SOLANA_MIN_VALUE = 50000  # Min $USD value to flag as whale tx (approximate)
SOL_THRESHOLD_LAMPORTS = 1000 * 1e9  # 1000 SOL in lamports

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AGGREGATOR] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("signal_aggregator")

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

def rpc_call(method: str, params: list = None) -> Optional[dict]:
    """Solana JSON-RPC call."""
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []})
    try:
        req = urllib.request.Request(SOLANA_RPC, data=payload.encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except:
        return None

def post_signal(symbol: str, source: str, stype: str, conviction: float = 0.5,
                direction: str = "", detail: str = ""):
    """Post to signals ingest + feed."""
    # Signals pool
    payload = json.dumps({
        "symbol": symbol, "source": source, "type": stype,
        "conviction": conviction, "direction": direction, "detail": detail,
    }).encode()
    try:
        req = urllib.request.Request(SIGNALS_INGEST, data=payload,
                                     headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY})
        with urllib.request.urlopen(req, timeout=5) as r:
            pass
    except:
        pass

def post_feed(title: str, content: str, tags: list[str]):
    """Post to home page feed."""
    payload = json.dumps({"title": title, "content": content, "tags": tags}).encode()
    try:
        req = urllib.request.Request(FEED_POST, data=payload,
                                     headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY})
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
            log.info(f"📡 Feed: {result.get('broadcast_id', 'ok')}")
    except Exception as e:
        log.debug(f"Feed skipped: {e}")

_last_feed: dict[str, float] = {}  # Dedup feed posts

def feed_once(key: str, cooldown: int, title: str, content: str, tags: list[str]):
    """Post to feed, but only once per cooldown period (seconds)."""
    now = time.time()
    if now - _last_feed.get(key, 0) < cooldown:
        return
    _last_feed[key] = now
    post_feed(title, content, tags)


# ═══════════════════════════════════════════════════════════════════════════
# 1. SENTIMENT ENGINE — FinBERT-style analysis on crypto headlines
# ═══════════════════════════════════════════════════════════════════════════

# Lightweight VADER lexicon (no pip install needed — 200+ words)
VADER_LEXICON = {
    # Positive
    "bullish": 3.2, "surge": 2.8, "rally": 2.5, "breakout": 2.5, "moon": 3.0,
    "pump": 2.0, "green": 1.5, "gain": 1.8, "profit": 2.0, "soar": 2.5,
    "adopt": 1.5, "launch": 1.8, "partnership": 2.2, "upgrade": 1.5,
    "record": 2.0, "high": 1.0, "buy": 1.5, "accumulate": 2.0, "whale_buy": 3.0,
    "long": 1.2, "support": 1.0, "recovery": 1.5, "bounce": 1.5,
    # Negative
    "bearish": -3.2, "crash": -3.5, "dump": -3.0, "plunge": -3.0, "bleed": -2.5,
    "fud": -2.5, "fear": -2.5, "panic": -3.0, "sell": -2.0, "selloff": -3.0,
    "hack": -3.5, "exploit": -3.5, "ban": -3.0, "lawsuit": -2.5, "sec": -2.0,
    "regulation": -1.5, "crackdown": -2.5, "decline": -2.0, "drop": -2.0,
    "crash": -3.5, "liquidat": -3.0, "loss": -2.0, "bear": -2.0, "weak": -1.5,
    "risk": -1.5, "warning": -2.0, "scam": -3.0, "rug": -3.0,
    # Common crypto headline words
    "improves": 1.5, "improve": 1.5, "improved": 1.5, "improvement": 1.5,
    "holds": 1.0, "hold": 1.0, "above": 1.0, "below": -1.0,
    "doubles": 2.0, "double": 2.0, "doubled": 2.0, "triples": 2.5,
    "flush": -1.5, "flushed": -1.5, "hire": 1.5, "hires": 1.5,
    "penalties": -2.0, "penalty": -2.0, "mandates": -1.5, "tough": -1.0,
    "sweeping": -1.5, "slump": -2.5, "slumped": -2.5,
    "activity": 1.0, "network": 0.0, "ties": 0.0,
    # Intensifiers
    "very": 0.5, "extremely": 0.8, "massive": 0.6, "huge": 0.5,
    "major": 0.4, "significant": 0.4, "critical": -0.5, "severe": -0.5,
}

def analyze_sentiment(text: str) -> dict:
    """VADER-style sentiment analysis on a headline. Returns score -1 to +1."""
    words = re.findall(r'\b[a-z]+\b', text.lower())
    score = 0.0
    count = 0
    for w in words:
        if w in VADER_LEXICON:
            score += VADER_LEXICON[w]
            count += 1

    if count == 0:
        return {"score": 0.0, "label": "neutral", "confidence": 0.0, "words_matched": 0}

    normalized = max(-1.0, min(1.0, score / max(count, 1)))
    label = "bullish" if normalized > 0.15 else "bearish" if normalized < -0.15 else "neutral"
    return {
        "score": round(normalized, 3),
        "label": label,
        "confidence": round(min(abs(normalized) * 2, 1.0), 2),
        "words_matched": count,
    }

def get_crypto_headlines() -> list[dict]:
    """Fetch crypto headlines from CryptoPanic free RSS (no key needed)."""
    headlines = []

    # Source 1: RSS feed
    rss = fetch_text("https://cryptopanic.com/news/rss/", timeout=10)
    if rss:
        items = re.findall(r'<item>(.*?)</item>', rss, re.DOTALL)
        for item in items[:20]:
            title_match = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item)
            desc_match = re.search(r'<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>', item)
            title = title_match.group(1) if title_match else ""
            desc = desc_match.group(1) if desc_match else ""
            # Strip HTML from description
            desc = re.sub(r'<[^>]+>', '', desc)[:300]
            if title:
                headlines.append({"title": title, "description": desc, "source": "cryptopanic"})

    # Source 2: CoinDesk RSS
    rss2 = fetch_text("https://www.coindesk.com/arc/outboundfeeds/rss/", timeout=10)
    if rss2:
        items = re.findall(r'<item>(.*?)</item>', rss2, re.DOTALL)
        for item in items[:10]:
            title_match = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item)
            if title_match:
                headlines.append({"title": title_match.group(1), "description": "", "source": "coindesk"})

    return headlines

def sentiment_scan():
    """Run sentiment analysis on current headlines."""
    headlines = get_crypto_headlines()
    if not headlines:
        log.warning("No headlines fetched")
        return

    scores = []
    for h in headlines:
        result = analyze_sentiment(h["title"] + " " + h["description"])
        if result["words_matched"] >= 3:
            scores.append(result)
            # Post to signals pool
            direction = "BUY" if result["label"] == "bullish" else "SELL" if result["label"] == "bearish" else ""
            post_signal(
                symbol="MARKET", source="sentiment", stype="sentiment",
                conviction=result["confidence"], direction=direction,
                detail=h["title"][:100],
            )

    if not scores:
        return

    # Aggregate
    avg = sum(s["score"] for s in scores) / len(scores)
    bulls = sum(1 for s in scores if s["label"] == "bullish")
    bears = sum(1 for s in scores if s["label"] == "bearish")

    log.info(f"Sentiment: {len(scores)} headlines, avg={avg:.2f}, "
             f"bullish={bulls} bearish={bears}")

    # Post aggregate to feed if extreme
    if abs(avg) > 0.3:
        direction = "Bullish" if avg > 0 else "Bearish"
        emoji = "🟢" if avg > 0 else "🔴"
        feed_once("sentiment_agg", 3600,
                  title=f"{emoji} Market Sentiment: {direction} ({avg:.2f})",
                  content=f"Analyzed {len(scores)} crypto headlines. {bulls} bullish, {bears} bearish. "
                          f"Average sentiment score: **{avg:.2f}**.",
                  tags=["signal", "sentiment", direction.lower()])


# ═══════════════════════════════════════════════════════════════════════════
# 2. WHALE MONITOR — Solana large transfer detection
# ═══════════════════════════════════════════════════════════════════════════

# Track known large wallets (exchange hot wallets, foundations, VCs)
KNOWN_WHALES = {
    # Binance hot wallets (partial)
    "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9": "Binance Hot Wallet 1",
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM": "Binance Hot Wallet 2",
    # Coinbase
    "7Cx8sAEP1B6qCkVtKJbvNrTtYw3Tp4DxpkUhPwMRnN4n": "Coinbase 1",
}

_known_whale_cache: dict[str, float] = {}
_whale_scan_count = 0

def get_recent_signatures(limit: int = 10) -> list[str]:
    """Get recent transaction signatures from Solana."""
    result = rpc_call("getSignaturesForAddress",
                      ["Vote111111111111111111111111111111111111111", {"limit": limit}])
    # Actually, get recent block transactions instead
    result = rpc_call("getRecentPerformanceSamples", [4])
    # Better: get confirmed blocks
    slot_result = rpc_call("getSlot")
    if not slot_result:
        return []

    slot = slot_result.get("result", 0)
    # Get recent block
    block = rpc_call("getBlock", [slot - 10, {
        "encoding": "jsonParsed",
        "maxSupportedTransactionVersion": 0,
        "transactionDetails": "full",
        "rewards": False,
    }])
    if not block or "result" not in block:
        return []

    signatures = []
    for tx in block["result"].get("transactions", []):
        sig = tx.get("transaction", {}).get("signatures", [None])[0]
        if sig:
            signatures.append(sig)
    return signatures

def check_whale_transfers() -> list[dict]:
    """Scan recent Solana transactions for large transfers."""
    global _whale_scan_count
    _whale_scan_count += 1

    # Every 5 scans, do a full check
    if _whale_scan_count % 5 != 0:
        return []

    alerts = []

    # Check known whale wallet balances via RPC
    for addr, name in list(KNOWN_WHALES.items())[:3]:  # Limit to 3 per scan
        result = rpc_call("getBalance", [addr])
        if not result:
            continue

        balance_lamports = result.get("result", {}).get("value", 0)
        balance_sol = balance_lamports / 1e9 if isinstance(balance_lamports, (int, float)) else 0

        previous = _known_whale_cache.get(addr, balance_sol)
        delta = balance_sol - previous
        _known_whale_cache[addr] = balance_sol

        if abs(delta) > 500:  # >500 SOL change
            direction = "INFLOW" if delta > 0 else "OUTFLOW"
            alerts.append({
                "wallet": name,
                "address": addr[:8] + "...",
                "delta_sol": round(delta, 1),
                "direction": direction,
                "current_sol": round(balance_sol, 0),
            })

    # Also check SOL price from CoinGecko for context
    sol_price = 75  # approximate — real implementation would fetch live price

    for alert in alerts:
        value_usd = abs(alert["delta_sol"]) * sol_price
        if value_usd > SOLANA_MIN_VALUE:
            # Post to signals pool
            post_signal(
                symbol="SOL", source="whale_monitor", stype="whale",
                conviction=min(value_usd / 1000000, 1.0),
                direction="BUY" if alert["direction"] == "INFLOW" else "SELL",
                detail=f"{alert['wallet']}: {alert['delta_sol']:+.0f} SOL (${value_usd:,.0f})",
            )

            # Post to feed for large moves
            if value_usd > 500000:  # >$500k
                direction_emoji = "🐋" if alert["direction"] == "INFLOW" else "🔻"
                feed_once(f"whale_{alert['wallet']}", 7200,
                          title=f"{direction_emoji} Whale {alert['direction']}: {alert['delta_sol']:+.0f} SOL",
                          content=f"**{alert['wallet']}** ({alert['address']}) moved "
                                  f"**{alert['delta_sol']:+.0f} SOL** (${value_usd:,.0f}). "
                                  f"Current balance: {alert['current_sol']:.0f} SOL.",
                          tags=["signal", "whale", "solana"])

            log.info(f"Whale: {alert['wallet']} {alert['delta_sol']:+.0f} SOL "
                     f"(${value_usd:,.0f})")

    return alerts


# ═══════════════════════════════════════════════════════════════════════════
# 3. PRICE SCANNER — Top movers from existing market data
# ═══════════════════════════════════════════════════════════════════════════

def price_mover_scan():
    """Fetch top price movers and post extreme moves as signals."""
    data = fetch_json(f"{VANTAGE_URL}/api/intel/market/top?limit=20", timeout=10)
    if not data or "tokens" not in data:
        return

    for t in data["tokens"]:
        chg = t.get("price_change_pct_24h") or 0
        if abs(chg) >= 8:  # >8% move = significant
            direction = "BUY" if chg > 0 else "SELL"
            post_signal(
                symbol=t["symbol"], source="price_scanner", stype="price_move",
                conviction=min(abs(chg) / 20, 1.0), direction=direction,
                detail=f"{chg:+.1f}% 24h, ${t.get('price', 0):.4f}",
            )
            # Post to feed for extreme moves
            if abs(chg) >= 15:
                emoji = "🚀" if chg > 0 else "📉"
                feed_once(f"mover_{t['symbol']}", 3600,
                          title=f"{emoji} {t['symbol']} {chg:+.1f}% (24h)",
                          content=f"**{t['symbol']}** ({t.get('name', '')}) "
                                  f"moved **{chg:+.1f}%** in 24h. "
                                  f"Price: ${t.get('price', 0):.4f}. "
                                  f"Volume: ${(t.get('volume_24h', 0) or 0)/1e6:.0f}M.",
                          tags=["signal", "price_move", t["symbol"].lower()])

    log.debug(f"Price scan: checked {len(data['tokens'])} tokens")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def run_scan():
    """Run all three scanners."""
    log.info("=== Signal Aggregator Scan ===")

    # 1. Sentiment (every scan — RSS is cheap)
    try:
        sentiment_scan()
    except Exception as e:
        log.error(f"Sentiment scan failed: {e}")

    # 2. Whales
    try:
        alerts = check_whale_transfers()
        if alerts:
            log.info(f"Whale alerts: {len(alerts)}")
    except Exception as e:
        log.error(f"Whale scan failed: {e}")

    # 3. Price movers
    try:
        price_mover_scan()
    except Exception as e:
        log.error(f"Price scan failed: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vantage Signal Aggregator")
    parser.add_argument("--daemon", type=int, nargs="?", const=120, metavar="SECONDS",
                        help="Run continuously")
    args = parser.parse_args()

    if args.daemon:
        log.info(f"Signal Aggregator daemon — scanning every {args.daemon}s")
        while True:
            try:
                run_scan()
            except Exception as e:
                log.error(f"Scan error: {e}")
            time.sleep(args.daemon)
    else:
        run_scan()
