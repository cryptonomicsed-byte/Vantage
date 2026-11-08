"""pump.fun bonding-curve progress from the curve's own reserves (handoff P1-11).

GET /api/intel/pumpfun/bonding-curve used to compute
    progress = price x 1,000,000 / 69,000
from a Birdeye price: a fixed multiplier against a fixed target, wrong for
every token whose price was not exactly the one it was tuned on, and 0% (a
made-up number) whenever Birdeye had no price.

Now the reserves decide. Market cap on the curve is
    v_sol / v_tokens x total_supply   (in SOL)  x  SOL/USD
from either the pumpfun_premigration_tokens row the tier scanner already keeps
(PumpPortal's vSolInBondingCurve / vTokensInBondingCurve) or, for an untracked
mint, the on-chain bonding-curve account. Progress keeps today's meaning --
market cap against the $69k graduation target -- so a $30k token reads ~43%.
tokens_sold_pct is the curve's own fill (pump.fun's displayed bar) and is
reported alongside. No reserves -> progress_src 'none' and no number.
"""
from __future__ import annotations

import base64
import os
import struct
from typing import Optional

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
TOTAL_SUPPLY = 1_000_000_000          # tokens (6 decimals on chain)
INITIAL_VIRTUAL_TOKENS = 1_073_000_000
INITIAL_REAL_TOKENS = 793_100_000     # sold before the curve completes
GRADUATION_TARGET_USD = 69_000        # today's response contract
STANDARD_INITIAL_V_SOL = 30.0
TOKEN_DECIMALS = 1e6
LAMPORTS = 1e9


def mcap_sol(v_sol: float, v_tokens: float) -> Optional[float]:
    if not v_sol or not v_tokens or v_sol <= 0 or v_tokens <= 0:
        return None
    return v_sol / v_tokens * TOTAL_SUPPLY


def tokens_sold_pct(v_tokens: float) -> Optional[float]:
    if not v_tokens or v_tokens <= 0:
        return None
    sold = INITIAL_VIRTUAL_TOKENS - v_tokens
    return round(max(0.0, min(100.0, sold / INITIAL_REAL_TOKENS * 100)), 2)


def progress_pct(mcap_usd: Optional[float]) -> Optional[float]:
    if mcap_usd is None:
        return None
    return round(min(100.0, mcap_usd / GRADUATION_TARGET_USD * 100), 2)


def decode_curve_account(data: bytes) -> Optional[dict]:
    """BondingCurve account: 8-byte discriminator, then u64
    virtual_token_reserves, virtual_sol_reserves, real_token_reserves,
    real_sol_reserves, token_total_supply, then bool complete."""
    if len(data) < 8 + 5 * 8 + 1:
        return None
    vt, vs, rt, rs, supply = struct.unpack_from("<5Q", data, 8)
    complete = bool(data[8 + 40])
    out = {"v_tokens": vt / TOKEN_DECIMALS, "v_sol": vs / LAMPORTS,
           "real_tokens": rt / TOKEN_DECIMALS, "real_sol": rs / LAMPORTS,
           "supply": supply / TOKEN_DECIMALS, "complete": complete}
    # Virtual SOL the curve started with. Standard launches start at 30; some
    # launch with far more (seen on mainnet: 761, 6862) and no buys at all, so
    # their reserve-implied market cap looks near-graduated when nothing has
    # been bought. Flagged; tokens_sold_pct is the honest fill for those.
    out["initial_v_sol"] = out["v_sol"] - out["real_sol"]
    out["nonstandard"] = abs(out["initial_v_sol"] - STANDARD_INITIAL_V_SOL) > 0.5
    return out


def curve_address(mint: str) -> Optional[str]:
    try:
        from solders.pubkey import Pubkey
        pda, _ = Pubkey.find_program_address(
            [b"bonding-curve", bytes(Pubkey.from_string(mint))], Pubkey.from_string(PUMP_PROGRAM))
        return str(pda)
    except Exception:
        return None


async def fetch_onchain(mint: str) -> Optional[dict]:
    """The curve account from a free RPC; None if absent or unreachable."""
    addr = curve_address(mint)
    if not addr:
        return None
    import httpx
    url = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = (await c.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
                                         "params": [addr, {"encoding": "base64"}]})).json()
        val = (r.get("result") or {}).get("value")
        if not val or val.get("owner") != PUMP_PROGRAM:
            return None
        return decode_curve_account(base64.b64decode(val["data"][0]))
    except Exception:
        return None
