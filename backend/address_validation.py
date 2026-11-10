"""Per-chain address / handle validation for the Add Tracker preview.

Pure functions, no network: each returns {"valid": bool, "reason": str, ...}.
The ingest regexes in routers/intel.py only check the alphabet and length; a
mistyped base58 character or a bad EIP-55 checksum used to be accepted and
silently tracked forever. These decode the address properly.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_IDX = {c: i for i, c in enumerate(_B58)}


def b58decode(s: str) -> Optional[bytes]:
    n = 0
    for ch in s:
        if ch not in _B58_IDX:
            return None
        n = n * 58 + _B58_IDX[ch]
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + body


def validate_solana(addr: str) -> dict:
    a = (addr or "").strip()
    if not 32 <= len(a) <= 44:
        return {"valid": False, "reason": f"Solana addresses are 32-44 base58 characters (got {len(a)})"}
    bad = sorted({c for c in a if c not in _B58_IDX})
    if bad:
        return {"valid": False, "reason": f"not base58: contains {''.join(bad)!r} (0, O, I and l are never used)"}
    raw = b58decode(a)
    if raw is None or len(raw) != 32:
        return {"valid": False, "reason": f"decodes to {len(raw or b'')} bytes, a Solana public key is 32"}
    return {"valid": True, "reason": "valid 32-byte base58 public key", "normalized": a}


def _keccak256(data: bytes) -> Optional[bytes]:
    try:
        from eth_hash.auto import keccak  # type: ignore
        return keccak(data)
    except Exception:  # noqa: BLE001
        pass
    try:
        from Crypto.Hash import keccak as _k  # type: ignore
        h = _k.new(digest_bits=256)
        h.update(data)
        return h.digest()
    except Exception:  # noqa: BLE001
        return None


def eip55(addr_hex40: str) -> Optional[str]:
    """Checksummed form of a 40-hex address, or None if keccak is unavailable."""
    lower = addr_hex40.lower()
    h = _keccak256(lower.encode())
    if h is None:
        return None
    hx = h.hex()
    return "0x" + "".join(c.upper() if c.isalpha() and int(hx[i], 16) >= 8 else c for i, c in enumerate(lower))


def validate_evm(addr: str) -> dict:
    a = (addr or "").strip()
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", a):
        return {"valid": False, "reason": "EVM addresses are 0x followed by 40 hex characters"}
    body = a[2:]
    checksummed = eip55(body)
    if body.islower() or body.isupper():
        return {"valid": True, "reason": "valid hex address (no checksum casing to verify)",
                "normalized": checksummed or a.lower(), "checksum": "absent"}
    if checksummed is None:
        return {"valid": True, "reason": "valid hex; EIP-55 checksum not verified (no keccak available)",
                "normalized": a, "checksum": "unverified"}
    if checksummed != a:
        return {"valid": False, "reason": "EIP-55 checksum mismatch -- a character was probably mistyped",
                "expected": checksummed, "checksum": "bad"}
    return {"valid": True, "reason": "valid EIP-55 checksummed address", "normalized": a, "checksum": "ok"}


_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _bech32_polymod(values) -> int:
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if ((b >> i) & 1) else 0
    return chk


def validate_bitcoin(addr: str) -> dict:
    a = (addr or "").strip()
    low = a.lower()
    if low.startswith(("bc1", "tb1")):
        if a != low and a != a.upper():
            return {"valid": False, "reason": "bech32 addresses must not mix upper and lower case"}
        hrp, _, data = low.rpartition("1")
        if not data or any(c not in _BECH32 for c in data) or len(data) < 6:
            return {"valid": False, "reason": "invalid bech32 characters"}
        vals = [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp] + [_BECH32.index(c) for c in data]
        pm = _bech32_polymod(vals)
        if pm not in (1, 0x2BC830A3):      # bech32 (v0) / bech32m (v1+)
            return {"valid": False, "reason": "bech32 checksum mismatch -- a character was probably mistyped"}
        if hrp != "bc":
            return {"valid": False, "reason": "testnet address (tb1...), not mainnet"}
        return {"valid": True, "reason": "valid bech32 mainnet address", "normalized": low}
    raw = b58decode(a)
    if raw is None or len(raw) != 25:
        return {"valid": False, "reason": "not a valid base58check or bech32 Bitcoin address"}
    if hashlib.sha256(hashlib.sha256(raw[:-4]).digest()).digest()[:4] != raw[-4:]:
        return {"valid": False, "reason": "base58check checksum mismatch -- a character was probably mistyped"}
    if raw[0] not in (0x00, 0x05):
        return {"valid": False, "reason": "not a mainnet P2PKH/P2SH version byte"}
    return {"valid": True, "reason": "valid base58check mainnet address", "normalized": a}


def validate_handle(platform: str, handle: str) -> dict:
    h = (handle or "").strip().lstrip("@")
    if platform == "twitter":
        if re.fullmatch(r"[A-Za-z0-9_]{1,15}", h):
            return {"valid": True, "reason": "valid X/Twitter username", "normalized": h.lower()}
        return {"valid": False, "reason": "X/Twitter usernames are 1-15 letters, digits or _"}
    if platform == "telegram":
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", h) and not h.endswith("_"):
            return {"valid": True, "reason": "valid Telegram username", "normalized": h.lower()}
        return {"valid": False, "reason": "Telegram usernames are 5-32 chars, start with a letter, no trailing _"}
    return {"valid": False, "reason": f"unknown platform {platform!r}"}


def looks_like_bitcoin(s: str) -> bool:
    s = (s or "").strip()
    return bool(re.fullmatch(r"(bc1|BC1)[0-9A-Za-z]{11,87}", s) or re.fullmatch(r"[13][1-9A-HJ-NP-Za-km-z]{25,34}", s))
