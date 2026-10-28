#!/usr/bin/env bash
# Image reproducibility check (AR-2-5): rebuild the verifier image twice from
# the committed Dockerfile + lockfile and diff `pip freeze`. The verifier is
# the root of every acceptance decision, so two builds of the same commit
# must resolve byte-identical dependencies. Fails loudly on any difference.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TAG_A="autobridge-verify-repro-a"
TAG_B="autobridge-verify-repro-b"

echo ">> building verifier image twice (AR-2-5 reproducibility check)"
docker build -f verifier/Dockerfile -t "$TAG_A" .
docker build --no-cache -f verifier/Dockerfile -t "$TAG_B" .

FREEZE_A="$(mktemp)"
FREEZE_B="$(mktemp)"
trap 'rm -f "$FREEZE_A" "$FREEZE_B"' EXIT

docker run --rm "$TAG_A" pip freeze --exclude-editable | sort > "$FREEZE_A"
docker run --rm "$TAG_B" pip freeze --exclude-editable | sort > "$FREEZE_B"

if diff -u "$FREEZE_A" "$FREEZE_B"; then
  echo "image reproducibility OK: identical pip freeze across two builds"
else
  echo "FAIL: verifier image is not reproducible (pip freeze differs)" >&2
  exit 1
fi

docker rmi "$TAG_A" "$TAG_B" >/dev/null
