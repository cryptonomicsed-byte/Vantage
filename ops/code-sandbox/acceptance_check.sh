#!/usr/bin/env bash
# Gap A acceptance check: the code sandbox hard gate is off.
#
# Preconditions this script does NOT set up for you (deliberately — both are
# live-service changes that should be applied knowingly, not as a side effect
# of running a check script):
#   1. `docker compose --profile code up -d code-sandbox` (container running)
#   2. CODE_SANDBOX_URL exported into the Vantage process's actual environment
#      (not just .env — os.environ.get() reads process env, and this repo
#      never calls load_dotenv(); see /etc/systemd/system/vantage.service.d/
#      for the drop-in that has to be present) followed by a restart of
#      vantage.service so it re-reads its environment.
#
# Usage: ops/code-sandbox/acceptance_check.sh <agent-x-agent-key>
set -euo pipefail

AGENT_KEY="${1:?usage: acceptance_check.sh <X-Agent-Key>}"
BASE_URL="${VANTAGE_URL:-http://localhost:8001}"

status_code=$(curl -s -o /tmp/gap_a_status.json -w '%{http_code}' \
  "$BASE_URL/api/workspace/status" -H "X-Agent-Key: $AGENT_KEY")

echo "HTTP $status_code"
cat /tmp/gap_a_status.json
echo

if [ "$status_code" != "200" ]; then
  echo "FAIL: expected 200, got $status_code" >&2
  exit 1
fi

available=$(python3 -c "import json;print(json.load(open('/tmp/gap_a_status.json'))['available'])")
if [ "$available" != "True" ]; then
  echo "FAIL: sandbox reports available=$available — check the container and CODE_SANDBOX_URL" >&2
  exit 1
fi

echo "PASS: workspace status is 200 and available"
