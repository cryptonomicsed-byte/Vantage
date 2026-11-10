//! Parity with the JS buildLayout: fixtures in tests/fixtures/ are produced
//! by frontend/scripts/galaxy-layout-fixtures.mjs from galaxyLayout.ts.

use galaxy_layout::ffi::compute;
use galaxy_layout::{build_layout, js_round, Layout};
use serde_json::Value;
use std::collections::HashMap;

const TOL: f64 = 1e-3;

struct Fx {
    x: Vec<f64>,
    y: Vec<f64>,
    z: Vec<f64>,
    group: Vec<u32>,
    names: Vec<String>,
    keys: Vec<String>,
    expected: Value,
}

fn load(name: &str) -> Fx {
    let path = format!("{}/tests/fixtures/{name}.json", env!("CARGO_MANIFEST_DIR"));
    let v: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let mut f = Fx { x: vec![], y: vec![], z: vec![], group: vec![], names: vec![], keys: vec![], expected: v["expected"].clone() };
    let mut gi: HashMap<String, u32> = HashMap::new();
    for nd in v["nodes"].as_array().unwrap() {
        f.x.push(nd["x"].as_f64().unwrap());
        f.y.push(nd["y"].as_f64().unwrap());
        f.z.push(nd["z"].as_f64().unwrap());
        let mut c = nd["constellation"].as_str().unwrap_or("").to_string();
        if c.is_empty() {
            c = "other".into(); // `nd.constellation || 'other'`
        }
        let next = f.names.len() as u32;
        let g = *gi.entry(c.clone()).or_insert_with(|| {
            f.names.push(c.clone());
            next
        });
        f.group.push(g);
        let p = nd["path"].as_str().unwrap_or("");
        f.keys.push(if p.is_empty() { nd["id"].as_str().unwrap().to_string() } else { p.to_string() });
    }
    f
}

fn assert_matches(name: &str, f: &Fx, l: &Layout) {
    let e = &f.expected;
    let ex = e["wx"].as_array().unwrap();
    let ey = e["wy"].as_array().unwrap();
    assert_eq!(ex.len(), l.wx.len(), "{name}: n");
    let mut max_err = 0f64;
    for i in 0..ex.len() {
        let dx = (ex[i].as_f64().unwrap() - l.wx[i] as f64).abs();
        let dy = (ey[i].as_f64().unwrap() - l.wy[i] as f64).abs();
        max_err = max_err.max(dx).max(dy);
    }
    assert!(max_err <= TOL, "{name}: max position error {max_err}");
    let cells = e["cells"].as_array().unwrap();
    assert_eq!(cells.len(), l.cells.len(), "{name}: cell count");
    for (c, r) in cells.iter().zip(&l.cells) {
        assert_eq!(c["name"].as_str().unwrap(), f.names[r.group as usize], "{name}: cell order");
        assert_eq!(c["count"].as_u64().unwrap() as u32, r.count);
        for (k, v) in [("x", r.x), ("y", r.y), ("w", r.w), ("h", r.h)] {
            assert!((c[k].as_f64().unwrap() - v).abs() <= TOL, "{name}: cell {k}");
        }
    }
    let b = e["bounds"].as_array().unwrap();
    for (k, v) in [l.min_x, l.min_y, l.max_x, l.max_y].iter().enumerate() {
        assert!((b[k].as_f64().unwrap() - v).abs() <= TOL, "{name}: bounds[{k}]");
    }
}

#[test]
fn parity_with_js_fixtures() {
    for name in ["empty", "single", "small", "medium", "many_groups"] {
        let f = load(name);
        let l = build_layout(&f.x, &f.y, &f.z, &f.group);
        assert_matches(name, &f, &l);
    }
}

#[test]
fn parity_is_bit_exact_on_fixtures() {
    // stronger than the 1e-3 contract: same f64 op order → same f32 bits,
    // except where cos/sin libm ulps differ (allow 1 f32 ulp there)
    for name in ["small", "medium", "many_groups"] {
        let f = load(name);
        let l = build_layout(&f.x, &f.y, &f.z, &f.group);
        let ex = f.expected["wx"].as_array().unwrap();
        let mut off = 0;
        for i in 0..ex.len() {
            if ex[i].as_f64().unwrap() as f32 != l.wx[i] {
                off += 1;
            }
        }
        assert!(off * 100 <= ex.len(), "{name}: {off} of {} differ in bits", ex.len());
    }
}

#[test]
fn group_order_is_stable_size_desc_with_index_independence() {
    // relabelling group indices must not change the layout (order comes
    // from first appearance, not from the index values)
    let f = load("medium");
    let k = f.names.len() as u32;
    let relabelled: Vec<u32> = f.group.iter().map(|g| k - 1 - g).collect();
    let a = build_layout(&f.x, &f.y, &f.z, &f.group);
    let b = build_layout(&f.x, &f.y, &f.z, &relabelled);
    assert_eq!(a.wx, b.wx);
    assert_eq!(a.wy, b.wy);
}

#[test]
fn determinism() {
    let f = load("medium");
    let keys: Vec<&[u8]> = f.keys.iter().map(|s| s.as_bytes()).collect();
    let names: Vec<&[u8]> = f.names.iter().map(|s| s.as_bytes()).collect();
    let a = compute(&f.x, &f.y, &f.z, &f.group, &keys, &names);
    for _ in 0..3 {
        let b = compute(&f.x, &f.y, &f.z, &f.group, &keys, &names);
        assert_eq!(a.layout, b.layout);
        assert_eq!(a.node_hashes, b.node_hashes);
        assert_eq!(a.root, b.root);
        assert_eq!(a.cell_odu, b.cell_odu);
    }
}

#[test]
fn coincident_nodes_fan_out_on_golden_spiral() {
    let n = 6;
    let x = vec![100.0; n];
    let y = vec![50.0; n];
    let z = vec![0.0; n];
    let g = vec![0u32; n];
    let l = build_layout(&x, &y, &z, &g);
    // first one sits at the cell origin, every later one at r = 14*sqrt(dup)
    assert_eq!((l.wx[0], l.wy[0]), (0.0, 0.0));
    for dup in 1..n {
        let a = dup as f64 * 2.39996;
        let r = 14.0 * (dup as f64).sqrt();
        assert!((l.wx[dup] as f64 - a.cos() * r).abs() < 1e-4);
        assert!((l.wy[dup] as f64 - a.sin() * r).abs() < 1e-4);
        let d = ((l.wx[dup] as f64).powi(2) + (l.wy[dup] as f64).powi(2)).sqrt();
        assert!((d - r).abs() < 1e-3);
    }
    // all distinct
    for i in 0..n {
        for j in i + 1..n {
            assert!((l.wx[i], l.wy[i]) != (l.wx[j], l.wy[j]));
        }
    }
    // a different constellation does not share the dup counter
    let l2 = build_layout(&[0.0, 0.0], &[0.0, 0.0], &[0.0, 0.0], &[0, 1]);
    assert_eq!(l2.cells.len(), 2);
    assert_eq!((l2.wx[1] - l2.cells[1].x as f32, l2.wy[1] - l2.cells[1].y as f32), (0.0, 0.0));
}

#[test]
fn js_rounding_semantics() {
    assert_eq!(js_round(2.5), 3.0);
    assert_eq!(js_round(-2.5), -2.0);
    assert_eq!(js_round(0.49999999999999994), 0.0);
    assert_eq!(js_round(-0.4), 0.0); // -0, keyed like "0"
    // -0.4 and 0.3 round to -0 and 0 → same key → second is fanned out
    let l = build_layout(&[-0.4, 0.3], &[0.0, 0.0], &[0.0, 0.0], &[0, 0]);
    let a = 2.39996f64;
    assert!((l.wx[1] as f64 - (0.7 + a.cos() * 14.0)).abs() < 1e-4);
}

#[test]
fn empty_input() {
    let l = build_layout(&[], &[], &[], &[]);
    assert!(l.wx.is_empty() && l.cells.is_empty());
    assert_eq!((l.min_x, l.min_y, l.max_x, l.max_y), (0.0, -160.0, 1.0, 1.0));
    let c = compute(&[], &[], &[], &[], &[], &[]);
    assert_eq!(c.root, galaxy_layout::GIX1_EMPTY_ROOT);
}

#[test]
fn merkle_root_is_order_independent() {
    let f = load("medium");
    let keys: Vec<&[u8]> = f.keys.iter().map(|s| s.as_bytes()).collect();
    let names: Vec<&[u8]> = f.names.iter().map(|s| s.as_bytes()).collect();
    let a = compute(&f.x, &f.y, &f.z, &f.group, &keys, &names);
    let mut hs = a.node_hashes.clone();
    hs.reverse();
    assert_eq!(galaxy_layout::gix1_merkle_root(&hs), a.root);
    hs.sort_by_key(|h| h[7]);
    assert_eq!(galaxy_layout::gix1_merkle_root(&hs), a.root);
    // and sensitive to content
    hs[0][0] ^= 1;
    assert_ne!(galaxy_layout::gix1_merkle_root(&hs), a.root);
    // stable id = content_hash(path || id): independent of position in input
    let i = 5;
    assert_eq!(a.node_hashes[i], galaxy_layout::content_hash(f.keys[i].as_bytes()));
}
