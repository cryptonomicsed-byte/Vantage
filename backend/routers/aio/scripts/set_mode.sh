#!/usr/bin/env bash
set -euo pipefail
: "${PKG:?}"
: "${CFG_ID:?}"
: "${MODE_ID:?}"
: "${TARGET:?}"  # normal|degraded|halted

case "$TARGET" in
  normal) FN=set_normal;;
  degraded) FN=set_degraded;;
  halted) FN=set_halted;;
  *) echo "bad TARGET"; exit 2;;
 esac

sui client call \
  --package "$PKG" \
  --module mode --function "$FN" \
  --args "$CFG_ID" "$MODE_ID" \
  --gas-budget 100000000
