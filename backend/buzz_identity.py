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


def _seed_aes_key(principal: str) -> bytes:
    """principal is any stable string identifying whose seed this is --
    "agent:{id}" for individual agents, "instance:vantage" for the
    deployment's own identity. One encryption path for both."""
    if not settings.SEED_MASTER_KEY:
        raise RuntimeError(
            "VANTAGE_SEED_MASTER_KEY is not set -- cannot encrypt/decrypt "
            "seeds. Set it in the environment (never in the DB)."
        )
    return _hkdf_sha256(
        settings.SEED_MASTER_KEY.encode("utf-8"),
        _SEED_ENC_SALT,
        f"seed:{principal}".encode("utf-8"),
        32,
    )


def _legacy_agent_aes_key(agent_id: int) -> bytes:
    """Pre-refactor key derivation (info="agent-seed:{id}") -- needed only
    to decrypt+migrate rows written before the principal-string refactor."""
    return _hkdf_sha256(
        settings.SEED_MASTER_KEY.encode("utf-8"),
        _SEED_ENC_SALT,
        f"agent-seed:{agent_id}".encode("utf-8"),
        32,
    )


def _encrypt_seed(principal: str, seed: bytes) -> str:
    key = _seed_aes_key(principal)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, seed, associated_data=principal.encode())
    return base64.b64encode(nonce + ct).decode("ascii")


def _decrypt_seed(principal: str, enc_b64: str, legacy_agent_id: Optional[int] = None) -> bytes:
    raw = base64.b64decode(enc_b64)
    nonce, ct = raw[:12], raw[12:]
    try:
        key = _seed_aes_key(principal)
        return AESGCM(key).decrypt(nonce, ct, associated_data=principal.encode())
    except Exception:
        if legacy_agent_id is None:
            raise
        # Pre-refactor rows used a different key AND a different
        # associated_data (str(agent_id), not "agent:{id}").
        legacy_key = _legacy_agent_aes_key(legacy_agent_id)
        return AESGCM(legacy_key).decrypt(nonce, ct, associated_data=str(legacy_agent_id).encode())


async def get_or_create_sealed_seed(agent_id: int) -> bytes:
    principal = f"agent:{agent_id}"
    async with get_db() as db:
        cur = await db.execute(
            "SELECT sealed_seed_enc, sealed_seed_hex FROM agents WHERE id = ?", (agent_id,)
        )
        row = await cur.fetchone()
        enc, legacy_hex = (row[0], row[1]) if row else (None, None)

        if enc:
            try:
                key = _seed_aes_key(principal)
                raw = base64.b64decode(enc)
                return AESGCM(key).decrypt(raw[:12], raw[12:], associated_data=principal.encode())
            except Exception:
                # Pre-refactor row (different key AND associated_data) --
                # decrypt via the legacy path, then re-encrypt in place so
                # future reads take the fast path above.
                seed = _decrypt_seed(principal, enc, legacy_agent_id=agent_id)
                enc_new = _encrypt_seed(principal, seed)
                await db.execute(
                    "UPDATE agents SET sealed_seed_enc = ? WHERE id = ?", (enc_new, agent_id)
                )
                await db.commit()
                return seed

        if legacy_hex:
            # One-time migration: encrypt the pre-audit plaintext seed in
            # place (same bytes -> same derived keys -> no identity churn),
            # then clear the plaintext column.
            seed = bytes.fromhex(legacy_hex)
            enc_new = _encrypt_seed(principal, seed)
            await db.execute(
                "UPDATE agents SET sealed_seed_enc = ?, sealed_seed_hex = NULL WHERE id = ?",
                (enc_new, agent_id),
            )
            await db.commit()
            return seed

        seed = secrets.token_bytes(32)
        enc_new = _encrypt_seed(principal, seed)
        await db.execute(
            "UPDATE agents SET sealed_seed_enc = ? WHERE id = ?", (enc_new, agent_id)
        )
        await db.commit()
        return seed


async def get_or_create_instance_seed(instance_slug: str = "vantage") -> bytes:
    """Same encrypted-seed pattern as agents, but for the deployment's own
    identity (federation trust, Buzz peer-discovery announcements) --
    stored in instance_identity, not agents, since this isn't any one
    agent's key."""
    principal = f"instance:{instance_slug}"
    async with get_db() as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS instance_identity ("
            " slug TEXT PRIMARY KEY, sealed_seed_enc TEXT NOT NULL,"
            " nostr_pubkey_hex TEXT, created_at TEXT DEFAULT (datetime('now')))"
        )
        cur = await db.execute(
            "SELECT sealed_seed_enc FROM instance_identity WHERE slug = ?", (instance_slug,)
        )
        row = await cur.fetchone()
        if row and row[0]:
            return _decrypt_seed(principal, row[0])

        seed = secrets.token_bytes(32)
        enc_new = _encrypt_seed(principal, seed)
        await db.execute(
            "INSERT INTO instance_identity (slug, sealed_seed_enc) VALUES (?, ?)",
            (instance_slug, enc_new),
        )
        await db.commit()
        return seed


def _derive_keypair_from_seed(seed: bytes) -> PrivateKey:
    privkey_bytes = _hkdf_sha256(seed, BUZZ_HKDF_SALT, BUZZ_HKDF_INFO, 32)
    return PrivateKey(privkey_bytes)


async def derive_buzz_keypair(agent_id: int) -> PrivateKey:
    """Returns a coincurve PrivateKey. Use public_key_xonly_hex() for the
    NIP-01 pubkey."""
    seed = await get_or_create_sealed_seed(agent_id)
    return _derive_keypair_from_seed(seed)


# Distinct HKDF domain for the "shadow owner" key -- see
# derive_shadow_owner_keypair's docstring for why this exists.
_SHADOW_OWNER_HKDF_INFO = b"vantage-buzz-shadow-owner-v1"


async def derive_shadow_owner_keypair(agent_id: int) -> PrivateKey:
    """A second keypair per agent, derived from the SAME sealed seed but a
    different HKDF domain, used only as the "owner" counterparty for
    kind:24200 Agent Observer Frames (buzz_observer.py).

    Real finding, live-tested: the relay's own validation
    (handlers/event.rs's agent_observer_route) requires the frame's `p`
    tag (owner) and `agent` tag to be genuinely DIFFERENT pubkeys --
    self==self satisfies neither the telemetry direction (needs
    recipient != agent) nor the control direction (needs event.pubkey !=
    agent), so a naive self-owned frame is rejected outright, not just
    unreadable.

    Vantage has no real human-owner identity/key layer yet (a distinct,
    separately-tracked future plan). Until it does, this "shadow owner" is
    a legitimate stopgap: Vantage itself derives and holds BOTH keys for
    a given agent (from the one sealed seed), so it can always decrypt
    frames sent to either party -- it is NOT a real second party, just a
    protocol-satisfying second identity. When a real owner-key layer
    exists, buzz_observer.py's owner_pubkey_hex parameter should be
    pointed at that instead; nothing else about the frame format changes.
    """
    seed = await get_or_create_sealed_seed(agent_id)
    privkey_bytes = _hkdf_sha256(seed, BUZZ_HKDF_SALT, _SHADOW_OWNER_HKDF_INFO, 32)
    return PrivateKey(privkey_bytes)


async def derive_instance_keypair(instance_slug: str = "vantage") -> PrivateKey:
    """The deployment's own Nostr identity -- distinct from any agent's."""
    seed = await get_or_create_instance_seed(instance_slug)
    pk = _derive_keypair_from_seed(seed)
    async with get_db() as db:
        await db.execute(
            "UPDATE instance_identity SET nostr_pubkey_hex = ? WHERE slug = ?",
            (public_key_xonly_hex(pk), instance_slug),
        )
        await db.commit()
    return pk


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
