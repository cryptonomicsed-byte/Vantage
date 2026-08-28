"""Real DB test for wallet_pruning.py's activity-scoring pass.

Covers the four keep-active criteria (whale balance, degen_score,
recent wallet_edges/wallet_trades activity, non-generic address_type)
and confirms a wallet with none of them gets archived, while re-scoring
an archived wallet that picks up new activity reactivates it.

Calls init_agents_db() directly (the real schema-migration entrypoint,
including tracked_wallets.archived_at) against the temp DB conftest.py
already points VANTAGE_DATA_DIR at -- not the `client` fixture, which
drags in the full app lifespan (including skills_registry.py, which uses
3.10+ `dict | None` syntax this local Python 3.9 can't parse).
"""
import aiosqlite
import pytest

from backend.db import DB_PATH, init_agents_db
from backend.wallet_pruning import prune_inactive_tracked_wallets, WHALE_BALANCE_USD


@pytest.fixture(autouse=True)
async def _init_schema():
    await init_agents_db()


async def _insert_wallet(address: str, **kwargs):
    defaults = {
        "chain": "solana", "label": "", "address_type": "wallet",
        "degen_score": 0, "balance_usd": 0.0, "archived_at": None,
    }
    defaults.update(kwargs)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO tracked_wallets (chain, address, label, address_type, degen_score, balance_usd, archived_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(chain, address) DO UPDATE SET
                 label=excluded.label, address_type=excluded.address_type,
                 degen_score=excluded.degen_score, balance_usd=excluded.balance_usd,
                 archived_at=excluded.archived_at""",
            (defaults["chain"], address, defaults["label"], defaults["address_type"],
             defaults["degen_score"], defaults["balance_usd"], defaults["archived_at"]),
        )
        await db.commit()


async def _archived_at(address: str):
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT archived_at FROM tracked_wallets WHERE address=?", (address,)
        )).fetchone()
    return row[0] if row else None


@pytest.mark.asyncio
async def test_zero_signal_wallet_gets_archived():
    addr = "PruneMe1111111111111111111111111111111111"
    await _insert_wallet(addr)

    await prune_inactive_tracked_wallets()

    assert await _archived_at(addr) is not None


@pytest.mark.asyncio
async def test_whale_balance_stays_active():
    addr = "WhaleWallet111111111111111111111111111111"
    await _insert_wallet(addr, balance_usd=WHALE_BALANCE_USD + 1)

    await prune_inactive_tracked_wallets()

    assert await _archived_at(addr) is None


@pytest.mark.asyncio
async def test_degen_score_stays_active():
    addr = "DegenWallet11111111111111111111111111111"
    await _insert_wallet(addr, degen_score=5)

    await prune_inactive_tracked_wallets()

    assert await _archived_at(addr) is None


@pytest.mark.asyncio
async def test_non_wallet_address_type_stays_active():
    addr = "ExchangeAddr1111111111111111111111111111"
    await _insert_wallet(addr, address_type="exchange")

    await prune_inactive_tracked_wallets()

    assert await _archived_at(addr) is None


@pytest.mark.asyncio
async def test_recent_wallet_edges_activity_stays_active():
    addr = "EdgeActiveWallet111111111111111111111111"
    counterparty = "CounterpartyWallet11111111111111111111111"
    await _insert_wallet(addr)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO wallet_edges (chain, address_a, address_b, role, tx_count, total_value, last_seen)
               VALUES ('solana', ?, ?, 'counterparty', 1, 10.0, datetime('now'))""",
            (addr, counterparty),
        )
        await db.commit()

    await prune_inactive_tracked_wallets()

    assert await _archived_at(addr) is None


@pytest.mark.asyncio
async def test_stale_wallet_edges_activity_still_archived():
    addr = "EdgeStaleWallet111111111111111111111111111"
    counterparty = "CounterpartyWallet22222222222222222222222"
    await _insert_wallet(addr)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO wallet_edges (chain, address_a, address_b, role, tx_count, total_value, last_seen)
               VALUES ('solana', ?, ?, 'counterparty', 1, 10.0, datetime('now', '-90 days'))""",
            (addr, counterparty),
        )
        await db.commit()

    await prune_inactive_tracked_wallets()

    assert await _archived_at(addr) is not None


@pytest.mark.asyncio
async def test_archived_wallet_reactivates_on_new_activity():
    addr = "ReactivateWallet1111111111111111111111111"
    await _insert_wallet(addr, archived_at="2026-01-01 00:00:00")

    # Still zero signal -- stays archived.
    await prune_inactive_tracked_wallets()
    assert await _archived_at(addr) is not None

    # Now gains a real signal -- next pass reactivates it.
    await _insert_wallet(addr, degen_score=3, archived_at="2026-01-01 00:00:00")
    await prune_inactive_tracked_wallets()
    assert await _archived_at(addr) is None
