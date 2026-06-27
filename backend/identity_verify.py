"""Ed25519 identity verification for ọmọ Kọ́dà sovereign agents.

A sovereign agent proves control of the keypair it claims by signing its own
`agent_id` with its Ed25519 private key at birth. Vantage verifies that
signature against the submitted public key, binding the on-platform identity to
the key material rather than merely trusting a claimed string.

All inputs are hex strings. The public key is 32 bytes (64 hex chars) and the
signature is 64 bytes (128 hex chars). Any malformed input or bad signature
returns False — this function never raises.
"""
import binascii

try:
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    _NACL = True
except Exception:  # pragma: no cover - PyNaCl is a hard dependency
    _NACL = False


def verify_identity(public_key_hex: str, message: str, signature_hex: str) -> bool:
    """Return True iff `signature_hex` is a valid Ed25519 signature of
    `message` produced by the private key matching `public_key_hex`."""
    if not _NACL or not public_key_hex or not signature_hex:
        return False
    try:
        verify_key = VerifyKey(binascii.unhexlify(public_key_hex))
        verify_key.verify(message.encode("utf-8"), binascii.unhexlify(signature_hex))
        return True
    except (BadSignatureError, ValueError, binascii.Error):
        return False
