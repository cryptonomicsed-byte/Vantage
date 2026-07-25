"""Buzz/Nostr identity for Vantage agents.

One sealed seed per agent (32 random bytes, generated lazily, stored hex in
agents.sealed_seed_hex — never derived from api_key). Purpose-specific keys
are derived via HKDF-SHA256 with a domain-separation info string, same
one-seed-many-purposes pattern as Omo-Koda2's BIPON39/derive_buzz_keys().

Domain-sep string and exact HKDF params to be reconciled with Omo-Koda2's
buzz.rs once they hand over the exact scheme (asked live 2026-07-25) — this
is a placeholder-but-real implementation so Vantage can connect now; only the
`info` string and salt need to change later if theirs differs, which just
means re-deriving (no data migration, the seed itself doesn't change).
"""
import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

from coincurve import PrivateKey

from .db import get_db

# NIP-01 domain separation. Reconcile with Omo-Koda2's buzz.rs derive_buzz_keys().
BUZZ_HKDF_INFO = b"vantage-buzz-nostr-v1"
BUZZ_HKDF_SALT = b"buzz-relay-shared-2026"


def _hkdf_sha256(seed: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 HKDF-SHA256 (extract-then-expand), stdlib-only."""
    prk = hmac.new(salt, seed, hashlib.sha256).digest()
    t = b""
    okm = b""
    counter = 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        okm += t
        counter += 1
    return okm[:length]


async def get_or_create_sealed_seed(agent_id: int) -> bytes:
    async with get_db() as db:
        cur = await db.execute("SELECT sealed_seed_hex FROM agents WHERE id = ?", (agent_id,))
        row = await cur.fetchone()
        if row and row[0]:
            return bytes.fromhex(row[0])
        seed = secrets.token_bytes(32)
        await db.execute(
            "UPDATE agents SET sealed_seed_hex = ? WHERE id = ?", (seed.hex(), agent_id)
        )
        await db.commit()
        return seed


async def derive_buzz_keypair(agent_id: int) -> PrivateKey:
    """Returns a coincurve PrivateKey. .public_key.format() etc for pubkey;
    x-only pubkey (NIP-01) is the last 32 bytes of the uncompressed pubkey
    minus the prefix -- use public_key_xonly() helper below."""
    seed = await get_or_create_sealed_seed(agent_id)
    privkey_bytes = _hkdf_sha256(seed, BUZZ_HKDF_SALT, BUZZ_HKDF_INFO, 32)
    return PrivateKey(privkey_bytes)


def public_key_xonly_hex(pk: PrivateKey) -> str:
    """NIP-01 pubkeys are the x-only (BIP340) 32-byte x-coordinate, hex."""
    compressed = pk.public_key.format(compressed=True)  # 33 bytes: 0x02/0x03 + x
    return compressed[1:].hex()


async def store_nostr_pubkey(agent_id: int, pubkey_hex: str) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE agents SET nostr_pubkey_hex = ? WHERE id = ?", (pubkey_hex, agent_id)
        )
        await db.commit()


def sign_event_id(pk: PrivateKey, event_id_hex: str) -> str:
    """BIP340 schnorr sign over the 32-byte event id (already sha256 of the
    NIP-01 serialized array). aux_randomness defaults to os.urandom(32) inside
    coincurve's sign_schnorr."""
    sig = pk.sign_schnorr(bytes.fromhex(event_id_hex))
    return sig.hex()
