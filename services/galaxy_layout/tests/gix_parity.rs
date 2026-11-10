//! Byte-identical output vs the canonical gix-types crate (native only;
//! gix-types does not build for wasm32-unknown-unknown).

use galaxy_layout::{content_hash, gix1_merkle_root, hex32, odu_link, GIX1_EMPTY_ROOT};

fn hex(d: &[u8; 32]) -> String {
    String::from_utf8(hex32(d).to_vec()).unwrap()
}

const SAMPLES: &[&str] = &["", "a", "vault/note/Intel Scan Complete.md", "ọmọ kọ́dà · 🧠", "n0", "other"];

#[test]
fn content_hash_matches_gix_types() {
    for s in SAMPLES {
        assert_eq!(content_hash(s.as_bytes()), gix_types::content_hash(s), "{s:?}");
    }
}

#[test]
fn odu_link_matches_gix_types() {
    for s in SAMPLES {
        let d = gix_types::content_hash(s);
        assert_eq!(odu_link(&d), gix_types::odu_link(&d), "{s:?}");
    }
}

#[test]
fn merkle_root_matches_gix_types() {
    assert_eq!(hex(&GIX1_EMPTY_ROOT), gix_types::GIX1_EMPTY_ROOT);
    assert_eq!(hex(&gix1_merkle_root(&[])), gix_types::gix1_merkle_root(&[]));
    for n in [1usize, 2, 3, 4, 5, 7, 8, 33, 1000] {
        let digests: Vec<[u8; 32]> = (0..n).map(|i| content_hash(format!("vault/n{i}.md").as_bytes())).collect();
        let ids: Vec<String> = digests.iter().map(hex).collect();
        let refs: Vec<&str> = ids.iter().map(|s| s.as_str()).collect();
        assert_eq!(hex(&gix1_merkle_root(&digests)), gix_types::gix1_merkle_root(&refs), "n={n}");
    }
}

#[test]
fn hex_matches_glyph_node_canonical_id() {
    for s in SAMPLES {
        let node = gix_types::GlyphNode::from_chunk(s, 0.0);
        assert_eq!(hex(&content_hash(s.as_bytes())), node.canonical_id);
        assert_eq!(node.odu_composed, odu_link(&content_hash(s.as_bytes())).1);
    }
}
