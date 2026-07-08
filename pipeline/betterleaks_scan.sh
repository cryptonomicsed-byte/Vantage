#!/bin/bash
# betterleaks wrapper for Vantage pipeline
# Usage: betterleaks_scan.sh <repo_path> [--json]

REPO="$1"
OUTPUT_MODE="${2:---json}"

docker run --rm -v "$REPO:/repo:ro" ghcr.io/betterleaks/betterleaks:latest \
  dir /repo -v --format="${OUTPUT_MODE#--}" 2>&1
