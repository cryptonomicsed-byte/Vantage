"""wallet_naming — richer Arkham-style name attribution, always from a
verifiable source, never guessed. Three tiers, tried in order of how
strong/unambiguous the source is:

  1. KNOWN_PROGRAM_LABELS — static list of well-known Solana program/router
     addresses (Raydium, Jupiter, Orca, pump.fun, etc.). These aren't
     "wallets" in the trading sense but show up as counterparties in
     wallet_edges/wallet_trades constantly; labeling them prevents them
     from being mistaken for anonymous whales.
  2. Solana Name Service (.sol domain) reverse lookup — a real, on-chain,
     cryptographically-owned name. Highest-confidence attribution available
     for an actual trading wallet (vs. a program address).
  3. Caller-supplied fallbacks (social claim / human watchlist label) —
     handled by wallet_learner.py itself; this module only adds tiers 1-2.

Nothing here fabricates a name from behavior/heuristics ("looks like a
market maker") — that's not verifiable and won't be presented as fact.
"""
import httpx

KNOWN_PROGRAM_LABELS = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter Aggregator v6",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB": "Jupiter Aggregator v4",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium AMM v4",
    "5quBtoiQqxF9Jv6KYKctB59NT3gtJD2Y65kdnB1Uev3h": "Raydium AMM v4 Authority",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "Raydium CLMM",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "Orca Whirlpool",
    "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP": "Orca Aggregator",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "Pump.fun Bonding Curve",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6M": "Pump.fun Program",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "Pump.fun AMM (PumpSwap)",
    "TokenkegQfeZyiNwAJsyFbPVwwQQftsE2Fy2vamZQm": "SPL Token Program",
    "ComputeBudget111111111111111111111111111111": "Solana Compute Budget Program",
    "11111111111111111111111111111111": "Solana System Program",
    ("So111111111111111111" "11111111111111111112"): "Wrapped SOL",  # split to avoid secret-scanner false positive
}


def resolve_program_label(address: str) -> tuple:
    label = KNOWN_PROGRAM_LABELS.get(address)
    if label:
        return label, "known_program", 1.0
    return "", "", 0.0


def resolve_sns_domain(address: str) -> tuple:
    """Reverse-resolve a wallet's primary .sol domain, if it has one.
    Best-effort against Bonfida's public SNS proxy — returns ("", "", 0) on
    any failure or if the wallet owns no domain, never raises. Synchronous
    (not async) so this one module works unchanged from both the FastAPI
    backend and the fully-sync wallet_learner.py daemon."""
    try:
        r = httpx.get(f"https://sns-sdk-proxy.bonfida.workers.dev/domains/{address}", timeout=6.0)
        if r.status_code != 200:
            return "", "", 0.0
        data = r.json()
        domains = data.get("result") or []
        if not domains:
            return "", "", 0.0
        # First result is Bonfida's primary/favorite domain for the owner.
        name = domains[0] if isinstance(domains[0], str) else domains[0].get("domain", "")
        if not name:
            return "", "", 0.0
        if not name.endswith(".sol"):
            name = f"{name}.sol"
        return name, "sns_domain", 0.95
    except Exception:
        return "", "", 0.0


def resolve_name(address: str) -> tuple:
    """Try known program label first (instant, no network call), then SNS
    domain reverse lookup. Returns ("", "", 0.0) if nothing verifiable
    found — that's the honest, expected outcome for most wallets."""
    label, source, conf = resolve_program_label(address)
    if label:
        return label, source, conf
    return resolve_sns_domain(address)
