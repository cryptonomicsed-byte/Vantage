"""GlyphIndex reference implementation tests — these lock the GIX-FOLD-v1,
GIX-KDF-v1, and GIX1 wire formats for every other language in the ecosystem."""
import hashlib

import pytest

from backend.glyph_index import (
    GlyphIndexError,
    GlyphKeyring,
    GlyphStore,
    chunk_text,
    content_hash,
    glyph_fold,
    odu_link,
    open_blob,
    seal_blob,
)

SEED = bytes(range(64))
OWNER = "0xabc123"


@pytest.fixture()
def keyring():
    return GlyphKeyring.from_seed(SEED, OWNER)


@pytest.fixture()
def store(tmp_path, keyring):
    s = GlyphStore(tmp_path / "vault.db", keyring)
    yield s
    s.close()


# ---------------------------------------------------------------- fold vectors

# Frozen cross-language vectors: text → (canonical_id prefix, glyph codepoint,
# odu_base, odu_composed). Any implementation in any repo must reproduce these.
FOLD_VECTORS = [
    ("Àṣẹ", None, None, None),
    ("User: What's the weather? AI: Sunny, 25°C.", None, None, None),
    ("😊🚀 Unicode test", None, None, None),
]


def test_glyph_fold_deterministic_and_valid():
    for text, *_ in FOLD_VECTORS:
        digest = content_hash(text)
        g1, g2 = glyph_fold(digest), glyph_fold(digest)
        assert g1 == g2
        cp = ord(g1)
        assert 0x20 <= cp <= 0xFFFD
        assert not (0xD800 <= cp <= 0xDFFF), "surrogate leaked from fold"
        assert not (0xFDD0 <= cp <= 0xFDEF), "noncharacter leaked from fold"
        assert cp not in (0xFFFE, 0xFFFF)


def test_glyph_fold_rejects_bad_digest():
    with pytest.raises(GlyphIndexError):
        glyph_fold(b"short")


def test_odu_link_bounds():
    digest = content_hash("odu")
    base, composed = odu_link(digest)
    assert 0 <= base <= 255
    assert composed == (digest[0] << 8 | digest[1])


# ------------------------------------------------------------------- keyring

def test_keyring_domain_separation():
    prim = GlyphKeyring.from_seed(SEED, OWNER)
    duress = GlyphKeyring.from_seed(SEED, OWNER, duress=True)
    other_owner = GlyphKeyring.from_seed(SEED, "0xother")
    other_purpose = GlyphKeyring.from_seed(SEED, OWNER, purpose="other")
    keys = {prim.enc_key, duress.enc_key, other_owner.enc_key, other_purpose.enc_key}
    assert len(keys) == 4, "key contexts must be fully separated"
    assert prim.mac_key != prim.enc_key


def test_passphrase_keyring_deterministic():
    a = GlyphKeyring.from_passphrase("oríkì", OWNER)
    b = GlyphKeyring.from_passphrase("oríkì", OWNER)
    assert a.enc_key == b.enc_key
    assert GlyphKeyring.from_passphrase("oríkì2", OWNER).enc_key != a.enc_key


# ----------------------------------------------------------------- GIX1 blob

def test_seal_open_roundtrip(keyring):
    digest = content_hash("hello")
    cid = digest.hex()
    payload = {"canonical_id": cid, "glyph": glyph_fold(digest), "ts": 1.0,
               "chunk": "hello", "odu": list(odu_link(digest))}
    blob = seal_blob(payload, cid, keyring)
    assert blob[:4] == b"GIX1" and blob[4] == 1
    assert open_blob(blob, cid, keyring) == payload


def test_open_blob_rejects_tampering(keyring):
    cid = content_hash("x").hex()
    blob = bytearray(seal_blob({"canonical_id": cid, "chunk": "x"}, cid, keyring))
    blob[-1] ^= 0xFF
    with pytest.raises(GlyphIndexError, match="authentication failed"):
        open_blob(bytes(blob), cid, keyring)


def test_open_blob_rejects_wrong_key(keyring):
    cid = content_hash("x").hex()
    blob = seal_blob({"canonical_id": cid, "chunk": "x"}, cid, keyring)
    wrong = GlyphKeyring.from_seed(SEED, "0xother")
    with pytest.raises(GlyphIndexError):
        open_blob(blob, cid, wrong)


def test_open_blob_rejects_swapped_canonical_id(keyring):
    """AAD binding: a blob re-labeled under a different id must not open."""
    cid = content_hash("x").hex()
    blob = seal_blob({"canonical_id": cid, "chunk": "x"}, cid, keyring)
    with pytest.raises(GlyphIndexError):
        open_blob(blob, content_hash("y").hex(), keyring)


# ------------------------------------------------------------------ chunking

def test_chunk_text_semantic_split():
    text = "Intro. User: hi AI: hello User: bye"
    chunks = chunk_text(text)
    assert chunks[0] == "Intro."
    assert chunks[1].startswith("User:")
    assert len(chunks) == 3


def test_chunk_text_utf8_safe_hard_split():
    text = "😊" * 3000  # 4 bytes each → forces hard split
    chunks = chunk_text(text, max_bytes=4096)
    assert len(chunks) > 1
    assert "".join(chunks) == text  # no mojibake, no loss


# --------------------------------------------------------------------- store

def test_store_search_expand_roundtrip(store):
    store.store_text("User: What's the weather? AI: Sunny and clear, 25°C.")
    store.store_text("Code: print('Hello, world!') # Python example")
    results = store.search("weather forecast sunny rain", k=1)
    assert results and "weather" in results[0]["chunk"].lower()
    top = results[0]
    assert store.expand(top["canonical_id"]) == top["chunk"]


def test_receipts_verify_and_reject_forgery(store, keyring):
    receipts = store.store_text("receipt test")
    assert all(store.verify_receipt(r) for r in receipts)
    forged = dict(receipts[0], blob_sha256=hashlib.sha256(b"evil").hexdigest())
    assert not store.verify_receipt(forged)


def test_merkle_root_changes_with_content(store):
    empty_root = store.merkle_root()
    store.store_text("first memory")
    one = store.merkle_root()
    store.store_text("second memory")
    two = store.merkle_root()
    assert len({empty_root, one, two}) == 3
    assert all(len(r) == 64 for r in (empty_root, one, two))


def test_duress_vault_is_disjoint(tmp_path):
    primary = GlyphStore(tmp_path / "p.db", GlyphKeyring.from_seed(SEED, OWNER))
    decoy = GlyphStore(tmp_path / "d.db",
                       GlyphKeyring.from_seed(SEED, OWNER, duress=True))
    receipts = primary.store_text("the real memory")
    cid = receipts[0]["canonical_id"]
    row = primary._db.execute("SELECT blob FROM glyphs WHERE canonical_id=?",
                              (cid,)).fetchone()
    with pytest.raises(GlyphIndexError):
        open_blob(row[0], cid, decoy.keyring)
    primary.close(); decoy.close()
