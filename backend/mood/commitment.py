"""Mood commitment — the record-not-restrain bond between an agent's private
affective state and its public receipts.

    commitment = sha256(pack(valence, arousal) + nonce).hex()

  · pack  = struct "<dd" — two little-endian float64, 16 bytes.
  · nonce = 16 fresh random bytes, generated per receipt and NOT stored.

What this buys, and what it deliberately does not:

  PROVABLE AFTER THE FACT — if the nonce is later produced (alongside the
  receipt), an auditor can confirm the committed (valence, arousal) pair is
  the one that was live when the receipt was written. Two receipts with the
  same revealed nonce were written in the same mood.

  NOT RECONSTRUCTIBLE — from the receipt alone, nobody can recover the
  state. The nonce is the only thing that could distinguish candidate
  states, and it isn't on the receipt. So the commitment is opaque to
  observers while remaining binding on the agent.

  ABSENT > FABRICATED — if the mood engine isn't running there is no
  commitment, and the field is null. We never invent a placeholder: a null
  is honest evidence that mood was not active, which is exactly what
  Zangbeto needs to flag once the flag flips on.
"""
from __future__ import annotations

import hashlib
import os
import struct
from typing import Optional, Tuple

_PACK = struct.Struct("<dd")
NONCE_BYTES = 16
HEX_LEN = 64


def make_with_nonce(valence: float, arousal: float,
                    nonce: Optional[bytes] = None) -> Tuple[str, bytes]:
    """Return (commitment_hex, nonce_bytes).

    The nonce is returned so the caller can reveal it later. It must NOT be
    written to the receipt — the receipt carries the hash only.
    """
    v, a = float(valence), float(arousal)
    if not -1.0 <= v <= 1.0:
        raise ValueError(f"valence out of range [-1, 1]: {v}")
    if not 0.0 <= a <= 1.0:
        raise ValueError(f"arousal out of range [0, 1]: {a}")
    n = nonce if nonce is not None else os.urandom(NONCE_BYTES)
    if len(n) != NONCE_BYTES:
        raise ValueError(f"nonce must be {NONCE_BYTES} bytes, got {len(n)}")
    return hashlib.sha256(_PACK.pack(v, a) + n).hexdigest(), n


def make(valence: float, arousal: float) -> str:
    """Commitment hex only — for attaching to a receipt."""
    return make_with_nonce(valence, arousal)[0]


def verify(valence: float, arousal: float, nonce: bytes, commitment: str) -> bool:
    """Recompute from a revealed (nonce, state) and compare. Constant-time-ish;
    used by auditors/tests, never on the write path."""
    import hmac
    recomputed, _ = make_with_nonce(valence, arousal, nonce)
    return hmac.compare_digest(recomputed, commitment or "")


def is_valid_format(commitment: Optional[str]) -> bool:
    """Cheap shape check — 64 lowercase hex chars, or null."""
    if commitment is None:
        return True
    return (
        isinstance(commitment, str)
        and len(commitment) == HEX_LEN
        and all(c in "0123456789abcdef" for c in commitment)
    )


def make_for_mood(mood, nonce: Optional[bytes] = None) -> Tuple[str, bytes]:
    """Convenience: commit an object exposing .valence and .arousal."""
    return make_with_nonce(mood.valence, mood.arousal, nonce)
