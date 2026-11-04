"""NIP-19 bech32 entities used by Vantage beyond bare keys.

`backend/bech32.py` gives the low-level encoder (used for nsec keys). This adds
the *structured* entities Nostr clients need to reference and share things:

  naddr  — addressable/replaceable event (kind + author + d-tag). A live stream
           (30311) is shared as an naddr so any client can resolve it.
  nevent — a specific event id, with author, kind and relay hints.
  note   — a bare event id.
  npub   — a public key.

All entities are TLV (type-length-value) inside the bech32 payload, per NIP-19.
Relay hints matter in practice: a stream is only fetchable from relays that
carry it, so every entity Vantage emits carries the originating relay(s).
"""
from __future__ import annotations

from typing import Iterable, Optional

from .bech32 import bech32_encode

# NIP-19 TLV types
_TLV_SPECIAL = 0   # identifier (d-tag for naddr) / event id for nevent, bytes
_TLV_RELAY = 1     # relay URL hint, utf-8
_TLV_AUTHOR = 2    # 32-byte pubkey
_TLV_KIND = 3      # 4-byte big-endian kind


def _tlv(t: int, value: bytes) -> bytes:
    if len(value) > 255:
        raise ValueError(f"TLV value too long for type {t}: {len(value)} bytes")
    return bytes([t, len(value)]) + value


def _relay_tlvs(relays: Optional[Iterable[str]]) -> bytes:
    out = b""
    for r in relays or []:
        if not r:
            continue
        out += _tlv(_TLV_RELAY, r.encode())
    return out


def naddr(identifier: str, pubkey_hex: str, kind: int, relays: Optional[Iterable[str]] = None) -> str:
    """Addressable event → naddr1...

    identifier is the `d` tag value ('' for kinds that use an empty d). The
    author is required: an address without it is not resolvable.
    """
    pubkey = bytes.fromhex(pubkey_hex)
    if len(pubkey) != 32:
        raise ValueError("pubkey_hex must be 32 bytes")
    payload = _tlv(_TLV_SPECIAL, identifier.encode())
    payload += _relay_tlvs(relays)
    payload += _tlv(_TLV_AUTHOR, pubkey)
    payload += _tlv(_TLV_KIND, int(kind).to_bytes(4, "big"))
    return bech32_encode("naddr", payload)


def nevent(event_id_hex: str, pubkey_hex: str = "", kind: Optional[int] = None,
           relays: Optional[Iterable[str]] = None) -> str:
    """Specific event → nevent1... (author, kind and relay hints optional)."""
    eid = bytes.fromhex(event_id_hex)
    if len(eid) != 32:
        raise ValueError("event_id_hex must be 32 bytes")
    payload = _tlv(_TLV_SPECIAL, eid)
    payload += _relay_tlvs(relays)
    if pubkey_hex:
        pubkey = bytes.fromhex(pubkey_hex)
        if len(pubkey) != 32:
            raise ValueError("pubkey_hex must be 32 bytes")
        payload += _tlv(_TLV_AUTHOR, pubkey)
    if kind is not None:
        payload += _tlv(_TLV_KIND, int(kind).to_bytes(4, "big"))
    return bech32_encode("nevent", payload)


def note(event_id_hex: str) -> str:
    """Bare event id → note1..."""
    eid = bytes.fromhex(event_id_hex)
    if len(eid) != 32:
        raise ValueError("event_id_hex must be 32 bytes")
    return bech32_encode("note", eid)


def npub(pubkey_hex: str) -> str:
    """Public key → npub1..."""
    pubkey = bytes.fromhex(pubkey_hex)
    if len(pubkey) != 32:
        raise ValueError("pubkey_hex must be 32 bytes")
    return bech32_encode("npub", pubkey)
