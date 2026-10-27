#!/usr/bin/env bash
# Wait until every Compose service reports /readyz status ok (P1.W1.2).
# Usage: ./scripts/wait_readyz.sh [timeout_seconds]
set -euo pipefail

TIMEOUT="${1:-120}"

# service -> host port (mirrors compose.yaml)
SERVICES="control:8101 worker:8102 runtime-sandbox:8103 verifier:8104 sim-atlas:8105 sim-harbor:8106 sim-legacystay:8111 ui:8107 bench:8108"

deadline=$((SECONDS + TIMEOUT))
for entry in $SERVICES; do
  name="${entry%%:*}"
  port="${entry##*:}"
  while true; do
    body="$(curl -s --max-time 3 "http://localhost:${port}/readyz" || true)"
    if [ -n "$body" ] && python3 -c "import json,sys; sys.exit(0 if json.loads(sys.argv[1]).get('status')=='ok' else 1)" "$body" 2>/dev/null; then
      echo "readyz ok: $name (:$port)"
      break
    fi
    if [ $SECONDS -ge $deadline ]; then
      echo "readyz TIMEOUT waiting for $name (:$port)" >&2
      exit 1
    fi
    sleep 2
  done
done
echo "all services readyz green"
