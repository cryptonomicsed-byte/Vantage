"""Tests for degen.py's platform-leaders logic (task (a)).

Covers the two DB-driven leader functions directly against a seeded temp
DB (pump.fun's own tier-scanner score, Vantage's own smart-wallet
conviction) -- these are the two platforms with no public ranking API,
computed entirely from Vantage's own persisted data, so they're the two
worth real regression coverage. The other four (GeckoTerminal,
DexScreener, CoinGecko, Moonshot) are thin passthroughs of an upstream
API's own response shape -- covered functionally by the moonshot_client
tests' fail-soft behavior and by the live browser verification step, not
duplicated here as DB tests.

token_wallet_roles/wallet_reputation are created by an external daemon
(pumpfun_wallet_intel.py, outside this repo -- see degen.py's
ensure_degen_indexes() docstring), not part of backend/db.py's own
migrations, so this file creates them directly (real production schema,
confirmed via sqlite3 .schema against the live DB) rather than assuming
init_agents_db() provides them.
"""
import json
from datetime import datetime, timezone

import aiosqlite
import pytest

from backend.db import DB_PATH, init_agents_db
from backend.degen_filters import PUMPFUN_MIN_MARKET_CAP_USD, PUMPFUN_MAX_MARKET_CAP_USD
from backend.routers.degen import _pumpfun_leader, _vantage_conviction_leader


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def _insert_pumpfun_token(db, mint: str, symbol: str, **overrides):
    """A pump.fun row that clears degen_filters.pumpfun_token_is_alive's
    full real screening by default (mcap mid-band, 2 distinct
    participants, 2 trades, fresh last_trade_at, real curve liquidity) --
    override individual fields per test to probe one criterion at a time,
    same pattern as test_degen_filters.py's _real_alive_token()."""
    row = dict(
        mint=mint, symbol=symbol,
        market_cap_usd=(PUMPFUN_MIN_MARKET_CAP_USD + PUMPFUN_MAX_MARKET_CAP_USD) / 2,
        score=20.0, manipulation_flags="[]", evicted=0, migrated=0,
        buy_count=2, sell_count=1,
        unique_buyers=json.dumps(["WalletA", "WalletB"]),
        unique_sellers=json.dumps(["WalletC"]),
        last_trade_at=_now_ts(),
        v_sol_in_curve=30.0,
    )
    row.update(overrides)
    await db.execute(
        """INSERT INTO pumpfun_premigration_tokens
           (mint, symbol, market_cap_usd, score, manipulation_flags, evicted, migrated,
            buy_count, sell_count, unique_buyers, unique_sellers, last_trade_at, v_sol_in_curve)
           VALUES (:mint,:symbol,:market_cap_usd,:score,:manipulation_flags,:evicted,:migrated,
                   :buy_count,:sell_count,:unique_buyers,:unique_sellers,:last_trade_at,:v_sol_in_curve)""",
        row,
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
        await db.execute("DELETE FROM token_wallet_roles")
        await db.execute("DELETE FROM wallet_reputation")
        await db.execute("DELETE FROM pumpfun_premigration_tokens")
        await db.commit()


@pytest.mark.asyncio
async def test_pumpfun_leader_picks_highest_score_among_live_tokens():
    async with aiosqlite.connect(DB_PATH) as db:
        await _insert_pumpfun_token(db, "LowScoreMint111111111111111111111111111", "LOW", score=5.0)
        await _insert_pumpfun_token(db, "HighScoreMint11111111111111111111111111", "HIGH", score=42.0)
        # Higher score, but evicted -- must not win.
        await _insert_pumpfun_token(db, "EvictedMint111111111111111111111111111", "EVICT", score=100.0, evicted=1)
        await db.commit()

    leader = await _pumpfun_leader()

    assert leader is not None
    assert leader["symbol"] == "HIGH"
    assert leader["metric_value"] == 42.0
    assert leader["platform"] == "pump.fun"


@pytest.mark.asyncio
async def test_pumpfun_leader_none_when_no_live_tokens():
    leader = await _pumpfun_leader()
    assert leader is None


@pytest.mark.asyncio
async def test_pumpfun_leader_surfaces_manipulation_flags():
    async with aiosqlite.connect(DB_PATH) as db:
        # Real screening note: manipulation_flags is surfaced (not
        # disqualifying by itself in _pumpfun_leader -- the daemon's own
        # score already discounts flagged tokens) -- this row must still
        # clear pumpfun_token_is_alive's real activity screen to appear
        # at all, hence the full helper insert.
        await _insert_pumpfun_token(
            db, "FlaggedMint111111111111111111111111111", "FLAG",
            score=30.0, manipulation_flags='["low_unique_buyer_diversity"]',
        )
        await db.commit()

    leader = await _pumpfun_leader()

    assert leader is not None
    assert leader["manipulation_flags"] == ["low_unique_buyer_diversity"]


@pytest.mark.asyncio
async def test_vantage_conviction_leader_sums_smart_wallet_overlap(monkeypatch):
    # _vantage_conviction_leader enriches its top candidates with a real
    # _dexscreener_mcap network call to check the dust floor -- mocked here
    # (no network in tests) to return a market cap that clears
    # degen_filters.MIN_MARKET_CAP_USD, isolating this test to the SQL
    # conviction-ranking logic it actually exists to verify.
    import backend.routers.degen as degen_module

    async def fake_mcap(mint):
        return {"market_cap": 50_000.0, "liquidity_usd": 10_000.0}

    monkeypatch.setattr(degen_module, "_dexscreener_mcap", fake_mcap)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO wallet_reputation (wallet_address, display_name, copy_trade_score) VALUES (?,?,?)",
            ("SmartWallet1111111111111111111111111111", "Smart One", 10.0),
        )
        await db.execute(
            "INSERT INTO wallet_reputation (wallet_address, display_name, copy_trade_score) VALUES (?,?,?)",
            ("SmartWallet2222222222222222222222222222", "Smart Two", 15.0),
        )
        # Two smart wallets both in TOKEN_A -- conviction should sum to 25.
        await db.execute(
            "INSERT INTO token_wallet_roles (mint, symbol, wallet_address, role) VALUES (?,?,?,?)",
            ("TokenAMint1111111111111111111111111111", "TOKEN_A", "SmartWallet1111111111111111111111111111", "top_holder"),
        )
        await db.execute(
            "INSERT INTO token_wallet_roles (mint, symbol, wallet_address, role) VALUES (?,?,?,?)",
            ("TokenAMint1111111111111111111111111111", "TOKEN_A", "SmartWallet2222222222222222222222222222", "first_buyer"),
        )
        # Only one smart wallet in TOKEN_B -- lower conviction, must not win.
        await db.execute(
            "INSERT INTO token_wallet_roles (mint, symbol, wallet_address, role) VALUES (?,?,?,?)",
            ("TokenBMint1111111111111111111111111111", "TOKEN_B", "SmartWallet1111111111111111111111111111", "top_holder"),
        )
        await db.commit()

    leader = await _vantage_conviction_leader()

    assert leader is not None
    assert leader["symbol"] == "TOKEN_A"
    assert leader["metric_value"] == 25.0
    assert leader["smart_wallet_count"] == 2


@pytest.mark.asyncio
async def test_vantage_conviction_leader_none_when_no_overlap():
    leader = await _vantage_conviction_leader()
    assert leader is None


@pytest.mark.asyncio
async def test_pumpfun_leader_skips_dust_for_higher_market_cap_candidate():
    """Real bug regression: pump.fun's slot showed a ~$10-mcap dead token.
    A dust-tier top-score row must be skipped in favor of the next
    real, in-band, actually-alive candidate, not returned (and not
    silently return None either, when a real candidate exists further
    down)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await _insert_pumpfun_token(db, "DustMint1111111111111111111111111111111", "DUST",
                                     score=99.0, market_cap_usd=10.0)
        await _insert_pumpfun_token(db, "RealMint11111111111111111111111111111111", "REAL", score=40.0)
        await db.commit()

    leader = await _pumpfun_leader()

    assert leader is not None
    assert leader["symbol"] == "REAL"


@pytest.mark.asyncio
async def test_vantage_conviction_leader_skips_major_for_lower_conviction_degen(monkeypatch):
    """Real bug regression: this slot showed USDC's real mint (labeled
    "penny" due to a separate upstream data-corruption bug) as the #1
    conviction pick. A major/stablecoin at the top of the conviction
    ranking must be skipped in favor of the next real degen candidate."""
    import backend.routers.degen as degen_module

    async def fake_mcap(mint):
        return {"market_cap": 50_000.0, "liquidity_usd": 10_000.0}

    monkeypatch.setattr(degen_module, "_dexscreener_mcap", fake_mcap)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO wallet_reputation (wallet_address, display_name, copy_trade_score) VALUES (?,?,?)",
            ("BigWallet111111111111111111111111111111", "Big", 100.0),
        )
        await db.execute(
            "INSERT INTO wallet_reputation (wallet_address, display_name, copy_trade_score) VALUES (?,?,?)",
            ("SmallWallet11111111111111111111111111111", "Small", 5.0),
        )
        # USDC's real mint -- highest conviction, but must be excluded.
        await db.execute(
            "INSERT INTO token_wallet_roles (mint, symbol, wallet_address, role) VALUES (?,?,?,?)",
            ("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "penny", "BigWallet111111111111111111111111111111", "top_holder"),
        )
        # A real degen token, lower conviction -- must win instead.
        await db.execute(
            "INSERT INTO token_wallet_roles (mint, symbol, wallet_address, role) VALUES (?,?,?,?)",
            ("RealDegenMint111111111111111111111111111", "DEGEN", "SmallWallet11111111111111111111111111111", "first_buyer"),
        )
        await db.commit()

    leader = await _vantage_conviction_leader()

    assert leader is not None
    assert leader["address"] == "RealDegenMint111111111111111111111111111"
    assert leader["symbol"] == "DEGEN"


@pytest.mark.asyncio
async def test_pumpfun_leader_accepts_legitimate_low_cap_token_in_band_with_real_activity():
    """Refined 2026-08-28: the owner replaced the earlier flat floor with
    an explicit $14k-$32k lifecycle band + real activity minimums (see
    degen_filters.pumpfun_token_is_alive). A token mid-band with genuine
    recent multi-participant activity must still win this slot -- the
    stricter screening isn't supposed to empty it out entirely, just
    exclude noise/dead tokens within or around the band."""
    async with aiosqlite.connect(DB_PATH) as db:
        await _insert_pumpfun_token(db, "LegitLowCapMint1111111111111111111111111", "LOWCAP", score=18.9)
        await db.commit()

    leader = await _pumpfun_leader()

    assert leader is not None
    assert leader["symbol"] == "LOWCAP"


async def test_pumpfun_leader_rejects_below_band_even_with_high_score():
    """Real finding 2026-08-28: right after a fresh pumpfun_tier_scanner.py
    restart, EVERY currently-tracked token sits well under the $14k band
    floor (confirmed live: max mcap $5,647 across 243 tokens) -- this is
    expected, not a bug, and the slot must correctly show no leader
    rather than falling back to an under-band token just because it has
    the highest score."""
    async with aiosqlite.connect(DB_PATH) as db:
        await _insert_pumpfun_token(db, "BelowBandMint1111111111111111111111111", "LOW",
                                     score=99.0, market_cap_usd=3500.0)
        await db.commit()

    leader = await _pumpfun_leader()

    assert leader is None
