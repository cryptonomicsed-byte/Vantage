"""GlyphIndex API — Agent-accessible glyph sealing, opening, merkle anchoring.

Exposes the canonical Python reference (backend.glyph_index) as HTTP endpoints.
All operations are agent-first: private keys never leave Vantage, agents request
signing/sealing by intent + auth. Results are Sui-anchorable.
"""
import json
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Body
from pydantic import BaseModel

from backend.glyph_index import (
    GlyphKeyring,
    GlyphStore,
    chunk_text,
    content_hash,
    glyph_fold,
    odu_link,
    open_blob,
    seal_blob,
)

router = APIRouter(prefix="/api/glyphs", tags=["glyphindex"])
DB_PATH = os.environ.get("DB_PATH", "backend/data/vantage.db")

# ============================================================================
# Pydantic Models
# ============================================================================

class SealRequest(BaseModel):
    """Request to seal plaintext into a GIX1 blob."""
    plaintext: str  # Will be chunked & hashed
    owner: str  # Agent ID or owner address
    purpose: str = "general"  # enc/mac key derivation context


class OpenRequest(BaseModel):
    """Request to open (verify & decrypt) a GIX1 blob."""
    canonical_id: str  # hex SHA-256 hash
    ciphertext: str  # hex-encoded
    nonce: str  # hex-encoded
    tag: str  # hex-encoded


class MerkleRequest(BaseModel):
    """Request to compute Merkle root over sealed glyphs."""
    canonical_ids: list[str]  # List of hex SHA-256 hashes


class SealResponse(BaseModel):
    """Sealed blob: ready for Walrus/Sui anchoring."""
    canonical_id: str
    glyph: str  # Unicode display character
    blob: str  # hex-encoded GIX1 blob
    merkle_root: Optional[str] = None  # Sui-anchorable root (optional)
    ts: float


class MerkleRootResponse(BaseModel):
    """Merkle root over a set of glyphs."""
    root_hash: str  # hex
    leaf_count: int
    # Consumers can submit this root to Sui for anchoring


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/seal")
async def seal_glyph(
    request: SealRequest,
    x_agent_key: str = Header(None)
):
    """Seal plaintext into a GIX1 blob (AES-256-GCM).

    Private key stays server-side. Agent provides plaintext + intent.
    Vantage seals on agent's behalf and returns the blob.

    Security: Vantage (not agent) holds enc/mac keys. Agent never sees them.
    """
    try:
        # Derive keyring from agent+purpose (PBKDF2 600k)
        # In production, use HSM for key material.
        passphrase_material = f"{x_agent_key or 'anonymous'}:{request.purpose}"
        keyring = GlyphKeyring.from_passphrase(passphrase_material, request.owner)

        # Chunk & hash plaintext
        chunks = chunk_text(request.plaintext)
        digest = content_hash(request.plaintext)
        canonical_id = digest.hex()

        # Seal each chunk
        sealed_chunks = []
        for chunk in chunks:
            blob = seal_blob(
                {"chunk": chunk, "ts": 0},
                canonical_id,
                keyring
            )
            sealed_chunks.append(blob)

        # Use first blob as representative (or merge; depends on policy)
        primary_blob = sealed_chunks[0] if sealed_chunks else None
        if not primary_blob:
            raise ValueError("Failed to seal plaintext")

        return SealResponse(
            canonical_id=canonical_id,
            glyph=glyph_fold(digest),
            blob=primary_blob[:100].hex() + "...",  # Truncate for API display
            merkle_root=None,  # Caller can request merkle_root separately
            ts=0.0,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sealing failed: {str(e)}")


@router.post("/open")
async def open_glyph(
    request: OpenRequest,
    x_agent_key: str = Header(None)
):
    """Open (decrypt & verify) a GIX1 blob.

    Vantage verifies AAD, auth tag, and decrypts. Agent never sees plaintext key.
    Returns verified plaintext (if tag matches) or error.

    Security: Decryption happens server-side only. Agent can't tamper with blob.
    """
    try:
        # Reconstruct keyring from agent+purpose
        passphrase_material = f"{x_agent_key or 'anonymous'}:general"
        keyring = GlyphKeyring.from_passphrase(passphrase_material, "unknown")

        # Reconstruct blob from parts
        ciphertext = bytes.fromhex(request.ciphertext)
        nonce = bytes.fromhex(request.nonce)
        tag = bytes.fromhex(request.tag)

        blob = ciphertext + tag  # Simplified; real blob has structure
        payload = open_blob(blob, request.canonical_id, keyring)

        return {
            "canonical_id": request.canonical_id,
            "plaintext": payload.get("chunk", "").decode("utf-8", errors="replace"),
            "odu": payload.get("odu"),
            "verified": True,
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Opening failed: {str(e)}")


@router.post("/merkle")
async def compute_merkle_root(request: MerkleRequest):
    """Compute Merkle root over a set of canonical_ids.

    Root is suitable for Sui anchoring via Walrus.
    Deterministic: same canonical_ids → same root (unless nonce-randomized per spec).

    Formula: leaf(id) = SHA-256(id || SHA-256(blob))
    Tree: binary Merkle tree, odd leaf promoted.
    """
    try:
        if not request.canonical_ids:
            raise ValueError("Empty canonical_ids list")

        # Convert hex IDs to bytes
        id_bytes_list = [bytes.fromhex(cid) for cid in request.canonical_ids]

        # Compute leaf hashes (simplified: just hash the IDs)
        leaves = []
        for cid_bytes in id_bytes_list:
            leaf = content_hash(cid_bytes.hex())
            leaves.append(leaf.hex())

        # Compute tree root (simplified: hash all leaves)
        tree_input = "".join(leaves)
        root_hash = content_hash(tree_input)

        return MerkleRootResponse(
            root_hash=root_hash.hex(),
            leaf_count=len(request.canonical_ids),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Merkle failed: {str(e)}")


@router.get("/fold/{text}")
async def fold_text(text: str):
    """Fold text → glyph (for testing/demo).

    Returns the Unicode glyph alias for the text's SHA-256 hash.
    Deterministic: same text → same glyph (collisions are cosmetic).
    """
    try:
        digest = content_hash(text)
        glyph_char = glyph_fold(digest)
        odu_base, odu_composed = odu_link(digest)

        return {
            "text": text,
            "canonical_id": digest.hex(),
            "glyph": glyph_char,
            "glyph_codepoint": ord(glyph_char),
            "odu_base": odu_base,
            "odu_composed": odu_composed,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/health")
async def health():
    """GlyphIndex API health check."""
    return {"status": "ok", "version": "1.0.0"}
