#!/usr/bin/env bash
set -euo pipefail

: "${ELDER1:?ELDER1 not set}"  # hex bech32 addr of elder signer 1
: "${ELDER2:?ELDER2 not set}"

elders_json="[\"$ELDER1\",\"$ELDER2\"]"

echo "Publishing AIO package…"
sui client publish --gas-budget 300000000 | tee publish.out

PKG=$(grep -m1 "PackageID:" publish.out | awk '{print $2}')
echo "Package: $PKG"

# Init Config & Mode (shared objects)
sui client call \
  --package "$PKG" \
  --module config --function init \
  --args "$elders_json" \
  --gas-budget 200000000

sui client call \
  --package "$PKG" \
  --module mode --function init \
  --gas-budget 100000000

echo "Deployed. Use sui client objects to copy shared IDs for Config and Mode."
