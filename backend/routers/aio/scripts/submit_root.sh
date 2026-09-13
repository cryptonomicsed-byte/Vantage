#!/usr/bin/env bash
set -euo pipefail
: "${PKG:?}"
: "${CFG_ID:?}"
: "${ROOT_HEX:?}"
: "${COUNT:?}"
: "${EPOCH:?}"

sui client call \
  --package "$PKG" \
  --module receipts --function submit_root \
  --args "$CFG_ID" 0x"$ROOT_HEX" "$COUNT" "$EPOCH" \
  --gas-budget 100000000
