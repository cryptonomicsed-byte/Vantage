#!/usr/bin/env python3
"""
VantagePredictor — Multi-indicator consensus engine for trading signals.

Generates BUY/SELL signals with conviction scores (0.0-1.0) by combining
8 technical indicators across multiple timeframes. Posts to Vantage
trading API for auto-order creation when conviction > 0.7.

Data sources: Binance (via CCXT), Kraken API
Output: POST /api/trading/signals/ingest with direction + conviction

Usage:
  python3 vantage_predictor.py              # single scan
  python3 vantage_predictor.py --daemon 120  # continuous, every 120s
"""

import json, os, sys, time, logging, argparse
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import ccxt
import urllib.request

# ── Config ──────────────────────────────────────────────────────────────

VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://127.0.0.1:8001")
VANTAGE_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()
SIGNALS_ENDPOINT = f"{VANTAGE_URL}/api/trading/signals/ingest"

# Dynamically populated from market/top
KRAKEN_MAP = {"SOL/USD": "SOL/USD", "BTC/USD": "BTC/USD", "ETH/USD": "ETH/USD"}
TIMEFRAMES = ["15m", "1h", "4h"]
TOP_N = 30  # Number of top tokens to analyze per scan
MIN_CHANGE_PCT = 3.0  # Minimum 24h change to include beyond top N
LOOKBACK = 200  # candles for indicator calculation

# ── Logging ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PREDICT] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("vantage_predictor")


# ═══════════════════════════════════════════════════════════════════════════
# INDICATOR CALCULATORS
# ═══════════════════════════════════════════════════════════════════════════

def rsi(closes: np.ndarray, period: int = 14) -> float:
    """Relative Strength Index — returns last value."""
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-period-1:])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd_signal(closes: np.ndarray) -> tuple[float, float, float]:
    """MACD line, signal line, histogram. Returns (macd, signal, hist)."""
    if len(closes) < 34:
        return 0, 0, 0
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = ema12[-1] - ema26[-1]
    # Signal = 9-period EMA of MACD
    macd_vals = np.array([ema12[i] - ema26[i] for i in range(len(closes))])
    signal = ema(macd_vals, 9)[-1]
    return macd_line, signal, macd_line - signal


def ema(data: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average."""
    if len(data) < period:
        return np.full_like(data, data[-1])
    k = 2.0 / (period + 1)
    result = np.zeros_like(data)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = data[i] * k + result[i-1] * (1 - k)
    return result


def bollinger_bands(closes: np.ndarray, period: int = 20) -> tuple[float, float, float]:
    """Returns (lower, middle, upper)."""
    if len(closes) < period:
        return 0, 0, 0
    sma = np.mean(closes[-period:])
    std = np.std(closes[-period:])
    return sma - 2 * std, sma, sma + 2 * std


def sma_crossover(closes: np.ndarray, fast: int = 10, slow: int = 30) -> int:
    """1 if fast > slow (bullish), -1 if fast < slow (bearish), 0 neutral."""
    if len(closes) < slow:
        return 0
    f = np.mean(closes[-fast:])
    s = np.mean(closes[-slow:])
    return 1 if f > s else -1 if f < s else 0


def volume_trend(volumes: np.ndarray, period: int = 20) -> int:
    """1 if volume increasing, -1 if decreasing."""
    if len(volumes) < period:
        return 0
    recent = np.mean(volumes[-period//2:])
    older = np.mean(volumes[-period:-period//2])
    return 1 if recent > older * 1.1 else -1 if recent < older * 0.9 else 0


def stochastic(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray, period: int = 14) -> float:
    """Stochastic oscillator %K — returns 0-100."""
    if len(closes) < period:
        return 50.0
    c = closes[-1]
    h = np.max(highs[-period:])
    l = np.min(lows[-period:])
    if h == l:
        return 50.0
    return ((c - l) / (h - l)) * 100.0


def adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    """Average Directional Index — trend strength 0-100."""
    if len(closes) < period + 1:
        return 25.0
    tr = np.maximum(highs[1:] - lows[1:],
                    np.abs(highs[1:] - closes[:-1]),
                    np.abs(lows[1:] - closes[:-1]))
    atr = np.mean(tr[-period:]) if len(tr) >= period else np.mean(tr)
    up = np.maximum(highs[1:] - highs[:-1], 0)[-period:]
    down = np.maximum(lows[:-1] - lows[1:], 0)[-period:]
    if atr == 0:
        return 25.0
    pdi = np.mean(up) / atr * 100
    ndi = np.mean(down) / atr * 100
    if pdi + ndi == 0:
        return 25.0
    return abs(pdi - ndi) / (pdi + ndi) * 100


# ═══════════════════════════════════════════════════════════════════════════
# CONSENSUS SIGNAL GENERATOR
# ═══════════════════════════════════════════════════════════════════════════

def analyze_symbol(exchange: ccxt.Exchange, kraken_symbol: str, display_symbol: str = None) -> Optional[dict]:
    """Run multi-timeframe multi-indicator analysis for one symbol."""
    if display_symbol is None:
        display_symbol = kraken_symbol
    signals = []

    for tf in TIMEFRAMES:
        try:
            ohlcv = exchange.fetch_ohlcv(kraken_symbol, tf, limit=LOOKBACK)
            if len(ohlcv) < 50:
                log.warning(f"{display_symbol} {tf}: insufficient data ({len(ohlcv)} candles)")
                continue

            closes = np.array([c[4] for c in ohlcv], dtype=float)
            highs = np.array([c[2] for c in ohlcv], dtype=float)
            lows = np.array([c[3] for c in ohlcv], dtype=float)
            volumes = np.array([c[5] for c in ohlcv], dtype=float)
            current_price = closes[-1]

            # ── Indicator votes (each returns: BUY=1, SELL=-1, NEUTRAL=0) ──
            votes = {}

            # RSI: oversold (<30) = BUY, overbought (>70) = SELL
            r = rsi(closes)
            votes["RSI"] = 1 if r < 35 else -1 if r > 65 else 0

            # MACD: histogram positive = BUY
            _, _, hist = macd_signal(closes)
            votes["MACD"] = 1 if hist > 0 else -1 if hist < 0 else 0

            # Bollinger: price near lower band = BUY, near upper = SELL
            low_bb, mid_bb, high_bb = bollinger_bands(closes)
            bb_pos = (current_price - low_bb) / (high_bb - low_bb) if high_bb != low_bb else 0.5
            votes["Bollinger"] = 1 if bb_pos < 0.2 else -1 if bb_pos > 0.8 else 0

            # SMA crossover: fast > slow = BUY
            votes["SMA"] = sma_crossover(closes)

            # Volume trend
            votes["Volume"] = volume_trend(volumes)

            # Stochastic: oversold (<20) = BUY, overbought (>80) = SELL
            stoch = stochastic(closes, highs, lows)
            votes["Stochastic"] = 1 if stoch < 20 else -1 if stoch > 80 else 0

            # ADX: strong trend (>25) amplify existing direction
            a = adx(highs, lows, closes)
            trend_strength = "strong" if a > 25 else "weak"

            # Price momentum (short-term)
            mom = (closes[-1] / closes[-min(20, len(closes))] - 1) * 100
            votes["Momentum"] = 1 if mom > 2 else -1 if mom < -2 else 0

            # ── Compute consensus ──
            buy_votes = sum(1 for v in votes.values() if v == 1)
            sell_votes = sum(1 for v in votes.values() if v == -1)
            total = len(votes)
            direction = "BUY" if buy_votes > sell_votes else "SELL"
            conviction = max(buy_votes, sell_votes) / total

            # Weight by timeframe (1h and 4h have more weight)
            tf_weight = {"15m": 0.6, "1h": 0.8, "4h": 1.0}.get(tf, 0.8)

            signals.append({
                "tf": tf,
                "direction": direction,
                "conviction": round(conviction * tf_weight, 3),
                "votes": votes,
                "price": current_price,
                "trend_strength": trend_strength,
                "momentum_pct": round(mom, 2),
                "rsi": round(r, 1),
                "stochastic": round(stoch, 1),
                "adx": round(a, 1),
            })

        except Exception as e:
            log.error(f"{display_symbol} {tf}: {e}")

    if not signals:
        return None

    # ── Fuse timeframes: weighted average of convictions ──
    total_conv = sum(s["conviction"] for s in signals)
    if total_conv == 0:
        return None

    # Dominant direction
    buy_weight = sum(s["conviction"] for s in signals if s["direction"] == "BUY")
    sell_weight = sum(s["conviction"] for s in signals if s["direction"] == "SELL")
    direction = "BUY" if buy_weight >= sell_weight else "SELL"
    conviction = round(max(buy_weight, sell_weight) / total_conv, 3)

    # Only emit if conviction is meaningful
    if conviction < 0.4:
        return None

    # Determine chain from original symbol
    chain = "solana" if "SOL" in display_symbol else "ethereum" if "ETH" in display_symbol else "bitcoin"

    return {
        "symbol": display_symbol,
        "direction": direction,
        "conviction": conviction,
        "chain": chain,
        "source": "vantage-predictor",
        "price": signals[0]["price"],
        "details": {
            "timeframes": signals,
            "consensus_breakdown": f"{direction} ({buy_weight:.2f} vs {sell_weight:.2f})",
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# VANTAGE API INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

def post_signal(signal: dict) -> bool:
    """Send signal to Vantage trading API AND post to feed for visibility."""
    payload = {
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "conviction": signal["conviction"],
        "chain": signal["chain"],
        "source": signal["source"],
        "details": json.dumps(signal.get("details", {})),
    }
    success = False
    try:
        # Post to trading pipeline
        req = urllib.request.Request(
            SIGNALS_ENDPOINT,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Agent-Key": VANTAGE_KEY,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            log.info(f"✅ {signal['symbol']} {signal['direction']} "
                     f"conviction={signal['conviction']:.2f} → {result}")
            success = True
    except Exception as e:
        log.error(f"❌ POST failed for {signal['symbol']}: {e}")

    # Also post to feed for visibility (if conviction is meaningful)
    if signal["conviction"] >= 0.6:
        try:
            direction_emoji = "🟢" if signal["direction"] == "BUY" else "🔴"
            feed_payload = json.dumps({
                "title": f"{direction_emoji} {signal['direction']}: {signal['symbol']} ({signal['conviction']:.1%} conviction)",
                "content": f"**{signal['direction']}** signal for **{signal['symbol']}** at ${signal.get('price', 0):.2f}. "
                           f"Conviction: **{signal['conviction']:.1%}**. Source: vantage-predictor (8 indicators).",
                "tags": ["signal", "predictor", signal["direction"].lower()],
            }).encode()
            req2 = urllib.request.Request(
                f"{VANTAGE_URL}/api/trading/signals/ingest",
                data=feed_payload,
                headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY},
            )
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                fb_result = json.loads(resp2.read())
                log.info(f"📡 Feed post: {fb_result.get('broadcast_id', 'ok')}")
        except Exception as e:
            log.debug(f"Feed post skipped: {e}")

    # Also post to signals pool for Trading dashboard
    try:
        sig_payload = json.dumps({
            "symbol": signal["symbol"].split("/")[0],
            "source": "predictor",
            "type": "signal",
            "conviction": signal["conviction"],
            "direction": signal["direction"],
            "detail": f"price=${signal.get('price',0):.2f}",
        }).encode()
        req3 = urllib.request.Request(
            f"{VANTAGE_URL}/api/intel/signals/ingest",
            data=sig_payload,
            headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY},
        )
        with urllib.request.urlopen(req3, timeout=5) as resp3:
            pass  # fire and forget
    except Exception:
        pass

    return success


def get_top_symbols() -> list[str]:
    """Fetch top tokens from Vantage market/top endpoint, map to Kraken pairs."""
    try:
        req = urllib.request.Request(
            f"{VANTAGE_URL}/api/intel/market/top?limit=100",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            tokens = data.get("tokens", [])

        pairs = []
        for t in tokens:
            sym = t["symbol"].upper()
            # Try direct Kraken mapping
            pair = f"{sym}/USD"
            # Skip stablecoins and wrapped tokens for prediction
            if sym in ("USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP", "FRAX", "WBTC", "WETH", "WSTETH"):
                continue
            # Top N by market cap always included
            if len(pairs) < TOP_N:
                pairs.append(pair)
            # Beyond TOP_N, only include if >MIN_CHANGE_PCT% move
            elif abs(t.get("price_change_pct_24h") or 0) >= MIN_CHANGE_PCT:
                pairs.append(pair)

        # Always include BTC/ETH/SOL
        for must in ["BTC/USD", "ETH/USD", "SOL/USD"]:
            if must not in pairs:
                pairs.insert(0, must)

        log.info(f"Scan targets: {len(pairs)} pairs (top {TOP_N} + {len(pairs)-TOP_N} movers)")
        return pairs[:50]  # Cap at 50 per scan
    except Exception as e:
        log.error(f"Failed to get top symbols: {e}")
        return ["BTC/USD", "ETH/USD", "SOL/USD"]  # Fallback


def map_to_kraken(symbol: str) -> str:
    """Map a CCXT-standard symbol to Kraken format."""
    base = symbol.split("/")[0]
    # Try known mappings first
    if symbol in KRAKEN_MAP:
        return KRAKEN_MAP[symbol]
    # Direct USD pair
    return f"{base}/USD"


def run_scan():
    """Single scan cycle across top tokens."""
    symbols = get_top_symbols()
    exchange = ccxt.kraken({"enableRateLimit": True, "timeout": 15000})
    signals_posted = 0

    for symbol in symbols[:30]:  # Analyze top 30 per scan
        ks = map_to_kraken(symbol)
        log.info(f"Analyzing {symbol} ({ks})...")
        signal = analyze_symbol(exchange, ks, symbol)
        if signal:
            log.info(f"  {signal['direction']} conviction={signal['conviction']:.2f} "
                     f"(price={signal['price']})")
            if signal["conviction"] >= 0.6:
                post_signal(signal)
                signals_posted += 1
            else:
                # Only log skips for interesting ones
                if signal["conviction"] >= 0.5:
                    log.info(f"  ⏭️ conviction too low ({signal['conviction']:.2f})")

    exchange.close()
    log.info(f"Scan complete: {signals_posted} signals posted from {len(symbols)} pairs")
    return signals_posted


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VantagePredictor — ML signal engine")
    parser.add_argument("--daemon", type=int, nargs="?", const=120, metavar="SECONDS",
                        help="Run continuously with given interval (default 120s)")
    args = parser.parse_args()

    if args.daemon:
        log.info(f"VantagePredictor daemon — scanning every {args.daemon}s")
        while True:
            try:
                run_scan()
            except Exception as e:
                log.error(f"Scan error: {e}")
            time.sleep(args.daemon)
    else:
        run_scan()
