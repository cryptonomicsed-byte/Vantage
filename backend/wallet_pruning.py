"""Wallet activity scoring + archival for tracked_wallets.

Root cause this addresses: three VPS-deployed daemons outside this repo
(wallet_intel/scanner.py, pumpfun_launch_radar.py, pumpfun_wallet_intel.py --
ad-hoc scripts at /opt/ares/, not version-controlled) unconditionally insert
every deployer/top_holder/top_trader/first_buyer wallet from every pump.fun
token they scan into tracked_wallets, forever, with no scoring gate. Measured
on production 2026-08-28: 86,573 tracked_wallets rows, of which 86,446
(99.85%) have zero trade_count, zero degen_score, were never balance-checked,
and only 218 total have EVER shown any real signal by any measure. This is
what made /api/moneyflow return 87,432 graph nodes -- unrenderable by any
force-directed graph library (ForceGraph3D creates a real Three.js Object3D
per node and runs live physics for all of them; the browser tab hangs/crashes
well before 10k nodes, let alone 87k).

Those three daemons live outside this repo, so this can't gate their INSERTs
at the source. Instead: score every tracked_wallets row against real
criteria, archive (never hard-delete) anything that doesn't clear the bar,
and have /api/moneyflow (and any other default listing) only count
non-archived wallets. Runs as a periodic background loop (see main.py) so
accumulation from any future source -- these daemons if restarted, or a new
one -- gets swept rather than silently growing forever again.

## Keep-active criteria (a wallet stays active if ANY of these hold)

1. Whale balance: balance_usd >= WHALE_BALANCE_USD ($10,000). Real threshold,
   not arbitrary -- of 86,573 tracked wallets, exactly 5 clear $10k today;
   this is a genuine top-tier bar, not a rubber stamp.
2. Demonstrated alpha: degen_score > 0. This score is pumpfun_wallet_intel.py
   / degen_alpha_fusion.py's own actual analysis of early-conviction/winning-
   token behavior -- real signal, just sparse (127 of 86,573 today).
3. Recent real activity: appears in wallet_edges (as either address) with
   last_seen within RECENT_ACTIVITY_DAYS (30), OR appears in wallet_trades
   with timestamp within the same window. This is actual observed on-chain
   counterparty/trade activity, not a label or a guess.
4. Explicitly classified: address_type != 'wallet' (exchange, CA, or any
   other explicit non-generic classification) -- these were deliberately
   tagged as something notable, not auto-stamped by the role-scanning
   daemons (which always use the generic 'wallet' type).

Deliberately NOT used as a criterion: the `label` field. 86,570 of 86,573
tracked wallets have a non-empty label (confirmed on production data), but
it's overwhelmingly auto-generated noise from the same three daemons
("Deployer: SYMBOL", "Top Holder: SYMBOL", etc) -- a label existing proves a
daemon touched the row once, not that the wallet is worth tracking.

Everything failing all four criteria gets `archived_at` set to now. A wallet
that later shows real activity again is automatically re-activated on the
next scoring pass (archived_at is unconditionally overwritten each run based
on current state, not a one-way flag).
"""
import logging
import time

from .db import get_db

logger = logging.getLogger(__name__)

WHALE_BALANCE_USD = 10_000.0
RECENT_ACTIVITY_DAYS = 30

_ARCHIVE_QUERY = """
UPDATE tracked_wallets
SET archived_at = datetime('now')
WHERE archived_at IS NULL
  AND COALESCE(balance_usd, 0) < ?
  AND COALESCE(degen_score, 0) <= 0
  AND address_type = 'wallet'
  AND NOT EXISTS (
      SELECT 1 FROM wallet_edges we
      WHERE (we.address_a = tracked_wallets.address OR we.address_b = tracked_wallets.address)
        AND we.last_seen >= datetime('now', ?)
  )
  AND NOT EXISTS (
      SELECT 1 FROM wallet_trades wt
      WHERE wt.wallet = tracked_wallets.address
        AND wt.timestamp >= ?
  )
"""

_REACTIVATE_QUERY = """
UPDATE tracked_wallets
SET archived_at = NULL
WHERE archived_at IS NOT NULL
  AND (
    COALESCE(balance_usd, 0) >= ?
    OR COALESCE(degen_score, 0) > 0
    OR address_type != 'wallet'
    OR EXISTS (
        SELECT 1 FROM wallet_edges we
        WHERE (we.address_a = tracked_wallets.address OR we.address_b = tracked_wallets.address)
          AND we.last_seen >= datetime('now', ?)
    )
    OR EXISTS (
        SELECT 1 FROM wallet_trades wt
        WHERE wt.wallet = tracked_wallets.address
          AND wt.timestamp >= ?
    )
  )
"""


async def prune_inactive_tracked_wallets() -> dict:
    """One scoring pass: archive newly-inactive wallets, reactivate any
    archived wallet that has picked up real activity since. Idempotent --
    safe to call repeatedly (periodic loop) or once (manual cleanup)."""
    # wallet_trades isn't in db.py's migrations -- alpha.py creates it
    # defensively at request time (_WALLET_TRADES_DDL). Same convention here
    # so this module works standalone (tests, a fresh DB before /moneyflow
    # has ever been hit once). wallet_edges IS in db.py's real migrations.
    from .routers.alpha import _WALLET_TRADES_DDL

    window = f"-{RECENT_ACTIVITY_DAYS} days"
    since_ts = int(time.time()) - RECENT_ACTIVITY_DAYS * 86400
    async with get_db() as db:
        await db.execute(_WALLET_TRADES_DDL)
        reactivate_cur = await db.execute(_REACTIVATE_QUERY, (WHALE_BALANCE_USD, window, since_ts))
        reactivated = reactivate_cur.rowcount or 0
        archive_cur = await db.execute(_ARCHIVE_QUERY, (WHALE_BALANCE_USD, window, since_ts))
        archived = archive_cur.rowcount or 0
        await db.commit()
        active_row = await (await db.execute(
            "SELECT COUNT(*) FROM tracked_wallets WHERE archived_at IS NULL"
        )).fetchone()
        total_row = await (await db.execute(
            "SELECT COUNT(*) FROM tracked_wallets"
        )).fetchone()
    result = {
        "archived_this_pass": archived,
        "reactivated_this_pass": reactivated,
        "active_total": active_row[0] if active_row else 0,
        "tracked_total": total_row[0] if total_row else 0,
    }
    if archived or reactivated:
        logger.info(
            "wallet_pruning: archived=%d reactivated=%d active=%d/%d",
            archived, reactivated, result["active_total"], result["tracked_total"],
        )
    return result
