"""Store runner-trace wallets in the Money Flow Galaxy, in its own layout.

The galaxy (/api/moneyflow) reads wallets per token from token_wallet_roles
(mint, symbol, wallet_address, role, rank, metric, metric_label) and funding
links from wallet_edges (role 'funded'). The P4 runner chain kept its wallets
in its own tables, so traced early buyers never reached the galaxy:

- curve-path traces wrote nothing to token_wallet_roles;
- RPC traces wrote every row (dust bots included) ranked by raw position,
  symbol = mint[:8], ON CONFLICT DO NOTHING -- so a re-trace never corrected
  a rank.

sync_runner(mint) writes the authoritative version for one traced runner:
role 'first_buyer', rank = rank among real buys (dust/liquidity excluded),
metric = SOL spent, metric_label = "Entry #n · x SOL · +ts after create",
the runner's real symbol; it refreshes discovered_at so the galaxy's time
window shows it, removes first_buyer rows for that mint that are not real
buys, and writes funder -> buyer edges. Labels (dev / sniper / bundler / ...)
stay in wallet_labels and are joined at render time.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

FIRST_N = 100


def _fmt_delay(s: Optional[float]) -> str:
    if s is None:
        return ""
    s = int(s)
    if s < 60:
        return f" · +{s}s after create"
    if s < 3600:
        return f" · +{s // 60}m after create"
    return f" · +{s / 3600:.1f}h after create"


async def sync_runner(db, mint: str) -> Dict[str, Any]:
    """Upsert one traced runner's real early buyers into token_wallet_roles
    and wallet_edges. Never raises; returns counts."""
    from .early_runner_intel import annotate
    out = {"mint": mint, "first_buyers": 0, "removed": 0, "funder_edges": 0}
    try:
        q = await (await db.execute(
            "SELECT origin_time FROM runner_trace_queue WHERE mint=?", (mint,))).fetchone()
        sym = await (await db.execute("SELECT symbol FROM runner_tokens WHERE mint=?", (mint,))).fetchone()
        cols = ["wallet", "rank", "entry_ts", "sol", "tokens", "funded_by"]
        rows = [dict(zip(cols, r)) for r in await (await db.execute(
            f"SELECT {', '.join(cols)} FROM runner_early_buyers WHERE mint=? ORDER BY rank", (mint,))).fetchall()]
        annotate(rows, q[0] if q else None)
        real = [r for r in rows if r["kind"] == "buy"][:FIRST_N]
        symbol = (sym[0] if sym and sym[0] else None) or f"{mint[:4]}…{mint[-4:]}"
        for r in real:
            sol = r.get("sol") or 0.0
            label = f"Entry #{r['real_rank']} · {sol:.3g} SOL{_fmt_delay(r.get('delay_s'))}"
            await db.execute(
                """INSERT INTO token_wallet_roles
                   (mint, symbol, wallet_address, role, rank, metric, metric_label, discovered_at)
                   VALUES (?,?,?,'first_buyer',?,?,?,datetime('now'))
                   ON CONFLICT(mint, wallet_address, role) DO UPDATE SET
                     symbol=excluded.symbol, rank=excluded.rank, metric=excluded.metric,
                     metric_label=excluded.metric_label, discovered_at=excluded.discovered_at""",
                (mint, symbol, r["wallet"], r["real_rank"], sol, label))
            out["first_buyers"] += 1
            f = r.get("funded_by")
            if f and f != r["wallet"]:
                await db.execute(
                    """INSERT INTO wallet_edges
                       (chain, address_a, address_b, role, tx_count, total_value, first_seen, last_seen)
                       VALUES ('solana',?,?,'funded',1,?,datetime('now'),datetime('now'))
                       ON CONFLICT(chain, address_a, address_b, role) DO UPDATE SET
                         total_value=MAX(total_value, excluded.total_value), last_seen=datetime('now')""",
                    (f, r["wallet"], sol))
                out["funder_edges"] += 1
        keep = [r["wallet"] for r in real]
        if rows:   # only once a trace exists -- never wipe another producer's rows blindly
            ph = ",".join("?" * len(keep)) or "''"
            cur = await db.execute(
                f"DELETE FROM token_wallet_roles WHERE mint=? AND role='first_buyer' "
                f"AND wallet_address NOT IN ({ph})", (mint, *keep))
            out["removed"] = cur.rowcount or 0
        await db.commit()
    except Exception as e:  # noqa: BLE001 -- the galaxy is a view; never break tracing
        log.warning("galaxy sync failed for %s: %s", mint, e)
        out["error"] = str(e)[:200]
    return out


async def backfill(db) -> Dict[str, int]:
    """Sync every finished trace (run once at startup of the runner loop)."""
    mints = [r[0] for r in await (await db.execute(
        "SELECT mint FROM runner_trace_queue WHERE status IN ('done','partial')")).fetchall()]
    tot = {"runners": 0, "first_buyers": 0, "removed": 0}
    for m in mints:
        r = await sync_runner(db, m)
        tot["runners"] += 1
        tot["first_buyers"] += r.get("first_buyers", 0)
        tot["removed"] += r.get("removed", 0)
    return tot
