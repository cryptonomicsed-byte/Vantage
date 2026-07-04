#!/bin/bash
# Code Collab Pipeline Agent
# OpenCode generates code → pushes to Gitea → STIX scans → posts to Vantage
#
# Usage: opencode "build a React dashboard" → auto-pushes to Gitea agent-workspace
#        pipeline scan → runs STIX on the repo
#        pipeline status → shows pipeline health

set -e
GITEA_URL="http://localhost:3001"
GITEA_TOKEN="2551cd513d981914a5be801068e797eb7e1878ac"
WORKSPACE="agent-workspace"
VANTAGE_URL="http://localhost:8001"

case "${1:-status}" in
  generate|gen)
    PROM...
    echo "🤖 OpenCode generating..."
    cd /opt/ares/${WORKSPACE}
    git pull origin main 2>/dev/null || true
    opencode "${@:2}"
    git add -A && git commit -m "opencode: ${@:2}" && git push origin main
    echo "✅ Pushed to Gitea"
    ;;

  scan)
    REPO="${2:-ares-bot/agent-workspace}"
    echo "🛡️  STIX scanning $REPO..."
    curl -s -X POST "${VANTAGE_URL}/api/code/repo/${REPO}/scan" \
      -H "X-Agent-Key: $(cat /opt/ares/.vantage_key)" \
      -H "Content-Type: application/json"
    echo ""
    echo "✅ Scan triggered"
    ;;

  status)
    echo "=== CODE COLLAB PIPELINE ==="
    echo "OpenCode: $(opencode --version 2>/dev/null || echo 'installed')"
    echo -n "Gitea: "; curl -s "${GITEA_URL}/api/v1/version" | python3 -c "import json,sys;print(json.load(sys.stdin).get('version','?'))"
    echo -n "STIX Webhook: "; pgrep -f stix_webhook >/dev/null && echo "running :9876" || echo "down"
    echo -n "STIX Scanner: "; pgrep -f stix_scanner >/dev/null && echo "running" || echo "down"
    echo -n "herdr: "; [ -f /opt/ares/herdr/target/release/herdr ] && echo "built" || echo "not built (Rust 1.85+ needed)"
    echo -n "supermemory: "; curl -s -o /dev/null -w '%{http_code}' http://localhost:3002 2>/dev/null && echo " :3002" || echo "not running"
    echo "Repos: $(curl -s "${GITEA_URL}/api/v1/repos/search?limit=20" | python3 -c "import json,sys;print(len(json.load(sys.stdin).get('data',[])))")"
    ;;
  *)
    echo "Usage: pipeline {generate|scan|status}"
    echo "  generate 'prompt'  - OpenCode generates code → pushes to Gitea"
    echo "  scan [repo]         - Run STIX scan on a repo"
    echo "  status              - Show pipeline health"
    ;;
esac
