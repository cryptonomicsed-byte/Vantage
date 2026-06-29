"""Pure-Python technical indicators over OHLCV candles (no external deps).

Each function takes a list of candle dicts ({time, open, high, low, close, volume})
and returns a list of {time, value} (or {time, ...fields}) aligned to candle time,
with None for warm-up periods. `compute()` is the single entry the API/agents use.

These are the built-in indicators; agent-authored Pine indicators run in the
sandboxed pine-runtime sidecar and return the same {time, value} series shape.
"""
from typing import Optional


def _closes(candles: list[dict]) -> list[float]:
    return [float(c["close"]) for c in candles]


def _times(candles: list[dict]) -> list[int]:
    return [int(c["time"]) for c in candles]


def sma(candles: list[dict], length: int = 20) -> list[dict]:
    closes, times = _closes(candles), _times(candles)
    out = []
    for i in range(len(closes)):
        if i + 1 < length:
            out.append({"time": times[i], "value": None})
        else:
            out.append({"time": times[i], "value": round(sum(closes[i + 1 - length:i + 1]) / length, 8)})
    return out


def ema(candles: list[dict], length: int = 20) -> list[dict]:
    closes, times = _closes(candles), _times(candles)
    out: list[dict] = []
    k = 2 / (length + 1)
    prev: Optional[float] = None
    for i in range(len(closes)):
        if i + 1 < length:
            out.append({"time": times[i], "value": None})
            continue
        if prev is None:  # seed with SMA of the first window
            prev = sum(closes[i + 1 - length:i + 1]) / length
        else:
            prev = closes[i] * k + prev * (1 - k)
        out.append({"time": times[i], "value": round(prev, 8)})
    return out


def _ema_values(values: list[float], length: int) -> list[Optional[float]]:
    out: list[Optional[float]] = []
    k = 2 / (length + 1)
    prev: Optional[float] = None
    for i in range(len(values)):
        if i + 1 < length:
            out.append(None)
            continue
        if prev is None:
            prev = sum(values[i + 1 - length:i + 1]) / length
        else:
            prev = values[i] * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(candles: list[dict], length: int = 14) -> list[dict]:
    closes, times = _closes(candles), _times(candles)
    out = [{"time": times[0], "value": None}] if closes else []
    gains, losses = 0.0, 0.0
    avg_gain = avg_loss = None
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain, loss = max(change, 0.0), max(-change, 0.0)
        if i <= length:
            gains += gain
            losses += loss
            if i == length:
                avg_gain, avg_loss = gains / length, losses / length
                rs = (avg_gain / avg_loss) if avg_loss else float("inf")
                out.append({"time": times[i], "value": round(100 - 100 / (1 + rs), 4)})
            else:
                out.append({"time": times[i], "value": None})
        else:
            avg_gain = (avg_gain * (length - 1) + gain) / length
            avg_loss = (avg_loss * (length - 1) + loss) / length
            rs = (avg_gain / avg_loss) if avg_loss else float("inf")
            out.append({"time": times[i], "value": round(100 - 100 / (1 + rs), 4)})
    return out


def macd(candles: list[dict], fast: int = 12, slow: int = 26, signal: int = 9) -> list[dict]:
    closes, times = _closes(candles), _times(candles)
    ef, es = _ema_values(closes, fast), _ema_values(closes, slow)
    macd_line: list[Optional[float]] = [
        (ef[i] - es[i]) if (ef[i] is not None and es[i] is not None) else None
        for i in range(len(closes))
    ]
    # Signal = EMA of the (compact) macd line, re-expanded to full length.
    compact = [(i, v) for i, v in enumerate(macd_line) if v is not None]
    sig_vals = _ema_values([v for _, v in compact], signal)
    sig_full: list[Optional[float]] = [None] * len(closes)
    for (idx, _), s in zip(compact, sig_vals):
        sig_full[idx] = s
    out = []
    for i in range(len(closes)):
        m, s = macd_line[i], sig_full[i]
        out.append({
            "time": times[i],
            "macd": round(m, 8) if m is not None else None,
            "signal": round(s, 8) if s is not None else None,
            "histogram": round(m - s, 8) if (m is not None and s is not None) else None,
        })
    return out


def bollinger(candles: list[dict], length: int = 20, mult: float = 2.0) -> list[dict]:
    closes, times = _closes(candles), _times(candles)
    out = []
    for i in range(len(closes)):
        if i + 1 < length:
            out.append({"time": times[i], "middle": None, "upper": None, "lower": None})
            continue
        window = closes[i + 1 - length:i + 1]
        m = sum(window) / length
        sd = (sum((x - m) ** 2 for x in window) / length) ** 0.5
        out.append({
            "time": times[i],
            "middle": round(m, 8),
            "upper": round(m + mult * sd, 8),
            "lower": round(m - mult * sd, 8),
        })
    return out


def vwap(candles: list[dict]) -> list[dict]:
    """Cumulative VWAP over the provided candles (typical price × volume)."""
    times = _times(candles)
    out = []
    cum_pv = cum_v = 0.0
    for i, c in enumerate(candles):
        tp = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3
        v = float(c.get("volume") or 0)
        cum_pv += tp * v
        cum_v += v
        out.append({"time": times[i], "value": round(cum_pv / cum_v, 8) if cum_v else None})
    return out


_REGISTRY = {
    "sma": sma, "ema": ema, "rsi": rsi, "macd": macd, "bollinger": bollinger, "vwap": vwap,
}


def available() -> list[str]:
    return list(_REGISTRY.keys())


def compute(candles: list[dict], specs: Optional[list[dict]] = None) -> dict:
    """Compute a default or requested set of indicators.

    specs: optional [{"name": "ema", "length": 50}, ...]. Defaults to a standard
    pack (sma20, ema50, rsi14, macd, bollinger20). Unknown names are skipped.
    Returns {key: series}.
    """
    if not candles:
        return {}
    if not specs:
        specs = [
            {"name": "sma", "length": 20},
            {"name": "ema", "length": 50},
            {"name": "rsi", "length": 14},
            {"name": "macd"},
            {"name": "bollinger", "length": 20},
        ]
    result: dict = {}
    for spec in specs:
        name = (spec.get("name") or "").lower()
        fn = _REGISTRY.get(name)
        if not fn:
            continue
        kwargs = {k: v for k, v in spec.items() if k != "name"}
        try:
            key = name + (f"_{spec['length']}" if "length" in spec else "")
            result[key] = fn(candles, **kwargs)
        except TypeError:
            result[name] = fn(candles)
    return result
