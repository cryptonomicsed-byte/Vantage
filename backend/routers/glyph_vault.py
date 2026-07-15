"""GlyphIndex sovereign memory API — /api/glyphs/*.

Two operating modes, decided per request by what the client sends:

* **Custodial** (`POST /store` with plaintext): Vantage seals the text with a
  per-agent keyring derived from ``settings.GLYPH_MASTER_SECRET``. Convenient
  for hosted agents; the DB still only holds GIX1 ciphertext.
* **Sovereign** (`POST /store-sealed` with a client-sealed GIX1 blob): Vantage
  never sees plaintext or keys — it journals the blob, indexes the metadata
  the client chose to reveal, and serves it back verbatim. This is the
  BlockMesh / wallet-owned path.

``GET /merkle-root`` exposes the anchor value for Sui receipts. Search only
works for custodial vaults (sovereign embeddings stay client-side).
"""
import base64
import hashlib
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import settings
from ..deps import get_agent
from ..glyph_index import (
    GlyphIndexError,
    GlyphKeyring,
    GlyphStore,
    content_hash,
    glyph_fold,
    odu_link,
)

router = APIRouter(prefix="/api/glyphs", tags=["glyph_index"])

_STORES: dict = {}


def _vault_dir() -> Path:
    d = Path(settings.DATA_DIR) / "glyph_vaults"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _store_for(agent: dict) -> GlyphStore:
    if not settings.GLYPH_MASTER_SECRET:
        raise HTTPException(503, "GlyphIndex custodial mode disabled "
                                 "(set VANTAGE_GLYPH_MASTER_SECRET)")
    name = agent["name"]
    if name not in _STORES:
        keyring = GlyphKeyring.from_passphrase(
            settings.GLYPH_MASTER_SECRET, owner=name)
        _STORES[name] = GlyphStore(_vault_dir() / f"{name}.db", keyring)
    return _STORES[name]


class StoreRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1_000_000)


class SealedStoreRequest(BaseModel):
    canonical_id: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    blob_b64: str
    glyph: str = Field(..., min_length=1, max_length=2)
    odu_composed: int = Field(..., ge=0, le=65535)
    walrus_blob_id: str = ""


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=8192)
    k: int = Field(3, ge=1, le=25)


@router.post("/store")
async def store_memory(body: StoreRequest, agent: dict = Depends(get_agent)):
    store = _store_for(agent)
    receipts = store.store_text(body.text)
    return {"receipts": receipts, "merkle_root": store.merkle_root()}


@router.post("/store-sealed")
async def store_sealed(body: SealedStoreRequest, agent: dict = Depends(get_agent)):
    """Sovereign path: journal a client-sealed GIX1 blob without keys."""
    blob = base64.b64decode(body.blob_b64)
    if blob[:4] != b"GIX1":
        raise HTTPException(422, "not a GIX1 blob")
    store = _store_for(agent) if settings.GLYPH_MASTER_SECRET else None
    # Journal directly — sovereign blobs bypass the keyring entirely.
    db_path = _vault_dir() / f"{agent['name']}.sovereign.db"
    import sqlite3
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE IF NOT EXISTS sealed (canonical_id TEXT PRIMARY KEY,"
                " glyph TEXT, odu_composed INTEGER, walrus_blob_id TEXT, blob BLOB)")
    con.execute("INSERT OR REPLACE INTO sealed VALUES (?,?,?,?,?)",
                (body.canonical_id, body.glyph, body.odu_composed,
                 body.walrus_blob_id, blob))
    con.commit()
    con.close()
    return {"canonical_id": body.canonical_id,
            "blob_sha256": hashlib.sha256(blob).hexdigest()}


@router.get("/sealed/{canonical_id}")
async def fetch_sealed(canonical_id: str, agent: dict = Depends(get_agent)):
    import sqlite3
    db_path = _vault_dir() / f"{agent['name']}.sovereign.db"
    if not db_path.exists():
        raise HTTPException(404, "no sovereign vault")
    con = sqlite3.connect(db_path)
    row = con.execute("SELECT glyph, odu_composed, walrus_blob_id, blob "
                      "FROM sealed WHERE canonical_id=?", (canonical_id,)).fetchone()
    con.close()
    if row is None:
        raise HTTPException(404, "unknown glyph")
    return {"canonical_id": canonical_id, "glyph": row[0],
            "odu_composed": row[1], "walrus_blob_id": row[2],
            "blob_b64": base64.b64encode(row[3]).decode()}


@router.post("/search")
async def search_memory(body: SearchRequest, agent: dict = Depends(get_agent)):
    store = _store_for(agent)
    try:
        return {"results": store.search(body.query, k=body.k)}
    except GlyphIndexError as exc:
        raise HTTPException(500, str(exc))


@router.get("/expand/{canonical_id}")
async def expand_glyph(canonical_id: str, agent: dict = Depends(get_agent)):
    store = _store_for(agent)
    try:
        return {"canonical_id": canonical_id, "chunk": store.expand(canonical_id)}
    except GlyphIndexError as exc:
        raise HTTPException(404, str(exc))


@router.get("/merkle-root")
async def merkle_root(agent: dict = Depends(get_agent)):
    store = _store_for(agent)
    return {"owner": agent["name"], "merkle_root": store.merkle_root()}


@router.get("/preview")
async def preview_glyph(text: str):
    """Stateless helper: what glyph/Odù would this text fold to?"""
    digest = content_hash(text)
    base, composed = odu_link(digest)
    return {"canonical_id": digest.hex(), "glyph": glyph_fold(digest),
            "odu_base": base, "odu_composed": composed}
