# AutoBridge — Runbook

Skeleton. Filled in during W10 (plan §4 W10, §8).

## Clean-clone check (plan §8 step 6)

On a machine with only Docker and `uv` installed:

```
git clone <origin> autobridge && cd autobridge
cp .env.example .env            # fill ANTHROPIC_API_KEY
make up && make test && make e2e && make demo
```

## Benchmark run (plan §5 hard dates: heldout run Nov 3)

```
make bench      # refuses to run if verifier/hidden_cases/heldout does not match SHA256SUMS
```

## Demo recording

Six steps of plan §6, driven by `make demo`; record the UI with the labelled Inject Fault drawer visible.

## Sandboxes for the ralph loop

Two loop keys (`sbx1`, `sbx-mini`), each with one sandbox per agent runtime. The loop picks the sandbox
from the tier it runs (see `ralph-afk.sh` header), and stops the previous one when it switches.
One sandbox per runtime, not one shared sandbox: Docker Sandboxes builds a sandbox around one built-in
agent (its CLI, credential proxy and sign-in state), and a hung run in one runtime can never take the
other down. `claude-autobridge` and `opencode-autobridge` exist (created 2026-10-27); the `-mini` pair is
created only when a second concurrent loop is wanted.

| Key | Claude Code sandbox | OpenCode sandbox |
|---|---|---|
| `sbx1` | `claude-autobridge` (holds the Claude sign-in) | `opencode-autobridge` |
| `sbx-mini` | `claude-autobridge-mini` | `opencode-autobridge-mini` |

One-time setup, from the repo root:

```
sbx create claude   --name claude-autobridge        "$PWD"
sbx run --name claude-autobridge                    # /login, then exit
sbx create claude   --name claude-autobridge-mini   "$PWD"
sbx create opencode --name opencode-autobridge      "$PWD"
sbx create opencode --name opencode-autobridge-mini "$PWD"

# OpenCode providers reach their endpoints through the sandbox network policy:
sbx policy allow network inference.baseten.co:443
sbx policy allow network inference.us-west.modal.direct:443
sbx policy allow network api.meta.ai:443
```

### Provider keys for the OpenCode tiers

| Tier | Provider | Where the key comes from | Env name (what OpenCode reads) |
|---|---|---|---|
| worker | Baseten | an API key from your Baseten account (workspace API keys page) | `BASETEN_API_KEY` |
| backup | Modal | a Modal **proxy token** for inference: Modal gives a token id and a token secret; OpenCode's catalog expects the combined value in one variable (Docker's OpenCode page calls it the "combined proxy token") | `MODAL_PROXY_TOKEN` |
| escalation | Meta | a Meta Model API key from Meta's developer console for the Muse models | `META_MODEL_API_KEY` |

Two ways to hand them to the loop. Pick one per provider; A is the simplest.

**A. `.env` on the host (default).** Put the values in `.env` (git-ignored):

```
BASETEN_API_KEY=...
MODAL_PROXY_TOKEN=...
META_MODEL_API_KEY=...
```

`ralph-afk.sh` sources `.env` and passes only the variable of the tier it runs into the OpenCode sandbox
as a bare `-e NAME` on `sbx exec` (the value is read from the script's environment, never written on a
command line or into the worktree). OpenCode picks the provider up from the variable; nothing else to
configure. The three hosts still have to be allowed once in the sandbox network policy:

```
sbx policy allow network inference.baseten.co:443
sbx policy allow network inference.us-west.modal.direct:443
sbx policy allow network api.meta.ai:443
```

**B. `sbx` custom secrets (value never enters the sandbox).** The sandbox sees a placeholder and Docker's
proxy swaps in the real value on requests to the named host:

```
sbx secret set-custom --host inference.baseten.co          --env BASETEN_API_KEY
sbx secret set-custom --host inference.us-west.modal.direct --env MODAL_PROXY_TOKEN
sbx secret set-custom --host api.meta.ai                    --env META_MODEL_API_KEY
```

Each command prompts for the value. Then leave that key **empty** in `.env` (the loop's "empty in .env"
warning for that tier is expected) and recreate `opencode-autobridge` so the placeholder variable appears
inside it (`sbx rm opencode-autobridge && sbx create opencode --name opencode-autobridge "$PWD"`). The
network allow rules above are still needed. `set-custom` is marked experimental by Docker.

**Check a key before trusting the ladder.** From the host, with `.env` filled (method A):

```
set -a; . ./.env; set +a
sbx exec -e BASETEN_API_KEY opencode-autobridge   bash -c 'curl -sS -H "Authorization: Bearer $BASETEN_API_KEY"   https://inference.baseten.co/v1/models | head -c 300'
sbx exec -e MODAL_PROXY_TOKEN opencode-autobridge bash -c 'curl -sS -H "Authorization: Bearer $MODAL_PROXY_TOKEN" https://inference.us-west.modal.direct/v1/models | head -c 300'
sbx exec -e META_MODEL_API_KEY opencode-autobridge bash -c 'curl -sS -H "Authorization: Bearer $META_MODEL_API_KEY" https://api.meta.ai/v1/models | head -c 300'
```

A model list means key and network policy are fine; a connection error means the policy rule is missing;
401/403 means the key. Then a one-iteration dry run on that tier, e.g.
`sh ralph-afk.sh --agent opencode --provider modal sbx1 1`, confirms OpenCode itself signs in (the loop
stops with an "could not authenticate" message otherwise).

**Check the Baseten model id itself** (same request as Baseten's sample code, via curl):

```
sbx exec -e BASETEN_API_KEY opencode-autobridge bash -c 'curl -sS https://inference.baseten.co/v1/chat/completions \
  -H "Authorization: Bearer $BASETEN_API_KEY" -H "Content-Type: application/json" \
  -d "{\"model\": \"meta-models/muse-spark-open-1026\", \"messages\": [{\"role\": \"user\", \"content\": \"Hello!\"}]}" | head -c 400'
```

A `choices[0].message.content` reply means the id in `BASETEN_MODEL` is live; a "model not found" error means the
id changed and `.env` plus `opencode.json` need the new one.

The Claude tier needs nothing in `.env`: sign in once with `sbx run --name claude-autobridge`, `/login`,
then `/model` to pick the model (saved in the sandbox; the loop reads it for the commit trailer), exit.

### The model ladder (`--agent auto`, the default)

| Tier | Agent | Provider / model (`.env`) | Role |
|---|---|---|---|
| worker | OpenCode | `baseten` / `BASETEN_MODEL` (Spark Open, `meta-models/muse-spark-open-1026`) | every normal iteration |
| backup | OpenCode | `modal` / `MODAL_MODEL` (Spark Open) | after a provider failure on worker |
| escalation | OpenCode | `meta` / `META_MODEL` (Spark 1.3 or Wonder) | after a failure on backup, or a task unfinished `ESCALATE_AFTER` iterations in a row; the daily audit starts here |
| final | Claude Code | the model saved by `/model` inside `claude-autobridge` (no `.env` setting) | after a failure on escalation (`RALPH_FINAL_TIER=claude`; `none` ends the ladder at meta) |

A clean, merged iteration drops the loop back to the worker tier. `--agent claude` or
`--agent opencode --provider <p> [--model <id>]` pins one tier for the whole run. Each iteration's
transcript is `ralph-logs/<time>-<key>-<tier>-iter<n>.jsonl`, and the agent records
`Agent: <runtime provider/model (tier)>` in its build note, so runtimes can be compared per task.

Open points to confirm on the first OpenCode run: the exact `--format json` event shape (the summary
parser in `ralph-afk.sh` reads `.part.text` / `.text` and falls back to the last line), and whether
Docker Compose runs inside a Docker Sandbox (plan §9 does not cover it): if it cannot, `make up` /
`make smoke` / `make e2e` are owner-run on the Mac and loops rely on unit tests plus the W1 fakes.
