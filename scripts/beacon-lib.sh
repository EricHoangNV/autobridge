#!/usr/bin/env bash
# Shared helpers for scripts/beacon-eval.sh (REQ-077 item 1).
# Sourced, never executed directly. Pure bash so pytest can exercise
# every branch (discovery, ralph guard, push opt-in) without a stack.

# Print the frozen protocol a run would use, or fail.
# Mirrors bench.beacon_run.resolve_protocol_path: the old single
# experiments/beacon/protocol.md wins when present (backward compat);
# otherwise the single unrun experiments/beacon/<round>/protocol.md
# (rehearsals excluded; a dir holding results is a finished run, never
# resumed). Zero or several unrun candidates is an error.
beacon_unrun_protocol() {
  local repo_root="${1:?repo root required}"
  local old="$repo_root/experiments/beacon/protocol.md"
  if [ -f "$old" ]; then
    printf '%s\n' "$old"
    return 0
  fi
  local found="" count=0 dir base
  for dir in "$repo_root"/experiments/beacon/*/; do
    [ -d "$dir" ] || continue
    base="$(basename "$dir")"
    case "$base" in
      rehearsals) continue ;;
      *[^0-9]*) continue ;;
    esac
    [ -f "$dir/protocol.md" ] || continue
    if [ -f "$dir/instances.json" ] || [ -f "$dir/summary.json" ] || [ -f "$dir/grades.jsonl" ]; then
      continue
    fi
    found="${dir%/}/protocol.md"
    count=$((count + 1))
  done
  if [ "$count" -eq 1 ]; then
    printf '%s\n' "$found"
    return 0
  fi
  if [ "$count" -eq 0 ]; then
    echo "beacon-eval: no frozen protocol (make eval-freeze first)" >&2
    return 2
  fi
  echo "beacon-eval: several unrun protocols — pass ROUND explicitly" >&2
  return 2
}

# True (0) when a ralph loop runs in this checkout's path.
# Other projects run ralph-afk.sh too, so a bare `pgrep -f ralph` would
# block this run for somebody else's loop. A loop counts only when its
# working directory is this checkout (Linux /proc) or its command line
# names this checkout's path (fallback, e.g. macOS without /proc).
beacon_ralph_running_here() {
  local repo_root="${1:?repo root required}"
  local pid cmdline cwd
  for pid in $(pgrep -f "ralph" 2>/dev/null); do
    [ "$pid" = "$$" ] && continue
    if [ -L "/proc/$pid/cwd" ]; then
      cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
      if [ "$cwd" = "$repo_root" ]; then
        return 0
      fi
      continue
    fi
    cmdline="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || ps -p "$pid" -o command= 2>/dev/null || true)"
    case "$cmdline" in
      *ralph*"$repo_root"*|*"$repo_root"*ralph*) return 0 ;;
    esac
  done
  return 1
}

# Push the branch and the freeze tag to the current branch's upstream.
# Used only when PUSH=yes (see beacon_maybe_push).
beacon_push() {
  local tag="${1:?tag required}"
  local branch remote
  branch="$(git branch --show-current)" || return 1
  remote="$(git config "branch.$branch.remote" 2>/dev/null || true)"
  [ -n "$remote" ] || remote="origin"
  git push "$remote" "$branch" || return 1
  git push "$remote" "tag" "$tag" || return 1
}

# Push the freeze (before the round) or the results (after) when
# PUSH=yes; otherwise commit locally and print how to push to a fork
# before the round for a public timestamp. CONFIRM=yes keeps its meaning:
# without it the script asks once before the first push.
beacon_maybe_push() {
  local tag="${1:?tag required}"
  local round="${2:-}"
  if [ "${PUSH:-}" != "yes" ]; then
    echo ">> beacon-eval: PUSH!=yes — committed locally only (no push)."
    if [ -n "$round" ]; then
      echo "   For a public timestamp, push to a fork BEFORE round $round is published:"
      echo "   git push <fork-remote> HEAD:$(git branch --show-current 2>/dev/null || echo BRANCH) \\"
      echo "     && git push <fork-remote> tag $tag"
    fi
    return 0
  fi
  if [ -z "${CONFIRM:-}" ]; then
    printf 'push the freeze/results (branch + tag %s) upstream? [yes/no] ' "$tag"
    local answer
    read -r answer
    if [ "$answer" != "yes" ]; then
      echo "beacon-eval: aborted before push (rerun with PUSH=yes to resume; the freeze stands)"
      return 3
    fi
    CONFIRM=yes
    export CONFIRM
  fi
  beacon_push "$tag"
}
