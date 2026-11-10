"""Money Flow Galaxy storage for traced runner wallets + compact vault galaxy."""
import pytest

from backend import galaxy_wallets as gw
from backend import runner_trace as rt
from backend.db import get_db


async def _seed(db, mint="GXMINT"):
    from backend.runner_detect import ensure_table
    await ensure_table(db)
    await rt._ensure(db)
    await db.execute("""CREATE TABLE IF NOT EXISTS token_wallet_roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, symbol TEXT, wallet_address TEXT NOT NULL,
        role TEXT NOT NULL, rank INTEGER, metric REAL, metric_label TEXT,
        discovered_at TEXT DEFAULT (datetime('now')), UNIQUE(mint, wallet_address, role))""")
    await db.execute("""CREATE TABLE IF NOT EXISTS wallet_edges (
        chain TEXT, address_a TEXT, address_b TEXT, role TEXT, tx_count INTEGER, total_value REAL,
        first_seen TEXT, last_seen TEXT, UNIQUE(chain, address_a, address_b, role))""")
    for t in ("runner_early_buyers", "token_wallet_roles", "runner_trace_queue", "runner_tokens"):
        await db.execute(f"DELETE FROM {t} WHERE mint=?", (mint,))
    await db.execute("DELETE FROM wallet_edges WHERE address_a='FUNDER'")
    await db.execute("INSERT INTO runner_tokens (mint, symbol, tier_usd, tier_weight, peak_mcap_usd, peak_at, "
                     "detected_at, backed) VALUES (?, 'GXT', 1e6, 1.0, 1e6, 1, 1, 1)", (mint,))
    await db.execute("INSERT INTO runner_trace_queue (mint, enqueued_at, status, origin_time) VALUES (?, 1, 'done', 1000)",
                     (mint,))
    rows = [("DUST1", 1, 1001, 0.0015, 0.01, None), ("REAL1", 2, 1003, 2.5, 5e6, "FUNDER"),
            ("DUST2", 3, 1004, 0.002, 0.02, None), ("REAL2", 4, 1010, 0.8, 1e6, None),
            ("POOL", 5, 1011, 5995.0, 2.0e8, None)]
    for w, r, ts, sol, tok, f in rows:
        await db.execute("INSERT INTO runner_early_buyers (mint, wallet, rank, entry_ts, sol, tokens, funded_by, source) "
                         "VALUES (?,?,?,?,?,?,?,'rpc')", (mint, w, r, ts, sol, tok, f))
    # a stale dust row an older early_buyer write left as a "first buyer"
    await db.execute("INSERT INTO token_wallet_roles (mint, symbol, wallet_address, role, rank, metric, metric_label) "
                     "VALUES (?, 'GXMINT', 'DUST1', 'first_buyer', 1, 0.0015, 'Entry rank #1')", (mint,))
    # another producer's top_holder row must survive
    await db.execute("INSERT INTO token_wallet_roles (mint, symbol, wallet_address, role, rank) "
                     "VALUES (?, 'GXT', 'HOLDER', 'top_holder', 1)", (mint,))
    await db.commit()


@pytest.mark.asyncio
async def test_sync_stores_real_buyers_ranked_with_labels_and_funders():
    async with get_db() as db:
        await _seed(db)
        out = await gw.sync_runner(db, "GXMINT")
        roles = await (await db.execute(
            "SELECT wallet_address, role, rank, metric, metric_label, symbol FROM token_wallet_roles "
            "WHERE mint='GXMINT' ORDER BY role, rank")).fetchall()
        edge = await (await db.execute(
            "SELECT address_b, total_value FROM wallet_edges WHERE address_a='FUNDER' AND role='funded'")).fetchone()
    fb = [tuple(r) for r in roles if r[1] == "first_buyer"]
    assert [(r[0], r[2]) for r in fb] == [("REAL1", 1), ("REAL2", 2)]           # dust + pool excluded, re-ranked
    assert fb[0][4].startswith("Entry #1 · 2.5 SOL") and "+3s after create" in fb[0][4]
    assert all(r[5] == "GXT" for r in fb)                                       # the real symbol, not mint[:8]
    assert ("HOLDER", "top_holder") in [(r[0], r[1]) for r in roles]            # other roles untouched
    assert out["removed"] == 1 and tuple(edge) == ("REAL1", 2.5)


@pytest.mark.asyncio
async def test_resync_updates_rank_instead_of_ignoring():
    async with get_db() as db:
        await _seed(db)
        await gw.sync_runner(db, "GXMINT")
        await db.execute("UPDATE runner_early_buyers SET rank=0, entry_ts=1002 WHERE mint='GXMINT' AND wallet='REAL2'")
        await db.commit()
        await gw.sync_runner(db, "GXMINT")
        got = dict(await (await db.execute(
            "SELECT wallet_address, rank FROM token_wallet_roles WHERE mint='GXMINT' AND role='first_buyer'")).fetchall())
    assert got == {"REAL2": 1, "REAL1": 2}


@pytest.mark.asyncio
async def test_untraced_mint_never_wipes_other_first_buyers():
    async with get_db() as db:
        await _seed(db, "GXOTHER")
        await db.execute("DELETE FROM runner_early_buyers WHERE mint='GXOTHER'")
        await db.execute("INSERT OR IGNORE INTO token_wallet_roles (mint, symbol, wallet_address, role, rank) "
                         "VALUES ('GXOTHER', 'O', 'SOMEONE', 'first_buyer', 1)")
        await db.commit()
        out = await gw.sync_runner(db, "GXOTHER")
        left = await (await db.execute(
            "SELECT COUNT(*) FROM token_wallet_roles WHERE mint='GXOTHER' AND role='first_buyer'")).fetchone()
    assert out["removed"] == 0 and left[0] >= 1


@pytest.mark.asyncio
async def test_backfill_covers_finished_traces():
    async with get_db() as db:
        await _seed(db)
        tot = await gw.backfill(db)
    assert tot["runners"] >= 1 and tot["first_buyers"] >= 2


def test_role_quotas_cover_every_role():
    from backend.routers import alpha
    roles = {r for r, _ in alpha.ROLE_QUOTAS}
    assert {"first_buyer", "top_trader", "top_holder", "deployer"} <= roles
    assert dict(alpha.ROLE_QUOTAS)["deployer"] <= dict(alpha.ROLE_QUOTAS)["first_buyer"]
