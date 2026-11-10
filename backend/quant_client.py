"""P4-14 -- thin, fail-open, read-only client for the kanban quant pieces.

Two vendored producers (both in services/, both default OFF, neither started
by Vantage):

  * ares-quant-julia  (services/quant/julia/TradingOSQuant, HTTP on
    QUANT_JULIA_URL, default http://127.0.0.1:8083): POST /backtest, GET /health.
  * whale_watcher     (services/whale_watcher/run.py): appends WhaleAlert rows
    as JSON lines to WHALE_WATCHER_OUT.

Flags (rule 5: off = code path not entered):
  QUANT_JULIA_ENABLED    -- read the Julia quant service.
  WHALE_WATCHER_ENABLED  -- read the whale_watcher output file.

Fail-open: every function returns a dict and never raises. A dead service,
timeout, bad JSON or missing file becomes {"ok": False, "error": ...}.

Read-only: nothing here writes to the signal pool, to signal_ledger, to any
trading table, or places an order. Results are informational intel only.
If this is ever wired into the pool it must use a NEW source (capped at 0.69
by signal_record.NEW_SOURCE_CAP) behind its own default-off flag; that is not
done here.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("vantage.quant_client")

SERVICE_NAME = "ares-quant-julia"
DEFAULT_URL = "http://127.0.0.1:8083"
TIMEOUT_S = float(os.environ.get("QUANT_JULIA_TIMEOUT_S", "10"))
CACHE_TTL_S = int(os.environ.get("QUANT_JULIA_CACHE_S", "300"))
MIN_BARS = 60  # the Julia engine needs warm-up bars for HMA/ADX/ATR

# Only these params are forwarded to the Julia engine; anything else is dropped.
PARAM_BOUNDS = {
    "hma_len": (int, 2, 200),
    "mom_req": (int, 0, 4),
    "vol_req": (int, 0, 2),
    "sl_mult": (float, 0.1, 20.0),
    "tp_mult": (float, 0.1, 50.0),
    "min_adx": (float, 0.0, 100.0),
}

_RESULT_KEYS = ("sharpe_ratio", "sortino_ratio", "max_drawdown_pct", "profit_factor", "win_rate",
                "total_trades", "winning_trades", "losing_trades", "net_profit", "net_profit_pct",
                "calmar_ratio", "avg_trade_duration_bars")

_cache: Dict[str, tuple] = {}


def _flag(name: str) -> bool:
    return os.environ.get(name, "0").strip().lower() in ("1", "true", "yes", "on")


def quant_enabled() -> bool:
    return _flag("QUANT_JULIA_ENABLED")


def whale_enabled() -> bool:
    return _flag("WHALE_WATCHER_ENABLED")


def base_url() -> str:
    return os.environ.get("QUANT_JULIA_URL", DEFAULT_URL).rstrip("/")


def whale_out_path() -> Path:
    default = Path(os.environ.get("VANTAGE_DATA_DIR", "/opt/ares/data")) / "whale_watcher.jsonl"
    return Path(os.environ.get("WHALE_WATCHER_OUT", str(default)))


def _disabled(flag: str) -> Dict[str, Any]:
    return {"enabled": False, "ok": False, "flag": flag, "error": f"{flag} is off"}


def clean_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, (typ, lo, hi) in PARAM_BOUNDS.items():
        if params and params.get(k) is not None:
            try:
                out[k] = typ(min(hi, max(lo, typ(params[k]))))
            except (TypeError, ValueError):
                continue
    return out


async def _http(method: str, path: str, body: Optional[dict] = None) -> Dict[str, Any]:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as c:
            r = await c.request(method, base_url() + path, json=body)
        if r.status_code != 200:
            return {"ok": False, "error": f"{SERVICE_NAME} HTTP {r.status_code}"}
        data = r.json()
        if not isinstance(data, dict):
            return {"ok": False, "error": f"{SERVICE_NAME} returned non-object"}
        return {"ok": True, "data": data}
    except Exception as ex:  # noqa: BLE001 -- fail-open by design
        return {"ok": False, "error": f"{SERVICE_NAME} unreachable: {type(ex).__name__}"}


async def status() -> Dict[str, Any]:
    out: Dict[str, Any] = {"service": SERVICE_NAME, "quant_enabled": quant_enabled(),
                           "whale_enabled": whale_enabled(), "read_only": True,
                           "posts_to_signal_pool": False}
    if quant_enabled():
        h = await _http("GET", "/health")
        out["quant_ok"] = h["ok"]
        out["quant_health"] = h.get("data") if h["ok"] else None
        if not h["ok"]:
            out["quant_error"] = h["error"]
    if whale_enabled():
        p = whale_out_path()
        out["whale_file_present"] = p.exists()
        out["whale_file_mtime"] = int(p.stat().st_mtime) if p.exists() else None
    return out


def candles_to_body(symbol: str, candles: List[dict], params: Dict[str, Any]) -> Optional[dict]:
    rows = [c for c in candles if all(isinstance(c.get(k), (int, float)) for k in
                                      ("open", "high", "low", "close"))]
    if len(rows) < MIN_BARS:
        return None
    return {
        "ticker": symbol.upper(),
        "params": params,
        "open": [float(c["open"]) for c in rows],
        "high": [float(c["high"]) for c in rows],
        "low": [float(c["low"]) for c in rows],
        "close": [float(c["close"]) for c in rows],
        "volume": [float(c.get("volume") or 0.0) for c in rows],
    }


# Bars per year for annualising Sharpe/Sortino. Crypto trades 24/7, so a
# 365-day year. The engine defaulted to hourly bars (252*24) for every
# request, so daily candles came back ~4.9x too high (sqrt(6048/365)).
_INTERVAL_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120,
                     "4h": 240, "6h": 360, "8h": 480, "12h": 720, "1d": 1440, "3d": 4320,
                     "1w": 10080}


def bars_per_year(interval: str) -> Optional[float]:
    m = _INTERVAL_MINUTES.get((interval or "").strip().lower())
    return None if m is None else 365 * 24 * 60 / m


async def backtest(symbol: str, candles: List[dict], params: Optional[Dict[str, Any]] = None,
                   interval: str = "1d") -> Dict[str, Any]:
    """Run the Julia engine over the given candles. Cached CACHE_TTL_S."""
    if not quant_enabled():
        return _disabled("QUANT_JULIA_ENABLED")
    p = clean_params(params)
    key = json.dumps([symbol.upper(), interval, len(candles),
                      candles[-1].get("time") if candles else None, p], sort_keys=True)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return {**hit[1], "cached": True}
    body = candles_to_body(symbol, candles, p)
    if body is None:
        return {"enabled": True, "ok": False, "symbol": symbol.upper(),
                "error": f"insufficient candles (need >= {MIN_BARS})", "bars": len(candles)}
    bpy = bars_per_year(interval)
    if bpy:
        body["params"] = {**(body.get("params") or {}), "bars_per_year": bpy}
    r = await _http("POST", "/backtest", body)
    if not r["ok"]:
        return {"enabled": True, "ok": False, "symbol": symbol.upper(), "error": r["error"]}
    metrics = {k: r["data"].get(k) for k in _RESULT_KEYS}
    out = {"enabled": True, "ok": True, "symbol": symbol.upper(), "interval": interval,
           "bars": len(body["close"]), "params": p, "engine": "TradingOSQuant (Julia)",
           "metrics": metrics, "cached": False, "ts": int(time.time()),
           "bars_per_year": bpy,
           # additive: the engine's curve + fills for the Backtest tab
           "total_return_pct": r["data"].get("total_return_pct"),
           "equity_curve": (r["data"].get("equity_curve") or [])[-1000:],
           "trades": (r["data"].get("trades") or [])[-200:]}
    _cache[key] = (time.time(), out)
    return out


def whale_alerts(limit: int = 50, chain: Optional[str] = None,
                 min_usd: float = 0.0) -> Dict[str, Any]:
    """Newest-first whale alerts from the whale_watcher JSONL output."""
    if not whale_enabled():
        return _disabled("WHALE_WATCHER_ENABLED")
    p = whale_out_path()
    limit = max(1, min(int(limit), 500))
    if not p.exists():
        return {"enabled": True, "ok": False, "error": "no whale_watcher output yet", "alerts": []}
    try:
        tail: deque = deque(maxlen=5000)
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                tail.append(line)
        alerts: List[dict] = []
        for line in reversed(tail):
            try:
                a = json.loads(line)
            except ValueError:
                continue
            if not isinstance(a, dict):
                continue
            if chain and str(a.get("chain", "")).lower() != chain.lower():
                continue
            try:
                if float(a.get("amount_usd") or 0) < min_usd:
                    continue
            except (TypeError, ValueError):
                continue
            alerts.append(a)
            if len(alerts) >= limit:
                break
        return {"enabled": True, "ok": True, "alerts": alerts, "count": len(alerts)}
    except Exception as ex:  # noqa: BLE001 -- fail-open by design
        return {"enabled": True, "ok": False, "error": f"read failed: {type(ex).__name__}", "alerts": []}
