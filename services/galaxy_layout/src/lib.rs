//! galaxy_layout — the memory-galaxy layout, ported from
//! `frontend/src/components/galaxyLayout.ts` (`buildLayout`), plus GIX
//! content-hash node ids and a sort-then-hash vault merkle root.
//!
//! Parity contract with the JS implementation (checked by
//! `tests/parity.rs` against fixtures produced by the JS code, and by
//! `frontend/scripts/galaxy-wasm-parity.mjs` on a 15k-node fixture):
//!   * all arithmetic in f64, in the same order as the JS source, positions
//!     stored as f32 (Float32Array) — round-to-nearest in both;
//!   * groups ordered by first appearance, then *stable* sort by size desc
//!     (Array.prototype.sort is stable);
//!   * coincident detection keys on `Math.round(px)|Math.round(py)`
//!     (JS rounding: half-up toward +inf, and `-0` prints as `0`);
//!   * `Math.max` NaN propagation is mirrored by [`js_max`].
//!
//! The GIX primitives (`content_hash`, `odu_link`, `gix1_merkle_root`) are
//! re-implemented on top of `sha2` only, because `gix-types` depends on
//! `uuid`/`getrandom`, which does not build for `wasm32-unknown-unknown`
//! without JS glue. `tests/gix_parity.rs` asserts byte-identical output
//! against `gix-types` natively.

use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::hash::{BuildHasherDefault, Hasher};

pub mod ffi;

// ── GIX primitives (identical to gix-types) ──────────────────────────────────

/// SHA-256 of the UTF-8 bytes (gix_types::content_hash).
pub fn content_hash(bytes: &[u8]) -> [u8; 32] {
    let mut h = Sha256::new();
    h.update(bytes);
    h.finalize().into()
}

/// `(odu_base, odu_composed)` = `(d[0], d[0]<<8 | d[1])` (gix_types::odu_link).
pub fn odu_link(digest: &[u8; 32]) -> (u8, u16) {
    (digest[0], (digest[0] as u16) << 8 | digest[1] as u16)
}

/// GIX1 empty root = SHA-256("").
pub const GIX1_EMPTY_ROOT: [u8; 32] = [
    0xe3, 0xb0, 0xc4, 0x42, 0x98, 0xfc, 0x1c, 0x14, 0x9a, 0xfb, 0xf4, 0xc8, 0x99, 0x6f, 0xb9, 0x24,
    0x27, 0xae, 0x41, 0xe4, 0x64, 0x9b, 0x93, 0x4c, 0xa4, 0x95, 0x99, 0x1b, 0x78, 0x52, 0xb8, 0x55,
];

const HEX: &[u8; 16] = b"0123456789abcdef";

pub fn hex32(d: &[u8; 32]) -> [u8; 64] {
    let mut out = [0u8; 64];
    for (i, b) in d.iter().enumerate() {
        out[i * 2] = HEX[(b >> 4) as usize];
        out[i * 2 + 1] = HEX[(b & 15) as usize];
    }
    out
}

/// gix_types::gix1_merkle_root over canonical ids given as raw digests:
/// the canonical id string is the lowercase hex of each digest, ids are
/// sorted (hex order == byte order), leaves are SHA-256(hex id), then a
/// pairwise reduction that pairs an odd last element with itself.
pub fn gix1_merkle_root(digests: &[[u8; 32]]) -> [u8; 32] {
    if digests.is_empty() {
        return GIX1_EMPTY_ROOT;
    }
    let mut sorted: Vec<[u8; 32]> = digests.to_vec();
    sorted.sort_unstable();
    let mut layer: Vec<[u8; 32]> = sorted.iter().map(|d| content_hash(&hex32(d))).collect();
    while layer.len() > 1 {
        let mut next = Vec::with_capacity((layer.len() + 1) / 2);
        let mut i = 0;
        while i < layer.len() {
            let left = layer[i];
            let right = if i + 1 < layer.len() { layer[i + 1] } else { left };
            let mut h = Sha256::new();
            h.update(left);
            h.update(right);
            next.push(h.finalize().into());
            i += 2;
        }
        layer = next;
    }
    layer[0]
}

// ── JS numeric semantics ─────────────────────────────────────────────────────

/// `Math.max(a, b)` — NaN if either is NaN (f64::max would drop the NaN).
#[inline]
pub fn js_max(a: f64, b: f64) -> f64 {
    if a.is_nan() || b.is_nan() {
        f64::NAN
    } else if a > b {
        a
    } else {
        b
    }
}

/// `Math.round(v)` — nearest integer, ties toward +inf.
#[inline]
pub fn js_round(v: f64) -> f64 {
    if !v.is_finite() {
        return v;
    }
    let f = v.floor();
    if v - f >= 0.5 {
        f + 1.0
    } else {
        f
    }
}

/// Bits of `Math.round(v)` normalised the way `${...}` stringifies:
/// `-0` and `0` collide, every NaN collides.
#[inline]
fn key_bits(v: f64) -> u64 {
    let r = js_round(v);
    if r == 0.0 {
        0
    } else if r.is_nan() {
        0x7ff8_0000_0000_0000
    } else {
        r.to_bits()
    }
}

/// Small deterministic hasher (FxHash). std's RandomState needs an entropy
/// source that wasm32-unknown-unknown does not have.
#[derive(Default)]
struct Fx(u64);
impl Hasher for Fx {
    #[inline]
    fn finish(&self) -> u64 {
        self.0
    }
    #[inline]
    fn write(&mut self, bytes: &[u8]) {
        for b in bytes {
            self.write_u64(*b as u64);
        }
    }
    #[inline]
    fn write_u64(&mut self, i: u64) {
        self.0 = (self.0.rotate_left(5) ^ i).wrapping_mul(0x51_7c_c1_b7_27_22_0a_95);
    }
}
type FxMap<K, V> = HashMap<K, V, BuildHasherDefault<Fx>>;

// ── Layout ───────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq)]
pub struct Cell {
    /// index into the caller's group (constellation) table
    pub group: u32,
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
    pub count: u32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Layout {
    pub wx: Vec<f32>,
    pub wy: Vec<f32>,
    /// in render order (groups by size desc, ties by first appearance)
    pub cells: Vec<Cell>,
    pub min_x: f64,
    pub min_y: f64,
    pub max_x: f64,
    pub max_y: f64,
}

#[inline]
fn iso(x: f64, y: f64, z: f64) -> (f64, f64) {
    (x - z * 0.4, y + z * 0.2)
}

/// Port of `buildLayout`. `group[i]` is node i's constellation index (the
/// caller maps `constellation || 'other'` to indices); any index assignment
/// works because group order is derived from first appearance here.
pub fn build_layout(x: &[f64], y: &[f64], z: &[f64], group: &[u32]) -> Layout {
    let n = x.len();
    assert!(y.len() == n && z.len() == n && group.len() == n);
    let mut wx = vec![0f32; n];
    let mut wy = vec![0f32; n];

    // groups in first-appearance order (Map insertion order)
    let mut slot: FxMap<u32, usize> = FxMap::default();
    let mut members: Vec<(u32, Vec<usize>)> = Vec::new();
    for (i, &g) in group.iter().enumerate() {
        let k = *slot.entry(g).or_insert_with(|| {
            members.push((g, Vec::new()));
            members.len() - 1
        });
        members[k].1.push(i);
    }
    // stable sort by size desc
    members.sort_by(|a, b| b.1.len().cmp(&a.1.len()));

    struct Local {
        group: u32,
        ids: Vec<usize>,
        mnx: f64,
        mny: f64,
        w: f64,
        h: f64,
    }
    let local: Vec<Local> = members
        .into_iter()
        .map(|(g, ids)| {
            let (mut mnx, mut mny) = (f64::INFINITY, f64::INFINITY);
            let (mut mxx, mut mxy) = (f64::NEG_INFINITY, f64::NEG_INFINITY);
            for &i in &ids {
                let (px, py) = iso(x[i], y[i], z[i]);
                if px < mnx {
                    mnx = px
                }
                if py < mny {
                    mny = py
                }
                if px > mxx {
                    mxx = px
                }
                if py > mxy {
                    mxy = py
                }
            }
            Local { group: g, ids, mnx, mny, w: js_max(200.0, mxx - mnx), h: js_max(200.0, mxy - mny) }
        })
        .collect();

    let cell_w = local.iter().fold(200.0, |m, l| js_max(m, l.w)) + 400.0;
    let cell_h = local.iter().fold(200.0, |m, l| js_max(m, l.h)) + 500.0;
    let cols = js_max(1.0, (local.len() as f64 * (cell_h / cell_w) * 1.6).sqrt().ceil());

    let mut cells = Vec::with_capacity(local.len());
    let mut seen: FxMap<(u64, u64), u32> = FxMap::default();
    for (k, l) in local.iter().enumerate() {
        let kf = k as f64;
        let cx = (kf % cols) * cell_w;
        let cy = (kf / cols).floor() * cell_h;
        seen.clear();
        for &i in &l.ids {
            let (px, py) = iso(x[i], y[i], z[i]);
            let mut xx = cx + (px - l.mnx);
            let mut yy = cy + (py - l.mny);
            let e = seen.entry((key_bits(px), key_bits(py))).or_insert(0);
            let dup = *e;
            *e += 1;
            if dup > 0 {
                let d = dup as f64;
                let a = d * 2.39996;
                let r = 14.0 * d.sqrt();
                xx += a.cos() * r;
                yy += a.sin() * r;
            }
            wx[i] = xx as f32;
            wy[i] = yy as f32;
        }
        cells.push(Cell { group: l.group, x: cx, y: cy, w: l.w, h: l.h, count: l.ids.len() as u32 });
    }

    let (mut min_x, mut min_y) = (f64::INFINITY, f64::INFINITY);
    let (mut max_x, mut max_y) = (f64::NEG_INFINITY, f64::NEG_INFINITY);
    for i in 0..n {
        let (a, b) = (wx[i] as f64, wy[i] as f64);
        if a < min_x {
            min_x = a
        }
        if b < min_y {
            min_y = b
        }
        if a > max_x {
            max_x = a
        }
        if b > max_y {
            max_y = b
        }
    }
    if n == 0 {
        min_x = 0.0;
        min_y = 0.0;
        max_x = 1.0;
        max_y = 1.0;
    }
    Layout { wx, wy, cells, min_x, min_y: min_y - 160.0, max_x, max_y }
}
