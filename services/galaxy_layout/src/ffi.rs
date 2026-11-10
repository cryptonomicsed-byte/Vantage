//! Dependency-free wasm ABI (no wasm-bindgen). All pointers are offsets into
//! the module's linear memory; JS copies inputs in via `ga_alloc` and reads
//! results through the header returned by `ga_layout`.
//!
//! ```text
//! ga_alloc(len) -> ptr            8-byte aligned scratch buffer
//! ga_free(ptr, len)
//! ga_layout(n, x, y, z, group, n_groups, str, str_len, offs) -> *u32 header
//!   x,y,z : f64[n]     server coordinates
//!   group : u32[n]     constellation index per node (< n_groups)
//!   str   : u8[str_len] UTF-8 of node keys (path || id) then group names
//!   offs  : u32[n + n_groups + 1] byte offsets into str
//! header (u32[12]):
//!   [0] wx ptr (f32[n])   [1] wy ptr (f32[n])   [2] n
//!   [3] cells ptr (f64[n_cells*8]: group, x, y, w, h, count, odu_base, odu_composed)
//!   [4] n_cells           [5] bounds ptr (f64[4]: minX, minY, maxX, maxY)
//!   [6] node hash ptr (u8[n*32], GIX content_hash of each node key)
//!   [7] merkle root ptr (u8[32], gix1_merkle_root over node hashes)
//!   [8] status (0 ok, 1 bad input)  [9..11] reserved
//! ga_result_free(header)
//! ```

use crate::{build_layout, content_hash, gix1_merkle_root, odu_link, Layout};
use std::alloc::{alloc, dealloc, Layout as AllocLayout};

pub const ABI_VERSION: u32 = 1;

/// Output of [`compute`]: layout + GIX ids + odu per cell + vault root.
pub struct Computed {
    pub layout: Layout,
    /// `(odu_base, odu_composed)` of `content_hash(group name)`, per cell
    pub cell_odu: Vec<(u8, u16)>,
    pub node_hashes: Vec<[u8; 32]>,
    pub root: [u8; 32],
}

/// Safe core behind `ga_layout`, also used by native tests.
pub fn compute(x: &[f64], y: &[f64], z: &[f64], group: &[u32], node_keys: &[&[u8]], group_names: &[&[u8]]) -> Computed {
    let layout = build_layout(x, y, z, group);
    let cell_odu = layout
        .cells
        .iter()
        .map(|c| odu_link(&content_hash(group_names.get(c.group as usize).copied().unwrap_or(b"other"))))
        .collect();
    let node_hashes: Vec<[u8; 32]> = node_keys.iter().map(|k| content_hash(k)).collect();
    let root = gix1_merkle_root(&node_hashes);
    Computed { layout, cell_odu, node_hashes, root }
}

#[no_mangle]
pub extern "C" fn ga_version() -> u32 {
    ABI_VERSION
}

#[no_mangle]
pub extern "C" fn ga_alloc(len: usize) -> *mut u8 {
    if len == 0 {
        return 8 as *mut u8;
    }
    match AllocLayout::from_size_align(len, 8) {
        Ok(l) => unsafe { alloc(l) },
        Err(_) => std::ptr::null_mut(),
    }
}

/// # Safety
/// `ptr`/`len` must come from `ga_alloc`.
#[no_mangle]
pub unsafe extern "C" fn ga_free(ptr: *mut u8, len: usize) {
    if len == 0 || ptr.is_null() {
        return;
    }
    if let Ok(l) = AllocLayout::from_size_align(len, 8) {
        dealloc(ptr, l)
    }
}

#[repr(C)]
struct ResultBuf {
    header: [u32; 12],
    wx: Vec<f32>,
    wy: Vec<f32>,
    cells: Vec<f64>,
    bounds: [f64; 4],
    hashes: Vec<u8>,
    root: [u8; 32],
}

unsafe fn slice<'a, T>(p: *const T, n: usize) -> &'a [T] {
    if n == 0 || p.is_null() {
        &[]
    } else {
        std::slice::from_raw_parts(p, n)
    }
}

/// # Safety
/// Pointers must reference `ga_alloc` buffers of the sizes documented above.
#[no_mangle]
pub unsafe extern "C" fn ga_layout(
    n: usize,
    xp: *const f64,
    yp: *const f64,
    zp: *const f64,
    gp: *const u32,
    n_groups: usize,
    sp: *const u8,
    s_len: usize,
    op: *const u32,
) -> *mut u32 {
    let x = slice(xp, n);
    let y = slice(yp, n);
    let z = slice(zp, n);
    let g = slice(gp, n);
    let s = slice(sp, s_len);
    let offs = slice(op, n + n_groups + 1);

    let mut ok = offs.len() == n + n_groups + 1 && g.iter().all(|&v| (v as usize) < n_groups.max(1));
    if ok {
        ok = offs.windows(2).all(|w| w[0] <= w[1]) && offs.last().map_or(false, |&e| e as usize <= s.len());
    }
    let mut buf = Box::new(ResultBuf {
        header: [0; 12],
        wx: Vec::new(),
        wy: Vec::new(),
        cells: Vec::new(),
        bounds: [0.0, -160.0, 1.0, 1.0],
        hashes: Vec::new(),
        root: crate::GIX1_EMPTY_ROOT,
    });
    if !ok {
        buf.header[8] = 1;
    } else {
        let part = |i: usize| &s[offs[i] as usize..offs[i + 1] as usize];
        let keys: Vec<&[u8]> = (0..n).map(part).collect();
        let names: Vec<&[u8]> = (n..n + n_groups).map(part).collect();
        let c = compute(x, y, z, g, &keys, &names);
        for (cell, (ob, oc)) in c.layout.cells.iter().zip(&c.cell_odu) {
            buf.cells.extend_from_slice(&[
                cell.group as f64,
                cell.x,
                cell.y,
                cell.w,
                cell.h,
                cell.count as f64,
                *ob as f64,
                *oc as f64,
            ]);
        }
        buf.bounds = [c.layout.min_x, c.layout.min_y, c.layout.max_x, c.layout.max_y];
        buf.hashes = c.node_hashes.concat();
        buf.root = c.root;
        buf.wx = c.layout.wx;
        buf.wy = c.layout.wy;
        buf.header[2] = n as u32;
        buf.header[4] = c.layout.cells.len() as u32;
    }
    buf.header[0] = buf.wx.as_ptr() as usize as u32;
    buf.header[1] = buf.wy.as_ptr() as usize as u32;
    buf.header[3] = buf.cells.as_ptr() as usize as u32;
    buf.header[5] = buf.bounds.as_ptr() as usize as u32;
    buf.header[6] = buf.hashes.as_ptr() as usize as u32;
    buf.header[7] = buf.root.as_ptr() as usize as u32;
    Box::into_raw(buf) as *mut u32
}

/// # Safety
/// `header` must be a value returned by `ga_layout`, freed once.
#[no_mangle]
pub unsafe extern "C" fn ga_result_free(header: *mut u32) {
    if !header.is_null() {
        drop(Box::from_raw(header as *mut ResultBuf));
    }
}
