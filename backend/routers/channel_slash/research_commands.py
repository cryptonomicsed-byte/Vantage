"""research_commands.py -- slash command handlers for research collab channels.

All intel data is fetched from the local Vantage intel API (port 8001).
Commands are read-only intel queries + watchlist management.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_BASE = "http://localhost:8001"

# ── helpers ──────────────────────────────────────────────────────────────────

def _ok(reply: str, **data) -> dict:
    return {"reply": reply, "data": data}

def _err(msg: str) -> dict:
    return {"reply": f":x: {msg}", "data": {"error": msg}}


def _agent_headers(agent: dict) -> dict:
    return {
        "Authorization": f"Bearer {agent.get('api_key', '')}",
        "X-Agent-Id": str(agent.get("id", "")),
    }


async def _get_intel(path: str, agent: dict, params: dict | None = None) -> tuple[int, dict | list]:
    """GET from intel API.  Returns (status_code, parsed_json_or_error_dict)."""
    try:
        async with httpx.AsyncClient(timeout=15) as cl:
            r = await cl.get(f"{_BASE}{path}", params=params or {}, headers=_agent_headers(agent))
            if r.status_code == 200:
                return 200, r.json()
            return r.status_code, {"error": r.text[:300]}
    except Exception as e:
        logger.warning("_get_intel %s error: %s", path, e)
        return 0, {"error": str(e)}


def _trunc(s, n=40) -> str:
    s = str(s or "")
    return s[:n] + "..." if len(s) > n else s


# ── command handlers ──────────────────────────────────────────────────────────

async def _cmd_alpha(args: str, channel: dict, agent: dict, db) -> dict:
    """GET /api/intel/alpha — top 5 alpha signals."""
    status, data = await _get_intel("/api/intel/alpha", agent)
    if status != 200:
        return _err(f"Alpha API returned {status}: {data.get('error', '')}")

    items = data if isinstance(data, list) else data.get("items") or data.get("alpha") or []
    if not items:
        return _ok("No alpha signals available right now.", items=[])

    top5 = items[:5]
    lines = [
        "**Top Alpha Signals**",
        "| Symbol | Score | Signal | Source |",
        "|---|---|---|---|",
    ]
    for item in top5:
        sym    = _trunc(item.get("symbol") or item.get("token") or item.get("ticker") or "?", 12)
        score  = item.get("score") or item.get("alpha_score") or item.get("composite_score") or "?"
        signal = _trunc(item.get("signal") or item.get("type") or item.get("direction") or "?", 20)
        source = _trunc(item.get("source") or item.get("platform") or "?", 16)
        lines.append(f"| {sym} | {score} | {signal} | {source} |")

    return _ok("\n".join(lines), items=top5)


async def _cmd_signals(args: str, channel: dict, agent: dict, db) -> dict:
    """GET /api/intel/signals?limit=10 — recent intel signals."""
    status, data = await _get_intel("/api/intel/signals", agent, {"limit": "10"})
    if status != 200:
        return _err(f"Signals API returned {status}: {data.get('error', '')}")

    items = data if isinstance(data, list) else data.get("signals") or data.get("items") or []
    if not items:
        return _ok("No signals in the pool right now.", signals=[])

    lines = [
        "**Intel Signals (last 10)**",
        "| Source | Type | Confidence | Timestamp |",
        "|---|---|---|---|",
    ]
    for sig in items[:10]:
        source = _trunc(sig.get("source") or sig.get("platform") or "?", 16)
        stype  = _trunc(sig.get("signal_type") or sig.get("type") or sig.get("direction") or "?", 18)
        conf   = sig.get("confidence") or sig.get("conviction") or "?"
        ts     = _trunc(sig.get("timestamp") or sig.get("created_at") or sig.get("ts_iso") or "?", 20)
        lines.append(f"| {source} | {stype} | {conf} | {ts} |")

    return _ok("\n".join(lines), signals=items[:10])


async def _cmd_scan(args: str, channel: dict, agent: dict, db) -> dict:
    """Combined rug check + bonding curve scan for a token."""
    token = args.strip()
    if not token:
        return _err("Usage: /scan <token_address_or_symbol>")

    risk_status, risk_data   = await _get_intel(f"/api/intel/pumpfun/risk/{token}", agent)
    curve_status, curve_data = await _get_intel("/api/intel/pumpfun/bonding-curve", agent, {"mint": token})

    lines = [f"**Token Scan: `{token}`**", ""]

    # Rug check section.
    if risk_status == 200:
        risk_score = risk_data.get("risk_score") or risk_data.get("score") or "?"
        verdict    = risk_data.get("verdict") or risk_data.get("result") or risk_data.get("label") or "?"
        flags      = risk_data.get("flags") or risk_data.get("risk_flags") or []
        lines += [
            "**Rug Check**",
            f"- Risk score: `{risk_score}`",
            f"- Verdict: `{verdict}`",
        ]
        if flags:
            flag_str = ", ".join(str(f) for f in flags[:5])
            lines.append(f"- Flags: {flag_str}")
    else:
        lines.append(f"Rug check unavailable ({risk_status})")

    lines.append("")

    # Bonding curve section.
    if curve_status == 200:
        progress  = curve_data.get("progress") or curve_data.get("bonding_progress") or "?"
        sol_pool  = curve_data.get("sol_in_pool") or curve_data.get("sol") or "?"
        graduated = curve_data.get("graduated") or curve_data.get("is_graduated") or False
        lines += [
            "**Bonding Curve**",
            f"- Progress: `{progress}%`",
            f"- SOL in pool: `{sol_pool}`",
            f"- Graduated: `{graduated}`",
        ]
    else:
        lines.append(f"Bonding curve unavailable ({curve_status})")

    return _ok(
        "\n".join(lines),
        token=token,
        risk=risk_data if risk_status == 200 else {},
        curve=curve_data if curve_status == 200 else {},
    )


async def _cmd_wallet(args: str, channel: dict, agent: dict, db) -> dict:
    """GET /api/intel/wallet-holdings/solana/{address}."""
    address = args.strip()
    if not address:
        return _err("Usage: /wallet <solana_address>")

    status, data = await _get_intel(f"/api/intel/wallet-holdings/solana/{address}", agent)
    if status != 200:
        return _err(f"Wallet API returned {status}: {data.get('error', '')}")

    holdings = (
        data.get("holdings")
        or data.get("tokens")
        or (data if isinstance(data, list) else [])
    )
    if not holdings:
        return _ok(f"No holdings found for `{address}`.", address=address, holdings=[])

    lines = [
        f"**Wallet Holdings: `{address[:12]}...`**",
        "| Token | Amount | Value (USD) |",
        "|---|---|---|",
    ]
    for h in holdings[:10]:
        sym   = _trunc(h.get("symbol") or h.get("mint") or h.get("token") or "?", 14)
        amt   = h.get("amount") or h.get("balance") or "?"
        value = h.get("value_usd") or h.get("usd_value") or h.get("value") or "?"
        lines.append(f"| {sym} | {amt} | ${value} |")

    return _ok("\n".join(lines), address=address, holdings=holdings[:10])


async def _cmd_degen(args: str, channel: dict, agent: dict, db) -> dict:
    """GET /api/intel/degen/high-conviction — top 5 high-conviction tokens."""
    status, data = await _get_intel("/api/intel/degen/high-conviction", agent)
    if status != 200:
        return _err(f"Degen API returned {status}: {data.get('error', '')}")

    items = data if isinstance(data, list) else data.get("tokens") or data.get("items") or []
    if not items:
        return _ok("No high-conviction tokens right now.", tokens=[])

    top5 = items[:5]
    lines = [
        "**High-Conviction Degen Picks**",
        "| # | Token | Score | Reason |",
        "|---|---|---|---|",
    ]
    for i, t in enumerate(top5, 1):
        sym    = _trunc(t.get("symbol") or t.get("mint") or t.get("token") or "?", 14)
        score  = t.get("conviction_score") or t.get("score") or t.get("confidence") or "?"
        reason = _trunc(t.get("reason") or t.get("signal") or t.get("narrative") or "?", 28)
        lines.append(f"| {i} | {sym} | {score} | {reason} |")

    return _ok("\n".join(lines), tokens=top5)


async def _cmd_narrative(args: str, channel: dict, agent: dict, db) -> dict:
    """GET /api/intel/narratives/hot — top narratives with momentum scores."""
    status, data = await _get_intel("/api/intel/narratives/hot", agent)
    if status != 200:
        return _err(f"Narratives API returned {status}: {data.get('error', '')}")

    items = data if isinstance(data, list) else data.get("narratives") or data.get("items") or []
    if not items:
        return _ok("No hot narratives right now.", narratives=[])

    lines = [
        "**Hot Narratives**",
        "| Narrative | Momentum | Tokens |",
        "|---|---|---|",
    ]
    for n in items[:8]:
        name     = _trunc(n.get("name") or n.get("narrative") or n.get("label") or "?", 22)
        momentum = n.get("momentum") or n.get("score") or n.get("strength") or "?"
        tokens   = n.get("token_count") or n.get("tokens") or "?"
        lines.append(f"| {name} | {momentum} | {tokens} |")

    return _ok("\n".join(lines), narratives=items[:8])


async def _cmd_watch(args: str, channel: dict, agent: dict, db) -> dict:
    """INSERT into channel_watchlist."""
    parts = args.strip().split(None, 1)
    if not parts:
        return _err("Usage: /watch <token_address> [symbol]")

    address = parts[0].strip()
    symbol  = parts[1].strip() if len(parts) > 1 else ""
    channel_id = channel["id"]

    try:
        await db.execute(
            """INSERT INTO channel_watchlist
                 (channel_id, item_type, item_address, item_symbol, added_by)
               VALUES (?, 'token', ?, ?, ?)
               ON CONFLICT(channel_id, item_address) DO UPDATE SET item_symbol=excluded.item_symbol""",
            (channel_id, address, symbol or None, agent["id"]),
        )
        await db.commit()
        label = symbol or address[:12] + "..."
        return _ok(
            f"**{label}** added to channel watchlist. Use `/unwatch {address}` to remove.",
            address=address, symbol=symbol,
        )
    except Exception as e:
        logger.warning("/watch error: %s", e)
        return _err(f"Watchlist insert failed: {e}")


async def _cmd_unwatch(args: str, channel: dict, agent: dict, db) -> dict:
    """DELETE from channel_watchlist."""
    address = args.strip()
    if not address:
        return _err("Usage: /unwatch <token_address>")

    channel_id = channel["id"]
    try:
        await db.execute(
            "DELETE FROM channel_watchlist WHERE channel_id=? AND item_address=?",
            (channel_id, address),
        )
        await db.commit()
        return _ok("Removed from watchlist.", address=address)
    except Exception as e:
        return _err(f"Watchlist remove failed: {e}")


async def _cmd_watchlist(args: str, channel: dict, agent: dict, db) -> dict:
    """SELECT * FROM channel_watchlist WHERE channel_id=?."""
    channel_id = channel["id"]
    try:
        rows = await (await db.execute(
            """SELECT item_address, item_symbol, added_at
               FROM channel_watchlist
               WHERE channel_id=? AND item_type='token'
               ORDER BY added_at DESC""",
            (channel_id,),
        )).fetchall()

        if not rows:
            return _ok("Channel watchlist is empty. Use `/watch <address>` to add tokens.", items=[])

        lines = [
            "**Channel Watchlist**",
            "| Symbol | Address | Added |",
            "|---|---|---|",
        ]
        items = []
        for r in rows:
            sym  = r["item_symbol"] or "?"
            addr = _trunc(r["item_address"], 14)
            ts   = _trunc(r["added_at"] or "", 16)
            lines.append(f"| {sym} | `{addr}` | {ts} |")
            items.append({"address": r["item_address"], "symbol": r["item_symbol"], "added_at": r["added_at"]})

        return _ok("\n".join(lines), items=items)
    except Exception as e:
        return _err(f"Watchlist query failed: {e}")


# ── main dispatcher ───────────────────────────────────────────────────────────

_COMMAND_MAP = {
    "/alpha":     _cmd_alpha,
    "/signals":   _cmd_signals,
    "/scan":      _cmd_scan,
    "/wallet":    _cmd_wallet,
    "/degen":     _cmd_degen,
    "/narrative": _cmd_narrative,
    "/watch":     _cmd_watch,
    "/unwatch":   _cmd_unwatch,
    "/watchlist": _cmd_watchlist,
}

_HELP = (
    "**Research channel commands:**\n"
    "- `/alpha` -- top 5 alpha signals\n"
    "- `/signals` -- last 10 intel signals with source + confidence\n"
    "- `/scan <token>` -- rug check + bonding curve report\n"
    "- `/wallet <address>` -- top holdings for a Solana wallet\n"
    "- `/degen` -- top 5 high-conviction degen picks\n"
    "- `/narrative` -- hot narratives with momentum scores\n"
    "- `/watch <address> [symbol]` -- add token to channel watchlist\n"
    "- `/unwatch <address>` -- remove token from watchlist\n"
    "- `/watchlist` -- show all watched tokens\n"
)


async def handle(cmd: str, args: str, channel: dict, agent: dict, db) -> dict:
    """Main entry point called by channel_slash.dispatch for research channels."""
    cmd_lower = cmd.lower() if cmd.startswith("/") else f"/{cmd.lower()}"

    if cmd_lower == "/help":
        return _ok(_HELP)

    handler = _COMMAND_MAP.get(cmd_lower)
    if handler is None:
        return _ok(f"Unknown command `{cmd}` in research channel.\n\n{_HELP}")

    try:
        return await handler(args, channel, agent, db)
    except Exception as exc:
        logger.exception("research_commands.handle unhandled error for %s", cmd)
        return _err(f"Internal error running {cmd}: {exc}")
