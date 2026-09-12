"""Mycelium trace pseudonymization (P0-5).

Real wallet addresses and agent IDs MUST NOT appear in Mycelium traces —
Mycelium miners run in a less-trusted context and the unauthenticated
localhost:8811 endpoint has no auth barrier.

Pseudonymization scheme:
  wallet address → "w_{hmac8}"   (8-char hex HMAC-SHA256 prefix)
  user/agent id  → "u_{hmac8}"
  any other PII  → "x_{hmac8}"

The HMAC key is MYCELIUM_HMAC_KEY env var (or a stable default for dev).
Production MUST set a random 32-byte hex key.

Reversal: intentionally impossible without the key.  The key itself is
never stored in Mycelium — only in Vantage's own env.  To look up a real
address from a slug, query Vantage directly (slug lookup endpoint TBD).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re

_DEV_KEY = "00" * 32   # development default — rotate in production
_HMAC_KEY = bytes.fromhex(os.environ.get("MYCELIUM_HMAC_KEY", _DEV_KEY))

_WALLET_RE = re.compile(r"^(0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$")


def _slug(prefix: str, value: str) -> str:
    digest = hmac.new(_HMAC_KEY, value.encode(), hashlib.sha256).hexdigest()
    return f"{prefix}_{digest[:8]}"


def pseudonymize_wallet(address: str) -> str:
    """Return a stable, opaque slug for a wallet address."""
    if not address:
        return "w_unknown"
    return _slug("w", address.lower())


def pseudonymize_agent(agent_id: str) -> str:
    """Return a stable, opaque slug for an agent or user ID."""
    if not agent_id:
        return "u_unknown"
    return _slug("u", agent_id)


def pseudonymize_pii(value: str) -> str:
    """Generic PII pseudonymization for arbitrary sensitive strings."""
    if not value:
        return "x_unknown"
    return _slug("x", value)


def scrub_payload(payload: dict) -> dict:
    """Recursively replace any value that looks like a wallet address.

    Keys named wallet_address, address, wallet, from_address, to_address,
    or whose values match the wallet regex, are replaced with slugs.

    Conservative: only replaces known-PII keys + obvious wallet patterns.
    Does NOT recurse into nested dicts/lists to avoid blowing up on large
    arbitrary payloads.  Call explicitly on each known-PII key instead.
    """
    WALLET_KEYS = {
        "wallet_address", "address", "wallet",
        "from_address", "to_address", "owner", "recipient",
    }
    result = {}
    for k, v in payload.items():
        if k in WALLET_KEYS and isinstance(v, str):
            result[k] = pseudonymize_wallet(v)
        elif k in ("agent_id", "user_id", "principal_id") and isinstance(v, str):
            result[k] = pseudonymize_agent(v)
        elif isinstance(v, str) and _WALLET_RE.match(v):
            result[k] = pseudonymize_wallet(v)
        else:
            result[k] = v
    return result
