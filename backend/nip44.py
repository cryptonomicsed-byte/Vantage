"""NIP-44 v2 encryption -- pure Python, byte-for-byte matched against the
Rust reference implementation (rust-nostr's nostr::nips::nip44::v2), which
is what buzz-relay's NIP-AB pairing implementation actually uses under the
hood. Needed because no Python NIP-44 library is installed and NIP-AB
device pairing (nostrpair://) requires it for the encrypted payload channel.

Spec: https://github.com/nostr-protocol/nips/blob/master/44.md
Verified against the official nip44.vectors.json test vectors (same file
rust-nostr's own test suite uses) -- see backend/tests/test_nip44.py.
"""
import base64
import hashlib
import hmac
import math
import os

from coincurve import PrivateKey, PublicKey as CoincurvePublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

_MIN_PLAINTEXT_LEN = 1
_MAX_PLAINTEXT_LEN = 65536 - 128


class Nip44Error(Exception):
    pass


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    t = b""
    okm = b""
    counter = 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        okm += t
        counter += 1
    return okm[:length]


def _ecdh_x_only(privkey: PrivateKey, pubkey_xonly_hex: str) -> bytes:
    """Unhashed x-coordinate of privkey * pubkey, per BIP-340 x-only pubkeys
    (secp256k1 has two possible y for each x -- Nostr/NIP-44 always uses the
    even-y point, i.e. prefix 0x02, matching how rust-nostr's
    generate_shared_key treats x-only keys)."""
    full_pub = CoincurvePublicKey(b"\x02" + bytes.fromhex(pubkey_xonly_hex))
    shared_point = full_pub.multiply(privkey.secret, update=False)
    return shared_point.format(compressed=True)[1:]


def get_conversation_key(privkey: PrivateKey, pubkey_xonly_hex: str) -> bytes:
    shared_x = _ecdh_x_only(privkey, pubkey_xonly_hex)
    return _hkdf_extract(b"nip44-v2", shared_x)


def _calc_padded_len(unpadded_len: int) -> int:
    if unpadded_len <= 32:
        return 32
    next_power = 1 << ((unpadded_len - 1).bit_length())
    chunk = 32 if next_power <= 256 else next_power // 8
    return chunk * (((unpadded_len - 1) // chunk) + 1)


def _pad(plaintext: bytes) -> bytes:
    length = len(plaintext)
    if length < _MIN_PLAINTEXT_LEN:
        raise Nip44Error("message empty")
    if length > _MAX_PLAINTEXT_LEN:
        raise Nip44Error("message too long")
    padded_len = _calc_padded_len(length)
    return length.to_bytes(2, "big") + plaintext + bytes(padded_len - length)


def _message_keys(conversation_key: bytes, nonce: bytes) -> tuple[bytes, bytes, bytes]:
    expanded = _hkdf_expand(conversation_key, nonce, 76)
    return expanded[0:32], expanded[32:44], expanded[44:76]


def _chacha20(key: bytes, nonce12: bytes, data: bytes) -> bytes:
    cipher = Cipher(algorithms.ChaCha20(key, b"\x00\x00\x00\x00" + nonce12), mode=None)
    encryptor = cipher.encryptor()
    return encryptor.update(data) + encryptor.finalize()


def encrypt(plaintext: str, conversation_key: bytes, nonce: bytes = None) -> str:
    nonce = nonce or os.urandom(32)
    enc_key, chacha_nonce, auth_key = _message_keys(conversation_key, nonce)
    padded = _pad(plaintext.encode("utf-8"))
    ciphertext = _chacha20(enc_key, chacha_nonce, padded)
    mac = hmac.new(auth_key, nonce + ciphertext, hashlib.sha256).digest()
    payload = b"\x02" + nonce + ciphertext + mac
    return base64.b64encode(payload).decode("ascii")


def decrypt(payload_b64: str, conversation_key: bytes) -> str:
    try:
        payload = base64.b64decode(payload_b64)
    except Exception:
        raise Nip44Error("invalid base64")
    if len(payload) < 1 + 32 + 32 or payload[0] != 2:
        raise Nip44Error("invalid payload / unsupported version")
    nonce = payload[1:33]
    ciphertext = payload[33:-32]
    mac = payload[-32:]
    enc_key, chacha_nonce, auth_key = _message_keys(conversation_key, nonce)
    expected_mac = hmac.new(auth_key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected_mac):
        raise Nip44Error("invalid hmac")
    buffer = _chacha20(enc_key, chacha_nonce, ciphertext)
    unpadded_len = int.from_bytes(buffer[0:2], "big")
    if len(buffer) < 2 + unpadded_len:
        raise Nip44Error("invalid padding")
    unpadded = buffer[2:2 + unpadded_len]
    if not unpadded:
        raise Nip44Error("message empty")
    if len(buffer) != 2 + _calc_padded_len(unpadded_len):
        raise Nip44Error("invalid padding")
    return unpadded.decode("utf-8")
