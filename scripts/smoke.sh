#!/usr/bin/env bash
# Stack smoke test (P1.W1.2): every service's /healthz + /readyz, plus the
# trust-boundary negative network checks from plan §2.1:
#   runtime-sandbox -/-> verifier fixtures, sandbox refused by the simulator
#   admin authorization boundary, ui -/-> simulators.
# REQ-035: the simulator admin API is an authorization boundary, not a
# no-route one — vendor-net peers may reach sim-*:8001 but get 401/403
# without SIM_ADMIN_TOKEN. The admin probes below distinguish "no route"
# from "reachable but rejected" and fail only when an unauthorized admin
# access is ACCEPTED (2xx).
# Positive reachability is asserted too (sandbox -> sim public API,
# control -> verifier), so a blanket network outage cannot pass as "isolated".
set -euo pipefail

SERVICES="control:8101 worker:8102 runtime-sandbox:8103 verifier:8104 sim-atlas:8105 sim-harbor:8106 sim-legacystay:8111 sim-meridian:8116 sim-fias-pabx:8118 edge-worker:8114 edge-relay:8120 mcp-hotel:8115 ui:8107 bench:8108"

fail() {
  echo "SMOKE FAIL: $1" >&2
  exit 1
}

echo ">> smoke: /healthz + /readyz on all services"
for entry in $SERVICES; do
  name="${entry%%:*}"
  port="${entry##*:}"
  for path in healthz readyz; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://localhost:${port}/${path}" || true)"
    [ "$code" = "200" ] || fail "$name /$path -> HTTP $code (want 200)"
    # AR-2-6: assert the body too, not just the code — a degraded service
    # answers /readyz with 503, and readyz must report status ok on a green stack.
    body="$(curl -s --max-time 5 "http://localhost:${port}/${path}" || true)"
    status="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('status',''))" "$body" 2>/dev/null || true)"
    [ "$status" = "ok" ] || fail "$name /$path body status '$status' (want ok)"
  done
  echo "  ok $name (:$port)"
done

# REQ-017: in local mode every service shares the host loopback, so Docker
# network attachment cannot isolate anything. The container probes below
# cannot run (no containers); the in-process allowlist checks still run
# inside runtime-sandbox /readyz ("runtime_target" check).
if [ "${AUTOBRIDGE_RUNTIME:-}" = "local" ] || [ -f .local/ACTIVE ]; then
  echo ">> smoke: network probes skipped: local mode (no Docker network isolation)"
  echo "  skipped: local mode — sandbox -/-> verifier/admin, ui -/-> sims, worker -/-> verifier, edge-net checks"
  echo "  skipped: local mode — simulator admin authorization probe (admin still needs SIM_ADMIN_TOKEN when set)"
  echo "smoke OK (local mode)"
  exit 0
fi

# Probe helper: run an HTTP GET from inside a service container.
# $1=service $2=url $3=want ("up" = must succeed, "down" = must fail)
probe() {
  local svc="$1" url="$2" want="$3" runner
  if [ "$svc" = "ui" ]; then
    runner="node -e \"fetch('$url',{signal:AbortSignal.timeout(5000)}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\""
  else
    runner="python -c \"import urllib.request; print(urllib.request.urlopen('$url', timeout=5).status)\""
  fi
  if docker compose exec -T "$svc" sh -c "$runner" >/dev/null 2>&1; then
    [ "$want" = "up" ] || fail "$svc can reach $url (must be unreachable)"
    echo "  ok $svc -> $url reachable (expected)"
  else
    [ "$want" = "down" ] || fail "$svc cannot reach $url (must be reachable)"
    echo "  ok $svc -/-> $url unreachable (expected)"
  fi
}

# GET probe with extra headers (the admin API 403s bare probes, so the
# plain probe() would report a reachable admin port as "down").
# $1=service $2=url $3=python dict literal of headers $4=want ("up"/"down")
probe_headers() {
  local svc="$1" url="$2" headers="$3" want="$4"
  local runner="python -c \"import urllib.request; req=urllib.request.Request('$url', headers=$headers); print(urllib.request.urlopen(req, timeout=5).status)\""
  if docker compose exec -T "$svc" sh -c "$runner" >/dev/null 2>&1; then
    [ "$want" = "up" ] || fail "$svc can reach $url (must be unreachable)"
    echo "  ok $svc -> $url reachable (expected)"
  else
    [ "$want" = "down" ] || fail "$svc cannot reach $url (must be reachable)"
    echo "  ok $svc -/-> $url unreachable (expected)"
  fi
}

# POST probe: attempt an admin write from inside a service container.
# $1=service $2=url $3=python dict literal of headers $4=want ("up"/"down")
probe_post() {
  local svc="$1" url="$2" headers="$3" want="$4"
  local runner="python -c \"import urllib.request; req=urllib.request.Request('$url', data=b'{}', headers=$headers, method='POST'); print(urllib.request.urlopen(req, timeout=5).status)\""
  if docker compose exec -T "$svc" sh -c "$runner" >/dev/null 2>&1; then
    [ "$want" = "up" ] || fail "$svc can POST $url (must be rejected)"
    echo "  ok $svc -> POST $url accepted (expected)"
  else
    [ "$want" = "down" ] || fail "$svc cannot POST $url (must be accepted)"
    echo "  ok $svc -/-> POST $url rejected (expected)"
  fi
}

# Admin GET probe (REQ-035): passes when the peer is REFUSED the admin API —
# either no route (connection failure) or reachable-but-rejected (401/403,
# the authorization boundary in simulators/common/admin.py). Fails only when
# the admin API answers 2xx with no token (unauthorized access accepted).
# $1=service $2=url (no auth headers are ever sent here)
probe_admin_denied() {
  local svc="$1" url="$2" out
  local runner="python -c \"import urllib.request, urllib.error
try:
    print(urllib.request.urlopen('$url', timeout=5).status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print('NOROUTE')\""
  out="$(docker compose exec -T "$svc" sh -c "$runner" 2>/dev/null | tr -d '[:space:]' || true)"
  case "$out" in
    401|403)
      echo "  ok $svc -/-> $url rejected (HTTP $out: reachable, unauthorized — auth boundary holds)"
      ;;
    NOROUTE|"")
      echo "  ok $svc -/-> $url no route (expected)"
      ;;
    *)
      fail "$svc reached simulator admin $url without a token (HTTP $out) — must be refused"
      ;;
  esac
}

echo ">> smoke: positive reachability (guards against blanket outages)"
probe runtime-sandbox http://sim-atlas:8000/healthz up
probe runtime-sandbox http://sim-harbor:8000/healthz up
probe runtime-sandbox http://sim-legacystay:8000/healthz up
probe runtime-sandbox http://sim-meridian:8000/healthz up
probe control http://verifier:8000/healthz up
probe edge-worker http://edge-relay:8000/healthz up
probe edge-worker http://sim-fias-pabx:8000/healthz up
probe control http://edge-relay:8000/healthz up
probe control http://mcp-hotel:8000/healthz up

echo ">> smoke: negative network checks (plan §2.1 trust boundaries + §5 S2 edge)"
probe runtime-sandbox http://verifier:8000/healthz down
# Simulator admin API (REQ-035): authorization boundary, not no-route —
# refuse (no route or 401/403) is a pass; 2xx without a token is a fail.
probe_admin_denied runtime-sandbox http://sim-atlas:8001/healthz
probe_admin_denied runtime-sandbox http://sim-harbor:8001/healthz
probe_admin_denied runtime-sandbox http://sim-legacystay:8001/healthz
probe_admin_denied runtime-sandbox http://sim-meridian:8001/healthz
probe ui http://sim-atlas:8000/healthz down
probe ui http://sim-harbor:8000/healthz down
probe ui http://sim-legacystay:8000/healthz down
probe ui http://sim-meridian:8000/healthz down
probe ui http://verifier:8000/healthz down
probe worker http://verifier:8000/healthz down
probe control http://sim-fias-pabx:8000/healthz down
probe runtime-sandbox http://sim-fias-pabx:8000/healthz down
probe ui http://sim-fias-pabx:8000/healthz down
probe worker http://sim-fias-pabx:8000/healthz down
probe edge-worker http://verifier:8000/healthz down
probe edge-worker http://sim-atlas:8000/healthz down
probe_admin_denied edge-worker http://sim-fias-pabx:8001/healthz
probe edge-worker http://mcp-hotel:8000/healthz down
probe edge-relay http://verifier:8000/healthz down
probe_admin_denied edge-relay http://sim-atlas:8001/healthz
probe_admin_denied edge-relay http://sim-fias-pabx:8001/healthz

echo ">> smoke: FIAS admin reachable from control on admin-net only (REQ-034)"
# The FIAS data plane (:8000, edge-net only) stays unreachable from control
# — asserted "down" above. Its admin listener (:8001) MUST be reachable
# from control through the sim-fias-pabx-admin sidecar (which carries the
# sim-fias-pabx DNS alias on admin-net), or /demo/reset + /demo/inject-fault
# cannot touch FIAS.
# The admin API 403s bare probes, so send the admin headers (with the token:
# non-empty by default since REQ-035, via the shell or .env).
ADMIN_HDR="{'X-Admin-Network': 'admin-net'}"
if [ -n "${SIM_ADMIN_TOKEN:-}" ]; then
  ADMIN_HDR="{'X-Admin-Network': 'admin-net', 'X-Admin-Token': '${SIM_ADMIN_TOKEN}'}"
fi
probe_headers control http://sim-fias-pabx:8001/healthz "$ADMIN_HDR" up

echo ">> smoke: simulator admin writes need authorization, not just the published header (AR-1-1)"
# A vendor-net peer with no headers must always be rejected on admin writes.
# REQ-035: these fail only when an unauthorized write is ACCEPTED (2xx),
# never when it is merely reachable — reachability without the token is
# covered by the probe_admin_denied checks above.
probe_post runtime-sandbox http://sim-atlas:8001/admin/reset "{}" down
if [ -n "${SIM_ADMIN_TOKEN:-}" ]; then
  # With the token configured (the default since REQ-035), the published
  # X-Admin-Network header alone must NOT authorize a write — only the token does.
  probe_post runtime-sandbox http://sim-atlas:8001/admin/reset "{'X-Admin-Network': 'admin-net'}" down
else
  echo "  WARN SIM_ADMIN_TOKEN explicitly empty — admin runs header-only dev mode (fresh clones default to sim-admin-dev-token)"
fi

echo "smoke OK"
