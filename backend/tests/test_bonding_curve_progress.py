"""P1-11: bonding-curve progress from reserves, not price x 1e6 / 69k.

Acceptance: a tracked token at $30k shows about 43% of a $69k graduation; an
unknown mint returns progress_src none, not a made-up number. Plus the
on-chain account decode (layout verified against mainnet accounts) and the
non-standard-curve flag.
"""
import struct

import aiosqlite
import pytest

from backend import pumpfun_curve as pc
from backend.db import DB_PATH

TODAY_KEYS = {"mint", "price", "curve_target_usd", "progress_pct", "graduated", "source"}


@pytest.fixture
def sol_150(monkeypatch):
    import backend.routers.trading as trading
    async def q(symbol, mint=None):
        assert symbol == "SOL"
        return 150.0
    monkeypatch.setattr(trading, "_fetch_quote", q)


async def _track(mint, v_sol, v_tokens, migrated=0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO pumpfun_premigration_tokens (mint, symbol, v_sol_in_curve, v_tokens_in_curve, migrated) "
            "VALUES (?,?,?,?,?)", (mint, "T", v_sol, v_tokens, migrated))
        await db.commit()


async def _get(client, agent, mint):
    r = await client.get(f"/api/intel/pumpfun/bonding-curve?mint={mint}", headers={"X-Agent-Key": agent["api_key"]})
    assert r.status_code == 200, r.text
    return r.json()


async def test_tracked_30k_token_reads_about_43_percent(client, fresh_agent, sol_150):
    # 60 SOL / 300M tokens x 1B supply = 200 SOL mcap = $30,000 at $150
    await _track("P111TrackedMint30k", 60.0, 300_000_000.0)
    b = await _get(client, await fresh_agent(), "P111TrackedMint30k")
    assert TODAY_KEYS <= set(b)
    assert b["market_cap_usd"] == 30000.0
    assert b["progress_pct"] == pytest.approx(43.48, abs=0.01)
    assert b["progress_src"] == "reserves:db" and b["graduated"] is False


async def test_unknown_mint_is_none_not_a_made_up_number(client, fresh_agent, sol_150, monkeypatch):
    async def nothing(mint):
        return None
    monkeypatch.setattr(pc, "fetch_onchain", nothing)
    b = await _get(client, await fresh_agent(), "P111UnknownMint")
    assert b["progress_src"] == "none" and b["progress_pct"] is None and b["graduated"] is False
    assert TODAY_KEYS <= set(b)


async def test_no_sol_price_gives_no_usd_number(client, fresh_agent, monkeypatch):
    import backend.routers.trading as trading
    async def none(symbol, mint=None):
        return None
    monkeypatch.setattr(trading, "_fetch_quote", none)
    await _track("P111NoSolPrice", 60.0, 300_000_000.0)
    b = await _get(client, await fresh_agent(), "P111NoSolPrice")
    assert b["progress_pct"] is None and b["progress_src"] == "none"
    assert b["tokens_sold_pct"] is not None  # reserves still say how full the curve is


async def test_migrated_token_is_graduated(client, fresh_agent, sol_150):
    await _track("P111Migrated", 115.0, 279_900_000.0, migrated=1)
    b = await _get(client, await fresh_agent(), "P111Migrated")
    assert b["graduated"] is True and b["progress_pct"] == 100.0


def _account(vt, vs, rt, rs, supply=1_000_000_000_000_000, complete=0):
    return b"\x17" * 8 + struct.pack("<5Q", vt, vs, rt, rs, supply) + bytes([complete]) + b"\x00" * 117


def test_decode_matches_the_mainnet_layout():
    # values as read from a real untouched curve on mainnet (Nov 8)
    d = pc.decode_curve_account(_account(1073000000000000, 30000000000, 793100000000000, 0))
    assert d["v_tokens"] == 1_073_000_000 and d["v_sol"] == 30.0 and d["complete"] is False
    assert d["nonstandard"] is False and pc.tokens_sold_pct(d["v_tokens"]) == 0.0
    odd = pc.decode_curve_account(_account(1073000000000000, 6862754513821, 793100000000000, 0))
    assert odd["nonstandard"] is True and round(odd["initial_v_sol"], 2) == 6862.75
    assert pc.decode_curve_account(b"short") is None


def test_curve_address_is_the_pump_pda():
    from solders.pubkey import Pubkey
    mint = "FxzofcdZgjDpaswyJPWKJ3momfdq3AbbiuW8iZSE2VrJ"
    expect, _ = Pubkey.find_program_address([b"bonding-curve", bytes(Pubkey.from_string(mint))],
                                            Pubkey.from_string(pc.PUMP_PROGRAM))
    assert pc.curve_address(mint) == str(expect)
    assert pc.curve_address("not-a-mint") is None
