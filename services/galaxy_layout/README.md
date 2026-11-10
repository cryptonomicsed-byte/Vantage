# galaxy_layout

Rust port of the memory-galaxy layout (`frontend/src/components/galaxyLayout.ts`
→ `buildLayout`) compiled to a dependency-free `wasm32-unknown-unknown` module,
plus GIX primitives:

- **stable node id** = GIX `content_hash(path || id)` (SHA-256), 32 bytes/node
- **constellation odu** = `odu_link(content_hash(name))` → `(base, composed)`
- **vault audit root** = `gix1_merkle_root` (sort-then-hash) over the node ids

`content_hash` / `odu_link` / `gix1_merkle_root` are re-implemented on `sha2`
only (gix-types pulls `uuid`/`getrandom`, which does not build for
wasm32-unknown-unknown without JS glue); `tests/gix_parity.rs` checks them
byte-for-byte against gix-types (git dev-dependency) natively.

Used by `frontend/src/workers/galaxyLayout.worker.ts` behind the default-off
flag `localStorage['galaxy.wasm'] = '1'` (or `VITE_GALAXY_WASM=1` at build).
The JS layout always renders first and stays the fallback; the worker result
is swapped in only if it matches the JS positions within 1e-3.

```sh
cargo test --manifest-path services/galaxy_layout/Cargo.toml   # native tests
frontend/scripts/build-galaxy-wasm.sh                           # → frontend/public/wasm/galaxy_layout.wasm
node --no-warnings frontend/scripts/galaxy-wasm-parity.mjs      # 15k-node JS vs wasm parity + timing
node --no-warnings frontend/scripts/galaxy-layout-fixtures.mjs  # regenerate tests/fixtures from the JS layout
```

ABI (no wasm-bindgen): see `src/ffi.rs`. Changing `buildLayout` in TS requires
the same change here, regenerating the fixtures, and rebuilding the wasm.
