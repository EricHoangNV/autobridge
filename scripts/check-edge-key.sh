#!/usr/bin/env bash
# Fail fast when a keyed deployment is missing its bundle signature.
#
# With EDGE_RELEASE_SECRET set, the edge worker verifies the declarative
# command bundle and refuses to run unsigned (fail-closed, AR-2-4). Without
# this check `make up` / `make local-up` start the whole stack and only
# report `readyz TIMEOUT waiting for edge-worker`, which hides the one
# command that fixes it. This script prints that command and exits 1
# BEFORE anything starts. Sourced through scripts/load_dotenv.sh so the
# effective environment (explicit env wins over .env) is what is checked.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$REPO_ROOT/load_dotenv.sh"

if [ -n "${EDGE_RELEASE_SECRET:-}" ] && [ -z "${EDGE_BUNDLE_SIGNATURE:-}" ]; then
  cat >&2 <<'EOF'
edge-sign: keyed deployment without a bundle signature.
EDGE_RELEASE_SECRET is set but EDGE_BUNDLE_SIGNATURE is empty, so the edge
worker would refuse every link with "command bundle is unsigned" and the
stack would never become ready. Pin the bundle first:

  make edge-sign

then re-run the command you just ran. (Never print or commit real secrets.)
EOF
  exit 1
fi
exit 0
