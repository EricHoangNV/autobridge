#!/usr/bin/env bash
# Stack smoke test (P1.W1.2): every service's /healthz + /readyz, plus the
# trust-boundary negative network checks from plan §2.1:
#   runtime-sandbox -/-> verifier fixtures, sandbox -/-> simulator admin,
#   ui -/-> simulators.
# Positive reachability is asserted too (sandbox -> sim public API,
# control -> verifier), so a blanket network outage cannot pass as "isolated".
set -euo pipefail

SERVICES="control:8101 worker:8102 runtime-sandbox:8103 verifier:8104 sim-atlas:8105 sim-harbor:8106 sim-legacystay:8111 sim-edgepos:8113 edge-worker:8114 mcp-hotel:8115 ui:8107 bench:8108"

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

echo ">> smoke: positive reachability (guards against blanket outages)"
probe runtime-sandbox http://sim-atlas:8000/healthz up
probe runtime-sandbox http://sim-harbor:8000/healthz up
probe runtime-sandbox http://sim-legacystay:8000/healthz up
probe control http://verifier:8000/healthz up
probe edge-worker http://sim-edgepos:8000/healthz up
probe control http://mcp-hotel:8000/healthz up

echo ">> smoke: negative network checks (plan §2.1 trust boundaries + §5 S2 edge)"
probe runtime-sandbox http://verifier:8000/healthz down
probe runtime-sandbox http://sim-atlas:8001/healthz down
probe runtime-sandbox http://sim-harbor:8001/healthz down
probe runtime-sandbox http://sim-legacystay:8001/healthz down
probe ui http://sim-atlas:8000/healthz down
probe ui http://sim-harbor:8000/healthz down
probe ui http://sim-legacystay:8000/healthz down
probe ui http://verifier:8000/healthz down
probe worker http://verifier:8000/healthz down
probe control http://sim-edgepos:8000/healthz down
probe runtime-sandbox http://sim-edgepos:8000/healthz down
probe ui http://sim-edgepos:8000/healthz down
probe edge-worker http://verifier:8000/healthz down
probe edge-worker http://sim-atlas:8000/healthz down
probe edge-worker http://sim-edgepos:8001/healthz down
probe edge-worker http://mcp-hotel:8000/healthz down

echo ">> smoke: simulator admin writes need authorization, not just the published header (AR-1-1)"
# A vendor-net peer with no headers must always be rejected on admin writes.
probe_post runtime-sandbox http://sim-atlas:8001/admin/reset "{}" down
if [ -n "${SIM_ADMIN_TOKEN:-}" ]; then
  # With the token configured, the published X-Admin-Network header alone
  # must NOT authorize a write — only the token does.
  probe_post runtime-sandbox http://sim-atlas:8001/admin/reset "{'X-Admin-Network': 'admin-net'}" down
else
  echo "  WARN SIM_ADMIN_TOKEN unset — admin runs header-only dev mode (set a random value in .env)"
fi

echo "smoke OK"
