#!/usr/bin/env bash
# Stack smoke test (P1.W1.2): every service's /healthz + /readyz, plus the
# trust-boundary negative network checks from plan §2.1:
#   runtime-sandbox -/-> verifier fixtures, sandbox -/-> simulator admin,
#   ui -/-> simulators.
# Positive reachability is asserted too (sandbox -> sim public API,
# control -> verifier), so a blanket network outage cannot pass as "isolated".
set -euo pipefail

SERVICES="control:8101 worker:8102 runtime-sandbox:8103 verifier:8104 sim-atlas:8105 sim-harbor:8106 sim-legacystay:8111 ui:8107 bench:8108"

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
  done
  echo "  ok $name (:$port)"
done

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

echo ">> smoke: positive reachability (guards against blanket outages)"
probe runtime-sandbox http://sim-atlas:8000/healthz up
probe runtime-sandbox http://sim-harbor:8000/healthz up
probe runtime-sandbox http://sim-legacystay:8000/healthz up
probe control http://verifier:8000/healthz up

echo ">> smoke: negative network checks (plan §2.1 trust boundaries)"
probe runtime-sandbox http://verifier:8000/healthz down
probe runtime-sandbox http://sim-atlas:8001/healthz down
probe runtime-sandbox http://sim-harbor:8001/healthz down
probe runtime-sandbox http://sim-legacystay:8001/healthz down
probe ui http://sim-atlas:8000/healthz down
probe ui http://sim-harbor:8000/healthz down
probe ui http://sim-legacystay:8000/healthz down
probe ui http://verifier:8000/healthz down
probe worker http://verifier:8000/healthz down

echo "smoke OK"
