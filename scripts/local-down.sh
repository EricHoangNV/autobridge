#!/usr/bin/env bash
# REQ-017: stop the Docker-free local stack started by local-up.sh.
# Kills every PID in ./.local/pids/, removes the ACTIVE marker. Data
# (./.local/autobridge.db) and logs (./.local/logs/) are kept.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_DIR="$REPO_ROOT/.local/pids"

if [ ! -d "$PID_DIR" ]; then
  echo "local-down: nothing to stop (no .local/pids)"
  exit 0
fi

# Stop UI first (it fronts the control API), then the rest.
for name in ui bench mcp-hotel edge-worker edge-relay sim-fias-pabx sim-meridian sim-legacystay sim-harbor sim-atlas verifier runtime-sandbox worker control; do
  pidfile="$PID_DIR/${name}.pid"
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      # Give uvicorn a moment, then force.
      for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
      done
      if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
        echo "  killed $name (pid $pid, forced)"
      else
        echo "  stopped $name (pid $pid)"
      fi
    else
      echo "  $name already stopped (stale pid $pid)"
    fi
    rm -f "$pidfile"
  fi
done

rm -f "$REPO_ROOT/.local/ACTIVE"
echo "local stack down (data kept in .local/autobridge.db; logs in .local/logs/)"
