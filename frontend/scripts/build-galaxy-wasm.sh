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
# Reproducible: rustc embeds the absolute path of every source file it compiles
# into the debug/panic metadata, so building the same crate from two different
# directories (CI checkout vs this VPS) produced different bytes (62741 vs
# 62765) with identical rustc. Remap both the checkout and the cargo registry
# root to fixed prefixes and disable incremental compilation.
export RUSTFLAGS="${RUSTFLAGS:-} --remap-path-prefix=$repo=/build --remap-path-prefix=${CARGO_HOME:-$HOME/.cargo}=/cargo --remap-path-prefix=$crate=/build/services/galaxy_layout"
export CARGO_INCREMENTAL=0
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1700000000}"
cargo build --manifest-path "$crate/Cargo.toml" --release --target wasm32-unknown-unknown
mkdir -p "$out"
cp "$crate/target/wasm32-unknown-unknown/release/galaxy_layout.wasm" "$out/galaxy_layout.wasm"
# wasm-opt is OFF by default: it is not installed on every builder, so a build
# that runs it here and not in CI (or vice versa) yields different bytes. Opt in
# with GALAXY_WASM_OPT=1 and commit the result from the same place CI builds.
if [ "${GALAXY_WASM_OPT:-0}" = "1" ] && command -v wasm-opt >/dev/null 2>&1; then
  wasm-opt -Oz --enable-bulk-memory --enable-nontrapping-float-to-int \
    "$out/galaxy_layout.wasm" -o "$out/galaxy_layout.wasm"
fi
bytes=$(wc -c < "$out/galaxy_layout.wasm")
echo "wrote $out/galaxy_layout.wasm ($bytes bytes)"
if [ "$bytes" -gt 307200 ]; then
  echo "warning: wasm is over 300 KB — do not commit it; build it in CI/deploy instead" >&2
fi
