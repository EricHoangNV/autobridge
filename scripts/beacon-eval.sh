#!/usr/bin/env bash
# REQ-025 item 6b: one unattended beacon-eval command (owner, hands-on: one
# command + one "yes"). REQ-077 item 1: anyone can rerun it end to end.
#
#   make beacon-eval FAULTS=<N> [DELAY_MIN=20] [PUSH=yes] [CONFIRM=yes]
#
# Checks the tree is clean and no ralph loop runs in this checkout; freezes
# a NEW future round (one protocol per run, never resuming another run's
# results — only an unrun protocol of this checkout is resumed); commits it,
# tags it `eval-freeze-<round>`; pushes only with PUSH=yes (before the
# round) and prints the fork-push line otherwise; records the push time;
# waits for the round; runs `bench-beacon` for that round unattended; writes
# experiments/beacon/<round>/EVIDENCE.md (+ terminal-style evidence.png for
# the deck); commits the results and pushes them only with PUSH=yes.
#
# Resumable: rerunning after the freeze continues the same round while its
# protocol is still unrun (never a new one silently).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# shellcheck source=beacon-lib.sh
source ./scripts/beacon-lib.sh

FAULTS="${FAULTS:-25}"
DELAY_MIN="${DELAY_MIN:-20}"
CONFIRM="${CONFIRM:-}"
PUSH="${PUSH:-}"
export CONFIRM PUSH

echo ">> beacon-eval: FAULTS=$FAULTS DELAY_MIN=$DELAY_MIN"

# 1. Guards: clean tree, no ralph loop in this checkout's path.
if [ -n "$(git status --porcelain)" ]; then
  echo "beacon-eval: refusing — git tree is dirty (commit or stash first)" >&2
  git status --porcelain | head -10 >&2
  exit 2
fi
if beacon_ralph_running_here "$REPO_ROOT"; then
  echo "beacon-eval: refusing — a ralph loop runs in this checkout ($REPO_ROOT)" >&2
  exit 2
fi

# 2. Freeze a new future round, or resume this checkout's unrun protocol.
if ! PROTOCOL="$(uv run python -m bench.beacon_run resolve-protocol)"; then
  ROUND_AFTER="$(python3 -c "import datetime; print((datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=$DELAY_MIN)).isoformat())")"
  echo ">> beacon-eval: freezing a new round with ROUND_AFTER=$ROUND_AFTER"
  ROUND_AFTER="$ROUND_AFTER" FAULTS="$FAULTS" make eval-freeze
  PROTOCOL="$(uv run python -m bench.beacon_run resolve-protocol)"
  echo ">> beacon-eval: froze $PROTOCOL"
else
  echo ">> beacon-eval: resuming unrun protocol $PROTOCOL (never another run's results)"
fi
# The round is parsed the way parse_protocol parses (the freeze writes
# `- beacon-target-round: N`, which a bare grep '^beacon-target-round:'
# never matched — REQ-077 item 1 bug).
TARGET_ROUND="$(uv run python -m bench.beacon_run protocol-round --protocol "$PROTOCOL")"
echo ">> beacon-eval: target round $TARGET_ROUND"
TAG="eval-freeze-$TARGET_ROUND"

# 3. Commit + tag the freeze (per-run tag), push only with PUSH=yes.
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo ">> beacon-eval: tag $TAG already exists — reusing"
else
  git add "$PROTOCOL"
  git commit -m "eval-freeze: beacon protocol for round $TARGET_ROUND" 2>/dev/null \
    || { echo "beacon-eval: nothing new to commit (protocol already committed?)"; }
  git tag "$TAG"
fi
beacon_maybe_push "$TAG" "$TARGET_ROUND"
PUSH_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ">> beacon-eval: freeze stands at $PUSH_TIME (local clock; GitHub event time below when gh works)"

# 4. Wait for the round (poll latest; fail closed after a bounded wait).
echo ">> beacon-eval: waiting for round $TARGET_ROUND"
WAIT_DEADLINE_MIN=$((DELAY_MIN + 15))
WAIT_END=$(( $(date +%s) + WAIT_DEADLINE_MIN * 60 ))
while true; do
  LATEST="$(uv run python -m bench.beacon_client verify --round "$TARGET_ROUND" 2>/dev/null | head -1 || true)"
  if [ -n "$LATEST" ]; then
    echo ">> beacon-eval: round published: $LATEST"
    break
  fi
  # verify fails pre-publication (404); check latest to report progress.
  PROGRESS="$(python3 -c "
import urllib.request, json
try:
    r = json.load(urllib.request.urlopen('https://api.drand.sh/public/latest', timeout=15))
    print(r.get('round'))
except Exception:
    print('unknown')
")"
  echo ">> beacon-eval: latest published $PROGRESS, waiting for $TARGET_ROUND ..."
  if [ "$(date +%s)" -gt "$WAIT_END" ]; then
    echo "beacon-eval: round $TARGET_ROUND not published after ${WAIT_DEADLINE_MIN}m — aborting (rerun to resume)" >&2
    exit 4
  fi
  sleep 30
done

# 5. Run (unattended) for this round.
ROUND="$TARGET_ROUND" make bench-beacon

# 6. Evidence: one-page markdown + terminal-style PNG for the deck.
ROUND_DIR="experiments/beacon/$TARGET_ROUND"
EVIDENCE="$ROUND_DIR/EVIDENCE.md"
FREEZE_SHA="$(git rev-list -n 1 "$TAG")"
# REQ-066: the freeze pins the frontier model; show it on the evidence page.
PINNED_FRONTIER="$(sed -n 's/.*beacon-frontier-model: *//p' "$PROTOCOL" | head -1)"
PINNED_AGENT="$(sed -n 's/.*beacon-agent-model: *//p' "$PROTOCOL" | head -1)"
WARM_LINE="$(python3 -c "
import json
try:
    s = json.load(open('$ROUND_DIR/summary.json'))
    w = s.get('grades', {}).get('warm', {})
    print(f\"warm: {w.get('entries', 0)} dev-split fix(es) ({w.get('source', 'unknown')})\")
except Exception as exc:
    print(f'warm: unknown ({exc})')
")"
{
  echo "# Beacon evaluation evidence — round $TARGET_ROUND"
  echo
  echo "Commit-then-reveal with public randomness (REQ-025): the instances"
  echo "were fixed by a drand round that did not exist when the protocol froze."
  echo
  echo "- round: $TARGET_ROUND"
  echo "- randomness: $(grep -o 'randomness=[0-9a-f]*' <<<"$LATEST" | cut -d= -f2)"
  echo "- verification URL: https://api.drand.sh/public/$TARGET_ROUND"
  echo "- freeze commit: $FREEZE_SHA (tag $TAG)"
  echo "- protocol: $PROTOCOL"
  echo "- frontier model: ${PINNED_FRONTIER:-unknown (pre-REQ-066 freeze)}"
  if [ -n "$PINNED_AGENT" ]; then
    echo "- agent model: $PINNED_AGENT"
  fi
  echo "- $WARM_LINE"
  echo
  echo "## Per-arm summary"
  echo
  echo '```'
  python3 -c "
import json
s = json.load(open('$ROUND_DIR/summary.json'))
for arm, b in sorted(s['grades']['by_arm'].items()):
    vc = b['verified_completion']
    print(f\"{arm:20s} verified {vc['mean']:.2f} [{vc['ci95_lo']:.2f},{vc['ci95_hi']:.2f}] n={vc['n']}  wrong_patch={b['wrong_patch_released']['total']}\")
"
  echo '```'
} > "$EVIDENCE"
if uv run --with pillow python scripts/beacon_evidence_png.py "$EVIDENCE" "$ROUND_DIR/evidence.png"; then
  echo ">> beacon-eval: wrote $EVIDENCE + evidence.png"
else
  echo ">> beacon-eval: wrote $EVIDENCE (PNG skipped: pillow unavailable)"
fi

# 7. Commit the results; push only with PUSH=yes.
git add "$ROUND_DIR" "$PROTOCOL"
git commit -m "beacon-eval: round $TARGET_ROUND results (one run, no retries)"
beacon_maybe_push "$TAG" "$TARGET_ROUND"
echo ">> beacon-eval: done. Results in $ROUND_DIR (verify any time: make verify-beacon ROUND=$TARGET_ROUND)"
