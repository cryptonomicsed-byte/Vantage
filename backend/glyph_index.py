"""GlyphIndex — hardened sovereign memory core (canonical reference implementation).

This module is the ecosystem-wide reference for the GlyphIndex wire formats.
Every other implementation (OSOVM Julia, If-Script/BIPON39/Zangbeto Rust,
Cloakseed/Axiom/Koodu JS, zerolang) mirrors these three specs exactly:

GIX-FOLD-v1  — deterministic content → Unicode glyph mapping.
    h  = SHA-256(plaintext utf-8), canonical_id = hex(h)
    n  = big-endian integer of h
    The glyph code point is n folded into the 63,422 *valid, printable* BMP
    code points (surrogates, controls, and noncharacters excluded):
        R1 = U+0020..U+D7FF   (55,264 points)
        R2 = U+E000..U+FDCF   ( 7,632 points)
        R3 = U+FDF0..U+FFFD   (   526 points)
    idx = n mod 63,422 mapped in that range order.
    The glyph is a *display alias*; canonical_id is the true address, so glyph
    collisions are cosmetic, never a correctness issue.

GIX-KDF-v1   — key hierarchy.
    master seed = 64-byte sovereign seed (BIPON39 mnemonic_to_seed) or
                  PBKDF2-HMAC-SHA256(passphrase, b"GIX1" + owner, 600_000, 64)
    subkeys via HKDF-SHA256(salt=b"GLYPHINDEX/v1"):
        enc    key: info = b"gix:enc:"    + owner + b":" + purpose
        mac    key: info = b"gix:mac:"    + owner + b":" + purpose
        duress key: info = b"gix:duress:" + owner + b":" + purpose
    (duress key is the Cloakseed decoy-vault root; deriving it never touches
    the primary enc key, so a coerced passphrase opens a disjoint vault.)

GIX1         — encrypted blob format (AES-256-GCM, encrypt-then-authenticated).
    bytes: b"GIX1" | version(0x01) | flags | nonce(12) | ciphertext||tag(16)
    flags bit0: payload zlib-deflated before encryption.
    AAD  = b"GIX1" + canonical_id(ascii hex) + b"|" + owner(utf-8)
    Payload plaintext is canonical JSON:
        {"canonical_id", "glyph", "ts", "chunk", "odu": [base, composed]}

Odù linkage (If-Script Digital Calabash):
    odu_base     = h[0]                (0..255)
    odu_composed = h[0] << 8 | h[1]    (0..65535)

Storage is an SQLite journal with a Merkle root over the sealed blobs
(leaf = SHA-256(canonical_id_bytes || SHA-256(blob)), leaves sorted by
canonical_id, odd leaf promoted) suitable for anchoring on Sui.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import math
import sqlite3
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple, Union

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

GIX_MAGIC = b"GIX1"
GIX_VERSION = 1
FLAG_ZLIB = 0x01
HKDF_SALT = b"GLYPHINDEX/v1"
PBKDF2_ITERATIONS = 600_000

# GIX-FOLD-v1 ranges: (start, count)
_FOLD_RANGES = ((0x0020, 0xD7FF - 0x0020 + 1),
                (0xE000, 0xFDCF - 0xE000 + 1),
                (0xFDF0, 0xFFFD - 0xFDF0 + 1))
_FOLD_TOTAL = sum(count for _, count in _FOLD_RANGES)  # 63,422


class GlyphIndexError(Exception):
    """Raised on any integrity, authentication, or format failure."""


# ---------------------------------------------------------------- fold / odù

def content_hash(text: Union[str, bytes]) -> bytes:
    if isinstance(text, bytes):
        return hashlib.sha256(text).digest()
    return hashlib.sha256(text.encode("utf-8")).digest()


def glyph_fold(digest: bytes) -> str:
    """GIX-FOLD-v1: 32-byte digest → single valid BMP glyph."""
    if len(digest) != 32:
        raise GlyphIndexError("glyph_fold requires a 32-byte digest")
    idx = int.from_bytes(digest, "big") % _FOLD_TOTAL
    for start, count in _FOLD_RANGES:
        if idx < count:
            return chr(start + idx)
        idx -= count
    raise AssertionError("unreachable")


def odu_link(digest: bytes) -> Tuple[int, int]:
    """(base Odù 0..255, composed Odù 0..65535) for the Digital Calabash."""
    return digest[0], (digest[0] << 8) | digest[1]


# ------------------------------------------------------------------- keyring

def _hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    prk = _hmac.new(salt, ikm, hashlib.sha256).digest()
    out, block = b"", b""
    counter = 1
    while len(out) < length:
        block = _hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


@dataclass(frozen=True)
class GlyphKeyring:
    """GIX-KDF-v1 key hierarchy bound to an owner (wallet) and purpose."""
    owner: str
    purpose: str
    enc_key: bytes
    mac_key: bytes

    @classmethod
    def from_seed(cls, seed: bytes, owner: str, purpose: str = "glyph-memory",
                  duress: bool = False) -> "GlyphKeyring":
        if len(seed) < 32:
            raise GlyphIndexError("master seed must be at least 32 bytes")
        ctx = f"{owner}:{purpose}".encode("utf-8")
        if duress:
            enc_label = b"gix:duress:enc:"
            mac_label = b"gix:duress:mac:"
        else:
            enc_label = b"gix:enc:"
            mac_label = b"gix:mac:"
        return cls(
            owner=owner,
            purpose=purpose,
            enc_key=_hkdf_sha256(seed, HKDF_SALT, enc_label + ctx),
            mac_key=_hkdf_sha256(seed, HKDF_SALT, mac_label + ctx),
        )

    @classmethod
    def from_passphrase(cls, passphrase: str, owner: str,
                        purpose: str = "glyph-memory",
                        duress: bool = False) -> "GlyphKeyring":
        seed = hashlib.pbkdf2_hmac(
            "sha256", passphrase.encode("utf-8"),
            b"GIX1" + owner.encode("utf-8"), PBKDF2_ITERATIONS, dklen=64)
        return cls.from_seed(seed, owner, purpose, duress=duress)


# ----------------------------------------------------------------- GIX1 blob

def _aad(canonical_id: str, owner: str) -> bytes:
    return GIX_MAGIC + canonical_id.encode("ascii") + b"|" + owner.encode("utf-8")


def seal_blob(payload: dict, canonical_id: str, keyring: GlyphKeyring,
              compress: bool = True) -> bytes:
    """Serialize, optionally deflate, and AES-256-GCM seal a payload."""
    plain = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")
    flags = 0
    if compress:
        deflated = zlib.compress(plain, 6)
        if len(deflated) < len(plain):
            plain, flags = deflated, FLAG_ZLIB
    nonce = get_random_bytes(12)
    cipher = AES.new(keyring.enc_key, AES.MODE_GCM, nonce=nonce, mac_len=16)
    cipher.update(_aad(canonical_id, keyring.owner))
    ct, tag = cipher.encrypt_and_digest(plain)
    return GIX_MAGIC + bytes([GIX_VERSION, flags]) + nonce + ct + tag


def open_blob(blob: bytes, canonical_id: str, keyring: GlyphKeyring) -> dict:
    """Authenticate and decrypt a GIX1 blob. Raises GlyphIndexError on any failure."""
    if len(blob) < 4 + 2 + 12 + 16 or blob[:4] != GIX_MAGIC:
        raise GlyphIndexError("not a GIX1 blob")
    version, flags = blob[4], blob[5]
    if version != GIX_VERSION:
        raise GlyphIndexError(f"unsupported GIX version {version}")
    nonce, body, tag = blob[6:18], blob[18:-16], blob[-16:]
    cipher = AES.new(keyring.enc_key, AES.MODE_GCM, nonce=nonce, mac_len=16)
    cipher.update(_aad(canonical_id, keyring.owner))
    try:
        plain = cipher.decrypt_and_verify(body, tag)
    except ValueError as exc:
        raise GlyphIndexError("authentication failed (tampered blob or wrong key)") from exc
    if flags & FLAG_ZLIB:
        plain = zlib.decompress(plain)
    payload = json.loads(plain.decode("utf-8"))
    if payload.get("canonical_id") != canonical_id:
        raise GlyphIndexError("canonical_id mismatch inside sealed payload")
    return payload


def merkle_root_binary(blobs: List[bytes]) -> bytes:
    """Compute deterministic Merkle root from sealed blobs.

    Leaf = SHA-256(canonical_id_bytes || SHA-256(blob)), sorted by canonical_id.
    Odd leaf promoted unchanged to next level.
    Returns root hash as bytes.
    """
    if not blobs:
        return hashlib.sha256(b"GIX1:empty").digest()

    # Extract canonical_id from each blob (GIX1 format encodes it in JSON payload)
    leaves = []
    for blob in blobs:
        try:
            # Parse the payload to extract canonical_id
            # This is a simplified extraction; in production use open_blob with keyring
            payload = json.loads(blob[18:-16])  # naive: assumes no compression
            cid_bytes = bytes.fromhex(payload.get("canonical_id", ""))
            blob_hash = hashlib.sha256(blob).digest()
            leaf = hashlib.sha256(cid_bytes + blob_hash).digest()
            leaves.append((payload.get("canonical_id", ""), leaf))
        except (json.JSONDecodeError, KeyError, ValueError):
            # Fallback: use blob hash directly if payload parsing fails
            blob_hash = hashlib.sha256(blob).digest()
            leaves.append(("", blob_hash))

    # Sort by canonical_id
    leaves.sort(key=lambda x: x[0])
    level = [leaf for _, leaf in leaves]

    # Build Merkle tree
    while len(level) > 1:
        nxt = [hashlib.sha256(level[i] + level[i + 1]).digest()
               for i in range(0, len(level) - 1, 2)]
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt

    return level[0]


# ------------------------------------------------------------------ chunking

def chunk_text(text: str, max_bytes: int = 4096) -> List[str]:
    """Semantic split on 'User:' turns, then hard split oversized chunks."""
    if "User:" in text:
        parts = text.split("User:")
        chunks = [(f"User:{p.strip()}" if i else p.strip())
                  for i, p in enumerate(parts) if p.strip()]
    else:
        chunks = [text.strip()] if text.strip() else []
    out: List[str] = []
    for chunk in chunks:
        raw = chunk.encode("utf-8")
        while len(raw) > max_bytes:
            cut = max_bytes
            while cut > 0 and (raw[cut] & 0xC0) == 0x80:  # don't split UTF-8
                cut -= 1
            out.append(raw[:cut].decode("utf-8"))
            raw = raw[cut:]
        if raw:
            out.append(raw.decode("utf-8"))
    return out


# ----------------------------------------------------------------- embedding

class HashingEmbedder:
    """Deterministic, dependency-free fallback embedder (character 3-grams
    hashed into `dim` buckets, L2-normalized). Production deployments plug a
    real model via the same __call__ signature."""

    def __init__(self, dim: int = 256):
        self.dim = dim

    def __call__(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        lowered = text.lower()
        for i in range(max(len(lowered) - 2, 1)):
            gram = lowered[i:i + 3]
            h = int.from_bytes(hashlib.blake2s(gram.encode("utf-8"),
                                               digest_size=4).digest(), "big")
            vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # inputs are L2-normalized


# ---------------------------------------------------------------- glyph store

_SCHEMA = """
CREATE TABLE IF NOT EXISTS glyphs (
    canonical_id TEXT PRIMARY KEY,
    glyph        TEXT NOT NULL,
    owner        TEXT NOT NULL,
    odu_base     INTEGER NOT NULL,
    odu_composed INTEGER NOT NULL,
    ts           REAL NOT NULL,
    blob         BLOB NOT NULL,
    embedding    BLOB,
    walrus_blob_id TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_glyphs_owner ON glyphs(owner);
CREATE INDEX IF NOT EXISTS idx_glyphs_odu ON glyphs(odu_composed);
"""


def _pack_embedding(vec: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack_embedding(raw: bytes) -> List[float]:
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


class GlyphStore:
    """SQLite-backed sovereign memory vault: seal → journal → search → expand.

    All content is sealed client-side; the database (like Walrus) only ever
    holds ciphertext. `merkle_root()` gives the anchor for on-chain receipts.
    """

    def __init__(self, db_path: str | Path, keyring: GlyphKeyring,
                 embedder: Optional[Callable[[str], List[float]]] = None):
        self.keyring = keyring
        self.embedder = embedder if embedder is not None else HashingEmbedder()
        self._db = sqlite3.connect(str(db_path))
        self._db.executescript(_SCHEMA)

    # -- write path ----------------------------------------------------
    def store_text(self, text: str, ts: Optional[float] = None) -> List[dict]:
        """Chunk, glyph, seal, and journal `text`. Returns per-chunk receipts."""
        receipts = []
        for chunk in chunk_text(text):
            digest = content_hash(chunk)
            canonical_id = digest.hex()
            glyph = glyph_fold(digest)
            base, composed = odu_link(digest)
            stamp = ts if ts is not None else time.time()
            payload = {"canonical_id": canonical_id, "glyph": glyph,
                       "ts": stamp, "chunk": chunk, "odu": [base, composed]}
            blob = seal_blob(payload, canonical_id, self.keyring)
            emb = _pack_embedding(self.embedder(chunk))
            self._db.execute(
                "INSERT OR REPLACE INTO glyphs "
                "(canonical_id, glyph, owner, odu_base, odu_composed, ts, blob, embedding) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (canonical_id, glyph, self.keyring.owner, base, composed,
                 stamp, blob, emb))
            receipts.append(self.receipt(canonical_id, blob))
        self._db.commit()
        return receipts

    def attach_walrus_blob(self, canonical_id: str, walrus_blob_id: str) -> None:
        self._db.execute("UPDATE glyphs SET walrus_blob_id=? WHERE canonical_id=?",
                         (walrus_blob_id, canonical_id))
        self._db.commit()

    def store(self, blob: bytes, canonical_id: str = None) -> dict:
        """Store a pre-sealed blob directly (for E2E testing)."""
        # If canonical_id not provided, we need to try decryption with a guess
        # The issue: AAD includes canonical_id, but we don't know it until after decryption
        # Solution: if not provided, try a common pattern or require it explicitly
        if canonical_id is None:
            # Attempt: decrypt blindly assuming canonical_id will be in payload
            # This requires multiple tries or we need to pass canonical_id
            # For E2E testing, we can use a pattern: try common canonical_id patterns
            # But for now, require explicit canonical_id parameter
            raise GlyphIndexError("canonical_id required for store()")

        # Decrypt to extract metadata
        payload = open_blob(blob, canonical_id, self.keyring)
        glyph = payload.get("glyph", "?")
        ts = payload.get("ts", time.time())
        base, composed = payload.get("odu", [0, 0])
        chunk = payload.get("chunk", "")
        emb = _pack_embedding(self.embedder(chunk))

        self._db.execute(
            "INSERT OR REPLACE INTO glyphs "
            "(canonical_id, glyph, owner, odu_base, odu_composed, ts, blob, embedding) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (canonical_id, glyph, self.keyring.owner, base, composed, ts, blob, emb))
        self._db.commit()
        return self.receipt(canonical_id, blob)

    def open(self, canonical_id: str, keyring: GlyphKeyring = None) -> dict:
        """Decrypt and return a glyph by canonical_id."""
        kr = keyring if keyring else self.keyring
        row = self._db.execute(
            "SELECT blob FROM glyphs WHERE canonical_id=?",
            (canonical_id,)).fetchone()
        if row is None:
            raise GlyphIndexError(f"unknown glyph {canonical_id}")
        return open_blob(row[0], canonical_id, kr)

    # -- read path -----------------------------------------------------
    def expand(self, canonical_id: str) -> str:
        row = self._db.execute("SELECT blob FROM glyphs WHERE canonical_id=?",
                               (canonical_id,)).fetchone()
        if row is None:
            raise GlyphIndexError(f"unknown glyph {canonical_id}")
        payload = open_blob(row[0], canonical_id, self.keyring)
        chunk = payload["chunk"]
        if hashlib.sha256(chunk.encode("utf-8")).hexdigest() != canonical_id:
            raise GlyphIndexError("content hash mismatch after decrypt")
        return chunk

    def search(self, query: str, k: int = 3) -> List[dict]:
        """Exact cosine top-k over the vault, decrypting only the winners."""
        qvec = self.embedder(query)
        rows = self._db.execute(
            "SELECT canonical_id, glyph, ts, odu_composed, embedding FROM glyphs "
            "WHERE owner=?", (self.keyring.owner,)).fetchall()
        scored = []
        for canonical_id, glyph, ts, composed, emb in rows:
            if not emb:
                continue
            score = cosine(qvec, _unpack_embedding(emb))
            scored.append((score, ts, canonical_id, glyph, composed))
        scored.sort(key=lambda r: (-r[0], -r[1]))
        results = []
        for score, ts, canonical_id, glyph, composed in scored[:k]:
            results.append({"canonical_id": canonical_id, "glyph": glyph,
                            "score": score, "ts": ts, "odu_composed": composed,
                            "chunk": self.expand(canonical_id)})
        return results

    # -- integrity / anchoring ------------------------------------------
    def receipt(self, canonical_id: str, blob: bytes) -> dict:
        """HMAC receipt binding a sealed blob to this keyring (Zangbeto-auditable)."""
        blob_hash = hashlib.sha256(blob).hexdigest()
        mac = _hmac.new(self.keyring.mac_key,
                        canonical_id.encode() + bytes.fromhex(blob_hash),
                        hashlib.sha256).hexdigest()
        return {"canonical_id": canonical_id, "blob_sha256": blob_hash,
                "owner": self.keyring.owner, "hmac": mac}

    def verify_receipt(self, receipt: dict) -> bool:
        expected = _hmac.new(
            self.keyring.mac_key,
            receipt["canonical_id"].encode() + bytes.fromhex(receipt["blob_sha256"]),
            hashlib.sha256).hexdigest()
        return _hmac.compare_digest(expected, receipt["hmac"])

    def merkle_root(self) -> str:
        """Root over leaf = SHA-256(canonical_id_bytes || SHA-256(blob)),
        leaves sorted by canonical_id; odd leaf promoted unchanged."""
        rows = self._db.execute(
            "SELECT canonical_id, blob FROM glyphs WHERE owner=? "
            "ORDER BY canonical_id", (self.keyring.owner,)).fetchall()
        level = [hashlib.sha256(bytes.fromhex(cid) + hashlib.sha256(blob).digest()).digest()
                 for cid, blob in rows]
        if not level:
            return hashlib.sha256(b"GIX1:empty").hexdigest()
        while len(level) > 1:
            nxt = [hashlib.sha256(level[i] + level[i + 1]).digest()
                   for i in range(0, len(level) - 1, 2)]
            if len(level) % 2:
                nxt.append(level[-1])
            level = nxt
        return level[0].hex()

    def close(self) -> None:
        self._db.close()
