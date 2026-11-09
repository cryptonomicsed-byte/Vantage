"""The trading pipeline as one readable surface.

Every repository in the trading stack was reachable only by SSH, a table of
ports, or somebody's memory. This router answers, in one call, what exists,
what it is for, whether it is alive, and what has actually flowed through it —
plus the two bridges that were missing entirely (attestation to Proof, and
signal conformance against the TradingOS schema).

Auth: agent key, same as the rest of /api/trading. This is operational state,
not public market data.
"""
from __future__ import annotations

import asyncio

import json
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import pipeline, proof_bridge
from ..db import get_db
from ..deps import get_agent

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

SUBSTRATE_GATEWAY_DB = os.environ.get("MYCELIUM_DB", "/opt/ares/mycelium-gateway/mycelium.db")


@router.get("/status")
async def pipeline_status(agent: dict = Depends(get_agent)):
    """Every node: role, flow direction, live probe, and traffic counts."""
    async with get_db() as db:
        db.row_factory = _row_factory()
        by_node = await pipeline.events_by_node(db)
    # Probes are blocking (HTTP/subprocess) — keep them off the event loop.
    import asyncio

    st = await asyncio.to_thread(pipeline.status)
    for node in st["nodes"]:
        node["events"] = by_node.get(node["name"], {})
    st["flow"] = (
        "signal producers -> Vantage /api/intel/signals/ingest -> signal_pool -> "
        "strategy/daemon decision -> /api/trading/orders -> execute-live -> "
        "mycelium trace -> proof attestation -> brier/outcome learner"
    )
    return st


@router.get("/events")
async def pipeline_events(limit: int = 50, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = _row_factory()
        return {"events": await pipeline.recent_events(db, limit=min(limit, 500))}


class AttestRequest(BaseModel):
    limit: int = 25
    dry_run: bool = False


@router.post("/attest")
async def attest(agent: dict = Depends(get_agent), data: AttestRequest = AttestRequest()):
    """Turn confirmed orders into Proof attestations (kind 30009).

    Only orders that actually executed are eligible: `status='confirmed'` with a
    tx hash. Failed and cancelled rows are not work and never earn reputation.
    """
    async with get_db() as db:
        db.row_factory = _row_factory()
        cur = await db.execute(
            """SELECT id, agent_id, symbol, side, chain, quantity, filled_quantity,
                      price, avg_fill_price, tx_hash, created_at, executed_at, settled_at
               FROM trading_orders
               WHERE status = 'confirmed'
               ORDER BY id DESC LIMIT ?""",
            (min(max(data.limit, 1), 200),))
        orders = [dict(r) for r in await cur.fetchall()]

    if not orders:
        return {"attested": 0, "skipped": 0, "failed": 0, "reason": "no confirmed orders"}

    result = proof_bridge.attest_orders(orders, dry_run=data.dry_run)

    if not data.dry_run and result.get("attested"):
        async with get_db() as db:
            await pipeline.record_event(
                db, "Proof", "attest", ref=f"batch:{result['attested']}",
                detail={"orders": result["order_ids"]["attested"][:25],
                        "proof_db": result["proof_db"]})
            await db.commit()
    return result


@router.get("/reputation")
async def reputation(limit: int = 20, agent: dict = Depends(get_agent)):
    """Read reputation back out of Proof — the return leg of the attest loop."""
    return proof_bridge.leaderboard(limit=min(limit, 100))


@router.get("/substrate")
async def substrate(agent: dict = Depends(get_agent)):
    """Substrate health: the gateway's trace store and mycelium-tools' view of it."""
    out = {"gateway_db": SUBSTRATE_GATEWAY_DB}
    try:
        import sqlite3

        con = sqlite3.connect(f"file:{SUBSTRATE_GATEWAY_DB}?mode=ro", uri=True, timeout=2.0)
        try:
            out["traces"] = con.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
            out["findings"] = con.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
            row = con.execute("SELECT ts FROM traces ORDER BY ts DESC LIMIT 1").fetchone()
            out["last_trace_ts"] = row[0] if row else None
            out["kinds"] = {r[0]: r[1] for r in con.execute(
                "SELECT kind, COUNT(*) FROM traces GROUP BY kind ORDER BY 2 DESC LIMIT 8")}
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"

    try:
        import sys

        pkg = "/opt/ares/mycelium-tools/packages/mycelium-substrate"
        if pkg not in sys.path:
            sys.path.insert(0, pkg)
        os.environ.setdefault("MYCELIUM_DB", SUBSTRATE_GATEWAY_DB)
        from mycelium_substrate import storage  # type: ignore

        backend = storage.get_backend()
        out["package"] = {"importable": True, "counts": backend.counts()}
    except Exception as e:  # noqa: BLE001
        out["package"] = {"importable": False, "error": f"{type(e).__name__}: {e}"}
    return out


class SignalPayload(BaseModel):
    symbol: str = ""
    direction: str = ""
    conviction: float = 0.0
    source: str = ""
    mint: str = ""


class RememberRequest(BaseModel):
    order_id: int


@router.post("/remember")
async def remember(data: RememberRequest, agent: dict = Depends(get_agent)):
    """Persist one order's reasoning as a portable memory engram.

    This is the memory leg of the pipeline: the substrate keeps the trace, this
    keeps the *reasoning* — strategy, trigger, execution, outcome — signed and
    addressable, so it survives the process that produced it.
    """
    from .. import minipae_bridge

    async with get_db() as db:
        db.row_factory = _row_factory()
        row = await (await db.execute(
            """SELECT id, agent_id, wallet_id, order_type, side, symbol, chain, quantity,
                      price, status, tx_hash, strategy_id, trigger_reason, notes,
                      created_at, executed_at, settled_at
               FROM trading_orders WHERE id = ?""", (data.order_id,))).fetchone()
        if not row:
            raise HTTPException(404, "Order not found")
        report = await __import__("asyncio").to_thread(
            minipae_bridge.remember_order, dict(row))
        if report.get("ok"):
            await pipeline.record_event(db, "minipae", "remember",
                                        ref=report.get("slug", ""),
                                        detail={"order_id": data.order_id,
                                                "relay": report.get("relay")})
            await db.commit()
    return report


@router.get("/remember/{order_id}")
async def recall(order_id: int, agent: dict = Depends(get_agent)):
    from .. import minipae_bridge

    return minipae_bridge.read(minipae_bridge.slug_for_order(order_id))


@router.post("/validate-signal")
async def validate_signal(data: SignalPayload, agent: dict = Depends(get_agent)):
    """Check a signal against TradingOS's SignalGenome schema.

    TradingOS (vendored at `protocols/tradingos/signal.proto`) is not running and does
    not need to be: what it contributes is the field contract — asset, direction,
    conviction, and a DNA block that no producer here currently fills. Conformance
    is checked, not enforced at ingest, so producers keep working while the gap is
    visible in one place.
    """
    problems, warnings = [], []
    if not data.symbol:
        problems.append("asset.symbol is required (SignalGenome.asset)")
    if data.direction.lower() not in ("long", "short", "buy", "sell", "neutral"):
        problems.append(f"signal_type/direction {data.direction!r} is not a recognised SignalType")
    if not (0.0 <= data.conviction <= 1.0):
        problems.append(f"conviction {data.conviction} is outside SignalGenome's [0,1]")
    if not data.source:
        problems.append("source is required to attribute the genome")

    # Present in the proto, absent from this ecosystem's producers today.
    if not data.mint:
        warnings.append("no mint: SignalGenome.asset.identifier would be empty")
    warnings.append("dna/temporal_embedding/fitness are unset — produced by no daemon yet")

    async with get_db() as db:
        await pipeline.record_event(
            db, "TradingOS", "validate", ref=data.symbol or "?",
            detail={"ok": not problems, "conviction": data.conviction})
        await db.commit()

    return {"conforms": not problems, "problems": problems, "warnings": warnings,
            "schema": "tradingos.v1.SignalGenome", "proto": pipeline.signal_proto_path()}


def _row_factory():
    import aiosqlite

    return aiosqlite.Row


class PromoteRequest(BaseModel):
    """Promote pooled signals into paper orders.

    apply=False is a dry run and the default: the funnel reports what *would*
    trade. apply=True needs an explicit size and a wallet the caller owns, and
    every created order still passes the same risk caps and wallet checks as a
    hand-placed one.
    """
    wallet_id: Optional[int] = None
    quantity: float = 0.0
    min_conviction: float = 0.75
    max_age_min: int = 60
    limit: int = 5
    chain: str = "solana"
    symbols: Optional[list[str]] = None  # restrict to a quotable universe
    allow_short: bool = False  # paper has no position to reduce, so SELL is skipped
    apply: bool = False


_BUY = ("buy", "long", "bullish")
_SELL = ("sell", "short", "bearish")


@router.get("/stale")
async def stale(agent: dict = Depends(get_agent)):
    """Daemons running code older than what is on disk -- restart these."""
    return await asyncio.to_thread(pipeline_mod.stale_processes)


@router.post("/promote")
async def promote_signals(data: PromoteRequest, agent: dict = Depends(get_agent)):
    import asyncio

    from .. import pipeline as pipeline_mod
    from . import trading as trading_router

    candidates = await asyncio.to_thread(
        pipeline_mod.promotable_signals, data.min_conviction, data.max_age_min,
        data.limit, data.symbols,
        # Only calls this run can act on compete for the limit.
        ["buy", "sell"] if data.allow_short else ["buy"])
    plan = [{
        "signal_id": c["id"], "source": c["source"], "symbol": c["symbol"],
        "direction": c["direction"], "conviction": c["conviction"],
        "age_min": c["age_min"], "repeats": c.get("repeats", 1),
        "mint": c.get("mint") or None,
    } for c in candidates]

    if not data.apply:
        return {"applied": False, "candidates": len(plan), "plan": plan,
                "note": "dry run — pass apply=true with wallet_id and quantity to place paper orders"}

    if not data.wallet_id or data.quantity <= 0:
        raise HTTPException(422, "apply=true requires wallet_id and a quantity greater than zero")

    placed, skipped = [], []
    for c in candidates:
        direction = (c.get("direction") or "").lower()
        side = "buy" if direction in _BUY else ("sell" if direction in _SELL else None)
        if side is None:
            skipped.append({"signal_id": c["id"], "reason": f"direction {direction!r} is not directional"})
            continue
        if side == "sell" and not data.allow_short:
            skipped.append({"signal_id": c["id"],
                            "reason": "SELL without a position: paper mode cannot reduce what it "
                                      "does not hold (pass allow_short=true to force a pending sell)"})
            continue
        try:
            order = await trading_router.create_order(
                trading_router.OrderCreate(
                    symbol=c["symbol"], side=side, chain=data.chain,
                    quantity=data.quantity, wallet_id=data.wallet_id,
                    trigger_reason=f"signal:{c['source']}",
                    signal_id=c["id"],  # exact attribution for the funnel
                    # Trade the exact token the producer saw. Without this a
                    # pump.fun token calling itself "BTC" was priced as bitcoin.
                    mint=(c.get("mint") or None),
                ),
                agent,
            )
        except HTTPException as e:
            skipped.append({"signal_id": c["id"], "reason": str(e.detail)[:160]})
            continue

        fill = None
        if side == "buy":
            try:
                fill = await trading_router.paper_fill_order(order["id"], agent)
            except HTTPException as e:
                fill = {"error": str(e.detail)[:160]}
        # Never leave a promoted order pending: a pending order is exactly what
        # the execution engine sweeps, and promotion is a paper action.
        if side == "sell" or (isinstance(fill, dict) and fill.get("error")):
            try:
                await trading_router.update_order(
                    order["id"], trading_router.OrderUpdate(status="cancelled"), agent)
            except Exception:  # noqa: BLE001
                pass

        async with get_db() as db:
            for sid in c.get("group_ids") or [c["id"]]:
                await db.execute(
                    """INSERT OR IGNORE INTO signal_promotions
                       (signal_id, order_id, agent_id, source, symbol, direction, conviction)
                       VALUES (?,?,?,?,?,?,?)""",
                    (sid, order["id"], agent["id"], c["source"], c["symbol"],
                     direction, c["conviction"]))
            await pipeline_mod.record_event(
                db, "pipeline", "promote", ref=f"signal:{c['id']}",
                detail={"order_id": order["id"], "source": c["source"],
                        "symbol": c["symbol"], "side": side},
            )
            await db.commit()
        placed.append({"signal_id": c["id"], "order_id": order["id"], "side": side,
                       "symbol": c["symbol"], "source": c["source"],
                       "filled": bool(fill and fill.get("status") == "filled")})

    return {"applied": True, "candidates": len(plan), "placed": placed, "skipped": skipped,
            "summary": f"{len(placed)} paper order(s) from {len(plan)} candidate signal(s)"}


@router.get("/shadow-rank")
async def shadow_rank_endpoint(agent: dict = Depends(get_agent)):
    """P2-05: the shadow rank -- conviction x source reliability, logged only.

    Returns the current promotable calls re-ranked by shadow_score, plus how
    many rows were logged to shadow_rank_log. This is never fed back into the
    live rank; switching shadow to live is a separate, human decision.
    """
    from .. import shadow_rank
    result = await shadow_rank.compute_and_log()
    return result


@router.get("/learning")
async def learning_loop_status(agent: dict = Depends(get_agent)):
    """Read-only view of the whole learning loop for the Learning panel.

    Never writes. Each section reports its own flag, so a section that is off
    says so instead of looking empty-but-broken.
    """
    import asyncio as _asyncio
    from .. import shadow_rank, signal_ledger, signal_record

    out: dict = {
        "flags": {
            "SIGNAL_RECORD_V1": signal_record.enabled(),
            "SIGNAL_LEDGER_ENABLED": signal_ledger.enabled(),
            "LEDGER_MIRROR_ENABLED": signal_ledger.mirror_enabled(),
            "SHADOW_RANK_ENABLED": shadow_rank.enabled(),
        },
    }
    try:
        recent = await signal_ledger.query(limit=10)
        async with get_db() as db:
            try:
                total = (await (await db.execute("SELECT COUNT(*) FROM signal_ledger")).fetchone())[0]
            except Exception:  # noqa: BLE001 - table absent until the flag is first on
                total = 0
        out["ledger"] = {"total": total, "recent": recent}
    except Exception as e:  # noqa: BLE001
        out["ledger"] = {"total": 0, "recent": [], "error": str(e)}
    try:
        out["hit_rates"] = await _asyncio.to_thread(shadow_rank.fetch_hit_rate_findings)
        out["mycelium_reachable"] = True
    except Exception as e:  # noqa: BLE001
        out["hit_rates"], out["mycelium_reachable"] = [], False
        out["hit_rates_error"] = str(e)
    out["shadow"] = await shadow_rank.recent_log()
    return out


@router.get("/tradingos-challenger")
async def tradingos_challenger_shadow(limit: int = 50):
    """P4-15: TradingOS challengers' verdicts on live promotable calls --
    shadow only, never read by ranking or execution. Read-only."""
    from backend import tradingos_challenger as tc
    return await tc.recent(max(1, min(int(limit), 500)))
