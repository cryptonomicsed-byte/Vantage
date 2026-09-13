#!/usr/bin/env bash
set -euo pipefail
: "${PKG:?}"
: "${MIN_BOND:?}"   # in MIST (SUI base units)
: "${PUBKEY_HEX:?}"

# Use first SUI coin >= MIN_BOND as bond
COIN=$(sui client gas --json | jq -r \
  --argjson MIN "$MIN_BOND" \
  '.[] | select(.balance >= $MIN) | .id.id' | head -n1)

[ -n "$COIN" ] || { echo "no coin >= MIN_BOND"; exit 3; }

echo "Registering oracle with coin $COIN…"
sui client call \
  --package "$PKG" \
  --module oracle --function register \
  --args 0x"$PUBKEY_HEX" "$MIN_BOND" "$COIN" \
  --gas-budget 150000000
