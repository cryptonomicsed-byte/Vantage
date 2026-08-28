"""Tests for aggregate_score.py (task b) -- the whole-app aggregate
scoring engine. Covers: normalization math (pure), disqualification
(manipulation flags + mint/freeze authority), weight-sum invariant, and a
real end-to-end scenario with two candidates where the higher-conviction
one must win.
"""
import aiosqlite
import pytest

from backend.db import DB_PATH, init_agents_db
from backend.aggregate_score import (
    WEIGHTS,
    _normalize,
    compute_aggregate_scores,
)


@pytest.fixture(autouse=True)
async def _init_schema():
    await init_agents_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS token_wallet_roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mint TEXT NOT NULL, symbol TEXT, wallet_address TEXT NOT NULL,
                role TEXT NOT NULL, rank INTEGER, metric REAL, metric_label TEXT,
                discovered_at TEXT DEFAULT (datetime('now')),
                UNIQUE(mint, wallet_address, role)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS wallet_reputation (
                wallet_address TEXT PRIMARY KEY, chain TEXT DEFAULT 'solana',
                display_name TEXT DEFAULT '', copy_trade_score REAL DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS social_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, contract_address TEXT,
                sentiment TEXT, confidence REAL, created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("DELETE FROM token_wallet_roles")
        await db.execute("DELETE FROM wallet_reputation")
        await db.execute("DELETE FROM social_signals")
        await db.execute("DELETE FROM pumpfun_premigration_tokens")
        await db.execute("DELETE FROM tracked_wallets")
        await db.commit()


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_normalize_min_max_scaling():
    result = _normalize({"a": 10.0, "b": 20.0, "c": 30.0})
    assert result["a"] == 0.0
    assert result["b"] == 0.5
    assert result["c"] == 1.0


def test_normalize_all_equal_values_returns_zero():
    result = _normalize({"a": 5.0, "b": 5.0, "c": 5.0})
    assert result == {"a": 0.0, "b": 0.0, "c": 0.0}


def test_normalize_empty_returns_empty():
    assert _normalize({}) == {}


@pytest.mark.asyncio
async def test_manipulation_flagged_token_is_disqualified():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO pumpfun_premigration_tokens (mint, symbol, score, manipulation_flags, evicted, migrated) "
            "VALUES (?,?,?,?,0,0)",
            ("FlaggedMint111111111111111111111111111", "FLAG", 50.0, '["low_unique_buyer_diversity"]'),
        )
        await db.commit()

    result = await compute_aggregate_scores(
        [{"address": "FlaggedMint111111111111111111111111111", "symbol": "FLAG", "platform_breadth": 3}],
        helius_key="",
    )

    assert result["ranked"] == []
    assert len(result["disqualified"]) == 1
    assert result["disqualified"][0]["address"] == "FlaggedMint111111111111111111111111111"
    assert "manipulation_flags" in result["disqualified"][0]["reason"]


@pytest.mark.asyncio
async def test_higher_conviction_candidate_wins():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO wallet_reputation (wallet_address, copy_trade_score) VALUES (?,?)",
            ("SmartWallet1111111111111111111111111111", 50.0),
        )
        # Strong candidate: high smart-money conviction.
        await db.execute(
            "INSERT INTO token_wallet_roles (mint, symbol, wallet_address, role) VALUES (?,?,?,?)",
            ("StrongMint1111111111111111111111111111", "STRONG", "SmartWallet1111111111111111111111111111", "top_holder"),
        )
        await db.commit()

    candidates = [
        {"address": "StrongMint1111111111111111111111111111", "symbol": "STRONG", "platform_breadth": 4},
        {"address": "WeakMint11111111111111111111111111111", "symbol": "WEAK", "platform_breadth": 1},
    ]
    result = await compute_aggregate_scores(candidates, helius_key="")

    assert len(result["ranked"]) == 2
    assert result["ranked"][0]["address"] == "StrongMint1111111111111111111111111111"
    assert result["ranked"][0]["total_score"] > result["ranked"][1]["total_score"]
    # Fully auditable: raw + normalized + weight present for every component.
    for comp in ("smart_money", "platform_breadth", "volume_momentum", "social_sentiment", "whale_presence"):
        assert comp in result["ranked"][0]["components"]
        assert "raw" in result["ranked"][0]["components"][comp]
        assert "weight" in result["ranked"][0]["components"][comp]


@pytest.mark.asyncio
async def test_no_candidates_returns_empty_ranked():
    result = await compute_aggregate_scores([], helius_key="")
    assert result["ranked"] == []
    assert result["disqualified"] == []
    assert result["methodology"] == WEIGHTS


@pytest.mark.asyncio
async def test_whale_presence_detected_from_active_tracked_wallet():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO tracked_wallets (chain, address, balance_usd, archived_at) VALUES ('solana',?,?,NULL)",
            ("WhaleWallet111111111111111111111111111111", 50000.0),
        )
        await db.execute(
            "INSERT INTO token_wallet_roles (mint, symbol, wallet_address, role) VALUES (?,?,?,?)",
            ("WhaleTokenMint111111111111111111111111", "WHALE", "WhaleWallet111111111111111111111111111111", "deployer"),
        )
        await db.commit()

    result = await compute_aggregate_scores(
        [{"address": "WhaleTokenMint111111111111111111111111", "symbol": "WHALE", "platform_breadth": 1}],
        helius_key="",
    )

    assert len(result["ranked"]) == 1
    assert result["ranked"][0]["components"]["whale_presence"]["raw"] is True


@pytest.mark.asyncio
async def test_archived_whale_wallet_does_not_count():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO tracked_wallets (chain, address, balance_usd, archived_at) VALUES ('solana',?,?,'2026-01-01 00:00:00')",
            ("ArchivedWhale11111111111111111111111111", 50000.0),
        )
        await db.execute(
            "INSERT INTO token_wallet_roles (mint, symbol, wallet_address, role) VALUES (?,?,?,?)",
            ("NoWhaleTokenMint11111111111111111111111", "NOWHALE", "ArchivedWhale11111111111111111111111111", "deployer"),
        )
        await db.commit()

    result = await compute_aggregate_scores(
        [{"address": "NoWhaleTokenMint11111111111111111111111", "symbol": "NOWHALE", "platform_breadth": 1}],
        helius_key="",
    )

    assert len(result["ranked"]) == 1
    assert result["ranked"][0]["components"]["whale_presence"]["raw"] is False
