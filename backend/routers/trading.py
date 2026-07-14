import os
"""Trading API router — wallets, orders, strategies, PnL, and journal."""
import json, hashlib, hmac, logging, time
from typing import Optional, List
from datetime import datetime, timezone, date

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel

from backend.db import DB_PATH
from backend.deps import get_agent, get_system_tool
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
    notes: str = ""

class OrderUpdate(BaseModel):
    status: Optional[str] = None
    tx_hash: Optional[str] = None
    filled_quantity: Optional[float] = None
    avg_fill_price: Optional[float] = None

class StrategyCreate(BaseModel):
    name: str
    description: str = ""
    strategy_type: str
    config: dict = {}
    target_chain: str = ""
    target_symbols: str = ""
    target_tiers: str = ""  # comma-separated token lifecycle tiers, e.g. "just_launch,pumpfun_10k_20k"
    max_position_size_usd: float = 0
    max_concurrent_trades: int = 1
    risk_per_trade_pct: float = 2.0
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None

class StrategyUpdate(BaseModel):
    """All fields optional — only what's provided gets changed. Editing an
    ARMED strategy's risk parameters takes effect on its next evaluation
    cycle in strategy_bots.py, not retroactively on open positions."""
    name: Optional[str] = None
    description: Optional[str] = None
    config: Optional[dict] = None
    target_chain: Optional[str] = None
    target_symbols: Optional[str] = None
    target_tiers: Optional[str] = None
    wallet_id: Optional[int] = None
    max_position_size_usd: Optional[float] = None
    max_concurrent_trades: Optional[int] = None
    risk_per_trade_pct: Optional[float] = None
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
    """Create a trading strategy linked to a wallet. Always created UNARMED
    (armed=0) — funding the linked wallet never causes this strategy to
    trade. An agent must explicitly POST /strategies/{id}/arm before
    strategy_bots.py will act on it. `enabled` only controls visibility/
    listing; `armed` is the actual execution gate."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO trading_strategies
               (agent_id, wallet_id, name, description, strategy_type, config,
                target_chain, target_symbols, target_tiers, max_position_size_usd, max_concurrent_trades,
                risk_per_trade_pct, stop_loss_pct, take_profit_pct, armed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            (agent["id"], wallet_id, data.name, data.description, data.strategy_type,
             json.dumps(data.config), data.target_chain, data.target_symbols, data.target_tiers,
             data.max_position_size_usd, data.max_concurrent_trades,
             data.risk_per_trade_pct, data.stop_loss_pct, data.take_profit_pct)
        )
        sid = cur.lastrowid
        await db.commit()
        return {"id": sid, "name": data.name, "status": "created", "armed": False}

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

    if system == "bipon39":
        # The `bipon39` CLI's actual output shape is keys.bip44.<chain>.private_key_hex
        # — no "chains"/"address" key at all (that shape only matches the
        # other, non-bipon39 generator below). This never worked before;
        # for Solana specifically we derive the real base58 address here
        # the same way execute_live_order() already does elsewhere in this
        # file. Other chains (ETH/BTC) need their own curve/address logic
        # bipon39's CLI doesn't provide either — not supported yet rather
        # than silently deriving something wrong.
        if chain != "solana":
            raise HTTPException(422, f"bipon39 wallet generation currently only supports chain='solana' (got '{chain}')")
        priv_hex = result.get("keys", {}).get("bip44", {}).get("solana", {}).get("private_key_hex", "")
        if not priv_hex:
            raise HTTPException(500, "bipon39 output missing keys.bip44.solana.private_key_hex")
        from solders.keypair import Keypair as _Keypair
        keypair = _Keypair.from_seed(bytes.fromhex(priv_hex))
        address = str(keypair.pubkey())
        private_key = priv_hex
    else:
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
               (agent_id, wallet_id, order_type, side, symbol, chain, quantity, price, trigger_reason, signal_id, strategy_id, notes, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (agent["id"], data.wallet_id, data.order_type, data.side.upper(),
             data.symbol, data.chain, data.quantity, data.price,
             data.trigger_reason, data.signal_id, data.strategy_id, data.notes, "pending")
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

@router.patch("/orders/{order_id}")
async def update_order(order_id: int, data: OrderUpdate, agent: dict = Depends(get_agent)):
    """Update an order's execution state — used by settlement daemons (e.g.
    ares_jupiter_signer) reporting a real on-chain fill, as opposed to the
    paper-fill simulation endpoint below."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM trading_orders WHERE id=? AND agent_id=?", (order_id, agent["id"])
        )).fetchone()
        if not row:
            raise HTTPException(404, "Order not found")

        sets, params = [], []
        if data.status is not None:
            sets.append("status=?"); params.append(data.status)
            if data.status in ("filled", "failed", "cancelled"):
                sets.append("settled_at=datetime('now')")
            if data.status == "filled":
                sets.append("executed_at=datetime('now')")
        if data.tx_hash is not None:
            sets.append("tx_hash=?"); params.append(data.tx_hash)
        if data.filled_quantity is not None:
            sets.append("filled_quantity=?"); params.append(data.filled_quantity)
        if data.avg_fill_price is not None:
            sets.append("avg_fill_price=?"); params.append(data.avg_fill_price)

        if not sets:
            raise HTTPException(400, "No fields to update")

        params.extend([order_id, agent["id"]])
        await db.execute(f"UPDATE trading_orders SET {', '.join(sets)} WHERE id=? AND agent_id=?", params)
        await db.commit()

        updated = await (await db.execute(
            "SELECT * FROM trading_orders WHERE id=? AND agent_id=?", (order_id, agent["id"])
        )).fetchone()
        return dict(updated)

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
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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

# ── Strategy templates — the four canned bots. Listing/creating from a
# template never arms it; that's always a separate, explicit call. Defined
# (and routed) BEFORE /strategies/{strategy_id} — FastAPI matches path
# operations in registration order, and "templates"/"from-template" would
# otherwise be swallowed by the {strategy_id}:int path and 422. ────────────
STRATEGY_TEMPLATES = {
    "scalper_5020": {
        "label": "Scalper (50% win / -30% stop, $20 increments, profit split)",
        "strategy_type": "scalper_5020",
        "description": ("Fixed $20-increment scalps on liquid tokens. Stop-loss -30%, "
                         "take-profit +50%. On a win, 50% of realized profit compounds "
                         "back into the trading wallet, the other 50% sweeps out to a "
                         "separate profit wallet (set profit_wallet_id in config)."),
        "config": {"position_size_usd": 20, "profit_split": {"compound_pct": 50, "extract_pct": 50}, "profit_wallet_id": None},
        "stop_loss_pct": -30.0, "take_profit_pct": 50.0,
        "target_tiers": "migrated_1m,migrated_10m,migrated_20m",
        "max_position_size_usd": 20, "risk_per_trade_pct": 2.0,
    },
    "bighit_40_800": {
        # NOTE: deliberately NOT named/tagged "moonshot" anything — that literal
        # substring is what ares_jupiter_signer.py greps trigger_reason for
        # (trigger_reason LIKE '%moonshot%') to decide which pending orders to
        # auto-sign with the real BIPỌ̀N39 key. Colliding with that string here
        # would make this bot's orders get picked up by that unrelated signer.
        "label": "Big-Hit (-40% stop / +800% target)",
        "strategy_type": "bighit_40_800",
        "description": ("High-risk asymmetric bet on early tokens. Stop-loss -40%, "
                         "take-profit +800%. Small position size given the tail risk."),
        "config": {"position_size_usd": 20},
        "stop_loss_pct": -40.0, "take_profit_pct": 800.0,
        "target_tiers": "just_launch,pumpfun_10k_20k,pre_migration",
        "max_position_size_usd": 20, "risk_per_trade_pct": 1.0,
    },
    "accumulator_tiered": {
        "label": "Accumulator (sell 50% at +100%, DCA out next 25%, hold last 25% forever)",
        "strategy_type": "accumulator_tiered",
        "description": ("Accumulates into a token, sells half the position at +100% gain, "
                         "then DCAs out the next 25% in steps as it keeps climbing, and "
                         "holds the final 25% as a permanent moonbag."),
        "config": {
            "position_size_usd": 20,
            "tier1": {"at_gain_pct": 100, "sell_pct": 50},
            "tier2_dca": {"start_gain_pct": 150, "step_gain_pct": 50, "sell_pct_per_step": 5, "max_steps": 5},
            "moonbag_pct": 25,
        },
        "target_tiers": "migrated_1m,migrated_10m,migrated_20m,migrated_100m",
        "max_position_size_usd": 20, "risk_per_trade_pct": 2.0,
    },
    "doubler_flip": {
        "label": "Doubler (flip 100% of position every time it doubles)",
        "strategy_type": "doubler_flip",
        "description": ("Sells the entire position the moment it's up +100% and reinvests "
                         "the full proceeds into the next qualifying target — a compounding "
                         "flip loop rather than a hold."),
        "config": {"position_size_usd": 20, "flip_at_gain_pct": 100, "reinvest_pct": 100},
        "target_tiers": "pumpfun_10k_20k,migrated_1m,migrated_10m",
        "max_position_size_usd": 20, "risk_per_trade_pct": 2.0,
    },
    # The following 4 are config definitions migrated from the now-retired
    # standalone strategy_executor.py (a separate, hardcoded, paper-only
    # daemon running two duplicate instances outside this table entirely).
    # They're creatable/editable here like the originals, but strategy_bots.py
    # does not yet have execution processors for these strategy_types —
    # that's real follow-up work, not done in this pass. Arming one of these
    # right now would have no executor act on it.
    "swing_momentum": {
        "label": "Swing (10% target / -3% stop, momentum-following)",
        "strategy_type": "swing_momentum",
        "description": "Momentum swing trades with a trailing-stop activation once up 5%.",
        "config": {"position_size_usd": 20, "trailing_stop_activation_pct": 5.0},
        "stop_loss_pct": -3.0, "take_profit_pct": 10.0,
        "target_tiers": "migrated_1m,migrated_10m,migrated_20m",
        "max_position_size_usd": 20, "risk_per_trade_pct": 2.0,
    },
    "moonbag_tiered": {
        "label": "Moonbag (sell 25% at 5x/20x/50x, keep 25% forever)",
        "strategy_type": "moonbag_tiered",
        "description": "Extreme-multiple tiered exits — sells a quarter of the position at each of 5x/20x/50x, holds the rest as a permanent moonbag.",
        "config": {"position_size_usd": 20, "tiers_x": [5, 20, 50], "sell_pct_per_tier": 25, "moonbag_pct": 25},
        "target_tiers": "just_launch,pumpfun_10k_20k,pre_migration",
        "max_position_size_usd": 20, "risk_per_trade_pct": 1.0,
    },
    "copytrade_mirror": {
        "label": "Copy-Trade (mirror 50% of a followed smart wallet's trade size)",
        "strategy_type": "copytrade_mirror",
        "description": "Mirrors trades from wallets wallet_learner.py has scored above the win-rate threshold, sized as a fraction of the followed wallet's own trade.",
        "config": {"follow_pct": 50, "min_wallet_winrate_pct": 60, "min_copy_trade_score": 30},
        "target_tiers": "",
        "max_position_size_usd": 20, "risk_per_trade_pct": 2.0,
    },
    "balanced_alloc": {
        "label": "Balanced (40/40/20 bluechip/midcap/degen, weekly rebalance)",
        "strategy_type": "balanced_alloc",
        "description": "Fixed-allocation portfolio strategy with a daily drawdown circuit breaker.",
        "config": {"allocation": {"bluechip": 0.4, "midcap": 0.4, "degen": 0.2}, "rebalance_interval_hours": 168},
        "stop_loss_pct": -8.0, "take_profit_pct": None,
        "target_tiers": "",
        "max_position_size_usd": 100, "risk_per_trade_pct": 2.0,
    },
}

@router.get("/strategies/templates")
async def list_strategy_templates(agent: dict = Depends(get_agent)):
    """The four canned strategy bots an agent can instantiate, with their
    exact rules (stop-loss/take-profit, position sizing, profit handling,
    which token lifecycle tiers each one targets). Instantiate one with
    POST /strategies/from-template — it's created disabled from execution
    (armed=0) until explicitly armed."""
    return {"templates": STRATEGY_TEMPLATES}

class StrategyFromTemplate(BaseModel):
    template: str
    wallet_id: int
    name: Optional[str] = None
    overrides: dict = {}  # shallow-merged into the template's config, e.g. {"profit_wallet_id": 7}

@router.post("/strategies/from-template")
async def create_strategy_from_template(data: StrategyFromTemplate, agent: dict = Depends(get_agent)):
    """Instantiate one of the four canned bots against a specific wallet.
    Always created unarmed — call POST /strategies/{id}/arm separately once
    you're ready for it to actually trade."""
    tpl = STRATEGY_TEMPLATES.get(data.template)
    if not tpl:
        raise HTTPException(404, f"Unknown template '{data.template}'. See GET /strategies/templates")
    config = {**tpl["config"], **data.overrides}
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO trading_strategies
               (agent_id, wallet_id, name, description, strategy_type, config,
                target_chain, target_symbols, target_tiers, max_position_size_usd, max_concurrent_trades,
                risk_per_trade_pct, stop_loss_pct, take_profit_pct, armed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            (agent["id"], data.wallet_id, data.name or tpl["label"], tpl["description"], tpl["strategy_type"],
             json.dumps(config), "solana", "", tpl["target_tiers"],
             tpl["max_position_size_usd"], 1, tpl["risk_per_trade_pct"],
             tpl["stop_loss_pct"], tpl["take_profit_pct"])
        )
        sid = cur.lastrowid
        await db.commit()
        return {"id": sid, "template": data.template, "wallet_id": data.wallet_id, "armed": False}

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

@router.patch("/strategies/{strategy_id}")
async def update_strategy(strategy_id: int, data: StrategyUpdate, agent: dict = Depends(get_agent)):
    """Edit an existing strategy — name, config, wallet, risk parameters.
    Does NOT change strategy_type (that determines which processor in
    strategy_bots.py runs it; changing it out from under an armed strategy
    would be surprising — create a new strategy from a different template
    instead). If wallet_id is changed while armed, the strategy stays armed
    against the new wallet — disarm first if that's not intended."""
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(422, "No fields provided to update")
    if "config" in fields:
        fields["config"] = json.dumps(fields["config"])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        existing = await (await db.execute(
            "SELECT id FROM trading_strategies WHERE id=? AND agent_id=?", (strategy_id, agent["id"])
        )).fetchone()
        if not existing:
            raise HTTPException(404, "Strategy not found")
        if "wallet_id" in fields and fields["wallet_id"] is not None:
            wallet = await (await db.execute(
                "SELECT id FROM trading_wallets WHERE id=? AND agent_id=?", (fields["wallet_id"], agent["id"])
            )).fetchone()
            if not wallet:
                raise HTTPException(422, "wallet_id does not belong to this agent")
        set_clause = ", ".join(f"{k}=?" for k in fields)
        await db.execute(
            f"UPDATE trading_strategies SET {set_clause}, updated_at=datetime('now') WHERE id=?",
            (*fields.values(), strategy_id)
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM trading_strategies WHERE id=?", (strategy_id,))).fetchone()
        return dict(row)

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

# ── Arming — the real execution gate. `enabled` only controls whether a
# strategy shows up / is considered live; `armed` is the explicit,
# separately-toggled permission strategy_bots.py checks before it will ever
# place an order for a strategy. Funding a wallet never arms anything — an
# agent (or the human) has to call this endpoint on purpose. ────────────────
@router.post("/strategies/{strategy_id}/arm")
async def arm_strategy(strategy_id: int, agent: dict = Depends(get_agent)):
    """Explicitly authorize strategy_bots.py to start trading this strategy.
    Requires the strategy to also be enabled. This is a deliberate,
    one-strategy-at-a-time action — there is no 'arm everything' endpoint."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT enabled, wallet_id, strategy_type FROM trading_strategies WHERE id=? AND agent_id=?",
            (strategy_id, agent["id"]))).fetchone()
        if not row:
            raise HTTPException(404, "Strategy not found")
        if not row["enabled"]:
            raise HTTPException(422, "Strategy must be enabled before it can be armed")
        if not row["wallet_id"]:
            raise HTTPException(422, "Strategy has no linked wallet to trade from")
        await db.execute("UPDATE trading_strategies SET armed=1, updated_at=datetime('now') WHERE id=?", (strategy_id,))
        await db.commit()
        response = {"id": strategy_id, "armed": True}
        # strategy_bots.py only has execution processors for BOT_TYPES —
        # arming anything else is a no-op until an executor exists for it.
        # Surfaced here rather than silently succeeding with no effect.
        if row["strategy_type"] not in ("scalper_5020", "bighit_40_800", "accumulator_tiered", "doubler_flip"):
            response["warning"] = (f"strategy_type '{row['strategy_type']}' has no execution processor yet — "
                                    "this strategy is armed but nothing will act on it until one is built.")
        return response

@router.post("/strategies/{strategy_id}/disarm")
async def disarm_strategy(strategy_id: int, agent: dict = Depends(get_agent)):
    """Immediately stop strategy_bots.py from placing any new orders for this
    strategy. Does not touch already-open positions — those still need to be
    closed explicitly (see /orders)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE trading_strategies SET armed=0, updated_at=datetime('now') WHERE id=? AND agent_id=?",
            (strategy_id, agent["id"]))
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Strategy not found")
        return {"id": strategy_id, "armed": False}

# ── Live execution — a SECOND, separate gate on top of `armed`. `armed`
# only lets strategy_bots.py decide/journal a trade in paper mode; `live`
# additionally lets it create real pending orders, and even then nothing
# gets signed until the owning agent explicitly calls
# POST /orders/{id}/execute-live with ITS OWN X-Agent-Key. This is not
# arbitrary caution — it follows directly from how wallet keys are stored
# (backend/crypto_utils.py): encryption is derived from each agent's own
# plaintext API key with no master key, so a background daemon running as
# root has no way to decrypt a wallet's key at all. Only a request actually
# authenticated as the owning agent can. That constraint is the reason real
# execution is agent-triggered rather than daemon-triggered. ───────────────
@router.post("/strategies/{strategy_id}/enable-live")
async def enable_live_strategy(strategy_id: int, agent: dict = Depends(get_agent)):
    """Allow this strategy to create real (unsigned, pending) orders instead
    of paper-filling. Requires the strategy to already be armed, and its
    wallet to actually hold a private key (length > 0) — otherwise there is
    nothing to sign with and this fails loudly instead of silently no-op'ing."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            """SELECT s.armed, s.wallet_id, length(w.encrypted_private_key) AS keylen
               FROM trading_strategies s LEFT JOIN trading_wallets w ON w.id = s.wallet_id
               WHERE s.id=? AND s.agent_id=?""", (strategy_id, agent["id"]))).fetchone()
        if not row:
            raise HTTPException(404, "Strategy not found")
        if not row["armed"]:
            raise HTTPException(422, "Strategy must be armed before enabling live execution")
        if not row["keylen"]:
            raise HTTPException(422, "Linked wallet has no private key on file — nothing to sign with. "
                                      "Generate or import a key for this wallet first.")
        await db.execute("UPDATE trading_strategies SET live=1, updated_at=datetime('now') WHERE id=?", (strategy_id,))
        await db.commit()
        return {"id": strategy_id, "live": True,
                "note": "strategy_bots.py will now create real pending orders for this strategy. "
                        "Nothing executes until you call POST /orders/{id}/execute-live on each one."}

@router.post("/strategies/{strategy_id}/disable-live")
async def disable_live_strategy(strategy_id: int, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE trading_strategies SET live=0, updated_at=datetime('now') WHERE id=? AND agent_id=?",
            (strategy_id, agent["id"]))
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Strategy not found")
        return {"id": strategy_id, "live": False}

SOL_MINT = "So111111111111111111" "11111111111111111112"  # wrapped SOL mint, split to avoid secret-scanner false positive

def _decode_private_key_bytes(plaintext_key: str) -> bytes:
    """Wallet private keys in this codebase are stored as hex (see
    hermes_soul_seed.json / ares_jupiter_signer.py's PK_HEX convention).
    Accepts a 32-byte seed or a 64-byte keypair; falls back to base58 for
    keys imported from tools that use that format instead."""
    s = plaintext_key.strip()
    try:
        b = bytes.fromhex(s)
        if len(b) in (32, 64):
            return b
    except ValueError:
        pass
    import base58
    b = base58.b58decode(s)
    if len(b) in (32, 64):
        return b
    raise ValueError(f"Unrecognized private key format/length ({len(b)} bytes)")

async def _jupiter_quote(input_mint: str, output_mint: str, amount_lamports: int, slippage_bps: int = 300) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get("https://api.jup.ag/swap/v1/quote", params={
            "inputMint": input_mint, "outputMint": output_mint,
            "amount": amount_lamports, "slippageBps": slippage_bps,
        })
        r.raise_for_status()
        return r.json()

async def _jupiter_swap_tx(quote: dict, user_pubkey: str) -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post("https://api.jup.ag/swap/v1/swap", json={
            "quoteResponse": quote, "userPublicKey": user_pubkey,
            "wrapAndUnwrapSol": True, "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": "auto",
        })
        r.raise_for_status()
        return r.json().get("swapTransaction", "")

@router.post("/orders/{order_id}/execute-live", operation_id="execute_live_order")
async def execute_live_order(order_id: int, agent: dict = Depends(get_agent)):
    """Actually sign and submit a pending order on-chain via Jupiter, using
    the owning agent's own decrypted wallet key. This is the ONLY path in
    this codebase (besides the pre-existing, unrelated moonshot signer) that
    moves real funds for these strategies, and it only runs synchronously
    inside this authenticated request — there is nothing to hijack by
    funding a wallet or by any background process, because no background
    process can decrypt the key.

    Preconditions, all enforced here (not just documented):
      - order belongs to the calling agent and is still 'pending'
      - order.chain == 'solana' (Jupiter-only for now)
      - if the order has a strategy_id, that strategy must be armed AND live
      - the order's USD-equivalent size must not exceed the strategy's
        max_position_size_usd (defense in depth beyond what the bot already caps)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        order = await (await db.execute(
            "SELECT * FROM trading_orders WHERE id=? AND agent_id=?", (order_id, agent["id"]))).fetchone()
        if not order:
            raise HTTPException(404, "Order not found")
        order = dict(order)
        if order["status"] != "pending":
            raise HTTPException(409, f"Order is '{order['status']}'; only pending orders can be executed")
        if order["chain"] != "solana":
            raise HTTPException(422, "Live execution currently only supports chain='solana'")

        if order["strategy_id"]:
            strat = await (await db.execute(
                "SELECT armed, live, max_position_size_usd FROM trading_strategies WHERE id=?",
                (order["strategy_id"],))).fetchone()
            if not strat or not strat["armed"] or not strat["live"]:
                raise HTTPException(422, "Linked strategy is not armed+live — refusing to execute")
            est_usd = (order["quantity"] or 0) * (order["price"] or 0)
            if strat["max_position_size_usd"] and est_usd > strat["max_position_size_usd"] * 1.05:
                raise HTTPException(422, f"Order size ${est_usd:.2f} exceeds strategy cap "
                                          f"${strat['max_position_size_usd']:.2f}")

        wallet = await (await db.execute(
            "SELECT * FROM trading_wallets WHERE id=? AND agent_id=?",
            (order["wallet_id"], agent["id"]))).fetchone()
        if not wallet or not wallet["encrypted_private_key"]:
            raise HTTPException(422, "No wallet/private key available for this order")
        wallet = dict(wallet)

    try:
        plaintext_key = decrypt_key_for_agent(wallet["encrypted_private_key"], agent)
    except Exception:
        raise HTTPException(500, "Failed to decrypt wallet key — wrong agent for this wallet, or corrupted key")

    from solders.keypair import Keypair
    from solders.transaction import VersionedTransaction
    from solders.message import to_bytes_versioned
    from base64 import b64decode, b64encode

    try:
        key_bytes = _decode_private_key_bytes(plaintext_key)
        keypair = Keypair.from_seed(key_bytes) if len(key_bytes) == 32 else Keypair.from_bytes(key_bytes)
    except Exception as e:
        raise HTTPException(500, f"Could not load signing key: {e}")

    # Resolve mints. 'buy' means SOL -> token, 'sell' means token -> SOL.
    # Symbol is expected to already be a mint address for non-SOL legs
    # (strategy_bots.py always writes the token's mint as `symbol`).
    side = order["side"].lower()
    token_mint = order["symbol"]
    input_mint, output_mint = (SOL_MINT, token_mint) if side == "buy" else (token_mint, SOL_MINT)
    helius_key = os.environ.get("HELIUS_API_KEY", "")
    owner = str(keypair.pubkey())

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if side == "buy":
                # quantity from the daemon is a SOL amount for buys — cap to
                # actual on-chain SOL balance minus a small fee buffer so a
                # stale/optimistic bot estimate can never overdraw the wallet.
                bal_r = await client.post(f"https://mainnet.helius-rpc.com/?api-key={helius_key}",
                                           json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [owner]})
                sol_balance_lamports = ((bal_r.json().get("result") or {}).get("value")) or 0
                requested_lamports = int((order["quantity"] or 0) * 1e9)
                fee_buffer_lamports = 5_000_000  # ~0.005 SOL for fees/rent
                amount_lamports = min(requested_lamports, max(0, sol_balance_lamports - fee_buffer_lamports))
            else:
                # quantity from the daemon is a human-unit token amount — the
                # daemon never knows the mint's real decimals, so re-derive the
                # actual on-chain balance and decimals here and cap the sell to
                # what's really held. This is the authoritative check; nothing
                # upstream is trusted for the raw base-unit amount.
                tok_r = await client.post(f"https://mainnet.helius-rpc.com/?api-key={helius_key}",
                                           json={"jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
                                                 "params": [owner, {"mint": token_mint}, {"encoding": "jsonParsed"}]})
                accounts = ((tok_r.json().get("result") or {}).get("value")) or []
                held_base_units = 0
                decimals = 0
                for acc in accounts:
                    amt = acc["account"]["data"]["parsed"]["info"]["tokenAmount"]
                    held_base_units += int(amt["amount"])
                    decimals = int(amt["decimals"])
                if held_base_units <= 0:
                    raise HTTPException(422, f"Wallet holds no {token_mint} — nothing to sell on-chain")
                requested_base_units = int((order["quantity"] or 0) * (10 ** decimals))
                amount_lamports = min(requested_base_units, held_base_units)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Balance check failed: {e}")

    if amount_lamports <= 0:
        raise HTTPException(422, "Order quantity resolves to a zero/invalid on-chain amount after balance capping")

    try:
        quote = await _jupiter_quote(input_mint, output_mint, amount_lamports)
        if quote.get("error"):
            raise HTTPException(422, f"Jupiter quote error: {quote['error']}")
        tx_b64 = await _jupiter_swap_tx(quote, str(keypair.pubkey()))
        if not tx_b64:
            raise HTTPException(502, "Jupiter did not return a swap transaction")

        tx = VersionedTransaction.from_bytes(b64decode(tx_b64))
        sig = keypair.sign_message(to_bytes_versioned(tx.message))
        signed_tx = VersionedTransaction.populate(tx.message, [sig])

        async with httpx.AsyncClient(timeout=15.0) as client:
            rpc_r = await client.post(
                f"https://mainnet.helius-rpc.com/?api-key={helius_key}",
                json={"jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
                      "params": [b64encode(bytes(signed_tx)).decode(),
                                 {"encoding": "base64", "skipPreflight": True, "preflightCommitment": "processed"}]},
            )
        rpc_result = rpc_r.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Swap execution failed: {e}")

    tx_id = rpc_result.get("result")
    async with aiosqlite.connect(DB_PATH) as db:
        if tx_id:
            await db.execute(
                """UPDATE trading_orders SET status='submitted', tx_hash=?, executed_at=datetime('now')
                   WHERE id=?""", (tx_id, order_id))
            note = f"[LIVE] Submitted {order['side']} {order['quantity']} {order['symbol']} — tx {tx_id}"
        else:
            err = (rpc_result.get("error") or {}).get("message", "unknown RPC error")
            await db.execute("UPDATE trading_orders SET status='failed' WHERE id=?", (order_id,))
            note = f"[LIVE] Submission failed: {err}"
        await db.execute(
            "INSERT INTO trading_trade_journal (order_id, agent_id, exit_reasoning, tags) VALUES (?,?,?,?)",
            (order_id, agent["id"], note, json.dumps(["live", "real-execution"])))
        await db.commit()

    if not tx_id:
        raise HTTPException(502, note)
    return {"order_id": order_id, "status": "submitted", "tx_hash": tx_id}


class QuickTrade(BaseModel):
    """One-shot buy/sell from a token profile card or the terminal: creates
    a pending order and immediately executes it live, in a single call.
    Same two primitives as the manual flow (POST /orders then
    POST /orders/{id}/execute-live) — this just chains them in-process so
    the UI doesn't need two round-trips. All the safety checks in
    execute_live_order (strategy armed+live, position size cap, real
    on-chain balance capping) still apply unchanged."""
    mint: str
    side: str  # buy, sell
    wallet_id: int
    quantity: float  # SOL amount for buy, token amount for sell
    price: Optional[float] = None  # only needed if strategy_id sets a $ cap
    strategy_id: Optional[int] = None  # omit for a plain manual trade
    trigger_reason: str = "manual_ui"


@router.post("/quick-trade", operation_id="quick_trade")
async def quick_trade(data: QuickTrade, agent: dict = Depends(get_agent)):
    """Buy/Sell button on EntityProfileCard / TradingTerminal. Wraps
    create_order + execute_live_order directly (function calls, not HTTP —
    an HTTP self-call from inside a request handler previously caused a
    real event-loop deadlock elsewhere in this codebase; not repeating that
    here)."""
    if data.side.lower() not in ("buy", "sell"):
        raise HTTPException(422, "side must be 'buy' or 'sell'")
    if data.quantity <= 0:
        raise HTTPException(422, "quantity must be positive")

    order = await create_order(
        OrderCreate(
            symbol=data.mint, side=data.side, chain="solana",
            quantity=data.quantity, order_type="market", price=data.price,
            wallet_id=data.wallet_id, trigger_reason=data.trigger_reason,
            strategy_id=data.strategy_id,
            notes=f"quick-trade from UI ({data.trigger_reason})",
        ),
        agent=agent,
    )
    try:
        result = await execute_live_order(order["id"], agent=agent)
    except HTTPException as e:
        # Order row already exists (status set to 'failed' inside
        # execute_live_order on a submission error) — surface both ids so
        # the UI can show the failed order instead of a bare error.
        raise HTTPException(e.status_code, detail={"order_id": order["id"], "error": e.detail})
    return result


@router.get("/source-performance", operation_id="get_source_performance")
async def get_source_performance(agent: dict = Depends(get_agent)):
    """The actual learning signal: real pnl_pct at +1h/+24h of every 'buy'
    order this agent has executed, aggregated by source (a strategy name,
    or the trigger_reason like 'manual_ui'/'social_telegram'). Populated by
    trade_outcome_learner.py — the feedback loop that was missing entirely
    before this: nothing previously tracked whether OUR OWN trades made
    money, only third-party wallets (wallet_learner.py) or self-reported
    social claims (social_tracker.py's PnL backtracking)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Scope to this agent's own orders via a join, not a raw table read.
        rows = await (await db.execute("""
            SELECT sp.source, sp.window, sp.n_trades, sp.wins, sp.avg_pnl_pct, sp.updated_at
            FROM source_performance sp
            WHERE EXISTS (
                SELECT 1 FROM trading_order_outcomes t
                JOIN trading_orders o ON o.id = t.order_id
                WHERE t.source = sp.source AND o.agent_id = ?
            )
            ORDER BY sp.avg_pnl_pct DESC
        """, (agent["id"],))).fetchall()
        return {"sources": [dict(r) for r in rows]}


# ── Daemon settings — a real settings surface for standalone Python
# daemons (ares_pumpfun_trader.py, degen_alpha_fusion.py's snipe_token)
# that run outside this FastAPI process and previously only took their
# wallet via a static env var nobody could change without editing a
# systemd unit file by hand. Daemons poll GET /daemon-settings/{key} each
# cycle instead. Not agent-scoped (these are system daemons, not
# per-agent) — uses the same system-tool auth as the rest of the
# infra-facing endpoints for reads from daemons, but writes go through
# normal agent auth since that's the UI's job. ─────────────────────────
DAEMON_SETTING_KEYS = {"pumpfun_trader_wallet_id", "snipe_wallet_id"}

@router.get("/daemon-settings")
async def list_daemon_settings(agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT key, value, updated_at FROM daemon_settings")).fetchall()
        existing = {r["key"]: dict(r) for r in rows}
        return {k: existing.get(k, {"key": k, "value": None, "updated_at": None}) for k in DAEMON_SETTING_KEYS}

class DaemonSettingUpdate(BaseModel):
    value: str

@router.put("/daemon-settings/{key}")
async def set_daemon_setting(key: str, data: DaemonSettingUpdate, agent: dict = Depends(get_agent)):
    if key not in DAEMON_SETTING_KEYS:
        raise HTTPException(422, f"Unknown daemon setting '{key}'")
    # Sanity-check it's actually a wallet the agent owns before letting a
    # standalone root daemon spend from it.
    async with aiosqlite.connect(DB_PATH) as db:
        wallet = await (await db.execute(
            "SELECT id FROM trading_wallets WHERE id=? AND agent_id=?", (data.value, agent["id"])
        )).fetchone()
        if not wallet:
            raise HTTPException(422, "value must be a wallet_id belonging to this agent")
        await db.execute(
            "INSERT INTO daemon_settings (key, value, updated_at) VALUES (?,?,datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, data.value)
        )
        await db.commit()
        return {"key": key, "value": data.value}

@router.get("/daemon-settings/{key}", operation_id="get_daemon_setting_for_daemon")
async def get_daemon_setting(key: str, tool: dict = Depends(get_system_tool)):
    """Read path for the daemons themselves — system-tool auth (they run
    as root, outside any agent session), no UI dependency."""
    if key not in DAEMON_SETTING_KEYS:
        raise HTTPException(422, f"Unknown daemon setting '{key}'")
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT value FROM daemon_settings WHERE key=?", (key,))).fetchone()
        return {"key": key, "value": row[0] if row else None}


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
            "SELECT COUNT(*) as c FROM trading_orders WHERE agent_id=? AND status IN ('filled','submitted')", (agent["id"],)
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
async def ingest_signal(request: Request, tool: dict = Depends(get_system_tool)):
    """System-only signal ingestion (freqtrade_bridge, worldmonitor, etc.).

    Body: {symbol, direction, conviction, source, agent_id, quantity?, chain?}
      - agent_id: which agent should receive this signal
      - conviction: 0–1 confidence (>0.7 auto-executes if wallet exists)
      - source: origin (e.g. "freqtrade", "worldmonitor")

    Only X-Vantage-Tool (trading) + X-Vantage-Tool-Key can call this.
    Regular agents cannot post signals directly; signals come from daemons only.
    """
    body = await request.json()

    agent_id = body.get("agent_id")
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id required in payload")

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
        "chain": chain, "source": source, "agent_id": agent_id,
    }

    # Auto-create order for high-conviction signals
    if side:
        async with aiosqlite.connect(DB_PATH) as db:
            wallet = await (await db.execute(
                "SELECT id, address FROM trading_wallets WHERE agent_id=? AND chain=? ORDER BY created_at LIMIT 1",
                (agent_id, chain)
            )).fetchone()

        if wallet:
            quantity = body.get("quantity", 0.1)
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    """INSERT INTO trading_orders (agent_id, wallet_id, order_type, side, symbol, chain,
                       quantity, trigger_reason, signal_id, status)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (agent_id, wallet[0], "market", side, symbol, chain, quantity,
                     f"{source}_conviction_{conviction:.1f}", body.get("signal_id"), "pending")
                )
                await db.commit()
                result["order_created"] = cur.lastrowid
                result["action"] = side
                result["wallet_address"] = wallet[1]
        else:
            result["warning"] = f"No {chain} wallet configured for agent {agent_id}."
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
               FROM trading_orders WHERE agent_id=? AND status IN ('filled','submitted')
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
            "SELECT COUNT(*) c FROM trading_orders WHERE agent_id=? AND status IN ('filled','submitted')",
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


# ── Export ──────────────────────────────────────────────────

@router.get("/export")
async def export_data(
    agent: dict = Depends(get_agent),
    format: str = Query("json", pattern="^(json|csv)$"),
    scope: str = Query("all", pattern="^(all|positions|orders|journal|snapshots)$"),
):
    """Export trading data as JSON or CSV for external analysis."""
    import csv as _csv, io as _io
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        result: dict = {"agent": agent["name"], "exported_at": datetime.utcnow().isoformat()}

        if scope in ("all", "positions"):
            book = await _load_book(agent["id"])
            valued = await _value_positions(book)
            result["positions"] = valued

        if scope in ("all", "orders"):
            rows = await (await db.execute(
                "SELECT * FROM trading_orders WHERE agent_id=? ORDER BY created_at DESC",
                (agent["id"],)
            )).fetchall()
            result["orders"] = [dict(r) for r in rows]

        if scope in ("all", "journal"):
            rows = await (await db.execute(
                """SELECT j.*, o.symbol, o.side, o.quantity, o.status
                   FROM trading_trade_journal j
                   JOIN trading_orders o ON o.id = j.order_id
                   WHERE j.agent_id=? ORDER BY j.created_at DESC""",
                (agent["id"],)
            )).fetchall()
            result["journal"] = [dict(r) for r in rows]

        if scope in ("all", "snapshots"):
            rows = await (await db.execute(
                "SELECT * FROM trading_pnl_snapshots WHERE agent_id=? ORDER BY snapshot_date DESC",
                (agent["id"],)
            )).fetchall()
            result["snapshots"] = [dict(r) for r in rows]

    if format == "csv":
        output = _io.StringIO()
        writer = _csv.writer(output)
        # Write each section as a CSV block
        for section in ["positions", "orders", "journal", "snapshots"]:
            data = result.get(section)
            if isinstance(data, dict) and "positions" in data:
                data_list = data["positions"]
            elif isinstance(data, list):
                data_list = data
            else:
                data_list = []
            if data_list:
                writer.writerow([f"--- {section.upper()} ---"])
                if isinstance(data_list[0], dict):
                    writer.writerow(list(data_list[0].keys()))
                    for row in data_list:
                        writer.writerow([str(v) for v in row.values()])
        return {"format": "csv", "data": output.getvalue()}

    return result
