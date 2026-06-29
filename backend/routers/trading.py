"""Trading API router — wallets, orders, strategies, PnL, and journal."""
import json, hashlib, hmac, logging, time
from typing import Optional, List
from datetime import datetime, date

import aiosqlite
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel

from backend.db import DB_PATH
from backend.deps import get_agent
from backend.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/trading", tags=["trading"])

# ── Models ──────────────────────────────────────────────────

class WalletCreate(BaseModel):
    label: str
    chain: str
    address: str
    encrypted_key: str = ""

class WalletUpdate(BaseModel):
    label: Optional[str] = None
    encrypted_key: Optional[str] = None

class OrderCreate(BaseModel):
    symbol: str
    side: str  # buy, sell
    chain: str
    quantity: float
    order_type: str = "market"
    price: Optional[float] = None
    wallet_id: Optional[int] = None
    trigger_reason: str = "manual"
    signal_id: Optional[int] = None
    strategy_id: Optional[int] = None

class StrategyCreate(BaseModel):
    name: str
    description: str = ""
    strategy_type: str
    config: dict = {}
    target_chain: str = ""
    target_symbols: str = ""
    max_position_size_usd: float = 0
    max_concurrent_trades: int = 1
    risk_per_trade_pct: float = 2.0
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None

class JournalCreate(BaseModel):
    entry_reasoning: str = ""
    exit_reasoning: str = ""
    conviction_score: float = 0
    lessons_learned: str = ""
    tags: list = []
    debate_id: Optional[int] = None

class PnLSnapshotCreate(BaseModel):
    snapshot_date: date
    portfolio_value_usd: float
    daily_pnl_usd: float
    daily_pnl_pct: float
    total_deposits_usd: float = 0
    total_withdrawals_usd: float = 0
    notes: str = ""

# ── Wallets ──────────────────────────────────────────────────

@router.post("/wallets")
async def create_wallet(data: WalletCreate, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            cur = await db.execute(
                "INSERT INTO trading_wallets (agent_id, label, chain, address, encrypted_private_key) VALUES (?,?,?,?,?)",
                (agent["id"], data.label, data.chain, data.address, data.encrypted_key)
            )
            await db.commit()
            return {"id": cur.lastrowid, "label": data.label, "chain": data.chain, "address": data.address}
        except aiosqlite.IntegrityError:
            raise HTTPException(409, f"Wallet '{data.label}' already exists for this agent")

@router.get("/wallets")
async def list_wallets(agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT id, label, chain, address, exchange, created_at, last_synced_at FROM trading_wallets WHERE agent_id=?",
            (agent["id"],)
        )).fetchall()
        wallets = []
        for r in rows:
            w = dict(r)
            # Get balances for each wallet
            bal_rows = await (await db.execute(
                "SELECT token, balance, value_usd FROM trading_balances WHERE wallet_id=?",
                (w["id"],)
            )).fetchall()
            w["balances"] = [dict(b) for b in bal_rows]
            wallets.append(w)
        return wallets

@router.get("/wallets/{wallet_id}")
async def get_wallet(wallet_id: int, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT id, label, chain, address, exchange, created_at, last_synced_at FROM trading_wallets WHERE id=? AND agent_id=?",
            (wallet_id, agent["id"])
        )).fetchone()
        if not row:
            raise HTTPException(404, "Wallet not found")
        w = dict(row)
        bal_rows = await (await db.execute(
            "SELECT token, balance, value_usd FROM trading_balances WHERE wallet_id=?",
            (wallet_id,)
        )).fetchall()
        w["balances"] = [dict(b) for b in bal_rows]
        return w

@router.delete("/wallets/{wallet_id}")
async def delete_wallet(wallet_id: int, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM trading_balances WHERE wallet_id=?", (wallet_id,))
        await db.execute("DELETE FROM trading_wallets WHERE id=? AND agent_id=?", (wallet_id, agent["id"]))
        await db.commit()
        return {"status": "deleted"}

@router.post("/wallets/{wallet_id}/sync")
async def sync_wallet(wallet_id: int, agent: dict = Depends(get_agent)):
    """Placeholder for balance refresh from chain. Actual sync delegated to external engine."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE trading_wallets SET last_synced_at=datetime('now') WHERE id=? AND agent_id=?",
            (wallet_id, agent["id"])
        )
        await db.commit()
    return {"status": "sync_requested", "wallet_id": wallet_id}

# ── Orders ──────────────────────────────────────────────────

@router.post("/orders")
async def create_order(data: OrderCreate, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO trading_orders 
               (agent_id, wallet_id, order_type, side, symbol, chain, quantity, price, trigger_reason, signal_id, strategy_id, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (agent["id"], data.wallet_id, data.order_type, data.side.upper(),
             data.symbol, data.chain, data.quantity, data.price,
             data.trigger_reason, data.signal_id, data.strategy_id, "pending")
        )
        order_id = cur.lastrowid
        await db.commit()
        
        # Auto-create journal entry
        await db.execute(
            "INSERT INTO trading_trade_journal (order_id, agent_id, entry_reasoning) VALUES (?,?,?)",
            (order_id, agent["id"], f"Order placed: {data.side} {data.quantity} {data.symbol} on {data.chain} ({data.trigger_reason})")
        )
        await db.commit()
        
        return {"id": order_id, "status": "pending", "symbol": data.symbol, "side": data.side}

@router.get("/orders")
async def list_orders(
    agent: dict = Depends(get_agent),
    status: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    limit: int = Query(50, le=200)
):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM trading_orders WHERE agent_id=?"
        params = [agent["id"]]
        if status:
            query += " AND status=?"
            params.append(status)
        if symbol:
            query += " AND symbol=?"
            params.append(symbol.upper())
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = await (await db.execute(query, params)).fetchall()
        return [dict(r) for r in rows]

@router.get("/orders/{order_id}")
async def get_order(order_id: int, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM trading_orders WHERE id=? AND agent_id=?", (order_id, agent["id"])
        )).fetchone()
        if not row:
            raise HTTPException(404, "Order not found")
        return dict(row)

@router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: int, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE trading_orders SET status='cancelled', settled_at=datetime('now') WHERE id=? AND agent_id=? AND status IN ('pending','open')",
            (order_id, agent["id"])
        )
        await db.commit()
    return {"status": "cancelled", "order_id": order_id}

@router.post("/orders/{order_id}/paper-fill")
async def paper_fill_order(order_id: int, agent: dict = Depends(get_agent)):
    """Simulated (paper) fill — explicitly NOT real settlement.

    Marks a pending order 'filled' at the live market quote (falling back to the
    order's own limit price). The fill is tagged tx_hash='paper:<uuid>' and a
    journal entry is written tagged 'simulated' so a paper fill is never confused
    with real execution. Intended for the Portfolio "Simulated (paper)" mode.
    """
    import uuid as _uuid
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM trading_orders WHERE id=? AND agent_id=?", (order_id, agent["id"])
        )).fetchone()
        if not row:
            raise HTTPException(404, "Order not found")
        order = dict(row)
        if str(order["status"]).lower() != "pending":
            raise HTTPException(409, f"Order is '{order['status']}'; only pending orders can be paper-filled")

        # Resolve a fill price: live quote first, then the order's own limit price.
        fill_price = await _fetch_quote(order["symbol"])
        if fill_price is None:
            fill_price = order.get("price")
        if not fill_price:
            raise HTTPException(422, "No live quote available and no limit price set; cannot simulate a fill")

        tx_hash = f"paper:{_uuid.uuid4().hex[:16]}"
        await db.execute(
            """UPDATE trading_orders SET status='filled', filled_quantity=quantity,
               avg_fill_price=?, tx_hash=?, executed_at=datetime('now'), settled_at=datetime('now')
               WHERE id=? AND agent_id=?""",
            (fill_price, tx_hash, order_id, agent["id"])
        )

        note = f"[SIMULATED] Paper-filled {order['side']} {order['quantity']} {order['symbol']} @ {fill_price} ({tx_hash})"
        existing = await (await db.execute(
            "SELECT id FROM trading_trade_journal WHERE order_id=?", (order_id,)
        )).fetchone()
        if existing:
            await db.execute(
                "UPDATE trading_trade_journal SET exit_reasoning=?, tags=? WHERE order_id=?",
                (note, json.dumps(["simulated", "paper-fill"]), order_id)
            )
        else:
            await db.execute(
                "INSERT INTO trading_trade_journal (order_id, agent_id, entry_reasoning, tags) VALUES (?,?,?,?)",
                (order_id, agent["id"], note, json.dumps(["simulated", "paper-fill"]))
            )
        await db.commit()

        updated = await (await db.execute(
            "SELECT * FROM trading_orders WHERE id=? AND agent_id=?", (order_id, agent["id"])
        )).fetchone()
        return dict(updated)

@router.post("/orders/{order_id}/journal")
async def add_journal(order_id: int, data: JournalCreate, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        # Verify order exists
        row = await (await db.execute(
            "SELECT id FROM trading_orders WHERE id=? AND agent_id=?", (order_id, agent["id"])
        )).fetchone()
        if not row:
            raise HTTPException(404, "Order not found")
        
        # Check if journal exists
        existing = await (await db.execute(
            "SELECT id FROM trading_trade_journal WHERE order_id=?", (order_id,)
        )).fetchone()
        
        if existing:
            await db.execute(
                """UPDATE trading_trade_journal SET 
                   entry_reasoning=COALESCE(NULLIF(?,''), entry_reasoning),
                   exit_reasoning=COALESCE(NULLIF(?,''), exit_reasoning),
                   conviction_score=?, lessons_learned=?, tags=?, debate_id=COALESCE(?, debate_id)
                   WHERE order_id=?""",
                (data.entry_reasoning, data.exit_reasoning, data.conviction_score,
                 data.lessons_learned, json.dumps(data.tags), data.debate_id, order_id)
            )
        else:
            await db.execute(
                "INSERT INTO trading_trade_journal (order_id, agent_id, entry_reasoning, exit_reasoning, conviction_score, lessons_learned, tags, debate_id) VALUES (?,?,?,?,?,?,?,?)",
                (order_id, agent["id"], data.entry_reasoning, data.exit_reasoning,
                 data.conviction_score, data.lessons_learned, json.dumps(data.tags), data.debate_id)
            )
        await db.commit()
    return {"status": "saved", "order_id": order_id}

# ── Strategies ──────────────────────────────────────────────

@router.post("/strategies")
async def create_strategy(data: StrategyCreate, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO trading_strategies 
               (agent_id, name, description, strategy_type, config, target_chain, target_symbols,
                max_position_size_usd, max_concurrent_trades, risk_per_trade_pct,
                stop_loss_pct, take_profit_pct)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (agent["id"], data.name, data.description, data.strategy_type,
             json.dumps(data.config), data.target_chain, data.target_symbols,
             data.max_position_size_usd, data.max_concurrent_trades, data.risk_per_trade_pct,
             data.stop_loss_pct, data.take_profit_pct)
        )
        await db.commit()
        return {"id": cur.lastrowid, "name": data.name}

@router.get("/strategies")
async def list_strategies(agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM trading_strategies WHERE agent_id=? ORDER BY created_at DESC",
            (agent["id"],)
        )).fetchall()
        return [dict(r) for r in rows]

@router.get("/strategies/{strategy_id}")
async def get_strategy(strategy_id: int, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM trading_strategies WHERE id=? AND agent_id=?", (strategy_id, agent["id"])
        )).fetchone()
        if not row:
            raise HTTPException(404, "Strategy not found")
        s = dict(row)
        # Get runs
        runs = await (await db.execute(
            "SELECT * FROM trading_strategy_runs WHERE strategy_id=? ORDER BY started_at DESC LIMIT 10",
            (strategy_id,)
        )).fetchall()
        s["runs"] = [dict(r) for r in runs]
        return s

@router.post("/strategies/{strategy_id}/toggle")
async def toggle_strategy(strategy_id: int, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT enabled FROM trading_strategies WHERE id=? AND agent_id=?", (strategy_id, agent["id"])
        )).fetchone()
        if not row:
            raise HTTPException(404, "Strategy not found")
        new = 0 if row[0] else 1
        await db.execute("UPDATE trading_strategies SET enabled=?, updated_at=datetime('now') WHERE id=?", (new, strategy_id))
        await db.commit()
        return {"status": "enabled" if new else "disabled"}

@router.delete("/strategies/{strategy_id}")
async def delete_strategy(strategy_id: int, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM trading_strategies WHERE id=? AND agent_id=?", (strategy_id, agent["id"]))
        await db.commit()
    return {"status": "deleted"}

# ── Performance ─────────────────────────────────────────────

@router.get("/performance")
async def get_performance(agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        # Total PnL from snapshots
        latest = await (await db.execute(
            "SELECT portfolio_value_usd, daily_pnl_usd, daily_pnl_pct, snapshot_date FROM trading_pnl_snapshots WHERE agent_id=? ORDER BY snapshot_date DESC LIMIT 1",
            (agent["id"],)
        )).fetchone()
        
        # Win rate from orders
        total = await (await db.execute(
            "SELECT COUNT(*) as c FROM trading_orders WHERE agent_id=? AND status='filled'", (agent["id"],)
        )).fetchone()
        
        winning = await (await db.execute(
            "SELECT COUNT(*) as c FROM trading_orders o JOIN trading_trade_journal j ON j.order_id=o.id WHERE o.agent_id=? AND j.conviction_score > 0.6",
            (agent["id"],)
        )).fetchone()
        
        return {
            "portfolio_value": dict(latest) if latest else None,
            "total_trades": total[0] if total else 0,
            "winning_trades": winning[0] if winning else 0,
            "win_rate": round(winning[0] / total[0] * 100, 1) if total and total[0] else 0,
        }

@router.get("/performance/daily")
async def get_daily_pnl(agent: dict = Depends(get_agent), days: int = Query(30, le=365)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT snapshot_date, portfolio_value_usd, daily_pnl_usd, daily_pnl_pct FROM trading_pnl_snapshots WHERE agent_id=? ORDER BY snapshot_date DESC LIMIT ?",
            (agent["id"], days)
        )).fetchall()
        return [dict(r) for r in rows]

@router.post("/performance/snapshot")
async def create_snapshot(data: PnLSnapshotCreate, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO trading_pnl_snapshots (agent_id, snapshot_date, portfolio_value_usd, daily_pnl_usd, daily_pnl_pct, total_deposits_usd, total_withdrawals_usd, notes) VALUES (?,?,?,?,?,?,?,?)",
                (agent["id"], data.snapshot_date.isoformat(), data.portfolio_value_usd,
                 data.daily_pnl_usd, data.daily_pnl_pct, data.total_deposits_usd,
                 data.total_withdrawals_usd, data.notes)
            )
            await db.commit()
            return {"status": "saved", "date": data.snapshot_date.isoformat()}
        except aiosqlite.IntegrityError:
            raise HTTPException(409, "Snapshot already exists for this date")

# ── Market Data (Bridge) ────────────────────────────────────

@router.get("/markets")
async def list_markets():
    """Available trading markets fetched from RPC proxy."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get("http://127.0.0.1:9861/api/rpc")
            data = r.json()
            endpoints = data.get("endpoints", {})
            markets = []
            for name, ep in endpoints.items():
                markets.append({
                    "chain": ep.get("chain", name),
                    "id": name,
                    "type": "rpc"
                })
            return markets
    except Exception as e:
        return {"available": ["solana", "hyperliquid", "base", "polygon"], "error": str(e)}

async def _fetch_quote(symbol: str) -> Optional[float]:
    """Live USD quote: direct no-auth sources (Pyth → CoinGecko) first, external
    RPC proxy only as a last resort. Returns None on total failure."""
    # Primary: Vantage-owned direct market sources (no external engine needed).
    try:
        from backend import market_sources as _ms
        price = await _ms.resolve_price(symbol)
        if price:
            return float(price)
    except Exception:
        pass
    # Fallback: external intel/RPC proxy, if it happens to be running.
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post("http://127.0.0.1:9861/api/rpc/coingecko",
                json={"path": f"/api/v3/simple/price?ids={symbol.lower()}&vs_currencies=usd"})
            data = r.json()
            price = data.get(symbol.lower(), {}).get("usd")
            return float(price) if price else None
    except Exception:
        return None

@router.get("/markets/{symbol}/price")
async def get_price(symbol: str):
    """Get real-time price for a symbol from available sources."""
    price = await _fetch_quote(symbol)
    if price is None:
        return {"symbol": symbol.upper(), "price": 0, "error": "price fetch failed"}
    return {"symbol": symbol.upper(), "price": price}

# ── Signals ─────────────────────────────────────────────────

@router.post("/signals/ingest")
async def ingest_signal(request: Request, agent: dict = Depends(get_agent)):
    """External signal ingestion — receives signals from Ares intel engine."""
    body = await request.json()
    symbol = body.get("symbol", body.get("pair", "UNKNOWN"))
    signal_type = body.get("type", body.get("signal", "alert"))
    conviction = float(body.get("conviction", body.get("confidence", 0)))
    
    # Auto-create a pending order if conviction is high
    if conviction > 0.7 and signal_type.upper() in ("BUY", "LONG"):
        side = "BUY"
    elif conviction > 0.7 and signal_type.upper() in ("SELL", "SHORT"):
        side = "SELL"
    else:
        side = "HOLD"
    
    return {
        "status": "ingested",
        "symbol": symbol,
        "signal": signal_type,
        "conviction": conviction,
        "suggested_action": side,
        "agent": agent["name"]
    }

# ── Trade Journal ───────────────────────────────────────────

@router.get("/journal")
async def list_journal(agent: dict = Depends(get_agent), limit: int = Query(50, le=200)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT j.*, o.symbol, o.side, o.quantity, o.status
               FROM trading_trade_journal j
               JOIN trading_orders o ON o.id = j.order_id
               WHERE j.agent_id=?
               ORDER BY j.created_at DESC LIMIT ?""",
            (agent["id"], limit)
        )).fetchall()
        return [dict(r) for r in rows]

# ── Risk ────────────────────────────────────────────────────

@router.get("/risk")
async def get_risk(agent: dict = Depends(get_agent)):
    """Current risk metrics: exposure, drawdown, concentration."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        # Open positions
        open_orders = await (await db.execute(
            "SELECT COUNT(*) as c, COALESCE(SUM(quantity * COALESCE(price,0)),0) as exposure FROM trading_orders WHERE agent_id=? AND status IN ('pending','open')",
            (agent["id"],)
        )).fetchone()
        
        # Latest portfolio value
        latest = await (await db.execute(
            "SELECT portfolio_value_usd FROM trading_pnl_snapshots WHERE agent_id=? ORDER BY snapshot_date DESC LIMIT 1",
            (agent["id"],)
        )).fetchone()
        
        # Active strategies
        strategies = await (await db.execute(
            "SELECT COUNT(*) as c FROM trading_strategies WHERE agent_id=? AND enabled=1",
            (agent["id"],)
        )).fetchone()
        
        return {
            "open_positions": open_orders[0] if open_orders else 0,
            "total_exposure_usd": round(open_orders[1] if open_orders else 0, 2),
            "portfolio_value_usd": latest[0] if latest else 0,
            "exposure_pct": round(open_orders[1] / latest[0] * 100, 1) if latest and latest[0] and open_orders else 0,
            "active_strategies": strategies[0] if strategies else 0,
            "max_drawdown_pct": 0,  # Computed from PnL history
        }

# ── Positions / Portfolio (live-valued, realized + unrealized P&L) ──────────

async def _load_book(agent_id: int) -> dict:
    """Build an average-cost book from filled orders processed chronologically.

    Returns {symbol: {net_qty, cost_basis, realized}}. A BUY adds quantity at its
    fill price; a SELL realizes P&L against the running average cost and reduces
    the basis proportionally. This is standard avg-cost accounting — the same a
    connecting agent (Hermes, OpenClaw, …) would expect when it reads its book."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT symbol, side, filled_quantity, quantity, avg_fill_price, price
               FROM trading_orders WHERE agent_id=? AND status='filled'
               ORDER BY COALESCE(executed_at, created_at), id""",
            (agent_id,)
        )).fetchall()

    book: dict[str, dict] = {}
    for r in rows:
        o = dict(r)
        sym = (o["symbol"] or "").upper()
        if not sym:
            continue
        qty = o["filled_quantity"] or o["quantity"] or 0
        fill = o["avg_fill_price"] or o["price"] or 0
        b = book.setdefault(sym, {"net_qty": 0.0, "cost_basis": 0.0, "realized": 0.0})
        if str(o["side"]).upper() == "BUY":
            b["net_qty"] += qty
            b["cost_basis"] += qty * fill
        else:
            avg = (b["cost_basis"] / b["net_qty"]) if b["net_qty"] else fill
            sell_qty = min(qty, b["net_qty"]) if b["net_qty"] > 0 else qty
            b["realized"] += (fill - avg) * sell_qty
            b["net_qty"] -= qty
            b["cost_basis"] -= avg * sell_qty
            if b["net_qty"] <= 1e-12:
                b["net_qty"] = max(b["net_qty"], 0.0)
                b["cost_basis"] = max(b["cost_basis"], 0.0)
    return book


async def _value_positions(book: dict) -> dict:
    """Value an avg-cost book at live quotes. Returns positions + totals."""
    positions = []
    total_value = 0.0
    total_unrealized = 0.0
    total_realized = 0.0
    for sym, b in book.items():
        total_realized += b["realized"]
        if abs(b["net_qty"]) < 1e-9:
            continue
        live = await _fetch_quote(sym)
        avg_cost = (b["cost_basis"] / b["net_qty"]) if b["net_qty"] else 0
        market_value = (live or 0) * b["net_qty"]
        unrealized = (market_value - b["cost_basis"]) if live else 0
        total_value += market_value
        total_unrealized += unrealized
        positions.append({
            "symbol": sym,
            "net_quantity": round(b["net_qty"], 8),
            "avg_cost": round(avg_cost, 6),
            "live_price": live,
            "market_value_usd": round(market_value, 2),
            "unrealized_pnl_usd": round(unrealized, 2),
            "unrealized_pnl_pct": round((unrealized / b["cost_basis"] * 100), 2) if b["cost_basis"] else 0,
            "realized_pnl_usd": round(b["realized"], 2),
        })
    positions.sort(key=lambda p: -abs(p["market_value_usd"]))
    return {
        "positions": positions,
        "total_market_value_usd": round(total_value, 2),
        "total_unrealized_pnl_usd": round(total_unrealized, 2),
        "total_realized_pnl_usd": round(total_realized, 2),
        "priced": all(p["live_price"] for p in positions) if positions else True,
    }


@router.get("/positions")
async def get_positions(agent: dict = Depends(get_agent)):
    """Open positions valued at the live quote, with realized + unrealized P&L."""
    book = await _load_book(agent["id"])
    return await _value_positions(book)


@router.get("/portfolio")
async def get_portfolio(agent: dict = Depends(get_agent)):
    """Live portfolio snapshot: positions + realized/unrealized P&L + trade stats.

    This is the real, live-valued view of the agent's book (no manual snapshot
    needed). Win rate is computed from realized closes across all symbols."""
    book = await _load_book(agent["id"])
    valued = await _value_positions(book)

    # Trade stats from realized closes.
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        filled = await (await db.execute(
            "SELECT COUNT(*) c FROM trading_orders WHERE agent_id=? AND status='filled'",
            (agent["id"],)
        )).fetchone()

    winners = len([p for p in valued["positions"] if p["realized_pnl_usd"] > 0])
    realized_symbols = len([s for s, b in book.items() if abs(b["realized"]) > 1e-9])
    return {
        **valued,
        "total_pnl_usd": round(valued["total_unrealized_pnl_usd"] + valued["total_realized_pnl_usd"], 2),
        "open_positions": len(valued["positions"]),
        "filled_orders": filled[0] if filled else 0,
        "symbols_realized": realized_symbols,
        "winning_symbols": winners,
    }


@router.post("/snapshot/auto")
async def auto_snapshot(agent: dict = Depends(get_agent)):
    """Value the live book and upsert today's PnL snapshot, so the equity curve
    auto-populates from real positions instead of manual entry."""
    book = await _load_book(agent["id"])
    valued = await _value_positions(book)
    value = valued["total_market_value_usd"]
    daily_pnl = valued["total_unrealized_pnl_usd"] + valued["total_realized_pnl_usd"]
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        # Yesterday's value for a daily delta, if present.
        prev = await (await db.execute(
            "SELECT portfolio_value_usd FROM trading_pnl_snapshots WHERE agent_id=? AND snapshot_date<? ORDER BY snapshot_date DESC LIMIT 1",
            (agent["id"], today)
        )).fetchone()
        daily_pct = round((value - prev[0]) / prev[0] * 100, 2) if prev and prev[0] else 0
        await db.execute(
            """INSERT INTO trading_pnl_snapshots
                 (agent_id, snapshot_date, portfolio_value_usd, daily_pnl_usd, daily_pnl_pct, notes)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(agent_id, snapshot_date) DO UPDATE SET
                 portfolio_value_usd=excluded.portfolio_value_usd,
                 daily_pnl_usd=excluded.daily_pnl_usd,
                 daily_pnl_pct=excluded.daily_pnl_pct""",
            (agent["id"], today, value, round(daily_pnl, 2), daily_pct, "auto: live position valuation")
        )
        await db.commit()
    return {"status": "snapshot_saved", "date": today, "portfolio_value_usd": value}
