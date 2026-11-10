#!/usr/bin/env bash
# Build services/galaxy_layout → frontend/public/wasm/galaxy_layout.wasm
# (dependency-free wasm32 module; no wasm-bindgen / wasm-pack needed).
#   rustup target add wasm32-unknown-unknown   # once
#   frontend/scripts/build-galaxy-wasm.sh
# Then verify: node frontend/scripts/galaxy-wasm-parity.mjs
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
crate="$repo/services/galaxy_layout"
out="$repo/frontend/public/wasm"

if ! rustup target list --installed 2>/dev/null | grep -qx wasm32-unknown-unknown; then
  echo "missing rust target: run 'rustup target add wasm32-unknown-unknown'" >&2
  exit 1
fi
cargo build --manifest-path "$crate/Cargo.toml" --release --target wasm32-unknown-unknown
mkdir -p "$out"
cp "$crate/target/wasm32-unknown-unknown/release/galaxy_layout.wasm" "$out/galaxy_layout.wasm"
if command -v wasm-opt >/dev/null 2>&1; then
  wasm-opt -Oz --enable-bulk-memory --enable-nontrapping-float-to-int \
    "$out/galaxy_layout.wasm" -o "$out/galaxy_layout.wasm"
fi
bytes=$(wc -c < "$out/galaxy_layout.wasm")
echo "wrote $out/galaxy_layout.wasm ($bytes bytes)"
if [ "$bytes" -gt 307200 ]; then
  echo "warning: wasm is over 300 KB — do not commit it; build it in CI/deploy instead" >&2
fi
