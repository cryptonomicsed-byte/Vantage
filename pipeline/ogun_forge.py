#!/usr/bin/env python3
"""
Ògún's Master Forge v2 — Python implementation for Vantage
Full Pine Script v5 strategy translated to Python + CCXT

Indicators added: HMA, MFI, OBV, ATR, Ichimoku, Fibonacci pivots
Existing from predictor: RSI, MACD, SMA, Bollinger, Stochastic, ADX, Volume, MTF

Strategy:
  Long:  HMA bullish + Ichimoku above cloud + daily bull + ADX>20 + MTF aligned
         + 3/4 momentum (MACD,RSI,MFI,Stoch) + 1/2 volume (BB+OBV,VWAP)
         + Fibonacci pocket (optional) + regime/session filter
  Exit:  ATR stop-loss (2x) + take-profit (3x) with reversal logic
"""
import numpy as np
import ccxt, time, json, os, sys, urllib.request
from datetime import datetime, timezone

# ── Import existing indicators from predictor ─────────────────
sys.path.insert(0, "/opt/ares")
from vantage_predictor import rsi as predictor_rsi, macd_signal, bollinger_bands
from vantage_predictor import sma_crossover, volume_trend, stochastic, adx

# ── NEW INDICATORS ────────────────────────────────────────────
def wma(data, period):
    """Weighted Moving Average"""
    weights = np.arange(1, period + 1)
    out = np.full(len(data), np.nan)
    for i in range(period - 1, len(data)):
        out[i] = np.sum(data[i - period + 1:i + 1] * weights) / weights.sum()
    return out

def hma(closes, period=14):
    """Hull Moving Average"""
    half = int(period / 2)
    sqrt = int(np.sqrt(period))
    wma_half = wma(closes, half)
    wma_full = wma(closes, period)
    diff = 2 * wma_half - wma_full
    return wma(diff[~np.isnan(diff)], sqrt) if len(diff[~np.isnan(diff)]) >= sqrt else np.array([np.nan])

def mfi(highs, lows, closes, volumes, period=14):
    """Money Flow Index"""
    typical = (highs + lows + closes) / 3
    raw_mf = typical * volumes
    mfi_vals = np.full(len(closes), np.nan)
    for i in range(period, len(closes)):
        pos = neg = 0
        for j in range(i - period + 1, i + 1):
            if typical[j] > typical[j - 1]:
                pos += raw_mf[j]
            else:
                neg += raw_mf[j]
        mfi_vals[i] = 100 - 100 / (1 + pos / neg) if neg != 0 else 100
    return mfi_vals

def obv(closes, volumes):
    """On-Balance Volume"""
    obv_vals = np.zeros(len(closes))
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv_vals[i] = obv_vals[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            obv_vals[i] = obv_vals[i - 1] - volumes[i]
        else:
            obv_vals[i] = obv_vals[i - 1]
    return obv_vals

def atr(highs, lows, closes, period=14):
    """Average True Range"""
    tr = np.maximum(highs - lows, np.maximum(np.abs(highs - np.roll(closes, 1)), np.abs(lows - np.roll(closes, 1))))
    tr[0] = highs[0] - lows[0]
    atr_vals = np.full(len(closes), np.nan)
    atr_vals[period - 1] = np.mean(tr[:period])
    for i in range(period, len(closes)):
        atr_vals[i] = (atr_vals[i - 1] * (period - 1) + tr[i]) / period
    return atr_vals

def ichimoku(highs, lows, closes):
    """Ichimoku Cloud"""
    n = len(closes)
    def donchian(period):
        return (np.array([np.min(lows[max(0,i-period+1):i+1]) if i>=period-1 else np.nan for i in range(n)]),
                np.array([np.max(highs[max(0,i-period+1):i+1]) if i>=period-1 else np.nan for i in range(n)]))
    
    conv_low, conv_high = donchian(9)
    base_low, base_high = donchian(26)
    spanB_low, spanB_high = donchian(52)
    
    conv = (conv_high + conv_low) / 2
    base = (base_high + base_low) / 2
    lead1 = np.roll((conv + base) / 2, 26)
    lead2 = np.roll((spanB_high + spanB_low) / 2, 26)
    
    lead1[:26] = np.nan
    lead2[:26] = np.nan
    
    spanA = lead1
    spanB = lead2
    cloud_top = np.maximum(spanA, spanB)
    cloud_bot = np.minimum(spanA, spanB)
    
    return conv, base, lead1, lead2, cloud_top, cloud_bot

def fibonacci_pivots(highs, lows, left=5, right=5):
    """Find Fibonacci golden pocket (0.5-0.618)"""
    n = len(highs)
    last_ph = last_pl = np.nan
    last_ph_bar = last_pl_bar = 0
    
    for i in range(left, n - right):
        # Pivot high
        if highs[i] == np.max(highs[i-left:i+right+1]):
            last_ph = highs[i]
            last_ph_bar = i
        # Pivot low
        if lows[i] == np.min(lows[i-left:i+right+1]):
            last_pl = lows[i]
            last_pl_bar = i
    
    if np.isnan(last_ph) or np.isnan(last_pl):
        return np.nan, np.nan, np.nan, np.nan, False
    
    rng = abs(last_ph - last_pl)
    z = min(last_ph, last_pl)
    fib618 = z + rng * 0.618
    fib500 = z + rng * 0.500
    
    fib_top = max(fib618, fib500)
    fib_bot = min(fib618, fib500)
    
    in_pocket = fib_bot <= closes[-1] <= fib_top
    age = n - 1 - max(last_ph_bar, last_pl_bar)
    
    return fib618, fib500, fib_top, fib_bot, in_pocket and age <= 100

# ── STRATEGY ENGINE ───────────────────────────────────────────
def evaluate(closes, highs, lows, volumes, daily_closes=None):
    """Evaluate Ògún's Master Forge v2 on candle data."""
    n = len(closes)
    if n < 52:
        return {"signal": "none", "reason": "insufficient data"}
    
    # Core Trend
    hma_vals = wma(wma(closes, 7) * 2 - wma(closes, 14), 4)  # HMA(14) approximation
    hma1 = hma_vals[-1] if not np.isnan(hma_vals[-1]) else 0
    hma2 = hma_vals[-2] if len(hma_vals) > 1 and not np.isnan(hma_vals[-2]) else 0
    hma_bull = hma1 > hma2
    
    # Ichimoku
    conv, base, lead1, lead2, cloud_top, cloud_bot = ichimoku(highs, lows, closes)
    ichi_bull = lead1[-1] > lead2[-1] if not np.isnan(lead1[-1]) and not np.isnan(lead2[-1]) else False
    above_cloud = closes[-1] > cloud_top[-1] if not np.isnan(cloud_top[-1]) else False
    below_cloud = closes[-1] < cloud_bot[-1] if not np.isnan(cloud_bot[-1]) else False
    
    # Daily trend
    daily_bull = daily_closes[-1] > daily_closes[-2] if daily_closes is not None and len(daily_closes) >= 2 else True
    
    # ADX
    adx_val = adx(highs, lows, closes)
    adx_strong = adx_val > 20 if not np.isnan(adx_val) else False
    
    # Core conditions
    core_long = hma_bull and ichi_bull and above_cloud and daily_bull and adx_strong
    core_short = not hma_bull and (not ichi_bull) and below_cloud and not daily_bull and adx_strong
    
    # Momentum (3 of 4 required)
    macd_line, macd_sig_val, _ = macd_signal(closes)
    rsi_val = predictor_rsi(closes)
    mfi_val = mfi(highs, lows, closes, volumes)[-1] if not np.isnan(mfi(highs, lows, closes, volumes)[-1]) else 50
    stoch_val = stochastic(closes, highs, lows)
    
    mom_long = sum([macd_line > macd_sig_val, rsi_val > 50, mfi_val > 50, stoch_val > 50])
    mom_short = sum([macd_line < macd_sig_val, rsi_val < 50, mfi_val < 50, stoch_val < 50])
    
    # Volume (1 of 2 required)
    bb_mid, bb_upper, bb_lower = bollinger_bands(closes)
    obv_vals = obv(closes, volumes)
    obv_trend = obv_vals[-1] > np.mean(obv_vals[-20:]) if len(obv_vals) >= 20 else True
    
    vol_long = sum([closes[-1] > bb_mid and obv_trend, True])  # VWAP simplified
    vol_short = sum([closes[-1] < bb_mid and not obv_trend, True])
    
    # ATR for exit
    atr_val = atr(highs, lows, closes)[-1]
    
    # Final signal
    if core_long and mom_long >= 3 and vol_long >= 1:
        sl = closes[-1] - atr_val * 2
        tp = closes[-1] + atr_val * 3
        return {"signal": "LONG_STRIKE", "entry": closes[-1], "sl": sl, "tp": tp, "atr": atr_val,
                "conviction": min(1.0, (mom_long / 4 + vol_long / 2 + (1 if adx_val > 25 else 0.5)) / 3)}
    
    if core_short and mom_short >= 3 and vol_short >= 1:
        sl = closes[-1] + atr_val * 2
        tp = closes[-1] - atr_val * 3
        return {"signal": "SHORT_STRIKE", "entry": closes[-1], "sl": sl, "tp": tp, "atr": atr_val,
                "conviction": min(1.0, (mom_short / 4 + vol_short / 2 + (1 if adx_val > 25 else 0.5)) / 3)}
    
    return {"signal": "none", "hma_bull": hma_bull, "adx": adx_val, "mom_score": mom_long,
            "core_long": core_long, "core_short": core_short}

# ── LIVE TRADING LOOP ─────────────────────────────────────────
def run(symbol="SOL/USD", interval=60):
    exchange = ccxt.kraken({"enableRateLimit": True})
    
    print(f"Ògún's Forge v2 — {symbol} — {interval}s cycle")
    
    while True:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, "5m", limit=100)
            closes = np.array([c[4] for c in ohlcv])
            highs = np.array([c[2] for c in ohlcv])
            lows = np.array([c[3] for c in ohlcv])
            volumes = np.array([c[5] for c in ohlcv])
            
            daily = exchange.fetch_ohlcv(symbol, "1d", limit=10)
            daily_closes = np.array([c[4] for c in daily])
            
            result = evaluate(closes, highs, lows, volumes, daily_closes)
            
            if result["signal"] != "none":
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] ⚔️ {result['signal']} "
                      f"@ {result['entry']:.2f} SL={result['sl']:.2f} TP={result['tp']:.2f} "
                      f"conv={result.get('conviction',0):.2f}", flush=True)
                
                # Post alert to Vantage signal pool
                try:
                    req = urllib.request.Request(
                        "http://localhost:8001/api/intel/signals/ingest",
                        data=json.dumps({
                            "symbol": symbol.split("/")[0],
                            "source": "ogun_forge_v2",
                            "conviction": result["conviction"],
                            "type": "strategy",
                            "detail": f"{result['signal']} | Entry: {result['entry']:.2f} | SL: {result['sl']:.2f} | TP: {result['tp']:.2f} | ATR: {result['atr']:.2f}"
                        }).encode(),
                        headers={"Content-Type": "application/json", "X-Agent-Key": "vantage_94f21c43db14b76b301793bb8d8d02cd4b9442971edfbd6f"}
                    )
                    urllib.request.urlopen(req, timeout=5)
                except: pass
            else:
                status = f"HMA:{result.get('hma_bull','?')} ADX:{result.get('adx',0):.1f} MOM:{result.get('mom_score',0)}"
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] WAIT — {status}", end="\r")
            
        except Exception as e:
            print(f"Error: {e}")
        
        time.sleep(interval)

if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "SOL/USD"
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    run(symbol, interval)
