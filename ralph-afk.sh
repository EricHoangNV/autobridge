#!/bin/bash

# AutoBridge autonomous build loop (adapted from fulltime5/ralph-afk.sh).
# Runs a headless coding agent in a Docker Sandbox, one task per iteration, on its own git worktree and
# branch, and merges finished work into origin/main. Bookkeeping lives in mds/ (see mds/*-INSTRUCTIONS.MD).
#
# Two agent runtimes, each in its own sandbox:
#   claude    Claude Code (sbx agent "claude"), signed in inside the sandbox
#   opencode  OpenCode (sbx agent "opencode") with a provider/model picked per iteration:
#               baseten  → $BASETEN_MODEL   (Spark Open)        tier "worker"
#               modal    → $MODAL_MODEL     (Spark Open)        tier "backup"
#               meta     → $META_MODEL      (Spark 1.3 / Contributor / Wonder) tier "escalation"
#   auto      starts on the worker tier and moves up the ladder on failures (see choose_tier)
#
#   sh ralph-afk.sh [--validate] [--agent claude|opencode|auto] [--provider baseten|modal|meta] [--model <id>] <sbx1|sbx-mini> <iterations>
#
#   sh ralph-afk.sh sbx1 10                                   auto ladder (default)
#   sh ralph-afk.sh --agent claude sbx1 10                    Claude Code only
#   sh ralph-afk.sh --agent opencode --provider meta sbx-mini 5          OpenCode on Meta, model from .env
#   sh ralph-afk.sh --agent opencode --provider meta --model muse-wonder-1.0 sbx1 3
#   sh ralph-afk.sh --validate sbx-mini 1                     daily directives audit (auto: escalation tier)

# Exit on unset variables, but not on every non-zero command
set -u

# Load environment variables (see .env.example: SLACK_WEBHOOK, provider keys, model ids, ladder settings)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.env"

# Model ids per provider (override in .env; --model overrides for one run). Ids are the ones OpenCode's
# catalog (models.dev) uses for each provider; Baseten's Spark Open id is declared in opencode.json.
BASETEN_MODEL="${BASETEN_MODEL:-meta-models/muse-spark-open-1026}"
MODAL_MODEL="${MODAL_MODEL:-meta-models/Muse-Spark-Open-816B-A42B}"
META_MODEL="${META_MODEL:-muse-spark-1.3}"
# The Claude tier uses the model saved inside the Claude sandbox (/model in an interactive session), never
# a value from .env; the loop reads it back from ~/.claude/settings.json for the commit trailer.
# Auto ladder: how many consecutive iterations the same task may stay unfinished before escalating, and
# whether Claude Code is the rung above Meta (RALPH_FINAL_TIER=claude) or the ladder ends at Meta (none).
ESCALATE_AFTER="${ESCALATE_AFTER:-2}"
RALPH_FINAL_TIER="${RALPH_FINAL_TIER:-claude}"

# Sandboxes (MAX 2 loops, one per key). Each agent runtime has its own sandbox per key, so a Claude
# sandbox and an OpenCode sandbox never share state. Create them once (see mds/DEPLOY.md):
#   sbx create claude   --name claude-autobridge        "$PWD"   (then sbx run --name claude-autobridge, /login, exit)
#   sbx create opencode --name opencode-autobridge      "$PWD"
#   sbx create claude   --name claude-autobridge-mini   "$PWD"
#   sbx create opencode --name opencode-autobridge-mini "$PWD"
sandbox_for() {  # <key> <agent>
  case "$1:$2" in
    sbx1:claude) echo "claude-autobridge" ;;
    sbx1:opencode) echo "opencode-autobridge" ;;
    sbx-mini:claude) echo "claude-autobridge-mini" ;;
    sbx-mini:opencode) echo "opencode-autobridge-mini" ;;
    *) return 1 ;;
  esac
}
valid_key() { case "$1" in sbx1|sbx-mini) return 0 ;; *) return 1 ;; esac; }

# Tiers of the auto ladder, in order. A tier is "<agent>:<provider>" (provider empty for claude).
tier_agent() { case "$1" in worker|backup|escalation) echo opencode ;; final) echo claude ;; esac; }
tier_provider() { case "$1" in worker) echo baseten ;; backup) echo modal ;; escalation) echo meta ;; final) echo "" ;; esac; }
next_tier() {
  case "$1" in
    worker) echo backup ;;
    backup) echo escalation ;;
    escalation) [ "$RALPH_FINAL_TIER" = claude ] && echo final || echo "" ;;
    final) echo "" ;;
  esac
}
# Tiers whose provider ran out of credit (or hit its usage limit) during this run. The ladder skips them
# until the loop is restarted; there is no point retrying a provider that has no balance.
EXHAUSTED_TIERS=" "
tier_exhausted() { case "$EXHAUSTED_TIERS" in *" $1 "*) return 0 ;; esac; return 1; }
usable_tier() {  # <tier> → that tier, or the first tier above it that is not exhausted ("" if none)
  local t="$1"
  while [ -n "$t" ] && tier_exhausted "$t"; do t=$(next_tier "$t"); done
  echo "$t"
}
model_for() {  # <provider> → model id
  case "$1" in
    baseten) echo "$BASETEN_MODEL" ;;
    modal) echo "$MODAL_MODEL" ;;
    meta) echo "$META_MODEL" ;;
    *) return 1 ;;
  esac
}
# The env var each OpenCode provider reads its key from (models.dev catalog). The value comes from .env
# on the host and is passed into the sandbox as a bare "-e NAME" (never on a command line).
provider_env() {
  case "$1" in
    baseten) echo BASETEN_API_KEY ;;
    modal) echo MODAL_PROXY_TOKEN ;;
    meta) echo META_MODEL_API_KEY ;;
  esac
}

# Per-iteration agent transcripts (stream-json), kept on the host; ralph-logs/ is git-ignored
LOG_DIR="$SCRIPT_DIR/ralph-logs"
mkdir -p "$LOG_DIR"

# Docker Sandboxes CLI (sbx)
if ! command -v sbx >/dev/null 2>&1; then
  echo "sbx not found. Install Docker Sandboxes: brew install docker/tap/sbx"
  exit 1
fi
if ! sbx ls -q >/dev/null 2>&1; then
  echo "sbx is not signed in. Run: sbx login"
  exit 1
fi

# Stops this loop's agent inside its sandbox (killing the host-side sbx exec does not stop it)
stop_agent() {
  sbx exec "$SANDBOX_NAME" bash -c 'f=/tmp/ralph-agent.pid; [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null; rm -f "$f"' >/dev/null 2>&1
}

cleanup() {
  [ -z "${SANDBOX_NAME:-}" ] && return
  if [ -n "${agent_pid:-}" ]; then
    kill "$agent_pid" 2>/dev/null
    echo "Stopping this loop's agent..."; stop_agent
  fi
  [ -n "${LOCK_DIR:-}" ] && [ "$(cat "$LOCK_DIR/pid" 2>/dev/null)" = "$$" ] && rm -rf "$LOCK_DIR"
  echo "Stopping sandbox $SANDBOX_NAME..."
  sbx stop "$SANDBOX_NAME" >/dev/null 2>&1
}
trap cleanup EXIT


# Notify function: echo to both console and Slack
# Splits long messages into multiple Slack posts (3500 char chunks)
notify() {
  local message="$1"
  local timestamp="[$(date '+%Y-%m-%d %H:%M:%S')]"

  # Echo to console
  echo "$timestamp $message"

  # Send to Slack
  if [ -n "${SLACK_WEBHOOK:-}" ]; then
    local full="${timestamp} ${message}"
    local chunk_size=3500
    local offset=0
    local len=${#full}

    while [ $offset -lt $len ]; do
      local chunk="${full:$offset:$chunk_size}"
      local payload
      payload=$(jq -n --arg text "$chunk" '{text: $text}')
      curl -s -X POST -H 'Content-type: application/json' \
        --data "$payload" \
        "$SLACK_WEBHOOK" > /dev/null
      offset=$((offset + chunk_size))
      # Small delay between chunks to preserve order
      [ $offset -lt $len ] && sleep 1
    done
  fi
}

# Extract the agent's final summary from a transcript.
# Claude Code stream-json: the "result" event's text, else the last assistant message's text.
# OpenCode --format json (shape not documented; confirm on the first real run): the text of the last
# text part, found under .part.text or .text; else the last non-empty line.
get_final_summary() {
  local log_file="$1"
  local summary
  summary=$(jq -Rrs '[split("\n")[] | fromjson? | select(.type == "result")] | last | .result // empty' "$log_file" 2>/dev/null)
  if [ -z "$summary" ]; then
    summary=$(jq -Rrs '[split("\n")[] | fromjson? | select(.type == "assistant")] | last | [.message.content[]? | select(.type == "text") | .text] | join("\n")' "$log_file" 2>/dev/null)
  fi
  if [ -z "$summary" ]; then
    summary=$(jq -Rrs '[split("\n")[] | fromjson? | select((.part.type? == "text") or (.type == "text")) | (.part.text // .text // empty) | select(length > 0)] | last // empty' "$log_file" 2>/dev/null)
  fi
  printf '%s' "$summary"
}

MODE="work"
AGENT_OPT="${RALPH_AGENT:-auto}"
PROVIDER_OPT=""
MODEL_OPT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --validate) MODE="validate"; shift ;;
    --agent) AGENT_OPT="${2:-}"; shift 2 ;;
    --provider) PROVIDER_OPT="${2:-}"; shift 2 ;;
    --model) MODEL_OPT="${2:-}"; shift 2 ;;
    --) shift; break ;;
    -*) echo "Unknown option $1"; exit 1 ;;
    *) break ;;
  esac
done

usage() {
  echo "Usage: $0 [--validate] [--agent claude|opencode|auto] [--provider baseten|modal|meta] [--model <id>] <sbx1|sbx-mini> <iterations>"
  echo "  e.g. sh ralph-afk.sh sbx1 10   and, in a second terminal,   sh ralph-afk.sh sbx-mini 10"
  echo "       sh ralph-afk.sh --agent opencode --provider meta --model muse-wonder-1.0 sbx1 3"
}
SBX_KEY="${1:-}"
SANDBOX_NAME=""
if ! valid_key "$SBX_KEY" || [ -z "${2:-}" ]; then usage; exit 1; fi
iterations=$2

case "$AGENT_OPT" in
  claude)
    [ -n "$PROVIDER_OPT$MODEL_OPT" ] && { echo "--provider/--model apply to --agent opencode only; the Claude model is chosen with /model inside the sandbox"; exit 1; }
    FIXED_TIER="final" ;;
  opencode)
    PROVIDER_OPT="${PROVIDER_OPT:-baseten}"
    case "$PROVIDER_OPT" in baseten) FIXED_TIER=worker ;; modal) FIXED_TIER=backup ;; meta) FIXED_TIER=escalation ;; *) echo "Unknown provider $PROVIDER_OPT"; exit 1 ;; esac ;;
  auto)
    [ -n "$PROVIDER_OPT$MODEL_OPT" ] && { echo "--provider/--model fix the tier: use --agent opencode with them"; exit 1; }
    FIXED_TIER="" ;;
  *) echo "Unknown agent $AGENT_OPT"; usage; exit 1 ;;
esac
# Auto mode starts on the worker tier; the audit starts on the escalation tier (strongest OpenCode model)
if [ -n "$FIXED_TIER" ]; then TIER="$FIXED_TIER"; elif [ "$MODE" = validate ]; then TIER="escalation"; else TIER="worker"; fi

# Checks every sandbox this run may use, so a missing one fails before the first iteration
check_sandboxes() {
  local t="$TIER" name
  while [ -n "$t" ]; do
    name=$(sandbox_for "$SBX_KEY" "$(tier_agent "$t")")
    if ! sbx ls -q 2>/dev/null | grep -qx "$name"; then
      echo "Sandbox $name ($SBX_KEY, $(tier_agent "$t")) does not exist. See mds/DEPLOY.md \"Sandboxes\"."
      return 1
    fi
    if [ "$(tier_agent "$t")" = opencode ]; then
      local var; var=$(provider_env "$(tier_provider "$t")")
      [ -z "${!var:-}" ] && echo "⚠️ $var is empty in .env: the $t tier ($(tier_provider "$t")) cannot sign in to its provider"
    fi
    [ -n "$FIXED_TIER" ] && break
    t=$(next_tier "$t")
  done
  return 0
}

# Each loop works in its own git worktree and branch, made from origin/main. The worktree sits inside
# the project folder so its sandbox (which mounts only this folder) can see it; .worktrees/ is git-ignored.
WT_DIR="$SCRIPT_DIR/.worktrees/$SBX_KEY"
BRANCH="ralph/$SBX_KEY"
LOOP_TAG="$SBX_KEY"

# One loop per sandbox: a second process on the same sandbox would share its worktree and claim
# the same task
mkdir -p "$SCRIPT_DIR/.worktrees"
LOCK_DIR="$SCRIPT_DIR/.worktrees/$SBX_KEY.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  holder=$(cat "$LOCK_DIR/pid" 2>/dev/null)
  if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
    LOCK_DIR=""; SANDBOX_NAME=""
    echo "$SBX_KEY is already running a loop (PID $holder). Use the other sandbox."
    exit 1
  fi
  echo "Taking over the stale $SBX_KEY lock (PID ${holder:-unknown} is gone)."
fi
echo "$$" > "$LOCK_DIR/pid"

check_sandboxes || exit 1

# Label every notification with the loop key, agent and model, so the two loops (and tiers) are told apart in Slack
AGENT_LABEL=""
lnotify() { notify "[$SBX_KEY${AGENT_LABEL:+ · $AGENT_LABEL}] $1"; }

# Set SANDBOX_NAME / AGENT / PROVIDER / MODEL / AGENT_LABEL for a tier; stops the previous tier's sandbox on a switch
select_tier() {
  local t="$1"
  AGENT=$(tier_agent "$t"); PROVIDER=$(tier_provider "$t")
  if [ "$AGENT" = opencode ]; then MODEL="${MODEL_OPT:-$(model_for "$PROVIDER")}"; else MODEL=$(claude_saved_model); fi
  local name; name=$(sandbox_for "$SBX_KEY" "$AGENT")
  if [ -n "$SANDBOX_NAME" ] && [ "$SANDBOX_NAME" != "$name" ]; then sbx stop "$SANDBOX_NAME" >/dev/null 2>&1; fi
  SANDBOX_NAME="$name"
  if [ "$AGENT" = opencode ]; then AGENT_LABEL="opencode $PROVIDER/$MODEL ($t)"; else AGENT_LABEL="claude ${MODEL:-default model} ($t)"; fi
  COMMIT_TRAILER=$(trailer_for "$AGENT" "$MODEL")
}

# The model saved in the Claude sandbox by /model (~/.claude/settings.json "model"); empty when none is saved,
# in which case Claude Code runs its default and the agent writes its own model name in the trailer.
claude_saved_model() {
  sbx exec "$(sandbox_for "$SBX_KEY" claude)" bash -c 'jq -r ".model // empty" "$HOME/.claude/settings.json" 2>/dev/null' 2>/dev/null | tr -d '[:space:]'
}

# Commit trailer naming the model (never the provider or runtime), CLAUDE.md / AGENTS.md "Git commits".
#   opencode  "meta-models/muse-spark-open-1026" → "Co-Authored-By: Muse Spark Open <muse-spark-open-1026@models.noreply>"
#   claude    "claude-opus-5-5" or alias "opus"       → "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" / "Claude Opus"
#             no saved model                            → the agent fills in its own model name
trailer_for() {  # <agent> <model id>
  local id name
  id=$(basename "$2" | tr '[:upper:]' '[:lower:]')
  if [ "$1" = claude ] && [ -z "$id" ]; then
    printf 'Co-Authored-By: <your exact model name, e.g. Claude Opus 5.5> <noreply@anthropic.com>'
  elif [ "$1" = claude ]; then
    # claude-<family>-<major>-<minor>[-date] → "Claude <Family> <major>.<minor>"
    name=$(printf '%s' "$id" | sed -E 's/^claude-//; s/-[0-9]{8}$//; s/^([a-z]+)-([0-9]+)-([0-9]+)$/\1 \2.\3/; s/^([a-z]+)-([0-9]+)$/\1 \2/; s/-/ /g' \
           | awk '{for(i=1;i<=NF;i++)$i=toupper(substr($i,1,1))substr($i,2)}1')
    printf 'Co-Authored-By: Claude %s <noreply@anthropic.com>' "$name"
  else
    # Meta's Contributor tier is the same model at another price: the trailer names the model.
    id=${id%-contributor}
    name=$(basename "$2" | sed -E 's/-contributor$//; s/-[0-9]+[bB]-[aA][0-9]+[bB]$//; s/-[0-9]{4}$//; s/-/ /g' | awk '{for(i=1;i<=NF;i++)$i=toupper(substr($i,1,1))substr($i,2)}1')
    printf 'Co-Authored-By: %s <%s@models.noreply>' "$name" "$id"
  fi
}

# Classify an iteration from its transcript and worktree: ok | auth | provider | empty
classify_run() {  # <log> → prints class
  local log="$1" last_err
  last_err=$(jq -Rrs '[split("\n")[] | fromjson? | select(.type == "result" or .type == "error")] | last | if (.is_error // (.type == "error")) then ((.result // .error // .message // .) | tostring) else empty end' "$log" 2>/dev/null)
  # Out of credit is checked before auth: some providers answer a billing failure with 401/403 and
  # "unauthorized", which would otherwise stop the whole loop as a bad key.
  if printf '%s' "$last_err" | grep -qiE 'insufficient.{0,20}(credit|balance|fund)|out of credit|no credits?( left| remaining)?|credits? (exhausted|depleted)|credit balance|billing|payment required|\b402\b|exceeded your (current )?quota|quota exceeded|usage limit|spend(ing)? limit'; then echo credit; return; fi
  if printf '%s' "$last_err" | grep -qiE 'authenticat|not logged in|invalid api key|unauthorized|401'; then echo auth; return; fi
  if [ -n "$last_err" ] || [ -z "$(get_final_summary "$log")" ]; then
    if printf '%s' "$last_err" | grep -qiE 'rate.?limit|429|overloaded|quota|insufficient|timeout|timed out|ECONN|ENOTFOUND|unavailable|5[0-9][0-9]|provider|model .*not found|context.*(length|window)'; then echo provider; return; fi
    tail -c 4000 "$log" | grep -qiE 'rate.?limit|429|overloaded|ECONN|ENOTFOUND|503|502|unavailable' && { echo provider; return; }
    echo empty; return
  fi
  echo ok
}

# Git identity: every commit is authored by the host's git user; the model is named only in the
# Co-Authored-By trailer. The sandboxes have no git identity of their own, so it is passed in as env
# (GIT_AUTHOR_* / GIT_COMMITTER_* override any git config the agent might write).
GIT_AUTHOR_NAME="$(git -C "$SCRIPT_DIR" config --global user.name)"
GIT_AUTHOR_EMAIL="$(git -C "$SCRIPT_DIR" config --global user.email)"
[ -n "$GIT_AUTHOR_NAME" ] && [ -n "$GIT_AUTHOR_EMAIL" ] || { echo "Set your git identity first: git config --global user.name / user.email"; exit 1; }
GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME"; GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
export GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
GIT_ID_ENV=(-e GIT_AUTHOR_NAME -e GIT_AUTHOR_EMAIL -e GIT_COMMITTER_NAME -e GIT_COMMITTER_EMAIL)

select_tier "$TIER"
UNFINISHED_STREAK=0
lnotify "🚀 Starting AFK mode ($MODE, agent ${AGENT_OPT}, start tier $TIER) with $iterations iterations in $WT_DIR..."

for ((i=1; i<=iterations; i++)); do
  lnotify "🔄 Starting iteration $i of $iterations on tier $TIER..."

  # Prepare this loop's worktree. Unfinished work from this loop's last iteration (uncommitted
  # changes or commits not yet on origin/main) is kept and handed to the agent to finish first;
  # otherwise the worktree is reset to a fresh branch from origin/main.
  # Drop any identity an agent wrote into the shared repo config (worktrees share .git/config).
  if [ -n "$(git -C "$SCRIPT_DIR" config --local --get user.name)$(git -C "$SCRIPT_DIR" config --local --get user.email)" ]; then
    lnotify "⚠️ Removed a repo-local git identity ($(git -C "$SCRIPT_DIR" config --local --get user.name)) written by an agent"
    git -C "$SCRIPT_DIR" config --local --unset-all user.name 2>/dev/null
    git -C "$SCRIPT_DIR" config --local --unset-all user.email 2>/dev/null
  fi
  git -C "$SCRIPT_DIR" fetch -q origin
  if [ ! -d "$WT_DIR" ]; then
    mkdir -p "$SCRIPT_DIR/.worktrees"
    git -C "$SCRIPT_DIR" worktree add -q -B "$BRANCH" "$WT_DIR" origin/main || { lnotify "❌ Could not create worktree $WT_DIR"; exit 1; }
  fi
  UNFINISHED=""
  if [ -n "$(git -C "$WT_DIR" status --porcelain)" ] || [ -n "$(git -C "$WT_DIR" log --oneline origin/main..HEAD 2>/dev/null)" ]; then
    UNFINISHED="$(git -C "$WT_DIR" status --short | head -30)
$(git -C "$WT_DIR" log --oneline origin/main..HEAD 2>/dev/null | head -10)"
    lnotify "♻️ Resuming unfinished work left in $WT_DIR"
  else
    git -C "$WT_DIR" checkout -q -B "$BRANCH" origin/main
  fi

  # Git push access: pass the host's current gh token into each sandbox command as GH_TOKEN
  # (bare "-e GH_TOKEN" reads it from this script's environment, so it never shows in process args),
  # and point git's credential helper at it. sbx exec starts the sandbox if it is stopped.
  if ! GH_TOKEN="$(gh auth token 2>/dev/null)" || [ -z "$GH_TOKEN" ]; then
    lnotify "❌ No GitHub token on the host. Run: gh auth login"
    exit 1
  fi
  export GH_TOKEN
  sbx exec "$SANDBOX_NAME" bash -c 'rm -f "$HOME/.git-credentials"; \
    git config --global --unset-all credential.helper 2>/dev/null; \
    git config --global credential.https://github.com.helper \
      "!f() { echo username=x-access-token; echo \"password=\$GH_TOKEN\"; }; f"'
  if ! sbx exec -e GH_TOKEN -w "$WT_DIR" "$SANDBOX_NAME" env GIT_TERMINAL_PROMPT=0 git ls-remote origin HEAD >/dev/null 2>&1; then
    lnotify "❌ Sandbox cannot reach GitHub with the host token. Check: gh auth status"
    exit 1
  fi

  # Build/scratch folders on in-memory storage inside the sandbox. On the host-mounted project
  # folder, a just-created directory can briefly read as missing and Rust/Go tools (uv, Next.js'
  # bundler) fail with EEXIST/ENOENT (fulltime5 LEARNINGS #1). The mounts vanish when the sandbox
  # stops, so redo them every iteration; folders already mounted are left alone.
  # Adjust the list once W1 lands (e.g. add ui/.next when the UI exists).
  if ! sbx exec -w "$WT_DIR" "$SANDBOX_NAME" bash -c '\
    for d in .venv .pytest_cache .mypy_cache .ruff_cache ui/.next; do \
      [ "$d" = ui/.next ] && [ ! -d ui ] && continue; \
      mountpoint -q "$d" 2>/dev/null && continue; \
      rm -rf "$d" && mkdir -p "$d" && sudo mount -t tmpfs tmpfs "$d" && sudo chown "$(id -u):$(id -g)" "$d" || exit 1; \
    done'; then
    lnotify "⚠️ Could not mount scratch folders on tmpfs; builds may fail (see mds/LEARNINGS.MD)"
  fi

  # Dependencies: uv for the Python workspace (skipped until pyproject.toml exists, i.e. before W1),
  # pnpm for the UI once ui/package.json declares dependencies (the W1 stub has none, so it is skipped).
  # pnpm is not in the sandbox images: it is installed at the version in "packageManager" (pnpm@x.y.z),
  # or the latest release if that field is missing. --frozen-lockfile only once pnpm-lock.yaml exists.
  if ! sbx exec -w "$WT_DIR" "$SANDBOX_NAME" bash -c '\
    command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1; export PATH="$HOME/.local/bin:$PATH"; \
    log=/tmp/deps-'"$LOOP_TAG"'.log; : > "$log"; \
    if [ -f pyproject.toml ]; then uv sync --all-packages >>"$log" 2>&1 || exit 1; fi; \
    if [ -f ui/package.json ] && jq -e "((.dependencies // {}) + (.devDependencies // {})) | length > 0" ui/package.json >/dev/null 2>&1; then \
      v=$(jq -r ".packageManager // empty | select(startswith(\"pnpm@\")) | sub(\"^pnpm@\"; \"\") | sub(\"\\\\+.*$\"; \"\")" ui/package.json); \
      if [ -z "$v" ]; then command -v pnpm >/dev/null 2>&1 || { echo "ui/package.json has no packageManager: installing the latest pnpm" >>"$log"; sudo npm install -g pnpm >>"$log" 2>&1 || exit 1; }; \
      elif [ "$(pnpm --version 2>/dev/null)" != "$v" ]; then sudo npm install -g "pnpm@$v" >>"$log" 2>&1 || exit 1; fi; \
      frozen=""; [ -f ui/pnpm-lock.yaml ] && frozen=--frozen-lockfile; \
      (cd ui && pnpm install $frozen --prefer-offline) >>"$log" 2>&1 || exit 1; \
    fi'; then
    lnotify "⚠️ Dependency install failed in $WT_DIR (log in the sandbox: /tmp/deps-$LOOP_TAG.log); the agent will retry"
  fi

  # Record origin/main before this iteration so we can list what reached main
  head_before=$(git -C "$SCRIPT_DIR" rev-parse origin/main 2>/dev/null)

  # Extract compact context snippets (avoids injecting full files via @)
  # Task Manifest: the dedicated tracking board (the plan holds only design/§ detail, not tasks)
  # Only the open, claimed and partial tasks in full (plus headings and the legend); done tasks are
  # one id list, their detail is in BUILD-NOTES.md.
  TRACK_FILE="$WT_DIR/mds/PRD-TRACKING.md"
  TASK_MANIFEST=$(grep -vE '^[[:space:]]*- \[x\]' "$TRACK_FILE")
  DONE_IDS=$(grep -oE '^[[:space:]]*- \[x\] P[0-9]+\.[WS][0-9]+\.[0-9]+' "$TRACK_FILE" | awk '{print $3}' | paste -sd, - | sed 's/,/, /g')
  DONE_BRIEF=$(grep -E '^[[:space:]]*- \[x\]' "$TRACK_FILE" | cut -c1-120)
  # Directives Quick Reference, injected so no agent reads DIRECTIVES.MD from the top (DIRECTIVES-INSTRUCTIONS.MD)
  QUICK_REF=$(sed -n '/^## Quick Reference/,/^---$/p' "$WT_DIR/mds/DIRECTIVES.MD" | sed '1d;$d')
  # Progress: the full state file (should be ~3KB per PROGRESS-INSTRUCTIONS.MD)
  PROGRESS_SUMMARY=$(cat "$WT_DIR/mds/progress-state.md")
  # Safety net: truncate to 8KB if an agent bloated the state file
  if [ ${#PROGRESS_SUMMARY} -gt 8192 ]; then
    PROGRESS_SUMMARY="${PROGRESS_SUMMARY:0:8192}
... (truncated — progress-state.md exceeds 8KB limit, see PROGRESS-INSTRUCTIONS.MD)"
  fi
  # Requests: only the "Open Requests" section (stop at first ---)
  OPEN_REQUESTS=$(sed -n '1,/^---$/p' "$WT_DIR/mds/REQUESTS.MD")
  # Feedbacks: only the "Latest Review Verdicts" section (stop at first ---)
  LATEST_VERDICTS=$(sed -n '1,/^---$/p' "$WT_DIR/mds/FEEDBACKS.MD")

  # Shared by both prompts: where this loop works, and how it reaches main
  RESUME_NOTE=""
  if [ -n "$UNFINISHED" ]; then
    RESUME_NOTE="
### Unfinished work from this loop's last iteration — finish it FIRST
Your worktree still has work that never reached origin/main (uncommitted changes and/or local commits):
${UNFINISHED}
Check what it is (git status, git log origin/main..HEAD, the claimed [>] task marked ${LOOP_TAG} in mds/PRD-TRACKING.md), finish it, run the feedback loops, and merge it (\"Merge into main\" below). Do not claim a new task this iteration."
  fi
  WORKTREE_RULES="## Parallel loops: your worktree, claim and merge
You are the loop in sandbox ${LOOP_TAG}. At most one other loop (the other sandbox) works at the same time, in its own worktree.
- Work ONLY in your worktree ${WT_DIR} (branch ${BRANCH}, made from origin/main). Never edit files in ${SCRIPT_DIR} itself or in another loop's worktree.
- Your git author identity is already set by the loop. Never run git config user.name/user.email (or --author): the model goes only in the Co-Authored-By trailer.
- **Merge into main** (at the end of your work, after the bookkeeping): commit on your branch, then git fetch origin && git rebase origin/main. Resolve conflicts:
  - mds/PRD-TRACKING.md and mds/REQUESTS.MD: keep every other loop's status changes; only change your own item.
  - mds/progress-log.md and mds/BUILD-NOTES.md: keep both entries, yours last. mds/LEARNINGS.MD: keep both, renumber yours to the next free number (and fix references to it).
  - mds/progress-state.md: rebuild it from both versions (it must describe main after your merge).
  - Alembic migrations: if another loop added a migration with the same down_revision, drop yours, finish the rebase, and regenerate it on top of theirs.
  - verifier/hidden_cases/heldout/SHA256SUMS: never changed by a loop after the W2 freeze; if it conflicts, stop and say so in your summary.
  - Code: keep both sides' intent; the transform registry and the rule registry keep both entries.
  If the rebase brought in other loops' code changes (anything outside mds/), run lint, typecheck and test again; if it brought only mds/ changes, do not. Then git push origin HEAD:main. If the push is rejected, fetch, rebase and push again. Never force-push main, and never push to another loop's branch.
- If you cannot merge (a conflict you cannot resolve safely), leave the work committed on your branch, set your task back to [~] with the reason in mds/PRD-TRACKING.md on main, and say so in your summary.
${RESUME_NOTE}"

  DIRECTIVES_BLOCK="## Directives — Quick Reference (from mds/DIRECTIVES.MD; every item applies)
${QUICK_REF}
This list is the whole Quick Reference: do not re-read it from the file. The other rules are printed for your change by ./directives-for.sh: run it with the files you expect to touch before you start (./directives-for.sh <path>...), and with no arguments before you commit (everything you changed against origin/main). It prints only the rules that apply. Never read mds/DIRECTIVES.MD from the top or in full."

  EFFICIENCY_RULES="## Working efficiently (every turn re-reads all earlier output, so output size and turn count are the cost)
- Read code in pieces: grep -n to find the spot, then sed -n '<a>,<b>p' (or Read with offset/limit) for the lines you need. Never cat whole files, and never several files in one command, unless a file is under 150 lines.
- The plan (mds/AutoBridge_Implementation_Plan_2026-10-26.md) is 33 KB: read only the § your task names (grep -n '^##' for line numbers, then sed -n). Never the whole file.
- Keep command output short: pipe long output through tail -30 or grep; for test and check runs show only the summary and the failures (e.g. make test 2>&1 | tail -40).
- Tests while you work: run only the test files you touched or that cover your change (uv run pytest <files> -q). Run the full make test (90% coverage gate per package) once, at the end, before committing; again after the rebase only if the rebase brought in code changes (not just mds/ files).
- lint and typecheck: once before committing (fix, then re-run only the one that failed); again after the rebase only if it brought in code changes.
- E2E: run only the tests/e2e file for the workstream you touched, once when it is done; after a fix, re-run only that file. Never the full make e2e (the owner runs it).
- Paid model calls (AnthropicProvider against the real API, make bench with a real baseline): at most twice per task, only when the task is about them; everything else uses the fake provider and the W1 fakes.
- No subagents (Agent/Task tools) unless the task truly needs parallel research; do the work yourself.
- No waiting or polling longer than 2 minutes (no ScheduleWakeup, no long sleeps): run long commands in the foreground with a timeout.
- Do not re-read a file you already read or edited this session unless it changed."

  PROJECT_BLOCK="## Project
AutoBridge: a generic Connect + Heal engine (plan: mds/AutoBridge_Implementation_Plan_2026-10-26.md; index of its sections in mds/PRD.MD).
A connector is a declarative ConnectorSpec the runtime interprets; a repair is a JSON Patch on it; drift goes up a ladder L0 knowledge → L1 rules → L2 small model → L3 frontier model → L4 human; a separate verifier with hidden fixtures decides acceptance (plan §1).
Stack (plan §2): Python 3.12 + uv workspace (src/autobridge, verifier/, simulators/ as separate packages), FastAPI control plane, SQLAlchemy + Alembic on SQLite (Postgres-ready), Docker Compose with four networks, Next.js 15 + TypeScript UI over the OpenAPI-typed client, pytest with a 90% coverage gate per package.
Hackathon window Oct 26 – Nov 4, 2026: Phase 2 stop-rule checkpoint end of Nov 1, feature freeze Nov 2, heldout benchmark Nov 3, submission Nov 4 (plan §5)."

  AGENT_NOTE="## Your runtime
You are ${AGENT_LABEL}, chosen by the loop's ladder (worker → backup → escalation → final). Record it in your build note and progress-log entry as \"Agent: ${AGENT_LABEL}\" so the owner can compare runtimes. If you hit a provider error you cannot work around (rate limit, model unavailable), say so plainly in your final summary and stop; the loop will switch tier.
Every commit you make (claim commits included) ends with this trailer, verbatim, on its own last line: ${COMMIT_TRAILER}"
  # OpenCode only: its edit tool needs a full read of the file first, and the agents were reading the whole
  # bookkeeping files (~18% of cached tokens) just to append to them. Claude Code's prompt is unchanged.
  if [ "$AGENT" = opencode ]; then
    AGENT_NOTE="${AGENT_NOTE}

## Bookkeeping without reading (OpenCode)
Never open mds/BUILD-NOTES.md, mds/progress-log.md or mds/PRD-TRACKING.md with the read tool, and never edit them with the edit tool: both pull the whole file into your context, and every later step pays for it again.
- Append a build note or a progress-log entry with a shell heredoc: cat >> mds/BUILD-NOTES.md <<'NOTE' ... NOTE (same for mds/progress-log.md).
- Change one task line in mds/PRD-TRACKING.md with sed on that line only, e.g. sed -i 's/^- \\[>\\] P1.W2.3 \\(.*\\) — in progress: .*/- [x] P1.W2.3 \\1/' mds/PRD-TRACKING.md, then check it with grep -n 'P1.W2.3' mds/PRD-TRACKING.md.
- To look something up in them, grep -n for the task id or keyword and sed -n the few lines you need.
- mds/progress-state.md is small: rewrite it with cat > mds/progress-state.md <<'STATE' ... STATE.
- Run tests and checks through make where a target exists: the loop has set them to print only the summary, the failures and the uncovered lines."
  fi

  # Build prompt based on mode
  if [ "$MODE" = "validate" ]; then
    PROMPT="${DIRECTIVES_BLOCK}

${EFFICIENCY_RULES}

${PROJECT_BLOCK}

${AGENT_NOTE}

${WORKTREE_RULES}

## Mode: VALIDATION (daily directives audit)
Your job is to audit the work merged since the last audit and validate implementation completeness — not to build new features.
Scope: mds/AUDIT.md names the last audited commit (\"Last audited: <sha>\"; if it is missing, audit the last 24 hours: git log --since=24.hours origin/main). Audit only the commits from that one to origin/main: git log --oneline <sha>..origin/main, then each commit's diff (git show --stat first, then the files that matter).
In this audit you may read every section of mds/DIRECTIVES.MD (one section at a time, not the whole file in one command): check those commits against every rule, not only the ones ./directives-for.sh prints. Also flag rules that are out of date, contradict each other, or break mds/DIRECTIVES-INSTRUCTIONS.MD's size limits, and rules that code could check (ruff, mypy, import-linter, a test) but nothing does yet.

## Task Manifest (from PRD-TRACKING.md: open, claimed and partial tasks in full)
${TASK_MANIFEST}

## Done tasks (first 120 characters each; the build note in mds/BUILD-NOTES.md has the files: grep -n '### <id>')
${DONE_BRIEF}

## Latest Review Verdicts (from FEEDBACKS.MD)
${LATEST_VERDICTS}

## Progress Summary
${PROGRESS_SUMMARY}

## Open Requests
${OPEN_REQUESTS}

## Files to read ON DEMAND (do NOT read in full)
- mds/PRD-TRACKING.md — the task manifest / status board (summarized above)
- mds/AutoBridge_Implementation_Plan_2026-10-26.md — read only the § for the deliverable you are verifying (§4 W<n> passing criteria, §2.x design); mds/PRD.MD is the index
- mds/BUILD-NOTES.md — per-task build notes (files/decisions) for completed work, keyed by task id
- mds/DIRECTIVES.MD — Quick Reference is above; read single § sections when checking specific rules (see Directives above)
- mds/FEEDBACKS.MD — already summarized above; read full review only if you need remediation details
- mds/REQUESTS.MD — already summarized above
- docs/*.md — the per-area docs each workstream must keep updated (plan §3 definition of done)

## Steps
1. Confirm you have read the Directives Quick Reference and the efficiency rules above (both are in this prompt; do not open the file for them).
2. Walk through every deliverable in the Task Manifest above.
   For each done task, verify the implementation exists:
   - Check the key files listed in its build note exist
   - Check exports, types, and function signatures match the plan section
   - Run relevant tests to confirm they pass
   - Flag any gap between the plan's passing criteria (§4 W<n>) and the implementation
3. Cross-check DIRECTIVES.MD: verify the codebase follows each directive.
   Also check the plan's standing rules (§1): repairs are JSON Patches, never code; the engine has no import of or route to verifier/ or simulators/; every model call is in the ModelCall ledger and known repairs show frontier_tokens == 0; every SyncRun ends Complete|Degraded|Blocked; money is Decimal; no credential value in specs, evidence, prompts or API responses.
4. Check the Latest Review Verdicts above for any FAIL items that were missed.
5. Check Open Requests above for any unresolved items.
6. Run ALL feedback loops:
   - Lint: make lint (must pass)
   - Types: make typecheck (must pass with no errors)
   - Tests: make test (must pass, 90% per package)
   - Smoke: make smoke if the Compose stack can run in this sandbox (report it as not runnable otherwise, do not fake it)
   - E2E: not the full suite (the owner runs it); the tests/e2e file of each workstream touched since the last audit must pass.
   If a script does not exist yet, report it as a gap — do not skip silently.
7. If you find gaps or issues:
   - Fix them if the fix is straightforward (< 30 min of work).
   - For larger gaps, add them as [ ] items to mds/PRD-TRACKING.md
     and add them to the Next Tasks list in mds/progress-state.md.
   - Commit fixes on your branch and merge them into main as described under \"Parallel loops\" above.
   - Update progress (read mds/PROGRESS-INSTRUCTIONS.MD first).
7b. Record the audit: overwrite mds/AUDIT.md with \"Last audited: <origin/main sha you audited up to>\", the date, and one line per finding (fixed / REQ-NNN / task id). Each finding you do not fix goes to mds/REQUESTS.MD as an open request (read mds/REQUESTS-INSTRUCTIONS.MD first) titled \"Directive: <rule> — <what breaks it, file:line>\".
8. Write a validation summary to mds/progress-log.md with:
   - Sections validated and their status
   - Gaps found and whether you fixed them or deferred them
   - Overall readiness assessment against the Phase 1 exit gates (plan §4)
   Then commit it and merge into main as described under \"Parallel loops\" above.
9. After finishing, if ALL deliverables are verified and no gaps remain:
   output <promise>COMPLETE</promise> at the very end of your response.
   If gaps remain, do NOT output the promise tag."
  else
    PROMPT="${DIRECTIVES_BLOCK}

${EFFICIENCY_RULES}

${PROJECT_BLOCK}

${AGENT_NOTE}

${WORKTREE_RULES}

## Project Status
You have three sources below to decide what to work on. Read them all before choosing.

### Task Manifest (from PRD-TRACKING.md — [ ] open, [>] claimed by a loop, [~] partial/blocked; done tasks listed by id below)
${TASK_MANIFEST}

Done: ${DONE_IDS}

### Build & Test Status
${PROGRESS_SUMMARY}

### Open Requests (from REQUESTS.MD)
${OPEN_REQUESTS}

### Latest Review Verdicts (from FEEDBACKS.MD — FAIL items need fixing)
${LATEST_VERDICTS}

## Files to read ON DEMAND (do NOT read in full)
- mds/PRD-TRACKING.md — the task manifest / status board (summarized above). Pick your task here.
- mds/AutoBridge_Implementation_Plan_2026-10-26.md — read ONLY the § for your chosen task (its workstream in §4 for deliverables and passing criteria, plus the §2.x design section it names); mds/PRD.MD is the index. **Do NOT edit the plan or PRD.MD on a task run.**
- mds/BUILD-NOTES.md — append your build note here after finishing (keyed by task id, e.g. ### P1.W2.1); read prior notes only if you need history.
- mds/DIRECTIVES.MD — Quick Reference is above; read single § sections only when implementing related features (see Directives above)
- mds/FEEDBACKS.MD — verdicts summarized above; read the full review section only if you need remediation details
- mds/LEARNINGS.MD — read before writing new learnings
- mds/REQUESTS.MD — open requests summarized above
- docs/<area>.md — update the docs page of the workstream you finish (plan §3 definition of done)

## Steps
1. Confirm you have read the Directives Quick Reference and the efficiency rules above (both are in this prompt; do not open the file for them).
2. Using the Task Manifest, Open Requests, and Latest Verdicts above, decide what to work on next.
   Look at open [ ] items in the manifest, any open requests, and any FAIL verdicts.
   Items marked [>] (or requests with Status: IN_PROGRESS) are claimed by another loop: never pick them, unless the claim's time is more than 6 hours old (then treat it as abandoned and say so in your summary).
   Make your own judgment — do NOT just follow the \"Next tasks\" list from a previous agent.
   Respect the dependency order (W1 → W2 → W3 → W4 → W5 → W6; W7 after W3; W8 after W6; W9 alongside; W10 last): do not start a workstream whose predecessor has open tasks it depends on. Work only on Phase 1 until every Phase 1 task is [x] and the owner has recorded the stop-rule decision in mds/PRD.MD; do not start Phase 2 early.
3. Pick the highest priority task using this order:
   a. Architectural decisions and core abstractions (spec, trust boundaries, ladder, data model)
   b. Integration points between modules (runtime ↔ verifier ↔ simulators, control ↔ engine)
   c. Unknown unknowns and spike work (Compose in the sandbox, headless CLI usage capture)
   d. Standard features and implementation
   e. Open requests from REQUESTS.MD
   f. FAIL items from latest review verdicts
   g. Improve test coverage. Polish, cleanup, and quick wins
   Fail fast on risky work. Save easy wins for later.
3b. **Claim it before doing any work**, so no other loop picks the same item:
   - In mds/PRD-TRACKING.md change your task's [ ] to [>] and append \" — in progress: ${LOOP_TAG} since <current UTC time, YYYY-MM-DD HH:MM>\". For a request, set its Status line in mds/REQUESTS.MD to \"IN_PROGRESS (${LOOP_TAG} since <UTC time>)\" instead.
   - Commit only that file (message \"Claim <task id> (${LOOP_TAG})\" plus the Co-Authored-By trailer from \"Your runtime\") and push it straight to main: git push origin HEAD:main.
   - If the push is rejected, another loop pushed first: git fetch origin && git reset --hard origin/main, read mds/PRD-TRACKING.md and mds/REQUESTS.MD again, and pick again (your choice may now be claimed).
   - Start work only once your claim is on origin/main. Claim exactly one item.
4. Read ONLY the plan section(s) for your chosen task (its workstream in §4 and the §2.x it names).
   Run ./directives-for.sh with the files you expect to touch and follow every rule it prints (run it again for a file you add later). Then implement.
4b. Standing rules from the plan (§1, §3) — check your work against them before committing:
   - Repairs are data: a connector is a ConnectorSpec, a repair is a JSON Patch, transforms are the closed set; no rule or model generates code.
   - Trust boundaries: src/autobridge never imports or reads verifier/ or simulators/; hidden fixtures and answer keys stay in their package; the engine has no network route to them.
   - Deterministic first: every model call goes through LLMProvider + Budget and lands in ModelCall; known repair patterns must show frontier_tokens == 0; BudgetExhausted escalates, never silently retries.
   - Outcomes: every SyncRun ends Complete | Degraded | Blocked with the unmet criterion named; unknown enum → Blocked; money is Decimal; dates by property-local business date.
   - Health: every new service has /healthz and /readyz; every workstream's tests/e2e file is part of the workstream.
   - Secrets: credential_ref only; no credential value in a spec, release, evidence record, log, prompt or API response; model ids in config, not code. You cannot create external accounts or freeze the heldout set from the sandbox: if a task needs that, do everything codeable, then mark the task [~] with the exact manual step needed.
5. Run ALL feedback loops:
   - Directives: ./directives-for.sh (no arguments) prints the rules for everything you changed; check your diff against each one and fix any violation. In your build note, list any rule you could not follow and why.
   - Lint: make lint (must pass)
   - Types: make typecheck (must pass with no errors)
   - Tests: make test (must pass with ≥ 90% coverage per package; once, at the end — targeted pytest runs while working, see \"Working efficiently\")
   - E2E: do NOT run the full suite. Only the tests/e2e file for the workstream you built or changed this task must pass, if the Compose stack runs in this sandbox; if it cannot, say so in the build note and make sure the W1 fakes cover your change in unit tests.
   - If a script or Makefile target does not exist yet (early foundation work), creating it is part of your task — do not skip the loop.
   - During testing, do not just test changed code. Pay attention to the callers and the consumers of updated code and test these functions too. Make sure no silent crashes or incompatibilities happen.
   Do NOT commit if any feedback loop fails. Fix issues first.
   Never end your run while a command you started is still running in the background: your final message ends the session and kills it. Wait for it to finish (poll its output), then commit. Run long commands in the foreground with a timeout instead where possible.
6. Commit your changes on your branch with the Co-Authored-By trailer from \"Your runtime\" as the last line of the message (do not push yet — you merge into main in step 8b, after the bookkeeping).
7. If you found and fixed any bugs, add the learning to mds/LEARNINGS.MD (read mds/LEARNINGS-INSTRUCTIONS.MD first).
   If the learning warrants a new directive, update mds/DIRECTIVES.MD as well (read mds/DIRECTIVES-INSTRUCTIONS.MD first: Quick Reference items are one line, the list stays under 2 KB).
   If you fix any feedback, update mds/FEEDBACKS.MD (read mds/FEEDBACKS-INSTRUCTIONS.MD first).
   If you resolve a request, update mds/REQUESTS.MD (read mds/REQUESTS-INSTRUCTIONS.MD first).
8. After completing each task, update progress (read mds/PROGRESS-INSTRUCTIONS.MD first):
   - Overwrite mds/progress-state.md with updated state (test counts and coverage per package, completed items, next tasks).
   - Append your detailed entry to the bottom of mds/progress-log.md.
   - In mds/PRD-TRACKING.md, flip your completed task [>]→[x] and remove its \"in progress\" note; append its build note (files, decisions, gotchas) to mds/BUILD-NOTES.md keyed by task id (e.g. ### P1.W2.1).
   - Update the docs/ page of the workstream if your task changed what it describes.
   - Do NOT edit the plan or mds/PRD.MD — they are the stable design docs; the owner changes them when (re)planning.
   ONLY DO ONE TASK AT A TIME.
8b. Commit the bookkeeping and **merge into main** as described under \"Parallel loops\" above (rebase on origin/main, resolve conflicts, re-run the checks if others' changes came in, git push origin HEAD:main). Your work is not done until it is on origin/main.
9. After finishing, run these quick checks (do NOT re-read full files):
   - git fetch origin && git show origin/main:mds/PRD-TRACKING.md | grep -E -- '- \\[( |>)\\]' — any open or claimed tasks left on main?
   - grep 'FAIL' mds/FEEDBACKS.MD | head -5 — any unresolved FAIL verdicts?
   - Check the Open Requests section above — any still unresolved?
   If ALL are clear, output <promise>COMPLETE</promise> at the very end of your response.
   If not, do NOT output the promise tag."
  fi

  # Run the agent headless in the sandbox. Output streams straight to the host, so there is
  # no session-log discovery or process polling: the pipeline ends when the agent exits.
  iter_log="$LOG_DIR/$(date '+%Y%m%d-%H%M%S')-$LOOP_TAG-$TIER-iter$i.jsonl"
  echo "Transcript: $iter_log"
  if [ "$AGENT" = claude ]; then
    # ENABLE_CLAUDEAI_MCP_SERVERS=false: don't load the claude.ai account's connectors (Gmail, Drive...)
    sbx exec -e GH_TOKEN "${GIT_ID_ENV[@]}" -e ENABLE_CLAUDEAI_MCP_SERVERS=false -w "$WT_DIR" "$SANDBOX_NAME" \
      bash -c 'echo $$ > /tmp/ralph-agent.pid; export PATH="$HOME/.local/bin:$PATH"; exec claude -p "$1" --output-format stream-json --verbose --dangerously-skip-permissions' _ "$PROMPT" \
      < /dev/null 2>&1 | tee "$iter_log" | \
      jq --unbuffered -rR '(fromjson? // {type: "raw", text: .}) |
        if .type == "assistant" then (.message.content[]? |
          if .type == "text" then .text
          elif .type == "tool_use" then ("  · " + .name + "  " + ((.input.file_path // .input.command // .input.pattern // .input.description // "") | tostring | gsub("\\S*/\\.worktrees/[^/]+/"; "") | gsub("\\s+"; " ") | .[0:120]))
          else empty end)
        elif .type == "raw" then .text
        else empty end' &
  else
    # OpenCode: provider key as a bare -e (read from this script's environment, never on the command line);
    # --model provider/model; --format json streams step_start/step_finish/tool_use/text/error events (confirmed
    # 2026-10-27, opencode 1.18.32); the filter prints text and one line per tool call; --auto approves what opencode.json does not deny.
    # OPENCODE_PERMISSION keeps edits/bash/webfetch allowed even if opencode.json is missing in the worktree.
    PROVIDER_KEY_VAR=$(provider_env "$PROVIDER")
    export "$PROVIDER_KEY_VAR"
    # COV_REPORT: the Makefile's coverage report style. Fully covered files are left out for OpenCode;
    # the Makefile default (used by Claude Code and by hand) is unchanged.
    sbx exec -e GH_TOKEN "${GIT_ID_ENV[@]}" -e COV_REPORT=term-missing:skip-covered -e "$PROVIDER_KEY_VAR" -e 'OPENCODE_PERMISSION={"edit":"allow","bash":"allow","webfetch":"allow","doom_loop":"allow","external_directory":"allow"}' \
      -w "$WT_DIR" "$SANDBOX_NAME" \
      bash -c 'echo $$ > /tmp/ralph-agent.pid; export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"; exec opencode run --format json --auto --model "$2" "$1"' _ "$PROMPT" "$PROVIDER/$MODEL" \
      < /dev/null 2>&1 | tee "$iter_log" | \
      jq --unbuffered -rR '(fromjson? // {type: "raw", text: .}) |
        if .type == "raw" then .text
        elif .type == "tool_use" then ("  · " + (.part.tool // "tool") + "  " + ([.part.state.title, .part.state.input.filePath, .part.state.input.command, .part.state.input.pattern] | map(select(. != null and . != "")) | first // "" | tostring | gsub("\\S*/\\.worktrees/[^/]+/"; "") | gsub("\\s+"; " ") | .[0:120]) + (if .part.state.status == "error" then "  ✗" else "" end))
        elif (.part.type? == "text") then (.part.text // empty)
        elif .type == "text" then (.text // .part.text // empty)
        elif .type == "error" then ("ERROR: " + ((.error // .message // .) | tostring))
        else empty end' &
  fi
  agent_pid=$!
  wait "$agent_pid"
  agent_pid=""

  # Get Claude's final summary from the transcript and send to Slack
  final_summary=$(get_final_summary "$iter_log")
  if [ -n "$final_summary" ]; then
    lnotify "📋 Iteration $i summary:
$final_summary"
  else
    lnotify "⚠️ Iteration $i produced no summary — see $iter_log"
  fi

  # Classify the run (auth | provider | empty | ok). Sign-in failures stop the loop: every later iteration
  # on that sandbox would fail the same way.
  run_class=$(classify_run "$iter_log")
  if [ "$run_class" = auth ]; then
    if [ "$AGENT" = claude ]; then
      lnotify "❌ Claude is not signed in inside the sandbox. Run: sbx run --name $SANDBOX_NAME, then /login, then exit"
    else
      lnotify "❌ OpenCode could not authenticate with $PROVIDER. Check $(provider_env "$PROVIDER") in .env and the sandbox network policy (mds/DEPLOY.md)"
    fi
    exit 1
  fi

  if [ "$run_class" = credit ] && [ -n "$FIXED_TIER" ]; then
    if [ "$AGENT" = claude ]; then
      lnotify "❌ Claude hit its usage limit. Stopping: a fixed --agent claude does not switch. Rerun later, or without --agent to use the ladder"
    else
      lnotify "❌ $PROVIDER is out of credit. Stopping: a fixed --provider does not switch. Top up $PROVIDER, or rerun with another --provider or without --agent to use the ladder"
    fi
    exit 1
  fi

  # What reached origin/main during this iteration (from any loop), and anything left behind
  git -C "$SCRIPT_DIR" fetch -q origin
  commits=$(git -C "$SCRIPT_DIR" log --oneline "${head_before}..origin/main" --format="%h %s" 2>/dev/null | head -10)
  left=$( { git -C "$WT_DIR" status --short; git -C "$WT_DIR" log --oneline origin/main..HEAD 2>/dev/null; } | head -10)

  # Check for completion signal
  if [ -n "${final_summary:-}" ] && [[ "$final_summary" =~ \<promise\>COMPLETE\</promise\>[[:space:]]*$ ]]; then
    lnotify "🎉 Manifest complete after $i iterations (last tier $TIER)!"
    [ -n "$commits" ] && lnotify "📦 New on main:
$commits"
    exit 0
  fi

  if [ -n "$commits" ]; then
    lnotify "📦 New on main (all loops):
$commits"
  else
    lnotify "📦 Nothing new on main this iteration"
  fi
  [ -n "$left" ] && lnotify "⚠️ Not merged yet (kept for the next iteration):
$left"

  lnotify "✅ Iteration $i completed ($run_class)"

  # Auto ladder (choose_tier): out of credit marks the tier exhausted for this run and moves up past it;
  # a provider failure or an empty run moves one tier up; the same task left
  # unfinished ESCALATE_AFTER times in a row moves to the escalation tier; a clean, merged iteration drops
  # back to the lowest tier that still has credit. A fixed --agent/--provider never switches.
  if [ -z "$FIXED_TIER" ]; then
    if [ -n "$left" ]; then UNFINISHED_STREAK=$((UNFINISHED_STREAK + 1)); else UNFINISHED_STREAK=0; fi
    new_tier="$TIER"
    case "$run_class" in
      credit)
        EXHAUSTED_TIERS="$EXHAUSTED_TIERS$TIER "
        new_tier=$(usable_tier "$(next_tier "$TIER")")
        if [ -z "$new_tier" ]; then lnotify "❌ Tier $TIER (${PROVIDER:-claude}) is out of credit and no tier above it has any left. Stopping."; exit 1; fi
        lnotify "💳 Tier $TIER (${PROVIDER:-claude}) is out of credit: skipping it for the rest of this run; next iteration runs on tier $new_tier" ;;
      provider|empty)
        new_tier=$(usable_tier "$(next_tier "$TIER")")
        if [ -z "$new_tier" ]; then lnotify "❌ Tier $TIER failed ($run_class) and there is no tier above it. Stopping."; exit 1; fi
        lnotify "⬆️ Tier $TIER failed ($run_class): next iteration runs on tier $new_tier" ;;
      ok)
        if [ "$UNFINISHED_STREAK" -ge "$ESCALATE_AFTER" ] && [ "$TIER" != escalation ] && [ "$TIER" != final ]; then
          new_tier=$(usable_tier escalation); [ -n "$new_tier" ] || new_tier="$TIER"
          [ "$new_tier" != "$TIER" ] && lnotify "⬆️ Task unfinished for $UNFINISHED_STREAK iterations: next iteration runs on tier $new_tier"
        elif [ -z "$left" ] && [ "$MODE" = work ] && [ "$(usable_tier worker)" != "$TIER" ]; then
          new_tier=$(usable_tier worker); lnotify "⬇️ Clean iteration: back to tier $new_tier"
        fi ;;
    esac
    if [ "$new_tier" != "$TIER" ]; then TIER="$new_tier"; select_tier "$TIER"; fi
  fi

  # Stop this loop's sandbox between iterations to free resources (the other loop has its own).
  # select_tier already stopped it when the tier switched sandboxes.
  sbx stop "$SANDBOX_NAME" >/dev/null 2>&1
done

lnotify "⚠️ Reached $iterations iterations without seeing <promise>COMPLETE</promise>."
exit 1
