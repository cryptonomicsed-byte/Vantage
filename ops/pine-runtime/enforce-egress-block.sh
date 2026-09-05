#!/bin/bash
# Hard (network-level) egress denial for the pine-runtime sandbox container,
# on top of the app-level isolation (worker_threads, resource limits,
# restricted grammar) and the container-level isolation (read-only FS,
# non-root, dropped caps) already in docker-compose.yml.
#
# Why this exists: pine_isolated was originally `internal: true`, which gave
# a real network-level egress guarantee but silently broke Docker's port
# publishing entirely (see docker-compose.yml's networks: comment, and the
# 2026-08-29 fix commit) -- every /api/pine/run call 503'd because
# 127.0.0.1:9871 was never actually bound. Switching pine_isolated to a
# normal (non-internal) network fixed the port but reopened real internet
# egress (confirmed live: `docker exec ares-pine-runtime wget 1.1.1.1`
# succeeded). This script closes that gap the way the docker-compose comment
# already recommended: drop new outbound connections from the pine_isolated
# subnet at the host firewall, in Docker's own DOCKER-USER chain (which
# Docker consults before its own rules and never overwrites).
#
# Idempotent: safe to run on every boot / redeploy. Removes any prior rule
# for this subnet before re-adding, so it never accumulates duplicates.
#
# Deployed via ops/pine-runtime/pine-egress-block.service.example (copy to
# /etc/systemd/system/pine-egress-block.service on the deploy host --
# *.service is gitignored repo-wide for secret-bearing units, this one has
# no secrets but follows the same .service.example convention regardless;
# systemd oneshot,
# After=docker.service, runs on every boot).
set -euo pipefail

SUBNET=$(docker network inspect vantage_pine_isolated --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null || true)
if [ -z "$SUBNET" ]; then
  echo "vantage_pine_isolated network not found -- is pine-runtime deployed? (docker compose up -d pine-runtime)" >&2
  exit 1
fi

# Remove any existing rule for this subnet first (idempotent re-apply, and
# self-heals if the subnet ever changes across a network recreation).
while iptables -C DOCKER-USER -s "$SUBNET" '!' -d "$SUBNET" -m conntrack --ctstate NEW -j DROP 2>/dev/null; do
  iptables -D DOCKER-USER -s "$SUBNET" '!' -d "$SUBNET" -m conntrack --ctstate NEW -j DROP
done

iptables -I DOCKER-USER -s "$SUBNET" '!' -d "$SUBNET" -m conntrack --ctstate NEW -j DROP
echo "pine-runtime egress block applied for $SUBNET"
