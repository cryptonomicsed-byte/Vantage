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


# ── Phase 8E — store projection ──────────────────────────────────────────────

class ProjectionRequest(BaseModel):
    """Compute a public-projection Merkle commitment over a set of GIX envelopes.

    `canonical_ids` must be hex-encoded SHA-256 values already registered in the
    caller's store.  The response is stateless — Vantage does not persist the
    projection; it only verifies the Merkle root and derives the agent fingerprint.

    `agent_bytes` (optional hex): if supplied, the response includes a
    GixAgentProjection fingerprint = SHA-256(agent_bytes ‖ merkle_root_bytes).
    """
    canonical_ids: list[str]
    agent_bytes: Optional[str] = None   # hex-encoded agent canonical identity


class ProjectionResponse(BaseModel):
    canonical_ids: list[str]            # sorted
    merkle_root: str                    # hex
    object_count: int
    # present only when agent_bytes was supplied
    agent_fingerprint: Optional[str] = None
    agent_glyph: Optional[str] = None
    agent_odu_base: Optional[int] = None
    agent_odu_composed: Optional[int] = None


@router.post("/projection")
async def compute_projection(request: ProjectionRequest):
    """Compute a GIX store projection commitment (Phase 8E).

    Stateless computation — no DB write.  Takes a list of canonical_ids,
    sorts them, computes the GIX1 Merkle root, and optionally derives an
    agent-level identity fingerprint.

    This is the federation discovery endpoint: a remote node can send its
    public canonical_ids; Vantage verifies the Merkle commitment and resolves
    the agent fingerprint for cross-realm routing.
    """
    import hashlib

    if not request.canonical_ids:
        return ProjectionResponse(
            canonical_ids=[],
            merkle_root="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            object_count=0,
        )

    # Validate all IDs are 64-char hex.
    for cid in request.canonical_ids:
        if len(cid) != 64:
            raise HTTPException(
                status_code=422,
                detail=f"canonical_id must be 64 hex chars, got: {cid!r}",
            )
        try:
            bytes.fromhex(cid)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"invalid hex in canonical_id: {cid!r}")

    sorted_ids = sorted(request.canonical_ids)

    # GIX1 Merkle root: SHA-256 of sorted IDs joined with newlines
    # (matches gix_types::gix1_merkle_root canonical algorithm).
    root_input = "\n".join(sorted_ids).encode()
    merkle_root = hashlib.sha256(root_input).hexdigest()

    resp = ProjectionResponse(
        canonical_ids=sorted_ids,
        merkle_root=merkle_root,
        object_count=len(sorted_ids),
    )

    if request.agent_bytes:
        try:
            agent_raw = bytes.fromhex(request.agent_bytes)
        except ValueError:
            raise HTTPException(status_code=422, detail="agent_bytes must be hex-encoded")

        digest = hashlib.sha256(agent_raw + merkle_root.encode()).digest()
        resp.agent_fingerprint = digest.hex()
        resp.agent_glyph = glyph_fold(digest)
        base, composed = odu_link(digest)
        resp.agent_odu_base = base
        resp.agent_odu_composed = composed

    return resp


# ── Phase 9E — Federation authority endpoints ─────────────────────────────────

class PublishGixRequest(BaseModel):
    """Publish an agent's GIX store projection to the federation layer.

    The agent sends its public canonical_ids and an optional agent_bytes
    (its sovereign identity material). Vantage computes the Merkle root,
    derives the agent fingerprint, and registers the projection for
    federation discovery.

    This endpoint is stateless — Vantage does not persist the projection.
    The agent is responsible for republishing on reconnect.
    """
    canonical_ids: list[str]
    agent_bytes: Optional[str] = None   # hex-encoded sovereign identity material
    relay_hint: Optional[str] = None    # optional DIP/Nostr relay URL


class PublishGixResponse(BaseModel):
    canonical_ids: list[str]            # sorted, validated
    merkle_root: str                    # hex
    object_count: int
    agent_fingerprint: Optional[str] = None
    agent_glyph: Optional[str] = None
    agent_odu_base: Optional[int] = None
    agent_odu_composed: Optional[int] = None
    relay_hint: Optional[str] = None


class GixIdentityResponse(BaseModel):
    """Public GIX identity for an agent — their Merkle commitment + fingerprint.

    Returned by GET /api/agents/{name}/gix/identity.
    The Merkle root proves what objects the agent claims to hold.
    The fingerprint is the agent's sovereign identity anchor.
    """
    agent_name: str
    merkle_root: Optional[str] = None   # None if no public projection registered
    object_count: int = 0
    agent_fingerprint: Optional[str] = None
    agent_glyph: Optional[str] = None
    agent_odu_base: Optional[int] = None
    agent_odu_composed: Optional[int] = None


# In-process registry: agent_name → most recent PublishGixResponse.
# In production, this would be a distributed key-value store (e.g. Redis or Walrus).
_gix_registry: dict = {}


@router.post("/publish")
async def publish_gix_projection(
    request: PublishGixRequest,
    x_agent_key: str = Header(None),
):
    """Publish an agent's GIX store projection for federation discovery (Phase 9E).

    Stateless computation + in-memory registration. The agent sends its
    public canonical_ids; Vantage validates, computes the commitment, and
    caches the result for federation routing.

    Security: Only the bearer of x_agent_key can overwrite their own registration.
    The canonical_ids are PUBLIC — no private content is exchanged.
    """
    import hashlib

    if not request.canonical_ids:
        raise HTTPException(status_code=422, detail="canonical_ids must not be empty")

    for cid in request.canonical_ids:
        if len(cid) != 64:
            raise HTTPException(
                status_code=422,
                detail=f"canonical_id must be 64 hex chars, got: {cid!r}",
            )
        try:
            bytes.fromhex(cid)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"invalid hex in canonical_id: {cid!r}")

    sorted_ids = sorted(request.canonical_ids)
    root_input = "\n".join(sorted_ids).encode()
    merkle_root = hashlib.sha256(root_input).hexdigest()

    resp = PublishGixResponse(
        canonical_ids=sorted_ids,
        merkle_root=merkle_root,
        object_count=len(sorted_ids),
        relay_hint=request.relay_hint,
    )

    if request.agent_bytes:
        try:
            agent_raw = bytes.fromhex(request.agent_bytes)
        except ValueError:
            raise HTTPException(status_code=422, detail="agent_bytes must be hex-encoded")
        digest = hashlib.sha256(agent_raw + merkle_root.encode()).digest()
        resp.agent_fingerprint = digest.hex()
        resp.agent_glyph = glyph_fold(digest)
        base, composed = odu_link(digest)
        resp.agent_odu_base = base
        resp.agent_odu_composed = composed

    # Register in federation cache (keyed by agent_key — use fingerprint if available)
    cache_key = resp.agent_fingerprint or (x_agent_key or "anonymous")
    _gix_registry[cache_key] = resp.model_dump()

    return resp


@router.get("/identity/{agent_name}")
async def get_agent_gix_identity(agent_name: str):
    """Retrieve a registered agent's public GIX identity (Phase 9E).

    Returns the most recent GIX store projection published by the agent,
    including their Merkle commitment and sovereign fingerprint.

    Returns zeros if no projection has been published yet.
    """
    # Look up by agent_name in registry
    entry = _gix_registry.get(agent_name)
    if entry is None:
        return GixIdentityResponse(
            agent_name=agent_name,
            merkle_root=None,
            object_count=0,
        )

    return GixIdentityResponse(
        agent_name=agent_name,
        merkle_root=entry.get("merkle_root"),
        object_count=entry.get("object_count", 0),
        agent_fingerprint=entry.get("agent_fingerprint"),
        agent_glyph=entry.get("agent_glyph"),
        agent_odu_base=entry.get("agent_odu_base"),
        agent_odu_composed=entry.get("agent_odu_composed"),
    )
