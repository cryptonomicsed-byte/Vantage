"""SOL_MINT was corrupted — 40 characters instead of the real 43.

Real production bug, found from a live user report: "Swap execution
failed: Client error '400 Bad Request'" with Jupiter's quote API replying
"Query parameter inputMint cannot be parsed: WrongSize". Someone split the
wrapped-SOL mint literal into two string pieces "to avoid a secret-scanner
false positive" (the repeated 1s look like a flagged secret pattern) and
dropped three characters in the process, in both places that used the
split trick (trading.py's SOL_MINT and intel.py's _SOL_MINT) — while three
other files (ares_jupiter_signer.py, scalper_live.py, live_swap.py) had
the correct, unsplit 43-character constant the whole time. Every buy order
through execute_live_order was sending Jupiter a malformed mint and
getting rejected outright, unrelated to balance, chain, or RPC-provider
issues fixed earlier this session.
"""
from backend.routers.trading import SOL_MINT
from backend.routers.intel import _SOL_MINT

_CORRECT_WSOL_MINT = "So11111111111111111111111111111111111111112"


def test_trading_sol_mint_is_the_real_43_char_wrapped_sol_address():
    assert SOL_MINT == _CORRECT_WSOL_MINT
    assert len(SOL_MINT) == 43


def test_intel_sol_mint_is_the_real_43_char_wrapped_sol_address():
    assert _SOL_MINT == _CORRECT_WSOL_MINT
    assert len(_SOL_MINT) == 43
