#!/usr/bin/env bash
# Pin the edge command bundle for keyed deployments (REQ-022 owner check B1).
#
# Usage: make edge-sign
#
# Mints EDGE_BUNDLE_SIGNATURE over the declarative command bundle
# (src/autobridge/edge/agent_bundle.json) and writes it into .env,
# creating .env when missing. With EDGE_RELEASE_SECRET set and the
# signature pinned, `make up` / `make local-up` come up with the PABX link
# connected; without it they fail fast with the hint (see
# scripts/check-edge-key.sh) instead of a readyz timeout.
#
# Secrets discipline: only EDGE_BUNDLE_SIGNATURE is ever written or named
# here; no other secret value is printed, and the minted value itself is
# NOT printed either (it lives in .env, which is git-ignored).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOTENV_FILE="${DOTENV_PATH:-$REPO_ROOT/.env}"

command -v uv >/dev/null || { echo "edge-sign: 'uv' not found — install Python 3.12 + uv first" >&2; exit 1; }

# Mint with the effective environment (explicit env wins over .env): the
# signature must use the same EDGE_RELEASE_SECRET the worker will verify
# with, or a keyed worker rejects the pin as a mismatch.
# shellcheck disable=SC1091
. "$REPO_ROOT/scripts/load_dotenv.sh"

signature="$(uv run --quiet python -c "from autobridge.edge.bundle import mint; print(mint())")"
if [ -z "$signature" ]; then
  echo "edge-sign: mint produced an empty signature — refusing to write" >&2
  exit 1
fi

if [ ! -f "$DOTENV_FILE" ]; then
  printf '# AutoBridge local secrets (git-ignored). Set random values before exposing the stack.\n' > "$DOTENV_FILE"
  echo "edge-sign: created $DOTENV_FILE" >&2
fi

python3 - "$DOTENV_FILE" "$signature" <<'PY'
import re
import sys

path, signature = sys.argv[1], sys.argv[2]
if not re.fullmatch(r"[0-9a-f]{64}", signature):
    sys.exit("edge-sign: minted signature has an unexpected shape — refusing to write")
with open(path, encoding="utf-8") as fh:
    lines = fh.read().splitlines(keepends=True)
key_re = re.compile(r"^EDGE_BUNDLE_SIGNATURE\s*=")
replaced = False
out: list[str] = []
for line in lines:
    stripped = line.lstrip()
    if not stripped.startswith("#") and key_re.match(line):
        out.append(f"EDGE_BUNDLE_SIGNATURE={signature}\n")
        replaced = True
    else:
        out.append(line)
if not replaced:
    if out and not out[-1].endswith("\n"):
        out[-1] += "\n"
    out.append(f"EDGE_BUNDLE_SIGNATURE={signature}\n")
with open(path, "w", encoding="utf-8") as fh:
    fh.writelines(out)
print(f"edge-sign: {'updated' if replaced else 'added'} EDGE_BUNDLE_SIGNATURE in {path}")
PY
