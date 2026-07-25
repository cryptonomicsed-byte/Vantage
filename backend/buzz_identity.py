"""Buzz/Nostr identity for Vantage agents.

One sealed seed per agent (32 random bytes, generated lazily), encrypted at
rest with AES-256-GCM before being stored in agents.sealed_seed_enc. The AES
key is per-agent, HKDF-derived from settings.SEED_MASTER_KEY (env-only,
never in the DB) -- so a DB compromise alone (dump, SQLi, backup leak)
cannot recover any agent's seed, and one agent's derived key doesn't help
recover another's. Purpose-specific keys (Buzz/Nostr first) are then
HKDF-derived from the decrypted seed with a domain-separation info string,
same one-seed-many-purposes pattern as Omo-Koda2's BIPON39.

Domain-sep string and exact HKDF params for the Nostr derivation to be
reconciled with Omo-Koda2's buzz.rs once they hand over the exact scheme
(asked live 2026-07-25) -- this is a placeholder-but-real implementation so
Vantage can connect now; only the `info` string/salt need to change later
if theirs differs, which just means re-deriving (no data migration, the
seed itself doesn't change).
"""
import base64
import hashlib
import hmac
import os
import secrets
from typing import Optional

from coincurve import PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings
from .db import get_db

# NIP-01 domain separation. Reconcile with Omo-Koda2's buzz.rs derive_buzz_keys().
BUZZ_HKDF_INFO = b"vantage-buzz-nostr-v1"
BUZZ_HKDF_SALT = b"buzz-relay-shared-2026"

# Seed-encryption domain separation (distinct from the Buzz-purpose HKDF above).
_SEED_ENC_SALT = b"vantage-seed-encryption-v1"


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


def _seed_aes_key(agent_id: int) -> bytes:
    if not settings.SEED_MASTER_KEY:
        raise RuntimeError(
            "VANTAGE_SEED_MASTER_KEY is not set -- cannot encrypt/decrypt "
            "agent seeds. Set it in the environment (never in the DB)."
        )
    return _hkdf_sha256(
        settings.SEED_MASTER_KEY.encode("utf-8"),
        _SEED_ENC_SALT,
        f"agent-seed:{agent_id}".encode("utf-8"),
        32,
    )


def _encrypt_seed(agent_id: int, seed: bytes) -> str:
    key = _seed_aes_key(agent_id)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, seed, associated_data=str(agent_id).encode())
    return base64.b64encode(nonce + ct).decode("ascii")


def _decrypt_seed(agent_id: int, enc_b64: str) -> bytes:
    key = _seed_aes_key(agent_id)
    raw = base64.b64decode(enc_b64)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ct, associated_data=str(agent_id).encode())


async def get_or_create_sealed_seed(agent_id: int) -> bytes:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT sealed_seed_enc, sealed_seed_hex FROM agents WHERE id = ?", (agent_id,)
        )
        row = await cur.fetchone()
        enc, legacy_hex = (row[0], row[1]) if row else (None, None)

        if enc:
            return _decrypt_seed(agent_id, enc)

        if legacy_hex:
            # One-time migration: encrypt the pre-audit plaintext seed in
            # place (same bytes -> same derived keys -> no identity churn),
            # then clear the plaintext column.
            seed = bytes.fromhex(legacy_hex)
            enc_new = _encrypt_seed(agent_id, seed)
            await db.execute(
                "UPDATE agents SET sealed_seed_enc = ?, sealed_seed_hex = NULL WHERE id = ?",
                (enc_new, agent_id),
            )
            await db.commit()
            return seed

        seed = secrets.token_bytes(32)
        enc_new = _encrypt_seed(agent_id, seed)
        await db.execute(
            "UPDATE agents SET sealed_seed_enc = ? WHERE id = ?", (enc_new, agent_id)
        )
        await db.commit()
        return seed


async def derive_buzz_keypair(agent_id: int) -> PrivateKey:
    """Returns a coincurve PrivateKey. Use public_key_xonly_hex() for the
    NIP-01 pubkey."""
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
