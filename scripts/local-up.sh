#!/usr/bin/env bash
# REQ-017: run the whole AutoBridge stack as local processes (no Docker).
#
# Usage: make local-up   (or ./scripts/local-up.sh)
#
# Starts every service the demo needs on the SAME host ports compose
# publishes (control 8101, worker 8102, runtime-sandbox 8103, verifier 8104,
# sims 8105/8106/8111/8116/8118 + admin 8109/8110/8112/8117/8119, edge-worker 8114,
# edge-relay 8120, mcp-hotel 8115, bench 8108, ui 8107), with SQLite in ./.local/,
# mcp-hotel 8115, bench 8108, ui 8107), with SQLite in ./.local/,
# logs in ./.local/logs/ and PIDs in ./.local/pids/.
#
# Trust boundaries in local mode (see docs/safety-model.md): the engine
# still never imports verifier/ or simulators/ (import-linter), the
# verifier and simulator admin still run as separate processes, and admin
# writes still need SIM_ADMIN_TOKEN when set. What local mode CANNOT
# enforce is Docker network separation (vendor-net / verifier-net /
# admin-net / edge-net are one loopback here) — printed once below and
# reported as "local mode: no network isolation" on every /readyz.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_DIR="$REPO_ROOT/.local"
LOG_DIR="$LOCAL_DIR/logs"
PID_DIR="$LOCAL_DIR/pids"

# Load repo .env when present (tokens, model ids). Parsed as data, never
# executed: only non-empty KEY=value lines are exported, comments and
# inline `#` comments are ignored, and explicit environment wins (REQ-014).
DOTENV_PATH="$REPO_ROOT/.env"
# shellcheck disable=SC1091
. "$REPO_ROOT/scripts/load_dotenv.sh"

mkdir -p "$LOCAL_DIR" "$LOG_DIR" "$PID_DIR"

if [ -f "$LOCAL_DIR/ACTIVE" ]; then
  echo "local stack already up (.local/ACTIVE present) — run 'make local-down' first" >&2
  exit 1
fi

command -v uv >/dev/null || { echo "local-up: 'uv' not found — install Python 3.12 + uv first (see README 'No Docker')" >&2; exit 1; }

# REQ-022 owner check B1: a keyed .env without the bundle pin would leave
# edge-worker at readyz 503 forever — fail fast with the fix, not a timeout.
"$REPO_ROOT/scripts/check-edge-key.sh"

# ---- Local-mode environment (after .env, so loopback always wins) ----
export AUTOBRIDGE_RUNTIME=local
export PYTHONPATH="$REPO_ROOT:$REPO_ROOT/src"
export DATABASE_URL="${DATABASE_URL:-sqlite:///$LOCAL_DIR/autobridge.db}"
export VERIFIER_URL="http://localhost:8104"
# REQ-029 (AR-1-2 residual, fail-closed): the gate never skips on this
# stack — a release persists only after a passing verifier verdict. The
# worker verifies through control's /internal/verify proxy when it has no
# direct verifier route (CONTROL_URL below; same loopback here).
export CONTROL_URL="http://localhost:8101"
export AUTOBRIDGE_REQUIRE_VERIFIER_GATE="1"
unset ADMIN_NET_HOST
export ATLAS_BASE_URL="http://localhost:8105"
export HARBOR_BASE_URL="http://localhost:8106"
export LEGACYSTAY_BASE_URL="http://localhost:8111"
export MERIDIAN_BASE_URL="http://localhost:8116"
export EDGE_RELAY_URL="http://localhost:8120"
# REQ-027 owner check (sim keys by default): every simulator credential a
# demo sync uses gets a working demo value unless the operator set a real
# one (.env / explicit env wins via ${VAR:-default}). Atlas/Harbor/
# LegacyStay accept any non-empty key; Meridian checks its documented pair
# (simulators/meridianpms/app.py). These are simulator demo values, not
# secrets — never real vendor credentials.
export ATLAS_API_KEY="${ATLAS_API_KEY:-sim-demo-key}"
export HARBOR_API_KEY="${HARBOR_API_KEY:-sim-demo-key}"
export LEGACYSTAY_API_KEY="${LEGACYSTAY_API_KEY:-sim-demo-key}"
export MERIDIAN_CLIENT_TOKEN="${MERIDIAN_CLIENT_TOKEN:-meridian-client-token}"
export MERIDIAN_ACCESS_TOKEN="${MERIDIAN_ACCESS_TOKEN:-meridian-access-token}"
# REQ-035: simulator admin writes need a shared token (AR-1-1). Ship the
# labelled demo value by default so a fresh clone never runs header-only;
# an explicit value (.env / shell) always wins. Simulator demo value, not
# a secret — never a real credential.
export SIM_ADMIN_TOKEN="${SIM_ADMIN_TOKEN:-sim-admin-dev-token}"
# REQ-031: the PABX edge release drains the edge relay through the relay's
# operator endpoints, which expect AUTOBRIDGE_CONTROL_TOKEN (open when it
# is unset). EDGE_TOKEN must carry that same value when a control token is
# set, or any non-empty demo value otherwise; an explicit EDGE_TOKEN
# always wins. Simulator demo value, not a secret.
export EDGE_TOKEN="${EDGE_TOKEN:-${AUTOBRIDGE_CONTROL_TOKEN:-sim-demo-key}}"
# REQ-030 item 1: the sync scheduler ticks every active connector on this
# cadence (unset/empty/0 disables it). Demo value, not a secret.
export AUTOBRIDGE_SYNC_INTERVAL_S="${AUTOBRIDGE_SYNC_INTERVAL_S:-10}"
export EDGE_RELAY_DB="$LOCAL_DIR/edge_relay.db"
export EDGE_STORE_PATH="$LOCAL_DIR/edge_agent.db"
# Serialize first-boot schema creation (REQ-039): control, worker and the
# engine each create tables at startup; on a fresh (or shared-filesystem)
# database the concurrent writers corrupt it ("database disk image is
# malformed" on the first write). Migrating once up front makes every
# service boot a no-op read afterwards.
uv run python -c "from autobridge.control.migrate import ensure_migrated; print('migrated:', ensure_migrated())"
# Persist the effective env for local-down diagnostics (values only used
# for logging; secrets stay in .env and are never printed).
export LOCAL_ACTIVE_ENV=1

start_svc() {
  local name="$1"; shift
  local port="$1"; shift
  local log="$LOG_DIR/${name}.log"
  # shellcheck disable=SC2068
  nohup "$@" >"$log" 2>&1 &
  echo $! >"$PID_DIR/${name}.pid"
  echo "  started $name (:$port, pid $(cat "$PID_DIR/${name}.pid"), log $log)"
}

echo ">> local-up: starting services as local processes (AUTOBRIDGE_RUNTIME=local)"
echo "   DB: $DATABASE_URL"
echo "   NOTE: local mode: no network isolation — Docker vendor-net / verifier-net /"
echo "   admin-net / edge-net separation is NOT enforced here (all loopback). The engine"
echo "   still never imports verifier/ or simulators/; admin writes need"
echo "   SIM_ADMIN_TOKEN (demo default, override in .env). See docs/safety-model.md."

cd "$REPO_ROOT"
start_svc control 8101 uv run uvicorn autobridge.control.app:app --host 127.0.0.1 --port 8101
start_svc worker 8102 uv run uvicorn autobridge.engine.app:app --host 127.0.0.1 --port 8102
start_svc runtime-sandbox 8103 uv run uvicorn autobridge.runtime.app:app --host 127.0.0.1 --port 8103
start_svc verifier 8104 uv run uvicorn verifier.app:app --host 127.0.0.1 --port 8104
APP_MODULE=simulators.atlaspms.app:app SIM_PUBLIC_PORT=8105 SIM_ADMIN_PORT=8109 \
SIM_STATE_FILE=/tmp/sim_atlas_state.json \
  start_svc sim-atlas 8105 uv run python -m simulators.run
APP_MODULE=simulators.harborpms.app:app SIM_PUBLIC_PORT=8106 SIM_ADMIN_PORT=8110 \
SIM_STATE_FILE=/tmp/sim_harbor_state.json \
  start_svc sim-harbor 8106 uv run python -m simulators.run
APP_MODULE=simulators.legacystay.app:app SIM_PUBLIC_PORT=8111 SIM_ADMIN_PORT=8112 \
SIM_STATE_FILE=/tmp/sim_legacystay_state.json \
  start_svc sim-legacystay 8111 uv run python -m simulators.run
APP_MODULE=simulators.meridianpms.app:app SIM_PUBLIC_PORT=8116 SIM_ADMIN_PORT=8117 \
SIM_STATE_FILE=/tmp/sim_meridian_state.json \
  start_svc sim-meridian 8116 uv run python -m simulators.run
# REQ-022 FIAS PABX (edge-net in Compose; loopback here): dials FIAS_HOST,
# served below by the edge agent's host listener.
APP_MODULE=simulators.fias_pabx.app:app SIM_PUBLIC_PORT=8118 SIM_ADMIN_PORT=8119 \
ADMIN_EXTRA_ROUTER=simulators.fias_pabx.admin:router SIM_STATE_FILE=/tmp/sim_fias_state.json \
FIAS_HOST=127.0.0.1 FIAS_PORT=5010 \
  start_svc sim-fias-pabx 8118 uv run python -m simulators.run
# REQ-022 B: cloud command queue (edge-net in Compose; loopback here).
start_svc edge-relay 8120 uv run uvicorn autobridge.edge.relay:app --host 127.0.0.1 --port 8120
# REQ-022 B: outbound-only on-premise agent (edge-net in Compose; loopback
# here). Listens for the PABX dial-out on 127.0.0.1:5010, polls the relay.
FIAS_LISTEN_HOST=127.0.0.1 FIAS_LISTEN_PORT=5010 \
  start_svc edge-worker 8114 uv run uvicorn autobridge.edge.app:app --host 127.0.0.1 --port 8114
start_svc mcp-hotel 8115 uv run uvicorn mcp_stretch.app:app --host 127.0.0.1 --port 8115
start_svc bench 8108 uv run uvicorn bench.app:app --host 127.0.0.1 --port 8108

# ---- UI (Next.js, same-origin /api rewrite; CONTROL_URL defaults to localhost:8101) ----
if command -v npm >/dev/null; then
  if [ ! -d "$REPO_ROOT/ui/node_modules" ]; then
    echo ">> local-up: installing UI deps (ui/node_modules missing)"
    (cd "$REPO_ROOT/ui" && npm ci)
  fi
  # REQ-021 owner check: the old marker-only rule rebuilt solely when
  # ui/.next/BUILD_ID was missing, so a `git pull` kept serving the stale
  # build (e.g. the missing /knowledge page). Rebuild whenever the ui/
  # sources changed since the last build: the git tree hash of HEAD:ui
  # plus a hash of uncommitted ui/ changes, stored next to the build.
  UI_TREE=""
  if UI_HEAD_TREE=$(git -C "$REPO_ROOT" rev-parse "HEAD:ui" 2>/dev/null); then
    # Hash the content of uncommitted changes (tracked diff + untracked files), not just
    # the list of changed paths: a second edit to an already-modified file must rebuild.
    UI_DIRTY=$( { git -C "$REPO_ROOT" diff HEAD -- ui 2>/dev/null
                  git -C "$REPO_ROOT" ls-files -z --others --exclude-standard -- ui 2>/dev/null \
                    | (cd "$REPO_ROOT" && xargs -0 cat 2>/dev/null); } \
                | git -C "$REPO_ROOT" hash-object --stdin 2>/dev/null || echo "nodirty")
    UI_TREE="$UI_HEAD_TREE/$UI_DIRTY"
  fi
  UI_TREE_FILE="$LOCAL_DIR/ui-tree"
  UI_STORED=""
  if [ -f "$UI_TREE_FILE" ]; then
    UI_STORED=$(cat "$UI_TREE_FILE" 2>/dev/null || true)
  fi
  if [ ! -f "$REPO_ROOT/ui/.next/BUILD_ID" ]; then
    UI_REASON="no production build marker (first build)"
  elif [ -z "$UI_TREE" ]; then
    UI_REASON=""
  elif [ "$UI_STORED" != "$UI_TREE" ]; then
    UI_REASON="ui/ sources changed since the last build"
  else
    UI_REASON=""
  fi
  if [ -n "$UI_REASON" ]; then
    echo ">> local-up: building UI ($UI_REASON)"
    (cd "$REPO_ROOT/ui" && npm run build)
    if [ -n "$UI_TREE" ]; then
      printf '%s' "$UI_TREE" >"$UI_TREE_FILE"
    fi
  else
    if [ -z "$UI_TREE" ]; then
      echo ">> local-up: reusing existing UI build (git unavailable, cannot check for source changes)"
    else
      echo ">> local-up: reusing existing UI build (ui/ unchanged since last build)"
    fi
  fi
  (
    cd "$REPO_ROOT/ui"
    CONTROL_URL="http://localhost:8101" nohup npm run start -- -p 8107 >"$LOG_DIR/ui.log" 2>&1 &
    echo $! >"$PID_DIR/ui.pid"
  )
  echo "  started ui (:8107, pid $(cat "$PID_DIR/ui.pid"), log $LOG_DIR/ui.log)"
else
  echo "  WARN npm not found — UI skipped (install Node 22 + npm, then re-run; demo/smoke still work)" >&2
fi

echo ">> local-up: waiting for every /readyz"
"$REPO_ROOT/scripts/wait_readyz.sh" 180

date -u +"%Y-%m-%dT%H:%M:%SZ" >"$LOCAL_DIR/ACTIVE"
echo "local stack up: http://localhost:8107 (ui) · control http://localhost:8101"
echo "stop with: make local-down"
