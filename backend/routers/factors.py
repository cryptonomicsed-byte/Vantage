"""Alpha factor zoo + statistical backtest validation — agent-first API.

Extracted from HKUDS/Vibe-Trading (MIT). 462 quant factors (alpha101, qlib158,
gtja191, academic) plus Monte Carlo / bootstrap / walk-forward validation for
any trade series, wired into Vantage's agent auth + rate limiting.

None of this replaces Ares's existing degen-alpha wallet-hunting pipeline
(backend/alpha_engine.py) — that's a different "alpha" (pump.fun sniper
scoring). This is classic quant signal research: turn OHLCV history into
ranked, testable trading factors, then validate a trade series statistically
before trusting it.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import get_agent

router = APIRouter(prefix="/api/factors", tags=["factors"])

_registry = None


def _get_registry():
    global _registry
    if _registry is None:
        from backend.factors.registry import Registry
        _registry = Registry()
    return _registry


@router.get("/health")
async def factors_health(agent: dict = Depends(get_agent)):
    """Registry load status — loaded/failed counts, one entry per bad file."""
    return _get_registry().health()


@router.get("/list")
async def list_factors(
    zoo: Optional[str] = None,
    theme: Optional[str] = None,
    universe: Optional[str] = None,
    agent: dict = Depends(get_agent),
):
    """List available alpha ids, optionally filtered by zoo/theme/universe."""
    ids = _get_registry().list(zoo=zoo, theme=theme, universe=universe)
    return {"count": len(ids), "ids": ids}


@router.get("/{alpha_id}")
async def get_factor_meta(alpha_id: str, agent: dict = Depends(get_agent)):
    """Metadata for one factor: formula, required columns, universe, etc."""
    try:
        alpha = _get_registry().get(alpha_id)
    except KeyError:
        raise HTTPException(404, f"unknown alpha_id: {alpha_id}")
    return {"id": alpha.id, "zoo": alpha.zoo, "meta": alpha.meta}


class ComputeRequest(BaseModel):
    alpha_id: str
    # panel[column][date_iso][symbol] = float — wide-format OHLCV columns
    panel: dict[str, dict[str, dict[str, float]]]


@router.post("/compute")
async def compute_factor(req: ComputeRequest, agent: dict = Depends(get_agent)):
    """Compute one alpha over a caller-supplied OHLCV panel.

    panel is {"close": {"2026-01-01": {"SOL": 100.5, "BTC": ...}, ...}, "open": {...}, ...}
    — the same wide-DataFrame contract every factor in the zoo expects,
    just JSON-shaped for the wire.
    """
    reg = _get_registry()
    try:
        reg.get(req.alpha_id)
    except KeyError:
        raise HTTPException(404, f"unknown alpha_id: {req.alpha_id}")

    try:
        pd_panel = {
            col: pd.DataFrame(rows).T if rows else pd.DataFrame()
            for col, rows in req.panel.items()
        }
        for col, df in pd_panel.items():
            if not df.empty:
                df.index = pd.to_datetime(df.index)
                pd_panel[col] = df.sort_index()
        out = reg.compute(req.alpha_id, pd_panel)
    except Exception as exc:
        raise HTTPException(422, f"compute failed: {exc}")

    def _json_safe(v: float) -> Optional[float]:
        return None if pd.isna(v) or v in (float("inf"), float("-inf")) else float(v)

    return {
        "alpha_id": req.alpha_id,
        "result": {
            ts.isoformat(): {col: _json_safe(v) for col, v in row.items()}
            for ts, row in out.iterrows()
        },
    }


# ── Statistical validation (Monte Carlo / bootstrap / walk-forward) ────────

class TradeIn(BaseModel):
    symbol: str
    direction: int  # 1 long, -1 short
    entry_price: float
    exit_price: float
    entry_time: str  # ISO timestamp
    exit_time: str
    size: float
    pnl: float
    leverage: float = 1.0
    pnl_pct: float = 0.0
    exit_reason: str = "signal"
    holding_bars: int = 1
    commission: float = 0.0
    entry_margin: float = 0.0
    exit_margin: float = 0.0


class ValidateRequest(BaseModel):
    trades: list[TradeIn]
    initial_capital: float = 10000.0
    n_simulations: int = 1000
    seed: int = 42


def _to_trade_records(trades: list[TradeIn]):
    from backend.backtest.models import TradeRecord
    return [
        TradeRecord(
            symbol=t.symbol, direction=t.direction,
            entry_price=t.entry_price, exit_price=t.exit_price,
            entry_time=pd.Timestamp(t.entry_time), exit_time=pd.Timestamp(t.exit_time),
            size=t.size, leverage=t.leverage, pnl=t.pnl, pnl_pct=t.pnl_pct,
            exit_reason=t.exit_reason, holding_bars=t.holding_bars,
            commission=t.commission, entry_margin=t.entry_margin, exit_margin=t.exit_margin,
        )
        for t in trades
    ]


def _equity_curve_from_trades(records, initial_capital: float) -> pd.Series:
    """Sort by exit time, cumsum pnl on top of starting capital — the same
    shape a real backtest engine's equity.csv artifact would produce."""
    ordered = sorted(records, key=lambda r: r.exit_time)
    idx = pd.DatetimeIndex([r.exit_time for r in ordered])
    equity = initial_capital + pd.Series([r.pnl for r in ordered], index=idx).cumsum()
    return equity


@router.post("/validate/monte-carlo")
async def validate_monte_carlo(req: ValidateRequest, agent: dict = Depends(get_agent)):
    """Shuffle trade PnL order N times: is the observed Sharpe/drawdown
    significantly better than a random ordering of the same trades?"""
    from backend.backtest.validation import monte_carlo_test
    if not req.trades:
        raise HTTPException(422, "trades must not be empty")
    result = monte_carlo_test(
        _to_trade_records(req.trades), req.initial_capital,
        n_simulations=req.n_simulations, seed=req.seed,
    )
    return result


@router.post("/validate/bootstrap")
async def validate_bootstrap(req: ValidateRequest, agent: dict = Depends(get_agent)):
    """Bootstrap confidence interval on Sharpe ratio — how stable is it?"""
    from backend.backtest.validation import bootstrap_sharpe_ci
    if not req.trades:
        raise HTTPException(422, "trades must not be empty")
    records = _to_trade_records(req.trades)
    equity = _equity_curve_from_trades(records, req.initial_capital)
    result = bootstrap_sharpe_ci(
        equity, n_bootstrap=req.n_simulations, seed=req.seed,
    )
    return result


@router.post("/validate/walk-forward")
async def validate_walk_forward(req: ValidateRequest, n_windows: int = 4, agent: dict = Depends(get_agent)):
    """Split trades into N time windows — is performance consistent across them?"""
    from backend.backtest.validation import walk_forward_analysis
    if not req.trades:
        raise HTTPException(422, "trades must not be empty")
    records = _to_trade_records(req.trades)
    equity = _equity_curve_from_trades(records, req.initial_capital)
    result = walk_forward_analysis(equity, records, n_windows=n_windows)
    return result
