# AutoBridge — benchmark (plan §2.10, §4 W8)

## Harness (matched-budget)

For each scenario × trial the harness runs two arms with **identical** inputs:

* **AutoBridge** via the control API (`POST /connectors` → `/build` → `/sync` → `/verify`).
* **Baseline** inside the same `runtime-sandbox` image with the same docs volume, sandbox URL, credentials (refs only), visible tests, token ceiling and wall-clock.

The harness self-test `assert_inputs_equal` (and `assert_budget_fields_equal` for the six task-named fields: `docs_volume`, `sandbox_url`, `sandbox_image`, `credential_refs`, `visible_tests`, `token_ceiling`, `wall_clock_s`) proves both arms received byte-identical `HarnessInputs` before either arm runs; `verify_matched_trial` is the post-hoc audit used by scoring.

Secrets are never in `HarnessInputs` — only `credential_ref` (env key names) are carried; values are read at runtime from the environment inside each arm (DIRECTIVES §6).

The harness is deterministic from `seed`: same seed → identical inputs and scenario seed. `make bench` is idempotent and reproducible — results go to `experiments/benchmark_runs/<scenario>--trial-<n>.jsonl` with `seed` in the payload, and `check_heldout_sha` guards the heldout split.

## Baseline drivers (P1.W8.2, REQ-024 B1)

### `opencode` — default headless coding-agent driver

`bench/baseline_coding_agent.py:OpenCodeCliDriver` drives
(the default since REQ-024: the untrained-frontier baseline is a coding
agent on the same `FRONTIER_MODEL` every arm uses):

```bash
opencode run --format json [-m <provider/model>] "<prompt>"
```

`--format json` streams one JSON event per line; `parse_opencode_output`
accumulates assistant text from message/content/delta shapes and takes the
maximum input/output token counters (cumulative, so the max is the run
total), plus model and cost when present. Unknown shapes are ignored, never
raised. Env: `OPENCODE_CLI_PATH` overrides the binary; `OPENCODE_MODEL`
(`provider/model` form — set it to the same provider+model as
`FRONTIER_MODEL`) selects the model, and is omitted when unset. Unit tests
use `FakeOpenCodeDriver` (in-memory, no subprocess).

### `claude_code_headless` — headless CLI with usage capture

`bench/baseline_coding_agent.py:ClaudeCodeDriver` drives:

```bash
claude -p "<prompt>" --output-format json [--model <id>] ...
```

Output is a single JSON object:

```json
{"type":"result","result":"generated spec yaml …","usage":{"input_tokens":1200,"output_tokens":3400},"model":"claude-sonnet-4-5-20250929","cost_usd":0.012}
```

The driver parses this (single object, line-delimited JSON, or plain-text fallback), captures `input_tokens` / `output_tokens` / `cost_usd` / `model` / `duration_ms`, and surfaces them as `ArmResult.frontier_tokens` / `cost_usd` / `elapsed_ms` so bench scoring can report frontier tokens, elapsed, cost and model calls on equal footing.

* Env: `CLAUDE_CLI_PATH` overrides the binary path; `--model` and `extra_args` are forwarded.
* Unit tests use `FakeClaudeDriver` (in-memory, no subprocess) and `scripts/fake_claude.py` (subprocess fake that respects `FAKE_CLAUDE_RESPONSES` / `FAKE_CLAUDE_USAGE` / `FAKE_CLAUDE_COST` env) — the W1 fakes from `P1.W1.9`, so `make test` never needs a real API key or network.

### `codex_cli` — adapter stub

`bench/baseline_coding_agent.py:CodexCliDriver` is the `codex` adapter stub (plan §2.10):

```bash
codex exec --json "<prompt>" [--model <id>] ...
```

```json
{"output":"generated spec yaml …","usage":{"input_tokens":1200,"output_tokens":3400},"model":"codex-mini-latest","cost_usd":0.012}
```

The adapter mirrors `ClaudeCodeDriver` (same `__init__` / `_build_command` / `run` / `calls` API, same usage/token capture via `parse_codex_output` handling `output` / `usage` / `tokens` shapes). Unit tests use `FakeCodexDriver` and `scripts/fake_codex.py`. The stub is pluggable: the harness passes the same `HarnessInputs` to either driver, so the six budget fields remain identical.

* Env: `CODEX_CLI_PATH` overrides the binary path.

### Fallback — `LLMProvider` with equivalent agent loop (plan §9)

> **Headless CLI usage capture may require parsing CLI output; the fallback is routing the baseline through the same `LLMProvider` with an equivalent agent loop, documented as such.** (plan §9)

When neither CLI is available (no binary, no API key, offline CI) the baseline is routed through `LLMProvider` directly:

* `bench/baseline_coding_agent.py:BaselineLLMDriver` — wraps any `LLMProvider` (e.g. `AnthropicProvider` live, `FakeProvider` in tests) and implements the same `run(prompt, cwd=None) -> ClaudeCliResult` shape. Token usage comes from the provider's `Usage` and `estimate_cost`, so `frontier_tokens` / `cost_usd` remain comparable. When a `Budget` is supplied (from `HarnessInputs.token_ceiling` / `wall_clock_s`) the call is wrapped in `BudgetedProvider`, so the same ceiling is enforced for both arms.
* `bench/harness.py:run_baseline_via_llm_provider` — functional entry point with the same semantics, used by the harness when `driver=None` and by `make bench` when `BASELINE_DRIVER=llm`.

Factory:

```python
from bench.baseline_coding_agent import get_baseline_driver

driver = get_baseline_driver()             # OpenCodeCliDriver (default, REQ-024)
driver = get_baseline_driver("opencode")   # OpenCodeCliDriver
driver = get_baseline_driver("claude")     # ClaudeCodeDriver
driver = get_baseline_driver("codex")      # CodexCliDriver (stub)
driver = get_baseline_driver("llm")        # BaselineLLMDriver (fallback, plan §9)
driver = get_baseline_driver("one_shot")   # OneShotGenerator (secondary control, P1.W8.3)
```

`make bench` selects via `BASELINE_DRIVER` env (`opencode` | `claude` | `codex` | `llm` | `one_shot`); default is `opencode`, and offline runs (no `FRONTIER_API_KEY`) fall back to the in-memory fake driver — labelled `fake-*` in every result so rehearsal numbers are never mistaken for live ones.

## Secondary diagnostic control — `one_shot_generator` (P1.W8.3)

`bench/one_shot.py:OneShotGenerator` is the secondary control: a single `LLMProvider.complete` that must answer the engine's repair task (REQ-060: a JSON Patch on the pre-drift spec, or an escalation with one question — never a whole spec) in one pass, with the *same* `HarnessInputs` the other two arms receive (docs volume, sandbox URL, credentials as refs, visible tests, token ceiling, wall-clock — asserted by `assert_inputs_equal` / `assert_budget_fields_equal` three-way). It goes through `Budget` + `BudgetedProvider` so the wall clock is identical and every call lands in `ModelCall`; cost is `Decimal` via `estimate_cost`. REQ-060: `token_ceiling` 0 = no limit (the default for every arm), so nothing is ever `budget_exhausted` on tokens; the per-call output cap stays `FRONTIER_MAX_TOKENS` because the API needs a value.

Helpers:

* `build_one_shot_prompt(inputs, docs_text)` — legacy inline prompt (unit tests / synthetic paths): scenario/trial/seed, docs Volume/ref, sandbox URL/image/runtime_target, credential refs (names only), visible tests, token/wall ceilings, plus loaded vendor docs text; no secret values (DIRECTIVES §6).
* `bench/context.py:build_bench_context(...)` — the shared fair-context bundle every real arm renders (REQ-046, below): the pre-drift spec under repair, the faulted sync's evidence, the ConnectorSpec JSON Schema, the vocabulary version's capabilities, a complete example spec for a *different* vendor, the vendor docs, the visible tests and the patch-or-escalate answer format.
* File prompts `config/prompts/bench/frontier_oneshot.md` + `frontier_agent.md` (same loader/front-matter as the engine prompts in `config/prompts/`; loaded once at bench start via `bench/bench_prompts.py`; edit + rerun to change) — the one-shot arm renders the first, the agent arm the second; the scorecard records each arm's prompt id/version/content-hash.
* `extract_spec_yaml(text)` / `try_parse_spec_yaml(text)` — legacy whole-spec extraction (unit tests / synthetic paths only).
* `bench/repair_answer.py:parse_repair_answer(text)` — the repair answer parser (REQ-060): `{"patch": [...]}` / bare patch list, `{"escalate": "<question>", ...}` with optional tested `proposal`, or `none` with a one-line reason. Never raises; a whole ConnectorSpec is explicitly not an answer.
* `bench/evidence.py:render_fault_evidence(spec, ...)` — the faulted sync's evidence (outcome, unmet criteria / failed checks, served response sample, redacted) from the same in-process observation the engine arms derive from. Never a drift class, delta, hypotheses, `expected`, or verifier material.
* `load_docs_text(inputs)` — reads docs file/dir from mounted volume (max 120k chars).
* `run_one_shot_arm(inputs, provider, budget, tier, ...)` → `ArmResult(arm="one_shot")` with usage/cost/answer payload (patch under `candidate_patch`, escalation under `answer_kind`/`question`, label `escalated`).
* `run_trial_with_one_shot(config, scenario_id, trial, ...)` — runs all three arms under matched audit, returning a `TrialResult` with extra `one_shot` arm.

If the agentic baseline beats the one-shot, the gain is from tool use/iteration, not just model size. The one-shot's answer is graded exactly like an engine arm's (patch → verifier on the patched spec; escalation → held back / not fixed).

## Ablation arms — engine vs. untrained frontier model (REQ-024 B)

`bench/ablation.py` runs five arms under one matched budget
(`assert_all_inputs_equal`, pairwise over all five — the B2 extension of the
harness self-test). Every arm that calls a frontier model uses the same
`FRONTIER_MODEL` through the same provider (`ablation_provider()`), so the
only difference between arms is the engine.

| arm | what it is |
| --- | --- |
| `frontier_oneshot` | the model alone, one prompt with the docs |
| `frontier_agent` | the model as a coding agent with tools (`opencode run` by default) |
| `engine_no_knowledge` | AutoBridge with L0+L1 disabled (ladder, verifier gate, budgets kept) |
| `autobridge_cold` | full AutoBridge, empty learned knowledge |
| `autobridge_warm` | full AutoBridge after learning on the dev split only |

Engine arms run the real end-to-end path (REQ-024 owner check on
`f32b4b4b`, fixed): sync the pre-drift spec against the served fault
(`bench/faults.py`, through the real simulator wire functions) →
detector → classifier → the engine's own hypothesis enumerator over the
vendor docs → ladder → verifier gate — with no case-supplied drift
class, delta, hypotheses or yield count. `config/ablation_cases.yaml`
holds only equal inputs (scenario id, pre-drift spec source, docs) plus
grading-only data (`expected`, never read by arms); the loader refuses
cases that smuggle diagnostics. Phase 1 runs the deterministic ladder
(never a model call); phase 2 climbs with the shared provider only for
derived `SEMANTIC_VALUE_DRIFT` with enumerated hypotheses. Two
self-tests (besides the pairwise `HarnessInputs` check) pin the
contract: `assert_arm_task_setup_equal` (every arm resolved the same
pre-drift spec) and the `handle`-kwargs spy test (drift class, delta and
hypotheses equal freshly engine-derived values). Deterministic first,
models last: structural/auth/transport classes run zero model calls by
construction.

Honest scope (rehearsal 2026-10-29, `BENCH_TRIALS=1 make bench-ablation`
on the keyed local stack, published as losses then fixed in REQ-028):
AutoBridge cold repairs 6/6 repair scenarios verified (`page_to_cursor`,
`offset_to_cursor`, `cursor_repeat_token`, `meridian_cursor_renamed`,
`rename_guest_id`, `meridian_state_renamed` — all 0 frontier tokens, 0
wrong patches) and warm is never worse than cold (verified and
correct-escalation equal on every scenario); overall cold/warm
V=0.500 E=1.000. Three REQ-028 fixes got here from the published gaps:
(1) one drift taxonomy end to end — the catalog said `FIELD_RENAME`
while the detector, ladder and `rename_field` rule speak `SCHEMA_RENAME`
(plan §2.5); the bench derive path now scopes detection to the
baseline-observed wire keys (the same scoping the live build probe
provides), so both renames classify from wire evidence and verify with
0 tokens; (2) L0 fires only on a matching (vendor, drift class,
observed delta) — transport/auth/capability classes and empty deltas
(which share one constant signature across scenarios) never consult or
populate it, so a learned fix is never reused on an unrelated drift;
(3) `frontier_oneshot` reads the one vendor docs file the engine arms
read for the scenario (not the whole docs volume, which blew the old
shared 20k token budget before the model was called) — proven by a unit test that
meters prompt size against the ceiling; it still needs a frontier key
or a prompt-hash recording to run for real (see arm availability below).
Still open: no frontier baseline ran for real in the sandbox (no
`FRONTIER_API_KEY` there — `frontier_oneshot` fails loudly with
`ReplayMissing`, `frontier_agent` runs on the fake driver); the owner
reruns with the key after REQ-060 lands (the re-run measures the
models, not the harness).

Metrics (`summarize_ablation`, per arm / per arm+scenario / overall, mean +
95 % normal-approx interval, ≥5 trials): verified completion rate (verifier
only), wrong patches released (must be 0), correct escalation rate,
frontier/small tokens, cost, wall time. `grade_ablation_trial` verifies
released patches over HTTP and degrades to unverified — never guessed —
when the verifier is unreachable. `render_ablation_table` prints the
per-run table (rung that resolved it, model calls, small/frontier tokens,
why it escalated comes from the ladder trace).

Frontier quality (REQ-052, 2026-10-30; REQ-060, 2026-10-31): the harness must not grade
itself. The frontier arms answer what the engine's L3 may answer — a
JSON Patch on the pre-drift spec, or an escalation with one question.
Every frontier arm gets the engine L3's output room
(`FRONTIER_MAX_TOKENS`, default 131072, for the one-shot and the LLM
fallback too); a cut-off answer (`finish_reason == "length"`, or a full
cap with no parseable answer) is labelled `truncated`, never scored as
a model failure. Escalations are first-class answers: graded like an
engine escalation (held back on unfixable, not fixed on fixable) with
the label `escalated`. The agent driver parses the live `opencode run
--format json` shapes (assistant text in `part.text`, usage in
`part.tokens`, failures as `error` events — a text-less error stream
raises instead of scoring silence) and resolves the model the run
itself used plus the run totals from the CLI's session row, falling
back to the stream sums when the row is unreachable. The agent row
records that model (never a default label); a live run whose model
differs from `FRONTIER_MODEL` fails as `model_mismatch` (provider
prefixes ignored). All arms share one wall-clock (default 600 s;
`--wall-clock` / `BENCH_WALL_CLOCK`); agent timeouts are labelled
`timed_out`.
No token limit (REQ-060, owner 2026-10-31): `token_ceiling` (and
`BENCH_TOKEN_CEILING`) 0 = no limit, and 0 is the default for every
arm — `Budget` skips the token check when its limit is 0, so nothing
is ever `budget_exhausted` on tokens. Every row records input, output
and reasoning tokens (when the provider reports them); the summary
adds per-arm min / median / max total tokens (`summary
["total_tokens_stats"]`) so the owner can pick a limit from the
results. The one-shot prices by the resolved model id from
`MODEL_PRICES_PER_MILLION` (Spark $1.25/$4.25 per M). With
`FRONTIER_API_KEY` set the learning curve uses the live agent driver
(the fake driver raises there) and reports per-repeat agent
verification — or fails loudly; it no longer crashes on
`default_model` for live drivers.
Every arm × scenario row carries a verdict + failed checks or an
explicit `truncated`/`timed_out`/`no_output`/`escalated`/`unverified`
label; the summary adds per-arm fixable-fixed / unfixable-held-back /
wrong-shipped counts plus label counts
(`render_frontier_quality_table`, `summary["quality"]`, per-scenario
`summary["frontier_detail"]`).

```bash
make bench-ablation   # all arms on the dev split + learning curve (REQ-024 B)
make bench            # scorecard first, then all ablation arms, then the learning curve
```

The benchmark runs in the sandbox (owner decision 2026-10-29: OpenCode
is there; the host has none). One documented command runs it against the
stack with the same keys and budgets for every arm and writes results
back to `experiments/`:

```bash
make local-up && BENCH_TRIALS=1 make bench-ablation   # or `make up` for the Compose stack
```

`make local-up` starts control/verifier/simulators as local processes
(no Docker needed); the engine arms run in-process through the same
code path either way, grading goes to the local verifier over HTTP, and
the run prints one arm-availability line per arm up front (see below)
plus the per-arm table at the end. The owner reruns this exact command
(and later `make bench`) only after all REQ-027/REQ-028 fixes land —
this is development rehearsal, not the pre-registered run.

Arm availability (`describe_arm_availability`, printed at the start of
every ablation run and stored on the summary): `frontier_oneshot` runs
live when `FRONTIER_API_KEY` is set, else replays the committed
recordings (a missing prompt-hash recording fails that scenario
loudly, never invented), else the fake provider (offline rehearsal, 0
tokens). `frontier_agent` needs the `opencode` CLI on PATH — the
supported path is running the bench inside the sandbox; without it the
harness uses `FakeOpenCodeDriver` and labels rows `fake-*` so rehearsal
numbers are never mistaken for live ones. The three engine arms always
run in-process.

Row labels: the per-trial rows carry `ran` (the arm ran to a decision —
propose/escalate, or a driver result), NOT a verifier pass. Whether a
produced patch verifies is judged only by the verifier (`verified`,
`correct_escalation` in the grades). A row with `ran: true` and
`verified_completion: 0` is a loss, published as one.

### Fair context — what each arm is given (REQ-046, REQ-060)

A starved baseline is a strawman judges will spot, so both frontier arms
receive **exactly the task the engine arms run** for the same case — not
a build task but the engine's repair task — and a unit test
(`tests/unit/test_autobridge_bench_req060.py`) asserts all three agree
by content hash on the spec + evidence. The shared bundle
(`bench/context.py:BenchContext`) is:

* the pre-drift ConnectorSpec under repair (as YAML; patch paths
  address this document only),
* the evidence of the faulted sync the engine's detector sees (sync
  outcome, unmet criteria / failed checks, the served response sample
  for the affected operations — rendered by `bench/evidence.py` from
  the same in-process observation the engine arms derive from, secrets
  redacted),
* the ConnectorSpec JSON Schema (the exact format the repaired spec
  validates against),
* the vocabulary version's capabilities for the case's `spec_version`
  (methods, auth, pagination, transforms, checks, stop conditions, L1
  rules — the same `capabilities_payload()` the `/capabilities`
  endpoint serves, so the bundle can never drift from what the engine
  reports it knows),
* **one complete example spec for a different vendor** — never the
  case's own vendor and never held-out material (`generated/`
  reference specs only; anything under `verifier/` is refused),
* the vendor docs for the case (full text in the hash; rendered prompts
  show them in full — there is no per-arm truncation anymore),
* the visible tests, and a precise answer format: what the engine's L3
  may answer — a JSON Patch on the given spec, or an escalation with
  one question (optionally a tested proposal).

What no arm gets: no drift class, no fingerprint delta, no hypotheses,
no `expected` outcome, nothing under `verifier/` — the context test
scans every dev scenario's bundle for leaks, and the leak audit still
passes.

The agent arm's sandbox holds the same files
(`schema.json`, `capabilities.json`, `example_spec.yaml`,
`vendor_docs.md`, `visible_tests.json`, `pre_drift_spec.yaml`,
`evidence.md`, `task.md`, written by `write_agent_sandbox`), and its
file-based brief names them. The `run_baseline_via_llm_provider`
fallback (plan §9, not used by the ablation — it drives
`run_baseline_arm`) renders the same full context for real scenarios;
only unknown/synthetic scenario ids with no ablation case keep the
legacy one-liner (unit tests). Grading is symmetric: a frontier patch
is applied to the pre-drift spec and goes to the verifier like an
engine patch (fixable fixed / wrong shipped); an escalation on an
unfixable scenario counts as held back, on a fixable one as not fixed
(`tests/unit/test_autobridge_bench_req060.py` pins all four).

The learning curve (`run_learning_curve`, `experiments/learning_curve.md`)
injects the same drift five times: the agent pays every repair; the
engine arms show what the ladder genuinely does (currently every repeat
escalates for drifts the L1 rules cannot yet match — learning appears as
rung 0 only once the detector gap closes) — with the driver/provider
provenance printed on the file so rehearsal rows (fake-labelled) cannot
be mistaken for publication numbers.

Pre-registration (frozen before the Nov 3 run): `experiments/preregistration.md`
— arms, metrics, trials, budgets, the primary hypothesis
(`autobridge_cold` ≥ `frontier_agent` verified completion at ≥10× fewer
frontier tokens), and the promise that results are published whatever they
are.

## Scoring and reports

Scoring (P1.W8.4, `bench/scoring.py`) queries the verifier in hidden mode and reports: verified completion, repair success, false-fix/unsafe rate, escalation quality, elapsed, tool calls, model calls, frontier tokens, estimated cost, zero-frontier solves. `bench/report.py` writes `experiments/scorecard.md` listing wins and losses — both are reported.

## `make bench` pipeline (P1.W8.5, REQ-024 B6)

One command runs the whole benchmark (plan §8 verification ladder:
heldout sha check → matched-budget run → `experiments/scorecard.md`):

```bash
make bench                                    # dev split: scorecard, then all ablation arms, then learning curve
BENCH_SCENARIOS=baseline BENCH_TRIALS=2 make bench
BENCH_SEED=99 make bench                      # reproducible from seeds
BENCH_NO_SCORE=1 make bench                   # harness JSONL only, skip scoring
make bench-ablation                           # all arms on the dev split + learning curve (needs the stack for grading: make local-up first)
make bench-heldout                            # Nov 3 run: BENCH_VISIBILITY=heldout
```

Steps inside `python -m bench.harness`:

1. **Heldout gate** (REQ-007) — dev runs skip it. `heldout`/`all` runs
   verify every file in `$HELDOUT_DIR` against the committed
   `verifier/hidden_cases/heldout/SHA256SUMS` (exit 2 on missing env,
   missing file or sha mismatch), load the private per-scenario seeds,
   push them to the simulator admin plane (`$SIMULATOR_ADMIN_URL`, when
   set), and verify each heldout trial with the private seed override.
   Seeds never appear in logs, JSONL, the scorecard or SSE events — only
   the scenario id and the manifest sha are recorded.
2. **Matched-budget run** — every scenario × trial through both arms with
   byte-identical `HarnessInputs` (asserted pre-run and audited post-run).
3. **JSONL output** — `experiments/benchmark_runs/<scenario>--trial-<n>.jsonl`
   plus `_summary.json` (overwritten, never appended — re-runs are idempotent).
4. **Scoring + report** (`score_and_report`) — every trial × arm scored via
   verifier hidden mode over HTTP (unreachable verifier records losses,
   never raises), then `experiments/benchmark_runs/_scorecard.json` and
   `experiments/scorecard.md` (wins AND losses) via `bench/report.py`.
   Heldout scorecards carry the header `Heldout seeds: private (manifest
   <sha of SHA256SUMS>)`; dev scorecards say `Heldout seeds: n/a — dev run
   on public YAML seeds`.

## Reproducibility

`make bench` is idempotent and reproducible from seeds (`BENCH_SEED`,
`BENCH_SCENARIOS`, `BENCH_TRIALS`, `BENCH_VISIBILITY` env, or the matching
`--seed` / `--scenarios` / `--trials` / `--visibility` flags):

* Same seed → same per-trial inputs, seeds and fingerprints
  (`make_trial_inputs` hashes `seed:scenario:trial`); re-running overwrites
  the same file names.
* Byte-identity holds for everything except wall-clock timing:
  `canonical_payload` / `canonical_json_bytes` /
  `assert_results_reproducible` in `bench/harness.py` drop only
  `elapsed_ms` / `started_at` / `finished_at` / `generated_at` — inputs,
  budgets, verdicts, token counts and costs must all match.
  `tests/unit/test_autobridge_bench_w85.py` proves two runs with the same
  seed produce byte-identical JSONL and that a different seed (or different
  arm outcomes) fails the check.

## Heldout freeze runbook (owner step, P1.W2.8 + REQ-007)

The owner holds the private seeds outside git and outside every sandbox
(`$HELDOUT_DIR`, e.g. `~/autobridge-heldout`): one file per heldout
scenario, `<scenario_id>.yaml` with `id`, `seed` (int) and `nonce`
(random hex, so published hashes cannot be brute-forced back to the seed).
The repo commits only the `sha256sum` manifest
(`verifier/hidden_cases/heldout/SHA256SUMS`, frozen in ff372083).

```bash
make bench-heldout HELDOUT_DIR=~/autobridge-heldout   # Nov 3 run
```

The target refuses to run without `HELDOUT_DIR` (exit 2). The harness
checks the private files against the committed manifest, uses each
scenario's private seed end to end (simulator admin scenario call +
verifier hidden-mode oracle override) for that run, and records only the
scenario id and the manifest sha — dev runs and every other path keep the
public YAML seed. Never create the heldout files inside the repo, and
never weaken the gate to make a run pass.

Agents prepare the gate; the owner freezes. When the dev/heldout split is
final, from the repo root:

```bash
cd verifier/hidden_cases/heldout && sha256sum ./*.yaml > SHA256SUMS && git add SHA256SUMS
```

From then on every heldout run (`make bench-heldout`) verifies the
manifest first and refuses to run on tampered heldout fixtures (exit 2);
dev runs (`make bench`, `test_07`) skip the gate.
`tests/e2e/test_07_benchmark_matched_budget.py` runs one dev scenario
through both arms and validates the scorecard schema.

## Held-out protocol (REQ-024 A)

"Ralph never saw the held-out data" is checkable, not claimed. Every
step below prints counts and hashes only — never seed or nonce values,
which live only in the owner's `$HELDOUT_DIR` and in memory while a
script runs.

### Isolation evidence

* Sandboxes mount only the project folder. On the owner's host `sbx ls`
  shows the workspace column per sandbox; in any checkout
  `python3 scripts/heldout_isolation_check.py` prints the same facts from
  inside: the git top-level, whether `$HELDOUT_DIR` is set and outside the
  repo (exit 2 when it points inside), the harness use sites proving the
  bench reads `$HELDOUT_DIR` only for heldout runs
  (`check_heldout_sha` / `load_heldout_seeds`, skipped entirely on dev
  runs), the freeze commit `ff372083` with its local date, and the sha256
  of the frozen `SHA256SUMS`.
* The bench refuses heldout runs without `$HELDOUT_DIR` (exit 2), and the
  leak audit refuses to run with it inside the repo (exit 2).

### Leak audit

Before the Nov 3 run the owner runs:

```bash
HELDOUT_DIR=~/autobridge-heldout python3 scripts/heldout_leak_audit.py \
  --write experiments/benchmark_runs/_leak_audit.json
```

It reads each private file's raw bytes and its nonce in memory — never the
sha256 hashes, which are public by design (they are the committed
manifest) and therefore never searched — then scans every
`ralph-logs/*.jsonl` transcript, every blob in the full git history
(`git rev-list --all` + `git cat-file`), every file under `.worktrees/`,
and the project folder for any occurrence of raw seed-file bytes or a
nonce. Output is one line per source with counts plus the per-kind
breakdown (`0 hits in 109 files (raw: 0, nonce: 0)` …) and exit 1 on any
hit, so a FAIL says what leaked. Bare seed integers are not searched
(small ints occur everywhere); the exact raw file bytes cover that case
with no false positives. A transcript or file that merely quotes the
public manifest is not a leak. Every copy of the committed manifest
itself, wherever it is found, is excluded by content and reported as
excluded. The audit result
(counts only) is written into the scorecard: `make bench` /
`make bench-heldout` pick up `<output-dir>/_leak_audit.json`
automatically (or pass `--leak-audit <path>`), and the scorecard header
renders `Leak audit: clean — …` or `not run`.

### Public timestamp (owner step)

After the freeze commit lands, from the owner's host:

```bash
git tag -s heldout-freeze ff372083 -m "heldout seeds sealed (REQ-024)"
git push origin heldout-freeze
gh release create heldout-freeze --title "heldout freeze" \
  --notes "Seals verifier/hidden_cases/heldout/SHA256SUMS at ff372083"
```

GitHub records the tag and release time. Optionally commit an
OpenTimestamps proof of `SHA256SUMS` beside it (`ots stamp
verifier/hidden_cases/heldout/SHA256SUMS`).

### Reveal after the run

After the single Nov 3 run, the owner publishes the seed files:

```bash
mkdir -p verifier/hidden_cases/heldout/revealed
cp ~/autobridge-heldout/*.yaml verifier/hidden_cases/heldout/revealed/
git add verifier/hidden_cases/heldout/revealed && git commit -m "heldout reveal (REQ-024)"
```

Then anyone reproduces the scorecard:

```bash
make verify-heldout-reveal   # checks the reveal against frozen SHA256SUMS, reruns make bench-heldout from it
```

Until the owner does this, the `revealed/` folder must not exist (guard
test `test_revealed_folder_absent_or_valid`).

### Honest scope

What is sealed is the instance data (seeds): the four scenario
definitions are public and the builder saw them. Unseen-vendor
generalization is measured separately on LegacyStay (REQ-019 item 5):
the `test_s1_legacystay.py` generalization guard keeps that vendor out of
engine code, so the deck never relies on having seen it.

## Beacon evaluation (REQ-025)

Why: the held-out split seals *instance data for four public fault
types*, and the builder has tests for those types. The beacon run is the
headline evaluation: the test's data *and* fault details derive from a
public random value (a League of Entropy drand round) that did not exist
when the code froze. Commit-then-reveal with public randomness — we could
not have prepared for the test even if we wanted to.

What a judge can check (everything is public except nothing needs to be):

1. `experiments/beacon/protocol.md` — the freeze: round rule (first
   round at/after a UTC time), N instances, arms, metrics, trials,
   budgets, the engine + generator commits, "one run, no retries,
   results published whatever they are".
2. The `eval-freeze` git tag and its push time — before the round's
   publication time (the round's own timestamp is inside the signed
   beacon; compare the two).
3. Re-fetch the round (`make verify-beacon ROUND=<n>`): the client
   verifies the threshold BLS signature against the LoE mainnet key with
   pinned `py_ecc` (fail closed), re-derives
   `seed = sha256(randomness || "autobridge-eval-v1")`, regenerates the
   instance descriptors through the simulator admin plane, and checks
   they match the published `experiments/beacon/<round>/instances.json`.
4. The per-arm grades (`grades.jsonl`) + summary (mean + 95% interval
   over trials): verified completion rate (judged only by the verifier),
   wrong patches that reached a release (must be 0 for AutoBridge),
   correct escalations, frontier tokens, estimated cost, wall time.

Protocol commands: `make eval-freeze ROUND_AFTER=<UTC> FAULTS=<N>`
(item 4), `make bench-beacon` (item 5: refuses an unpublished round or
an existing result dir — no re-rolls), `make verify-beacon ROUND=<n>`
(item 7), `make beacon-eval FAULTS=<N>` (item 6b: one unattended command
— checks the tree is clean and no ralph loop runs, freezes with
ROUND_AFTER = now + DELAY_MIN, commits, tags `eval-freeze`, pushes after
one "yes", waits, runs, writes `EVIDENCE.md` + `evidence.png`,
commits + pushes; resumable). Rehearsals on past rounds:
`make bench-beacon REHEARSE=<round>` (item 6: labelled rehearsal under
`experiments/beacon/rehearsals/`, at least five before the freeze).

Grading: every arm attempts the same instances under matched budget.
Arms that produce a candidate spec sync it with the real runtime against
the live beacon shape; produced records are judged by the verifier
(`/beacon/records`: canonical set, no loss/duplicates, exact money,
property-local business dates, canonical status vocabulary, plus
contract coverage by the spec). The reported outcome class is judged by
`/beacon/grade` against the accepted set. Beacon randomness, seeds and
descriptors are public by design; what is computed server-side, after
arms report, is the answer key. FIAS instances are served (dynamic PS
layout, link behaviors) and outcome-graded; deep link verification stays
in the FIAS e2e. Engine arms that cannot attempt a vendor in-process
(FIAS has no HTTP connector spec) are recorded `executed: false` with
the reason named — a fail-closed loss, the same for every arm.
Known asymmetry (REQ-024 owner check on `f32b4b4b`, FIXED
2026-10-29): engine arms used to run in-process from descriptor-built
repair inputs (drift class, fingerprint delta, hypotheses), i.e. from
a pre-diagnosed state, while the frontier arms start from docs and
the live sandbox. The fix landed in `bench/ablation.py` (moved out
of `src/autobridge`, no case-supplied diagnostics — the loader
refuses cases that smuggle them, pinned by the `handle`-kwargs spy
test) and `bench/beacon_run.py` adopts it (`derive_beacon_diagnostics`
observes each beacon instance end to end through the public
descriptor only). Beacon engine-vs-frontier deltas from runs before
the fix carry this caveat and are published as-is.

Honest scope: fault *families* are known by design (that is the engine's
everyday knowledge: renames, nesting, pagination switches, code lists,
semantic scaling, date shifts, transport/auth, FIAS layout/link); what
could not be known are the *instances* (family + parameters per seed).
The REQ-007 held-out split is kept as the frozen secondary benchmark:
new data for known fault types.
