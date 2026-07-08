import os
"""Trading API router — wallets, orders, strategies, PnL, and journal."""
import json, hashlib, hmac, logging, time
from typing import Optional, List
from datetime import datetime, timezone, date

import aiosqlite
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel

from backend.db import DB_PATH
from backend.deps import get_agent
from backend.config import settings
from backend.crypto_utils import encrypt_key_for_agent, decrypt_key_for_agent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/trading", tags=["trading"])

# ── Models ──────────────────────────────────────────────────

class WalletCreate(BaseModel):
    label: str
    chain: str
    address: str
    encrypted_key: str = ""
    exchange: str = ""  # e.g. "Coinbase", "Binance" — blank means self-custody

class WalletUpdate(BaseModel):
    label: Optional[str] = None
    encrypted_key: Optional[str] = None
    exchange: Optional[str] = None

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

class WalletGenerate(BaseModel):
    system: str = "bip39"
    chain: str = "solana"
    label: str = ""

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
                "INSERT INTO trading_wallets (agent_id, label, chain, address, encrypted_private_key, exchange) VALUES (?,?,?,?,?,?)",
                (agent["id"], data.label, data.chain, data.address, data.encrypted_key, data.exchange)
            )
            await db.commit()
            return {"id": cur.lastrowid, "label": data.label, "chain": data.chain, "address": data.address, "exchange": data.exchange}
        except aiosqlite.IntegrityError:
            raise HTTPException(409, f"Wallet '{data.label}' already exists for this agent")

@router.get("/wallets/live")
async def wallets_live(agent: dict = Depends(get_agent)):
    """Return all wallets with live on-chain balances via Helius RPC."""
    import urllib.request, json as _json
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        wallets = await db.execute(
            "SELECT id, label, chain, address, balance_hint, last_synced_at FROM trading_wallets WHERE agent_id = ?",
            (agent["id"],)
        )
        wallets = [dict(w) for w in await wallets.fetchall()]
    
    # Try live Helius refresh for Solana wallets
    helius_key = os.environ.get("HELIUS_API_KEY", "")
    
    for w in wallets:
        if w.get("chain") == "solana" and w.get("address"):
            try:
                payload = _json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "getBalance",
                    "params": [w["address"]]
                }).encode()
                req = urllib.request.Request(
                    f"https://mainnet.helius-rpc.com/?api-key={helius_key}",
                    data=payload,
                    headers={"Content-Type": "application/json"}
                )
                resp = urllib.request.urlopen(req, timeout=5)
                data = _json.loads(resp.read().decode())
                sol = data.get("result", {}).get("value", 0) / 1e9
                w["balance_live"] = f"{sol} SOL"
                w["balance_value_usd"] = round(sol * 81, 2)  # approximate
            except:
                w["balance_live"] = w.get("balance_hint", "unknown")
                w["balance_value_usd"] = 0
    
    return {
        "wallets": wallets,
        "count": len(wallets),
        "total_value_usd": sum(w.get("balance_value_usd", 0) for w in wallets),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }

@router.post("/strategies")
async def create_strategy(data: StrategyCreate, wallet_id: int = Query(...), agent: dict = Depends(get_agent)):
    """Create a trading strategy linked to a wallet."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO trading_strategies 
               (agent_id, wallet_id, name, description, strategy_type, config, 
                target_chain, target_symbols, max_position_size_usd, max_concurrent_trades,
                risk_per_trade_pct, stop_loss_pct, take_profit_pct)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (agent["id"], wallet_id, data.name, data.description, data.strategy_type,
             json.dumps(data.config), data.target_chain, data.target_symbols,
             data.max_position_size_usd, data.max_concurrent_trades,
             data.risk_per_trade_pct, data.stop_loss_pct, data.take_profit_pct)
        )
        sid = cur.lastrowid
        await db.commit()
        return {"id": sid, "name": data.name, "status": "created"}

@router.patch("/wallets/{wallet_id}")
async def update_wallet(wallet_id: int, data: WalletUpdate, agent: dict = Depends(get_agent)):
    """Rename a wallet, mark/unmark it as held on an exchange, or rotate its
    stored encrypted key — whatever's provided. Ownership-scoped like every
    other wallet endpoint."""
    fields, params = [], []
    if data.label is not None:
        fields.append("label=?"); params.append(data.label)
    if data.exchange is not None:
        fields.append("exchange=?"); params.append(data.exchange)
    if data.encrypted_key is not None:
        fields.append("encrypted_private_key=?"); params.append(data.encrypted_key)
    if not fields:
        raise HTTPException(422, "At least one field (label, exchange, encrypted_key) is required")
    params.extend([wallet_id, agent["id"]])
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            cur = await db.execute(
                f"UPDATE trading_wallets SET {', '.join(fields)} WHERE id=? AND agent_id=?", params,
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            raise HTTPException(409, f"Wallet '{data.label}' already exists for this agent")
        if cur.rowcount == 0:
            raise HTTPException(404, "Wallet not found")
    return {"status": "updated"}

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
    """Refresh balance from chain for bitcoin/solana wallets (mempool.space /
    public Solana RPC via market_sources.address_lookup) — real, not a no-op.
    Falls back to the old timestamp-only bump for any other chain, since
    there's no free no-key balance source for those yet."""
    from backend import market_sources as ms
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT chain, address FROM trading_wallets WHERE id=? AND agent_id=?",
            (wallet_id, agent["id"])
        )).fetchone()
        if not row:
            raise HTTPException(404, "Wallet not found")

        status = "chain_unsupported_for_live_sync"
        result = await ms.address_lookup(row["chain"], row["address"])
        if result.get("supported") and result.get("balance"):
            amount = result["balance"]["amount"]
            unit = result["balance"]["unit"]
            price = await ms.resolve_price(unit)
            value_usd = round(amount * price, 2) if price else None
            await db.execute(
                """INSERT INTO trading_balances (wallet_id, token, balance, value_usd)
                   VALUES (?,?,?,?)
                   ON CONFLICT(wallet_id, token) DO UPDATE SET
                     balance=excluded.balance, value_usd=excluded.value_usd, updated_at=datetime('now')""",
                (wallet_id, unit, amount, value_usd),
            )
            status = "synced"

        await db.execute(
            "UPDATE trading_wallets SET last_synced_at=datetime('now') WHERE id=? AND agent_id=?",
            (wallet_id, agent["id"])
        )
        # Keep the equity curve synced: refresh today's net-worth point from the
        # freshly-synced linked-wallet balances.
        await _upsert_networth_snapshot(db, agent["id"])
        await db.commit()
    return {"status": status, "wallet_id": wallet_id}

# ── Wallet Generation ────────────────────────────────────

@router.post("/wallets/generate")
async def generate_wallet(data: WalletGenerate, agent: dict = Depends(get_agent)):
    """Generate a new wallet (BIP-39 or BIPON39) and store encrypted."""
    import asyncio as _asyncio
    import subprocess as _sp, json as _json
    
    system = data.system.lower()
    chain = data.chain.lower()
    label = data.label or f"{system.upper()} {chain.title()}"
    
    if system == "bipon39":
        try:
            r = await _asyncio.to_thread(lambda: _sp.run(
                ["bipon39", "generate"], capture_output=True, text=True, timeout=10
            ))
            result = _json.loads(r.stdout) if r.stdout else None
        except Exception:
            result = None
    else:
        try:
            r = await _asyncio.to_thread(lambda: _sp.run(
                ["curl", "-s", "-X", "POST", "http://127.0.0.1:8778/api/wallet/create",
                 "-H", "Content-Type: application/json", "-d", "{}", "--connect-timeout", "10"],
                capture_output=True, text=True, timeout=15
            ))
            result = _json.loads(r.stdout) if r.stdout else None
        except Exception:
            result = None
    
    if not result:
        raise HTTPException(500, "Wallet generation failed")
    
    address = result.get("chains", {}).get(chain, {}).get("address",
              result.get("address", ""))
    private_key = result.get("chains", {}).get(chain, {}).get("privateKey", result.get("chains", {}).get(chain, {}).get("private_key", result.get("privateKey", result.get("private_key", ""))))
    
    if not address or not private_key:
        raise HTTPException(422, f"Chain '{chain}' not available in generated wallet")
    
    encrypted = encrypt_key_for_agent(private_key, agent)
    
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO trading_wallets (agent_id, label, chain, address, encrypted_private_key) VALUES (?,?,?,?,?)",
            (agent["id"], label, chain, address, encrypted)
        )
        await db.commit()
        wallet_id = cur.lastrowid
    
    response = {
        "id": wallet_id, "label": label, "chain": chain,
        "address": address, "system": system,
        "warning": "Private key encrypted at rest. Store the mnemonic safely.",
        "mnemonic": result.get("mnemonic", ""),
    }
    if "ifascript" in result:
        response.update(result["ifascript"])
    return response


@router.get("/wallets/{wallet_id}/key")
async def reveal_wallet_key(wallet_id: int, agent: dict = Depends(get_agent)):
    """Reveal the decrypted private key. Requires explicit request."""
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT encrypted_private_key FROM trading_wallets WHERE id=? AND agent_id=?",
            (wallet_id, agent["id"]))).fetchone()
        if not row or not row[0]:
            raise HTTPException(404, "Wallet or key not found")
        try:
            key = decrypt_key_for_agent(row[0], agent)
            return {"private_key": key, "warning": "Never share this."}
        except Exception:
            raise HTTPException(500, "Failed to decrypt wallet key")


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


# ── Live Wallet Feed ─────────────────────────────────────────

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
async def list_markets(agent: dict = Depends(get_agent)):
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
    """External signal ingestion — auto-creates orders from high-conviction signals."""
    body = await request.json()
    symbol = body.get("symbol", body.get("pair", "UNKNOWN"))
    direction = body.get("direction", body.get("signal", "NEUTRAL"))
    conviction = float(body.get("conviction", body.get("confidence", 0)))
    chain = body.get("chain", "solana")
    source = body.get("source", "unknown")
    
    direction_upper = direction.upper()
    if conviction > 0.7 and direction_upper in ("BUY", "LONG", "BULLISH"):
        side = "BUY"
    elif conviction > 0.7 and direction_upper in ("SELL", "SHORT", "BEARISH"):
        side = "SELL"
    else:
        side = None
    
    result = {
        "status": "ingested", "symbol": symbol,
        "direction": direction, "conviction": conviction,
        "chain": chain, "source": source,
    }
    
    # Auto-create order for high-conviction signals
    if side:
        async with aiosqlite.connect(DB_PATH) as db:
            wallet = await (await db.execute(
                "SELECT id, address FROM trading_wallets WHERE agent_id=? AND chain=? ORDER BY created_at LIMIT 1",
                (agent["id"], chain)
            )).fetchone()
        
        if wallet:
            quantity = body.get("quantity", 0.1)
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    """INSERT INTO trading_orders (agent_id, wallet_id, order_type, side, symbol, chain, 
                       quantity, trigger_reason, signal_id, status)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (agent["id"], wallet[0], "market", side, symbol, chain, quantity,
                     f"{source}_conviction_{conviction:.1f}", body.get("signal_id"), "pending")
                )
                await db.commit()
                result["order_created"] = cur.lastrowid
                result["action"] = side
                result["wallet_address"] = wallet[1]
        else:
            result["warning"] = f"No {chain} wallet configured. Create one first."
    else:
        result["action"] = "HOLD"
        result["note"] = "Conviction too low for auto-execution"
    
    return result

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


# ══════════════════════════════════════════════════════════════════════════════
# LINKED-WALLET LEDGER — real on-chain holdings, activity, per-trade P&L, equity.
#
# The honest ledger is sourced from the agent's LINKED WALLETS, not manually
# logged orders: trades come from wallet_trades (populated by the wallet_tracker
# daemon), holdings from trading_balances (wallet sync), and net worth/equity is
# the live USD value of those balances. Everything below is agent-scoped by
# joining wallet_trades / trading_balances back to trading_wallets.agent_id.
# ══════════════════════════════════════════════════════════════════════════════

# Schema the wallet_tracker daemon writes; created here too so the endpoints work
# before the daemon has ever run.
_WALLET_TRADES_DDL = """
CREATE TABLE IF NOT EXISTS wallet_trades (
    signature    TEXT PRIMARY KEY,
    wallet       TEXT,
    timestamp    INTEGER,
    ts_iso       TEXT,
    type         TEXT,
    source       TEXT,
    description  TEXT,
    fee_sol      REAL,
    sol_change   REAL,
    token_mint   TEXT,
    token_amount REAL,
    raw          TEXT
)
"""


def _avgcost_from_trades(trades: list) -> tuple:
    """Pure SOL-denominated average-cost engine over on-chain swaps.

    Each trade is a dict with token_mint, token_amount (+recv/-sent) and
    sol_change (+recv/-spent). A token BUY (token_amount>0) costs the SOL it
    spent; a SELL (token_amount<0) realizes P&L against the running average SOL
    cost. Returns (book, annotated_trades) where every annotated trade carries
    its own realized_pnl_sol — so P&L is attributed to each trade, not just the
    aggregate. Kept pure (no DB) so it is unit-testable.
    """
    book: dict = {}
    annotated = []
    for t in trades:
        mint = t.get("token_mint")
        amt = float(t.get("token_amount") or 0)
        sol = float(t.get("sol_change") or 0)
        realized = 0.0
        if mint and abs(amt) > 1e-12:
            b = book.setdefault(mint, {"net_qty": 0.0, "cost_basis_sol": 0.0, "realized_sol": 0.0})
            if amt > 0:  # BUY token with SOL
                qty = amt
                b["net_qty"] += qty
                b["cost_basis_sol"] += max(-sol, 0.0)
            else:        # SELL token for SOL
                qty = -amt
                proceeds = max(sol, 0.0)
                sell_price = proceeds / qty if qty else 0.0
                avg = (b["cost_basis_sol"] / b["net_qty"]) if b["net_qty"] > 1e-12 else 0.0
                sell_qty = min(qty, b["net_qty"]) if b["net_qty"] > 0 else 0.0
                realized = (sell_price - avg) * sell_qty
                b["realized_sol"] += realized
                b["net_qty"] -= qty
                b["cost_basis_sol"] -= avg * sell_qty
                if b["net_qty"] <= 1e-12:
                    b["net_qty"] = max(b["net_qty"], 0.0)
                    b["cost_basis_sol"] = max(b["cost_basis_sol"], 0.0)
        ann = dict(t)
        ann["realized_pnl_sol"] = round(realized, 9)
        ann["running_qty"] = round(book.get(mint, {}).get("net_qty", 0.0), 9) if mint else None
        annotated.append(ann)
    return book, annotated


async def _agent_wallet_trades(db, agent_id: int) -> list:
    """All on-chain trades for the agent's linked wallets, oldest first."""
    await db.execute(_WALLET_TRADES_DDL)
    db.row_factory = aiosqlite.Row
    rows = await (await db.execute(
        """SELECT wt.signature, wt.wallet, wt.timestamp, wt.ts_iso, wt.type,
                  wt.source, wt.description, wt.fee_sol, wt.sol_change,
                  wt.token_mint, wt.token_amount
           FROM wallet_trades wt
           JOIN trading_wallets tw ON tw.address = wt.wallet
           WHERE tw.agent_id = ?
           ORDER BY wt.timestamp ASC, wt.signature ASC""",
        (agent_id,)
    )).fetchall()
    return [dict(r) for r in rows]


async def _agent_networth_usd(db, agent_id: int) -> float:
    """Live USD net worth of the agent's linked wallets, from synced balances."""
    db.row_factory = aiosqlite.Row
    row = await (await db.execute(
        """SELECT COALESCE(SUM(b.value_usd), 0) AS total
           FROM trading_balances b
           JOIN trading_wallets w ON w.id = b.wallet_id
           WHERE w.agent_id = ?""",
        (agent_id,)
    )).fetchone()
    return round(float(row["total"] or 0), 2)


async def _upsert_networth_snapshot(db, agent_id: int) -> dict:
    """Upsert today's equity point from real linked-wallet net worth. Callable
    inside another transaction (does not commit)."""
    value = await _agent_networth_usd(db, agent_id)
    today = date.today().isoformat()
    prev = await (await db.execute(
        "SELECT portfolio_value_usd FROM trading_pnl_snapshots WHERE agent_id=? AND snapshot_date<? ORDER BY snapshot_date DESC LIMIT 1",
        (agent_id, today)
    )).fetchone()
    prev_val = (prev[0] if prev else None)
    daily_pnl = round(value - prev_val, 2) if prev_val else 0.0
    daily_pct = round((value - prev_val) / prev_val * 100, 2) if prev_val else 0.0
    await db.execute(
        """INSERT INTO trading_pnl_snapshots
             (agent_id, snapshot_date, portfolio_value_usd, daily_pnl_usd, daily_pnl_pct, notes)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(agent_id, snapshot_date) DO UPDATE SET
             portfolio_value_usd=excluded.portfolio_value_usd,
             daily_pnl_usd=excluded.daily_pnl_usd,
             daily_pnl_pct=excluded.daily_pnl_pct""",
        (agent_id, today, value, daily_pnl, daily_pct, "auto: linked-wallet net worth")
    )
    return {"snapshot_date": today, "net_worth_usd": value,
            "daily_pnl_usd": daily_pnl, "daily_pnl_pct": daily_pct}


@router.get("/activity")
async def wallet_activity(agent: dict = Depends(get_agent), limit: int = Query(100, le=500)):
    """Real on-chain trades across the agent's linked wallets, newest first, each
    annotated with its own realized P&L (SOL + USD). This IS the honest activity
    ledger — it reflects what the wallets actually did, not logged intent."""
    async with aiosqlite.connect(DB_PATH) as db:
        trades = await _agent_wallet_trades(db, agent["id"])
    book, annotated = _avgcost_from_trades(trades)
    sol_price = await _fetch_quote("SOL") or 0.0
    total_realized_sol = round(sum(b["realized_sol"] for b in book.values()), 9)
    for a in annotated:
        a["realized_pnl_usd"] = round(a["realized_pnl_sol"] * sol_price, 2) if sol_price else None
    annotated.reverse()  # newest first
    return {
        "trades": annotated[:limit],
        "count": len(annotated),
        "realized_pnl_sol": total_realized_sol,
        "realized_pnl_usd": round(total_realized_sol * sol_price, 2) if sol_price else None,
        "sol_price_usd": round(sol_price, 4) if sol_price else None,
    }


@router.get("/holdings")
async def wallet_holdings(agent: dict = Depends(get_agent)):
    """Real current holdings aggregated across the agent's linked wallets (from
    synced balances), plus realized P&L from on-chain activity. This is the
    honest 'positions' view — what the wallets actually hold right now."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT b.token,
                      SUM(b.balance)   AS balance,
                      SUM(b.value_usd) AS value_usd,
                      COUNT(DISTINCT w.id) AS wallet_count
               FROM trading_balances b
               JOIN trading_wallets w ON w.id = b.wallet_id
               WHERE w.agent_id = ?
               GROUP BY b.token
               ORDER BY COALESCE(SUM(b.value_usd), 0) DESC""",
            (agent["id"],)
        )).fetchall()
        holdings = [{
            "token": r["token"],
            "balance": round(float(r["balance"] or 0), 8),
            "value_usd": round(float(r["value_usd"]), 2) if r["value_usd"] is not None else None,
            "wallet_count": r["wallet_count"],
        } for r in rows]
        trades = await _agent_wallet_trades(db, agent["id"])
    book, _ = _avgcost_from_trades(trades)
    sol_price = await _fetch_quote("SOL") or 0.0
    realized_sol = round(sum(b["realized_sol"] for b in book.values()), 9)
    return {
        "holdings": holdings,
        "total_value_usd": round(sum(h["value_usd"] or 0 for h in holdings), 2),
        "realized_pnl_sol": realized_sol,
        "realized_pnl_usd": round(realized_sol * sol_price, 2) if sol_price else None,
        "sol_price_usd": round(sol_price, 4) if sol_price else None,
        "open_tokens": len([b for b in book.values() if b["net_qty"] > 1e-9]),
    }


@router.get("/networth")
async def get_networth(agent: dict = Depends(get_agent)):
    """Live net worth of the agent's linked wallets (sum of synced balance USD)."""
    async with aiosqlite.connect(DB_PATH) as db:
        value = await _agent_networth_usd(db, agent["id"])
    return {"net_worth_usd": value}


@router.post("/networth/snapshot")
async def snapshot_networth(agent: dict = Depends(get_agent)):
    """Record today's equity point from real linked-wallet net worth."""
    async with aiosqlite.connect(DB_PATH) as db:
        result = await _upsert_networth_snapshot(db, agent["id"])
        await db.commit()
    return {"status": "snapshot_saved", **result}
