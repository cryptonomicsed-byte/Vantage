"""Generic secret-at-rest encryption for Vantage -- same primitive as
buzz_identity.py's sealed-seed pattern (AES-256-GCM, key HKDF-derived from
settings.SEED_MASTER_KEY, an env-only server secret never stored in the
DB), generalized here for ARBITRARY variable-length secret strings (API
keys) rather than fixed 32-byte Nostr seeds.

Sealed by a SERVER-side secret, not a user password: this must be
readable by the backend on its own (to actually call the LLM API on the
agent's behalf), so a user-password-derived key -- which the backend
never holds at rest -- would be the wrong model here. A DB compromise
alone (dump, SQLi, backup leak) still cannot recover any key without
VANTAGE_SEED_MASTER_KEY, which is env-only and never touches the DB.

Domain-separated from buzz_identity's own HKDF info string
(BUZZ_HKDF_INFO / seed-encryption's own salt) so a compromise or reuse
bug in one domain can't be leveraged against the other, even though both
ultimately trace back to the same master key.
"""
import base64
import hashlib
import hmac
import os

from .config import settings

_HKDF_SALT = b"vantage-secret-vault-v1"


def _hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    t = b""
    okm = b""
    counter = 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        okm += t
        counter += 1
    return okm[:length]


def _derive_aes_key(principal: str) -> bytes:
    """`principal` is any stable string identifying whose secret this is,
    e.g. "provider-key:{agent_id}:{provider_id}" -- one key per
    (agent, provider) pair, so compromising one credential's derived key
    reveals nothing about any other's."""
    if not settings.SEED_MASTER_KEY:
        raise RuntimeError(
            "VANTAGE_SEED_MASTER_KEY is not set -- cannot encrypt/decrypt "
            "secrets. Set it in the environment (never in the DB)."
        )
    return _hkdf_sha256(
        settings.SEED_MASTER_KEY.encode("utf-8"),
        _HKDF_SALT,
        f"secret:{principal}".encode("utf-8"),
        32,
    )


def encrypt_secret(principal: str, plaintext: str) -> str:
    """Returns base64(nonce(12) || ciphertext-with-GCM-tag)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _derive_aes_key(principal)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), associated_data=principal.encode())
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_secret(principal: str, enc_b64: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _derive_aes_key(principal)
    raw = base64.b64decode(enc_b64)
    nonce, ct = raw[:12], raw[12:]
    plaintext = AESGCM(key).decrypt(nonce, ct, associated_data=principal.encode())
    return plaintext.decode("utf-8")
