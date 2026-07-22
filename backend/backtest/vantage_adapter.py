"""Adapt Vantage's own trading_orders history into backend.backtest.models.TradeRecord.

Mirrors the exact average-cost book logic in backend/routers/trading.py's
_load_book() (BUY adds qty at fill price; SELL realizes P&L against the
running average cost) so the numbers agree with what an agent already sees
on GET /api/trading/positions — this just also emits one TradeRecord per
closing (SELL) fill instead of only the running totals, so real trading
history can be fed straight into monte_carlo_test / bootstrap_sharpe_ci /
walk_forward_analysis.

Known simplification: average-cost accounting blends multiple BUYs into one
running cost basis, so there is no single true "entry time" for a partial
close the way FIFO lot-matching would give you. entry_time here is the most
recent BUY fill's timestamp before the closing SELL — an approximation,
documented rather than hidden. Fees aren't tracked in trading_orders, so
commission is always 0.0.
"""
from __future__ import annotations

import aiosqlite
import pandas as pd

from ..db import get_db
from .models import TradeRecord


async def load_trade_records(agent_id: int, symbol: str | None = None) -> list[TradeRecord]:
    """Replay this agent's filled orders chronologically, emitting one
    TradeRecord per closing (SELL) fill. Optionally scoped to one symbol."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        query = (
            "SELECT symbol, side, filled_quantity, quantity, avg_fill_price, price, "
            "COALESCE(executed_at, created_at) AS ts "
            "FROM trading_orders WHERE agent_id=? AND status IN ('filled','submitted')"
        )
        params: list = [agent_id]
        if symbol:
            query += " AND symbol=?"
            params.append(symbol.upper())
        query += " ORDER BY COALESCE(executed_at, created_at), id"
        rows = await (await db.execute(query, params)).fetchall()

    book: dict[str, dict] = {}
    records: list[TradeRecord] = []

    for r in rows:
        o = dict(r)
        sym = (o["symbol"] or "").upper()
        if not sym:
            continue
        qty = o["filled_quantity"] or o["quantity"] or 0
        fill = o["avg_fill_price"] or o["price"] or 0
        if qty <= 0 or fill <= 0:
            continue
        ts = pd.Timestamp(o["ts"]) if o["ts"] else pd.Timestamp.utcnow()

        b = book.setdefault(sym, {
            "net_qty": 0.0, "cost_basis": 0.0, "last_buy_time": ts,
        })
        if str(o["side"]).upper() == "BUY":
            b["net_qty"] += qty
            b["cost_basis"] += qty * fill
            b["last_buy_time"] = ts
        else:
            avg = (b["cost_basis"] / b["net_qty"]) if b["net_qty"] else fill
            sell_qty = min(qty, b["net_qty"]) if b["net_qty"] > 0 else qty
            pnl = (fill - avg) * sell_qty
            entry_margin = avg * sell_qty
            exit_margin = fill * sell_qty
            records.append(TradeRecord(
                symbol=sym, direction=1,
                entry_price=avg, exit_price=fill,
                entry_time=b["last_buy_time"], exit_time=ts,
                size=sell_qty, leverage=1.0, pnl=pnl,
                pnl_pct=(pnl / entry_margin) if entry_margin else 0.0,
                exit_reason="signal", holding_bars=1, commission=0.0,
                entry_margin=entry_margin, exit_margin=exit_margin,
            ))
            b["net_qty"] -= qty
            b["cost_basis"] -= avg * sell_qty
            if b["net_qty"] <= 1e-12:
                b["net_qty"] = max(b["net_qty"], 0.0)
                b["cost_basis"] = max(b["cost_basis"], 0.0)

    return records
