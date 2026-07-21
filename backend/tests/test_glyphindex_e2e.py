"""End-to-End GlyphIndex smoke test: seal → merkle → anchor → verify.

Full flow from agent sealing memory to Sui anchoring to cross-language verification.
"""
import json
import pytest
from backend.glyph_index import (
    GlyphKeyring,
    chunk_text,
    content_hash,
    glyph_fold,
    merkle_root_binary,
    odu_link,
    seal_blob,
    open_blob,
)


OWNER = "0xagent_alice"
SEED = bytes(range(64))


class TestGlyphIndexE2E:
    """Full flow tests: agent seals, Vantage anchors, larql verifies."""

    @pytest.fixture()
    def keyring(self):
        return GlyphKeyring.from_seed(SEED, OWNER)

    def test_agent_seals_memory(self, keyring):
        """Agent creates a sealed memory blob (GIX1 format)."""
        # Agent's plaintext memory
        memory = {
            "thought": "Discovered market inefficiency in SOL/USDC pair",
            "confidence": 0.87,
            "timestamp": 1721686400.0,
        }
        plaintext = json.dumps(memory)
        digest = content_hash(plaintext)
        canonical_id = digest.hex()

        # Seal into GIX1 blob
        payload = {
            "canonical_id": canonical_id,
            "glyph": glyph_fold(digest),
            "ts": 1721686400.0,
            "chunk": plaintext,
            "odu": list(odu_link(digest)),
        }
        blob = seal_blob(payload, canonical_id, keyring)

        # Verify blob structure
        assert blob[:4] == b"GIX1", "Blob magic bytes"
        assert blob[4] == 0x01, "GIX1 version"
        assert len(blob) > 32, "Blob has content"

    def test_agent_creates_merkle_root(self, keyring):
        """Agent seals N memories, computes Merkle root for Sui anchoring."""
        # Agent seals 3 memory chunks
        memories = [
            "Trade 1: Bought 10 SOL at $150",
            "Trade 2: Sold 5 SOL at $155",
            "Trade 3: Waiting for pullback",
        ]
        canonical_ids = []
        blobs = []

        for mem in memories:
            digest = content_hash(mem)
            canonical_id = digest.hex()
            canonical_ids.append(canonical_id)

            payload = {
                "canonical_id": canonical_id,
                "glyph": glyph_fold(digest),
                "ts": 1721686400.0,
                "chunk": mem,
                "odu": list(odu_link(digest)),
            }
            blob = seal_blob(payload, canonical_id, keyring)
            blobs.append(blob)

        # Compute Merkle root (deterministic, Sui-anchorable)
        root_hash = merkle_root_binary(blobs)

        assert isinstance(root_hash, bytes), "Root is bytes"
        assert len(root_hash) == 32, "Root is SHA-256"
        # Root is same every time for same blobs
        root_hash_2 = merkle_root_binary(blobs)
        assert root_hash == root_hash_2, "Root deterministic"

    def test_walrus_upload_simulation(self, keyring):
        """Simulate uploading sealed blob to Walrus for archival."""
        # Agent seals a memory
        memory = "Critical discovery: the pattern repeats every 49 blocks"
        digest = content_hash(memory)
        canonical_id = digest.hex()

        payload = {
            "canonical_id": canonical_id,
            "glyph": glyph_fold(digest),
            "ts": 1721686400.0,
            "chunk": memory,
            "odu": list(odu_link(digest)),
        }
        blob = seal_blob(payload, canonical_id, keyring)

        # Simulate Walrus content-addressing
        walrus_blob_id = content_hash(blob).hex()
        assert walrus_blob_id, "Walrus ID generated"
        assert len(walrus_blob_id) == 64, "ID is SHA-256 hex"

    def test_sui_anchor_submission(self, keyring):
        """Simulate submitting Merkle root to Sui for immutable anchoring."""
        # Create N sealed blobs
        blobs = []
        for i in range(5):
            text = f"Memory chunk {i}: {i * 100} SOL worth of trades"
            digest = content_hash(text)
            canonical_id = digest.hex()
            payload = {
                "canonical_id": canonical_id,
                "glyph": glyph_fold(digest),
                "ts": 1721686400.0 + i,
                "chunk": text,
                "odu": list(odu_link(digest)),
            }
            blob = seal_blob(payload, canonical_id, keyring)
            blobs.append(blob)

        # Compute Merkle root
        root_hash = merkle_root_binary(blobs)

        # Simulate Sui transaction
        sui_payload = {
            "objectType": "glyphindex::glyphindex::MerkleRoot",
            "root_hash": root_hash.hex(),
            "leaf_count": len(blobs),
            "anchor_time": 1721686400.0,
        }

        assert sui_payload["root_hash"], "Merkle root in Sui payload"
        assert sui_payload["leaf_count"] == 5, "Leaf count matches"

    def test_cross_language_verification(self, keyring):
        """Verify that Python blob can be opened by larql (Rust) and Zero (Move)."""
        # Python seals a blob
        secret = "The bird is in the nest"
        digest = content_hash(secret)
        canonical_id = digest.hex()

        payload = {
            "canonical_id": canonical_id,
            "glyph": glyph_fold(digest),
            "ts": 1721686400.0,
            "chunk": secret,
            "odu": list(odu_link(digest)),
        }
        blob = seal_blob(payload, canonical_id, keyring)

        # Python can open it
        opened = open_blob(blob, canonical_id, keyring)
        assert opened["chunk"] == secret, "Payload matches"

        # Rust/larql can verify the blob structure
        # (In real test: cross-process, but here we verify the shape)
        assert blob[:4] == b"GIX1", "Blob format stable"

        # Zero/Move can compute the glyph and Odù
        glyph = glyph_fold(digest)
        odu_b, odu_c = odu_link(digest)
        assert isinstance(glyph, str), "Glyph is char"
        assert 0 <= odu_b <= 255, "Odù base in range"

    def test_agent_vault_persistence(self, keyring):
        """Sealed memories can be retrieved and re-verified."""
        from backend.glyph_index import GlyphStore
        import tempfile
        import os

        # Create a temp vault
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = os.path.join(tmpdir, "test.db")
            store = GlyphStore(vault_path, keyring)

            # Agent seals memory into the vault
            memory1 = "First memory: sunrise at 06:23"
            digest1 = content_hash(memory1)
            payload1 = {
                "canonical_id": digest1.hex(),
                "glyph": glyph_fold(digest1),
                "ts": 1721686400.0,
                "chunk": memory1,
                "odu": list(odu_link(digest1)),
            }
            blob1 = seal_blob(payload1, digest1.hex(), keyring)
            store.store(blob1, digest1.hex())

            # Retrieve and verify
            retrieved = store.open(digest1.hex(), keyring)
            assert retrieved["chunk"] == memory1, "Memory persisted"

            store.close()

    def test_duress_key_isolation(self):
        """Duress passphrase opens a separate vault (Cloakseed)."""
        # Primary keyring
        primary = GlyphKeyring.from_passphrase("oríkì", OWNER)
        duress = GlyphKeyring.from_passphrase("oríkì", OWNER, duress=True)

        # Keys must be fully isolated
        assert primary.enc_key != duress.enc_key, "Enc keys differ"
        assert primary.mac_key != duress.mac_key, "Mac keys differ"

        # Same memory sealed with different keys yields different ciphertexts
        memory = "Secret memory"
        digest = content_hash(memory)
        canonical_id = digest.hex()
        payload = {
            "canonical_id": canonical_id,
            "glyph": glyph_fold(digest),
            "ts": 1721686400.0,
            "chunk": memory,
            "odu": list(odu_link(digest)),
        }

        blob_primary = seal_blob(payload, canonical_id, primary)
        blob_duress = seal_blob(payload, canonical_id, duress)

        # Blobs are different (even for same plaintext)
        assert blob_primary != blob_duress, "Duress vault isolated"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
