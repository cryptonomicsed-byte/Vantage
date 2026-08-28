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
import aiosqlite
import pytest

from backend.db import DB_PATH, init_agents_db
from backend.routers.degen import _pumpfun_leader, _vantage_conviction_leader


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
        await db.execute(
            "INSERT INTO pumpfun_premigration_tokens (mint, symbol, market_cap_usd, score, manipulation_flags, evicted, migrated) "
            "VALUES (?,?,?,?,?,0,0)",
            ("LowScoreMint111111111111111111111111111", "LOW", 10000.0, 5.0, "[]"),
        )
        await db.execute(
            "INSERT INTO pumpfun_premigration_tokens (mint, symbol, market_cap_usd, score, manipulation_flags, evicted, migrated) "
            "VALUES (?,?,?,?,?,0,0)",
            ("HighScoreMint11111111111111111111111111", "HIGH", 20000.0, 42.0, "[]"),
        )
        # Higher score, but evicted -- must not win.
        await db.execute(
            "INSERT INTO pumpfun_premigration_tokens (mint, symbol, market_cap_usd, score, manipulation_flags, evicted, migrated) "
            "VALUES (?,?,?,?,?,1,0)",
            ("EvictedMint111111111111111111111111111", "EVICT", 99999.0, 100.0, "[]"),
        )
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
        # market_cap_usd must clear degen_filters.MIN_MARKET_CAP_USD ($7,000)
        # or the real dust floor filters this row out before the
        # manipulation-flag surfacing this test actually checks ever runs.
        await db.execute(
            "INSERT INTO pumpfun_premigration_tokens (mint, symbol, market_cap_usd, score, manipulation_flags, evicted, migrated) "
            "VALUES (?,?,?,?,?,0,0)",
            ("FlaggedMint111111111111111111111111111", "FLAG", 25000.0, 30.0,
             '["low_unique_buyer_diversity"]'),
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
    real-mcap candidate, not returned (and not silently return None
    either, when a real candidate exists further down)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO pumpfun_premigration_tokens (mint, symbol, market_cap_usd, score, manipulation_flags, evicted, migrated) "
            "VALUES (?,?,?,?,?,0,0)",
            ("DustMint1111111111111111111111111111111", "DUST", 10.0, 99.0, "[]"),
        )
        await db.execute(
            "INSERT INTO pumpfun_premigration_tokens (mint, symbol, market_cap_usd, score, manipulation_flags, evicted, migrated) "
            "VALUES (?,?,?,?,?,0,0)",
            ("RealMint11111111111111111111111111111111", "REAL", 25000.0, 40.0, "[]"),
        )
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
async def test_pumpfun_leader_accepts_legitimate_low_cap_premigration_token():
    """Real finding 2026-08-28: applying the general $7k dust floor to
    pump.fun's own pre-migration tokens emptied this slot entirely --
    real top-scored pump.fun tokens legitimately sit at $3,000-4,500
    (graduation is ~$69k). Must use the lower PUMPFUN_MIN_MARKET_CAP_USD
    floor and still return a real leader, not go empty."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO pumpfun_premigration_tokens (mint, symbol, market_cap_usd, score, manipulation_flags, evicted, migrated) "
            "VALUES (?,?,?,?,?,0,0)",
            ("LegitLowCapMint1111111111111111111111111", "LOWCAP", 3500.0, 18.9, "[]"),
        )
        await db.commit()

    leader = await _pumpfun_leader()

    assert leader is not None
    assert leader["symbol"] == "LOWCAP"
