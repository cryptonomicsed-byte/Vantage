"""Copy trading -- ported from HKUDS/AI-Trader's follow/subscribe model.
An agent follows another agent's published orders ("Operations") and can
explicitly one-click-copy any of them into their own order. Deliberately
NOT an auto-mirror: copying is always an explicit action the follower (or
the human acting through a scoped grant) takes, same sovereignty principle
as the rest of Vantage -- no agent's funds move without that agent's own
order being explicitly created."""
import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import get_db
from ..deps import get_agent, _parse_body

router = APIRouter(prefix="/api/copytrade", tags=["trading"])


async def _resolve_agent_id(db, name_or_id) -> int | None:
    if isinstance(name_or_id, int) or (isinstance(name_or_id, str) and name_or_id.isdigit()):
        row = await (await db.execute("SELECT id FROM agents WHERE id=?", (int(name_or_id),))).fetchone()
    else:
        row = await (await db.execute("SELECT id FROM agents WHERE name=?", (name_or_id,))).fetchone()
    return row[0] if row else None


@router.post("/subscribe")
async def subscribe(request: Request, agent: dict = Depends(get_agent)):
    """Body: {leader: agent name or id}. Follow another agent's Operations feed."""
    body = await _parse_body(request)
    leader = body.get("leader")
    if not leader:
        raise HTTPException(422, "leader (agent name or id) is required")

    async with get_db() as db:
        leader_id = await _resolve_agent_id(db, leader)
        if not leader_id:
            raise HTTPException(404, "Leader agent not found")
        if leader_id == agent["id"]:
            raise HTTPException(422, "Cannot follow yourself")
        try:
            await db.execute(
                "INSERT INTO copy_trading_subscriptions (follower_agent_id, leader_agent_id) VALUES (?, ?)",
                (agent["id"], leader_id),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            await db.execute(
                "UPDATE copy_trading_subscriptions SET status='active' WHERE follower_agent_id=? AND leader_agent_id=?",
                (agent["id"], leader_id),
            )
            await db.commit()

    return {"status": "subscribed", "leader_agent_id": leader_id}


@router.delete("/subscribe/{leader_id}")
async def unsubscribe(leader_id: int, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        await db.execute(
            "UPDATE copy_trading_subscriptions SET status='paused' WHERE follower_agent_id=? AND leader_agent_id=?",
            (agent["id"], leader_id),
        )
        await db.commit()
    return {"status": "unsubscribed", "leader_agent_id": leader_id}


@router.get("/leaders")
async def my_leaders(agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("""
            SELECT a.id AS agent_id, a.name, a.bio, a.reputation, s.created_at AS followed_since
            FROM copy_trading_subscriptions s JOIN agents a ON a.id = s.leader_agent_id
            WHERE s.follower_agent_id=? AND s.status='active'
            ORDER BY s.created_at DESC
        """, (agent["id"],))).fetchall()
    return [dict(r) for r in rows]


@router.get("/feed")
async def feed(agent: dict = Depends(get_agent), limit: int = 50):
    """Recent orders ("Operations") from agents this agent follows, most
    recent first -- excludes anything this agent has already copied."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("""
            SELECT o.id AS order_id, o.agent_id AS leader_agent_id, a.name AS leader_name,
                   o.side, o.symbol, o.chain, o.quantity, o.price, o.avg_fill_price,
                   o.status, o.created_at, o.notes
            FROM trading_orders o
            JOIN copy_trading_subscriptions s ON s.leader_agent_id = o.agent_id
            JOIN agents a ON a.id = o.agent_id
            WHERE s.follower_agent_id=? AND s.status='active'
              AND o.status IN ('filled', 'submitted')
              AND o.id NOT IN (
                  SELECT copied_from_order_id FROM trading_orders
                  WHERE agent_id=? AND copied_from_order_id IS NOT NULL
              )
            ORDER BY o.created_at DESC
            LIMIT ?
        """, (agent["id"], agent["id"], min(limit, 200)))).fetchall()
    return [dict(r) for r in rows]


@router.post("/copy/{order_id}")
async def copy_order(order_id: int, request: Request, agent: dict = Depends(get_agent)):
    """One-click copy: creates a new PENDING order for the calling agent,
    mirroring a leader's order's symbol/side/chain. Quantity defaults to the
    leader's own quantity but can be overridden in the body -- the follower
    still must call the existing execute-live endpoint themselves, same
    two-step create-then-execute flow every other order already uses (no
    new execution path, no bypass of the existing safety checks)."""
    body = await _parse_body(request)

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        leader_order = await (await db.execute(
            "SELECT * FROM trading_orders WHERE id=?", (order_id,)
        )).fetchone()
        if not leader_order:
            raise HTTPException(404, "Order not found")

        is_following = await (await db.execute(
            "SELECT 1 FROM copy_trading_subscriptions WHERE follower_agent_id=? AND leader_agent_id=? AND status='active'",
            (agent["id"], leader_order["agent_id"]),
        )).fetchone()
        if not is_following:
            raise HTTPException(403, "You must follow this agent before copying their orders")

        quantity = body.get("quantity", leader_order["quantity"])
        wallet_id = body.get("wallet_id")

        cur = await db.execute(
            """INSERT INTO trading_orders
               (agent_id, wallet_id, order_type, side, symbol, chain, quantity, notes,
                copied_from_order_id, copied_from_agent_id, status)
               VALUES (?, ?, 'market', ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (agent["id"], wallet_id, leader_order["side"], leader_order["symbol"], leader_order["chain"],
             quantity, f"Copied from {leader_order['agent_id']}'s order #{order_id}",
             order_id, leader_order["agent_id"]),
        )
        new_order_id = cur.lastrowid
        await db.commit()

    return {"id": new_order_id, "status": "pending", "copied_from_order_id": order_id,
            "symbol": leader_order["symbol"], "side": leader_order["side"], "quantity": quantity}
