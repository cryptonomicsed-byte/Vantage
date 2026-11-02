"""trading_commands.py -- slash command handlers for trading collab channels.

Multi-agent pooled trading, individual orders, token launches on pump.fun,
and strategy management -- all scoped to a guild channel.

Commands
--------
  /price   <symbol>
  /buy     <amount> <symbol>
  /sell    <amount> <symbol>
  /pool    <amount> <symbol> [quorum%]
  /join    [amount]
  /launch  <name> <symbol> <description> [image_url]
  /strategy [description]
  /positions
  /pools
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

PINATA_JWT = os.environ.get("PINATA_JWT", "")
PINATA_UPLOAD_URL = "https://uploads.pinata.cloud/v3/files"

# ── helpers ──────────────────────────────────────────────────────────────────


def _ok(reply: str, **data) -> dict:
    return {"reply": reply, "data": data}


def _err(msg: str) -> dict:
    return {"reply": f":x: {msg}", "data": {"error": msg}}


async def _get_default_wallet(db, agent_id: int) -> Optional[dict]:
    """Return the agent's first trading wallet, or None."""
    cur = await db.execute(
        "SELECT * FROM trading_wallets WHERE agent_id=? ORDER BY id ASC LIMIT 1",
        (agent_id,),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def _emit_receipt(agent_id: int, action: str, verb: str, evidence: dict) -> None:
    """Best-effort receipt log — wired to the real append-to-chain function."""
    try:
        from backend.utils import _append_receipt
        payload = {"verb": verb, **evidence}
        await _append_receipt(str(agent_id), action, payload)
        logger.debug(
            "channel_trade_receipt action=%s verb=%s agent=%s",
            action, verb, agent_id,
        )
    except Exception as exc:
        logger.debug("channel receipt emit skipped: %s", exc)


# ── /price ───────────────────────────────────────────────────────────────────


async def _cmd_price(args: str, channel: dict, agent: dict, db) -> dict:
    parts = args.split()
    if not parts:
        return _err("Usage: /price <symbol>")
    symbol = parts[0].upper()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                "http://localhost:8001/api/intel/ticker",
                params={"symbol": symbol},
            )
            if r.status_code == 200:
                data = r.json()
                price = data.get("price") or data.get("last") or data.get("close") or 0
                change = data.get("change_24h") or data.get("price_change_pct_24h") or 0
                volume = data.get("volume_24h") or data.get("volume") or 0
                sign = "+" if float(change or 0) >= 0 else ""
                vol_str = _fmt_volume(float(volume or 0))
                return _ok(
                    f"{symbol}: ${float(price or 0):,.2f} | 24h: {sign}{float(change or 0):.1f}% | Vol: {vol_str}",
                    symbol=symbol,
                    price=price,
                    change_24h=change,
                    volume_24h=volume,
                )
            else:
                return _err(f"Price lookup failed (HTTP {r.status_code})")
    except Exception as exc:
        return _err(f"Price lookup error: {exc}")


def _fmt_volume(v: float) -> str:
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:.2f}"


# ── /buy and /sell ────────────────────────────────────────────────────────────


async def _cmd_trade(side: str, args: str, channel: dict, agent: dict, db) -> dict:
    """Shared handler for /buy and /sell."""
    parts = args.split()
    if len(parts) < 2:
        return _err(f"Usage: /{side} <amount> <symbol>")
    try:
        amount = float(parts[0])
    except ValueError:
        return _err(f"Invalid amount: {parts[0]}")
    symbol = parts[1].upper()

    wallet = await _get_default_wallet(db, agent["id"])
    if not wallet:
        return _err("No wallet found. Create one at /api/trading/wallets first.")

    await db.execute(
        """INSERT INTO trading_orders
             (agent_id, wallet_id, order_type, side, symbol, chain, quantity, price, status)
           VALUES (?, ?, 'market', ?, ?, ?, ?, NULL, 'pending')""",
        (agent["id"], wallet["id"], side, symbol, wallet.get("chain", ""), amount),
    )
    await db.commit()
    cur = await db.execute("SELECT last_insert_rowid() as lid")
    row = await cur.fetchone()
    order_id = row["lid"] if row else "?"

    await _emit_receipt(
        agent["id"],
        action="trade.execute",
        verb=side,
        evidence={"symbol": symbol, "amount": amount, "wallet_id": wallet["id"]},
    )

    emoji = ":green_circle:" if side == "buy" else ":red_circle:"
    return _ok(
        f"{emoji} {side.upper()} order created -- #{order_id} | {amount} {symbol} | market | pending",
        order_id=order_id,
        side=side,
        symbol=symbol,
        amount=amount,
        wallet_id=wallet["id"],
        status="pending",
    )


# ── /pool ─────────────────────────────────────────────────────────────────────


async def _cmd_pool(args: str, channel: dict, agent: dict, db) -> dict:
    parts = args.split()
    if len(parts) < 2:
        return _err("Usage: /pool <amount> <symbol> [quorum%]")
    try:
        target_amount = float(parts[0])
    except ValueError:
        return _err(f"Invalid amount: {parts[0]}")
    symbol = parts[1].upper()
    quorum_pct = 0.5
    if len(parts) >= 3:
        try:
            q = float(parts[2].rstrip("%"))
            quorum_pct = q / 100.0 if q > 1 else q
        except ValueError:
            pass

    wallet = await _get_default_wallet(db, agent["id"])
    if not wallet:
        return _err("No wallet found. Create one at /api/trading/wallets first.")

    await db.execute(
        """INSERT INTO channel_trading_pools
             (channel_id, initiator_id, pool_type, symbol, token, target_amount, quorum_pct, status)
           VALUES (?, ?, 'trade', ?, ?, ?, ?, 'open')""",
        (channel["id"], agent["id"], symbol, symbol, target_amount, quorum_pct),
    )
    await db.commit()
    cur = await db.execute("SELECT last_insert_rowid() as lid")
    row = await cur.fetchone()
    pool_id = row["lid"] if row else "?"

    # Auto-commit initiator's share
    await db.execute(
        """INSERT OR IGNORE INTO channel_pool_commitments (pool_id, agent_id, amount, wallet_id)
           VALUES (?, ?, ?, ?)""",
        (pool_id, agent["id"], target_amount, wallet["id"]),
    )
    await db.commit()

    quorum_display = int(quorum_pct * 100)
    return _ok(
        f"Pool #{pool_id} created for {target_amount} {symbol}. "
        f"Members can /join to commit funds. Pool executes at {quorum_display}% participation.",
        pool_id=pool_id,
        symbol=symbol,
        target_amount=target_amount,
        quorum_pct=quorum_pct,
    )


# ── /join ─────────────────────────────────────────────────────────────────────


async def _cmd_join(args: str, channel: dict, agent: dict, db) -> dict:
    cur = await db.execute(
        "SELECT * FROM channel_trading_pools WHERE channel_id=? AND status='open' ORDER BY id DESC LIMIT 1",
        (channel["id"],),
    )
    pool = await cur.fetchone()
    if not pool:
        return _err("No open pool in this channel. Create one with /pool first.")
    pool = dict(pool)

    wallet = await _get_default_wallet(db, agent["id"])
    if not wallet:
        return _err("No wallet found. Create one at /api/trading/wallets first.")

    parts = args.split()
    if parts:
        try:
            amount = float(parts[0])
        except ValueError:
            return _err(f"Invalid amount: {parts[0]}")
    else:
        amount = pool["target_amount"]

    # Best-effort balance check (balance column may be 0/null when not synced)
    balance = wallet.get("balance") or wallet.get("balance_usd") or 0
    if float(balance) > 0 and float(balance) < amount:
        return _err(
            f"Insufficient balance ({balance:.4f}) for requested amount {amount}. "
            "Deposit funds or specify a smaller amount."
        )

    try:
        await db.execute(
            """INSERT INTO channel_pool_commitments (pool_id, agent_id, amount, wallet_id)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(pool_id, agent_id) DO UPDATE SET
                 amount=excluded.amount, wallet_id=excluded.wallet_id""",
            (pool["id"], agent["id"], amount, wallet["id"]),
        )
        await db.commit()
    except Exception as exc:
        return _err(f"Could not commit to pool: {exc}")

    cur2 = await db.execute(
        "SELECT COALESCE(SUM(amount), 0) as total, COUNT(*) as members "
        "FROM channel_pool_commitments WHERE pool_id=?",
        (pool["id"],),
    )
    agg = dict(await cur2.fetchone())
    committed = float(agg["total"])
    members = int(agg["members"])
    target = float(pool["target_amount"])
    quorum_pct = float(pool["quorum_pct"])
    quorum_threshold = target * quorum_pct

    symbol = pool.get("symbol") or pool.get("token", "")
    status_line = f"Pool: {committed:.4f}/{target:.4f} {symbol} ({members} members)"

    if committed >= quorum_threshold:
        await db.execute(
            """INSERT INTO trading_orders
                 (agent_id, wallet_id, order_type, side, symbol, chain,
                  quantity, price, status, notes)
               VALUES (?, ?, 'market', 'buy', ?, ?, ?, NULL, 'pending', ?)""",
            (
                pool["initiator_id"],
                wallet["id"],
                symbol,
                wallet.get("chain", ""),
                committed,
                json.dumps({"pool_id": pool["id"], "members": members}),
            ),
        )
        await db.execute(
            "UPDATE channel_trading_pools SET status='executing' WHERE id=?",
            (pool["id"],),
        )
        await db.commit()

        await _emit_receipt(
            agent["id"],
            action="pool.execute",
            verb="buy",
            evidence={
                "pool_id": pool["id"],
                "symbol": symbol,
                "committed": committed,
                "members": members,
            },
        )
        return _ok(
            f"QUORUM MET! {status_line} -- executing pool trade for {committed:.4f} {symbol}.",
            pool_id=pool["id"],
            committed=committed,
            members=members,
            status="executing",
        )

    pct_done = int((committed / target) * 100) if target > 0 else 0
    return _ok(
        f"{status_line} | {pct_done}% -- waiting for quorum ({int(quorum_pct * 100)}% needed).",
        pool_id=pool["id"],
        committed=committed,
        members=members,
        status="open",
    )


# ── /launch ───────────────────────────────────────────────────────────────────


async def _cmd_launch(args: str, channel: dict, agent: dict, db) -> dict:
    parts = args.split(None, 3)  # name, symbol, description, [image_url]
    if len(parts) < 3:
        return _err("Usage: /launch <name> <symbol> <description> [image_url]")
    name = parts[0]
    symbol = parts[1].upper()
    description = parts[2]
    image_url = parts[3] if len(parts) >= 4 else ""

    if len(symbol) > 10:
        return _err(f"Symbol '{symbol}' exceeds pump.fun's 10-character limit")

    wallet = await _get_default_wallet(db, agent["id"])
    if not wallet:
        return _err("No wallet found. Create one at /api/trading/wallets first.")

    metadata_json = json.dumps({
        "name": name,
        "symbol": symbol,
        "description": description,
        "image_url": image_url,
    })
    await db.execute(
        """INSERT INTO channel_trading_pools
             (channel_id, initiator_id, pool_type, symbol, token,
              target_amount, quorum_pct, status, committed_json)
           VALUES (?, ?, 'launch', ?, ?, 0.02, 0.5, 'open', ?)""",
        (channel["id"], agent["id"], symbol, name, metadata_json),
    )
    await db.commit()
    cur = await db.execute("SELECT last_insert_rowid() as lid")
    row = await cur.fetchone()
    pool_id = row["lid"] if row else "?"

    # Auto-commit initiator with minimum SOL
    await db.execute(
        """INSERT OR IGNORE INTO channel_pool_commitments (pool_id, agent_id, amount, wallet_id)
           VALUES (?, ?, 0.02, ?)""",
        (pool_id, agent["id"], wallet["id"]),
    )
    await db.commit()

    ipfs_note = ""
    if image_url and not PINATA_JWT:
        ipfs_note = " (Note: PINATA_JWT not set -- image will not be pinned to IPFS at launch time)"
    elif image_url:
        ipfs_note = " (Image URL queued for IPFS pin at launch)"

    return _ok(
        f"Launch pool #{pool_id} open for {name} ({symbol}). "
        f"Target: 0.02 SOL minimum. Members /join to participate.{ipfs_note}",
        pool_id=pool_id,
        name=name,
        symbol=symbol,
        description=description,
        image_url=image_url,
        pool_type="launch",
    )


# ── /strategy ─────────────────────────────────────────────────────────────────


async def _cmd_strategy(args: str, channel: dict, agent: dict, db) -> dict:
    description = args.strip()
    channel_meta = json.dumps({
        "channel_id": channel["id"],
        "channel_slug": channel.get("slug", ""),
    })

    if description:
        wallet = await _get_default_wallet(db, agent["id"])
        wallet_id = wallet["id"] if wallet else None
        ch_slug = channel.get("slug", "channel")
        await db.execute(
            """INSERT INTO trading_strategies
                 (agent_id, name, description, strategy_type, config, wallet_id)
               VALUES (?, ?, ?, 'manual', ?, ?)""",
            (
                agent["id"],
                f"Channel strategy -- {ch_slug}",
                description,
                channel_meta,
                wallet_id,
            ),
        )
        await db.commit()
        cur = await db.execute("SELECT last_insert_rowid() as lid")
        row = await cur.fetchone()
        strat_id = row["lid"] if row else "?"
        return _ok(
            f"Strategy #{strat_id} created: {description}",
            strategy_id=strat_id,
            description=description,
        )
    else:
        ch_id = channel["id"]
        cur = await db.execute(
            """SELECT id, name, description, enabled, created_at FROM trading_strategies
               WHERE agent_id=? AND (config LIKE ? OR config LIKE ?)
               ORDER BY id DESC LIMIT 10""",
            (agent["id"], f'%"channel_id": {ch_id}%', f'%"channel_id":{ch_id}%'),
        )
        rows = await cur.fetchall()
        if not rows:
            return _ok("No strategies for this channel yet. Use /strategy <description> to create one.")
        ch_slug = channel.get("slug", "channel")
        lines = [f"Strategies in #{ch_slug}:"]
        for r in rows:
            r = dict(r)
            status = "on" if r.get("enabled") else "off"
            lines.append(f"  [{status}] #{r['id']} -- {r['description'] or r['name']}")
        return _ok("\n".join(lines), strategies=[dict(r) for r in rows])


# ── /positions ────────────────────────────────────────────────────────────────


async def _cmd_positions(channel: dict, agent: dict, db) -> dict:
    # Collect all agent_ids who have committed to any pool in this channel
    cur = await db.execute(
        "SELECT DISTINCT agent_id FROM channel_pool_commitments WHERE pool_id IN "
        "(SELECT id FROM channel_trading_pools WHERE channel_id=?)",
        (channel["id"],),
    )
    member_rows = await cur.fetchall()
    member_ids = [r[0] for r in member_rows] if member_rows else []
    # Always include the caller
    if agent["id"] not in member_ids:
        member_ids.append(agent["id"])

    placeholders = ",".join("?" * len(member_ids))
    cur2 = await db.execute(
        f"""SELECT o.id, a.name as agent_name, o.symbol, o.side, o.quantity, o.status
            FROM trading_orders o
            JOIN agents a ON a.id = o.agent_id
            WHERE o.agent_id IN ({placeholders})
              AND o.status NOT IN ('cancelled', 'completed', 'filled')
            ORDER BY o.created_at DESC LIMIT 50""",
        member_ids,
    )
    rows = await cur2.fetchall()
    if not rows:
        return _ok("No open positions in this channel.")

    lines = ["Agent | Symbol | Side | Qty | Status"]
    for r in rows:
        r = dict(r)
        lines.append(
            f"  {r['agent_name']} | {r['symbol']} | {r['side']} "
            f"| {r.get('quantity', 0)} | {r['status']}"
        )
    return _ok("\n".join(lines), positions=[dict(r) for r in rows])


# ── /pools ────────────────────────────────────────────────────────────────────


async def _cmd_pools(channel: dict, agent: dict, db) -> dict:
    cur = await db.execute(
        """SELECT p.id, p.pool_type, p.symbol, p.target_amount, p.quorum_pct, p.status,
                  COALESCE(SUM(c.amount), 0) as committed,
                  COUNT(c.id) as members
           FROM channel_trading_pools p
           LEFT JOIN channel_pool_commitments c ON c.pool_id = p.id
           WHERE p.channel_id=? AND p.status IN ('open', 'executing')
           GROUP BY p.id
           ORDER BY p.id DESC""",
        (channel["id"],),
    )
    rows = await cur.fetchall()
    if not rows:
        return _ok("No open pools in this channel. Start one with /pool <amount> <symbol>.")

    lines = ["Pool ID | Type | Target | Committed | Members | Status"]
    for r in rows:
        r = dict(r)
        lines.append(
            f"  #{r['id']} | {r['pool_type']} | {r['target_amount']} {r['symbol']} | "
            f"{r['committed']:.4f} | {r['members']} | {r['status']}"
        )
    return _ok("\n".join(lines), pools=[dict(r) for r in rows])


# ── main dispatch ─────────────────────────────────────────────────────────────


async def handle(cmd: str, args: str, channel: dict, agent: dict, db) -> dict:
    """Entry point called by channel_slash/__init__.py for trading channels."""
    cmd = cmd.lower()

    if cmd == "price":
        return await _cmd_price(args, channel, agent, db)
    elif cmd in ("buy", "sell"):
        return await _cmd_trade(cmd, args, channel, agent, db)
    elif cmd == "pool":
        return await _cmd_pool(args, channel, agent, db)
    elif cmd == "join":
        return await _cmd_join(args, channel, agent, db)
    elif cmd == "launch":
        return await _cmd_launch(args, channel, agent, db)
    elif cmd == "strategy":
        return await _cmd_strategy(args, channel, agent, db)
    elif cmd == "positions":
        return await _cmd_positions(channel, agent, db)
    elif cmd == "pools":
        return await _cmd_pools(channel, agent, db)
    else:
        cmds = "/price, /buy, /sell, /pool, /join, /launch, /strategy, /positions, /pools"
        return _ok(f"Trading channel commands: {cmds}")
