"""Nostr identity for Vantage agents.

Re-exports from buzz_identity with NOSTR_ aliases added.
"""
from ..buzz_identity import (
    BUZZ_HKDF_INFO,
    BUZZ_HKDF_SALT,
    _hkdf_sha256,
    _seed_aes_key,
    _encrypt_seed,
    _decrypt_seed,
    get_or_create_sealed_seed,
    get_or_create_instance_seed,
    _derive_keypair_from_seed,
    derive_buzz_keypair,
    get_or_create_human_sealed_seed,
    derive_human_buzz_keypair,
    derive_shadow_owner_keypair,
    derive_instance_keypair,
    public_key_xonly_hex,
    store_nostr_pubkey,
    sign_event_id,
    get_owner_attestation_tag,
)

# NOSTR_ aliases for forward-compat with the naming wave
NOSTR_HKDF_INFO = BUZZ_HKDF_INFO
NOSTR_HKDF_SALT = BUZZ_HKDF_SALT

derive_nostr_keypair = derive_buzz_keypair

__all__ = [
    "NOSTR_HKDF_INFO",
    "NOSTR_HKDF_SALT",
    "BUZZ_HKDF_INFO",
    "BUZZ_HKDF_SALT",
    "derive_nostr_keypair",
    "derive_buzz_keypair",
    "derive_instance_keypair",
    "public_key_xonly_hex",
    "sign_event_id",
    "get_owner_attestation_tag",
    "get_or_create_sealed_seed",
    "get_or_create_instance_seed",
]
