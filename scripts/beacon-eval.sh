#!/usr/bin/env bash
# REQ-025 item 6b: one unattended beacon-eval command (owner, hands-on: one
# command + one "yes").
#
#   make beacon-eval FAULTS=<N> [DELAY_MIN=20] [CONFIRM=yes]
#
# Checks the tree is clean and no ralph loop is running; freezes the
# protocol (ROUND_AFTER = now + DELAY_MIN); commits it, tags `eval-freeze`,
# pushes (asks once before the push unless CONFIRM=yes); records the push
# time; waits for the round; runs bench-beacon unattended; writes
# experiments/beacon/<round>/EVIDENCE.md (+ terminal-style evidence.png for
# the deck); commits and pushes the results.
#
# Resumable: rerunning after the freeze continues the same round (the
# existing protocol.md is reused; a result dir that already exists makes
# bench-beacon refuse — that is the no-re-roll guard, not a bug).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

FAULTS="${FAULTS:-25}"
DELAY_MIN="${DELAY_MIN:-20}"
CONFIRM="${CONFIRM:-}"
PROTOCOL="experiments/beacon/protocol.md"

echo ">> beacon-eval: FAULTS=$FAULTS DELAY_MIN=$DELAY_MIN"

# 1. Guards: clean tree, no ralph loop.
if [ -n "$(git status --porcelain)" ]; then
  echo "beacon-eval: refusing — git tree is dirty (commit or stash first)" >&2
  git status --porcelain | head -10 >&2
  exit 2
fi
if pgrep -f "ralph" >/dev/null 2>&1; then
  echo "beacon-eval: refusing — a ralph loop process is running (pgrep ralph)" >&2
  exit 2
fi

# 2. Freeze (or resume an existing one).
if [ -f "$PROTOCOL" ]; then
  echo ">> beacon-eval: resuming existing $PROTOCOL (never a new one silently)"
else
  ROUND_AFTER="$(python3 -c "import datetime; print((datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=$DELAY_MIN)).isoformat())")"
  echo ">> beacon-eval: freezing with ROUND_AFTER=$ROUND_AFTER"
  ROUND_AFTER="$ROUND_AFTER" FAULTS="$FAULTS" make eval-freeze
fi
TARGET_ROUND="$(grep '^beacon-target-round:' "$PROTOCOL" | awk '{print $2}')"
echo ">> beacon-eval: target round $TARGET_ROUND"

# 3. Commit + tag + push the freeze.
if git rev-parse -q --verify "refs/tags/eval-freeze" >/dev/null; then
  echo ">> beacon-eval: tag eval-freeze already exists — reusing"
else
  git add "$PROTOCOL"
  git commit -m "eval-freeze: beacon protocol for round $TARGET_ROUND" \
    -m "Co-Authored-By: Muse Spark 1.3 <muse-spark-1.3@models.noreply>" 2>/dev/null \
    || { echo "beacon-eval: nothing new to commit (protocol already committed?)"; }
  git tag eval-freeze
fi
if [ -z "$CONFIRM" ]; then
  printf "push eval-freeze (protocol + tag) to origin? [yes/no] "
  read -r answer
  if [ "$answer" != "yes" ]; then
    echo "beacon-eval: aborted before push (rerun to resume; the freeze stands)"
    exit 3
  fi
fi
git push origin HEAD:main
git push origin tag eval-freeze
PUSH_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ">> beacon-eval: pushed at $PUSH_TIME (local clock; GitHub event time below when gh works)"

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

# 5. Run (unattended).
make bench-beacon

# 6. Evidence: one-page markdown + terminal-style PNG for the deck.
ROUND_DIR="experiments/beacon/$TARGET_ROUND"
EVIDENCE="$ROUND_DIR/EVIDENCE.md"
FREEZE_SHA="$(git rev-list -n 1 eval-freeze)"
{
  echo "# Beacon evaluation evidence — round $TARGET_ROUND"
  echo
  echo "Commit-then-reveal with public randomness (REQ-025): the instances"
  echo "were fixed by a drand round that did not exist when the protocol froze."
  echo
  echo "- round: $TARGET_ROUND"
  echo "- randomness: $(grep -o 'randomness=[0-9a-f]*' <<<"$LATEST" | cut -d= -f2)"
  echo "- verification URL: https://api.drand.sh/public/$TARGET_ROUND"
  echo "- freeze commit: $FREEZE_SHA (tag eval-freeze, pushed $PUSH_TIME)"
  echo "- protocol: experiments/beacon/protocol.md"
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

# 7. Commit + push the results.
git add "$ROUND_DIR" experiments/beacon/protocol.md
git commit -m "beacon-eval: round $TARGET_ROUND results (one run, no retries)" \
  -m "Co-Authored-By: Muse Spark 1.3 <muse-spark-1.3@models.noreply>"
git push origin HEAD:main
echo ">> beacon-eval: done. Results in $ROUND_DIR (verify any time: make verify-beacon ROUND=$TARGET_ROUND)"
