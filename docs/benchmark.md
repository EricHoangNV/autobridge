# AutoBridge — benchmark (plan §2.10, §4 W8)

## Harness (matched-budget)

For each scenario × trial the harness runs two arms with **identical** inputs:

* **AutoBridge** (`run_autobridge_arm`): with an injected HTTP client it posts `POST /connectors` to the control API; with none it returns a synthetic result (`model: autobridge-synthetic`, 0 tool calls).
* **Baseline** (`run_baseline_arm`): with an injected driver it runs the coding-agent CLI under the same docs volume, sandbox URL, credentials (refs only), visible tests, token ceiling and wall-clock; with none it returns a synthetic result (`model: baseline-synthetic`).

`run_trial` / `run_harness` call both arms with no client or driver unless a test injects runners, so the CLI matched mode (`python -m bench.harness --arms matched`) runs **synthetic arms only**; its `_guard_zero_tool_calls` check then refuses to score (exit 1, "refusing empty scorecard") whenever the service probe ran. Matched mode stays behind `--arms matched` for `test_07` / harness tests only. The comparison `make bench` runs is the ablation (`--arms ablation`, below): `make bench` runs the dev ablation + learning curve and writes `experiments/scorecard.md` from the ablation results (refusing to publish an empty scorecard when the ablation produced no trials).

The harness self-test `assert_inputs_equal` (and `assert_budget_fields_equal` over `HarnessInputs.budget_fields()`: `docs_volume`, `docs_ref`, `sandbox_url`, `sandbox_image`, `credential_refs`, `visible_tests`, `token_ceiling`, `wall_clock_s`) proves both arms received byte-identical `HarnessInputs` before either arm runs; `verify_matched_trial` is the post-hoc audit used by scoring.

Secrets are never in `HarnessInputs` — only `credential_ref` (env key names) are carried; values are read at runtime from the environment inside each arm (DIRECTIVES §6).

The harness is deterministic from `seed`: same seed → identical inputs and scenario seed. `make bench` is idempotent and reproducible — results go to `experiments/benchmark_runs/<scenario>--trial-<n>.jsonl` (plus `_summary.json`) with `seed` in the payload. The run outputs under `experiments/benchmark_runs/` (`*.jsonl`, `_summary.json`, `_ablation_summary.json`, `_scorecard.json`) and `experiments/scorecard.md` are gitignored: they are local, not committed results. The one exception is `experiments/learning_curve.md`, which is committed and rewritten by every `make bench-ablation` run (see the learning curve below).

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
(`provider/model` form) selects the model — when unset it is derived from
`FRONTIER_MODEL` (REQ-061: bare `muse-spark-1.3` becomes
`meta/muse-spark-1.3`), so the agent arm always runs the same model as the
one-shot and `-m` is always passed. The driver runs `opencode` with stdin
closed (`DEVNULL`, REQ-061: never waits on inherited input) and, on a
non-zero exit, raises with the stderr plus the parsed stdout `error` events
and the stdout tail (exit-1 causes usually arrive on stdout with empty
stderr). Before the ablation's agent rows, one live `opencode run` must
return text, the model id and token usage (preflight, REQ-061); otherwise
the run stops with the reason instead of filling the table with empty
rows. Unit tests use `FakeOpenCodeDriver` (in-memory, no subprocess).

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

The adapter mirrors `ClaudeCodeDriver` (same `__init__` / `_build_command` / `run` / `calls` API, same usage/token capture via `parse_codex_output` handling `output` / `usage` / `tokens` shapes). Unit tests use `FakeCodexDriver` and `scripts/fake_codex.py`. The stub is pluggable: the harness passes the same `HarnessInputs` to either driver, so the budget fields remain identical.

* Env: `CODEX_CLI_PATH` overrides the binary path.

### Fallback — `LLMProvider` with equivalent agent loop (plan §9)

> **Headless CLI usage capture may require parsing CLI output; the fallback is routing the baseline through the same `LLMProvider` with an equivalent agent loop, documented as such.** (plan §9)

When neither CLI is available (no binary, no API key, offline CI) the baseline is routed through `LLMProvider` directly:

* `bench/baseline_coding_agent.py:BaselineLLMDriver` — wraps any `LLMProvider` (e.g. `AnthropicProvider` live, `FakeProvider` in tests) and implements the same `run(prompt, cwd=None) -> ClaudeCliResult` shape. Token usage comes from the provider's `Usage` and `estimate_cost`, so `frontier_tokens` / `cost_usd` remain comparable. When a `Budget` is supplied (from `HarnessInputs.token_ceiling` / `wall_clock_s`) the call is wrapped in `BudgetedProvider`, so the same ceiling is enforced for both arms.
* `bench/harness.py:run_baseline_via_llm_provider` — functional entry point with the same semantics; no bench path calls it today (unit tests only — with `driver=None` the harness returns the synthetic baseline above).

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

The ablation's `frontier_agent` arm (`make bench-ablation`, step 2 of `make bench`, and the learning curve when a key is set) selects via `BASELINE_DRIVER` env (`bench/ablation.py:frontier_agent_driver`: `opencode` | `claude` | `codex` | `llm` | `one_shot`); default is `opencode`, and without `FRONTIER_API_KEY` the `opencode`/`claude`/`codex` kinds fall back to the in-memory `FakeOpenCodeDriver` — labelled `fake-*` in every result so rehearsal numbers are never mistaken for live ones. `llm` and `one_shot` are built there without a provider, so they run on `FakeProvider` (test use only).

## Secondary diagnostic control — `one_shot_generator` (P1.W8.3)

`bench/one_shot.py:OneShotGenerator` is the secondary control: a single `LLMProvider.complete` that must answer the engine's repair task (REQ-060: a JSON Patch on the pre-drift spec, or an escalation with one question — never a whole spec) in one pass, with the *same* `HarnessInputs` the other two arms receive (docs volume, sandbox URL, credentials as refs, visible tests, token ceiling, wall-clock — asserted by `assert_inputs_equal` / `assert_budget_fields_equal` three-way). It goes through `Budget` + `BudgetedProvider` so the wall clock is identical and every call lands in `ModelCall`; cost is `Decimal` via `estimate_cost`. The token ceiling defaults to 200,000 per attempt (`DEFAULT_TOKEN_CEILING`, see "Token limit" below); `token_ceiling` 0 = no limit (REQ-060), in which case nothing is ever `budget_exhausted` on tokens. The per-call output cap stays `FRONTIER_MAX_TOKENS` because the API needs a value.

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
only difference between arms is the engine. The provider is Meta Muse
(`MuseProvider`, `FRONTIER_BASE_URL` default `https://api.meta.ai/v1`)
when `FRONTIER_API_KEY` is set, else replay over
`tests/fixtures/replay/`. `FRONTIER_MODEL` defaults to `muse-spark-1.3`
(`src/autobridge/config.py`); set it (e.g. `muse-spark-1.3-contributor`,
`muse-wonder-1.0`) to change every arm at once — the agent arm derives
`meta/<FRONTIER_MODEL>` unless `OPENCODE_MODEL` is set, and a live agent
row whose model differs fails as `model_mismatch`.

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
construction. Detection compares the faulted sync against a baseline
sync of the same spec (`derive_from_summaries`, shared with the beacon
run). Since REQ-075 the money baseline reaches the detector only through
the bounded money sketch the live path persists
(`autobridge.drift.value_baseline.build_money_sketch` →
`baseline_summary_from_sketch`: join keys + money fields, at most 32
entities × 200 rows), never the raw baseline records, so the bench
credits no detection the product cannot make; the baseline wire
observations still feed the wire-key scoping below.

Honest scope — last recorded engine-arm numbers, **not a current
result**: a sandbox rerun after the REQ-028 fixes (2026-10-29,
`BENCH_TRIALS=1 make bench-ablation`, local stack, no frontier key)
recorded AutoBridge cold repairing 6/6 repair scenarios verified
(`page_to_cursor`, `offset_to_cursor`, `cursor_repeat_token`,
`meridian_cursor_renamed`, `rename_guest_id`, `meridian_state_renamed`
— all 0 frontier tokens, 0 wrong patches), warm equal to cold on every
scenario, overall cold/warm V=0.500 E=1.000. Source: `mds/BUILD-NOTES.md`
(REQ-028 section) and `mds/progress-log.md` only — the raw rows are not
committed (gitignored), n=1, and the engine and bench have changed since
(REQ-060 to REQ-075). The owner's keyed dev rerun is still pending
(REQ-028 is BLOCKED on it). Three REQ-028 fixes got here from the published gaps:
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
Still open: no keyed frontier-arm ablation result is in the repo (the
sandbox has no `FRONTIER_API_KEY` — `frontier_oneshot` fails loudly with
`ReplayMissing`, `frontier_agent` runs on the fake driver); REQ-060 has
landed and the owner's keyed rerun is pending (the re-run measures the
models, not the harness).

Metrics (`summarize_ablation`, per arm / per arm+scenario / overall, mean +
95 % normal-approx interval, ≥5 trials): verified completion rate (verifier
only: the arm ran, the target passed and the regression gate passed),
wrong patches released (target passed but the regression gate failed;
must be 0), correct escalation rate,
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
Token limit (owner 2026-10-31): `token_ceiling` (and
`BENCH_TOKEN_CEILING`, or `--token-ceiling`) defaults to 200,000 tokens
per attempt for every arm (`bench/harness.py:DEFAULT_TOKEN_CEILING`),
sized by the owner at about 1.3x the largest keyed run seen with no
limit (`frontier_agent` 156,389 on `duplicate_late_records`, run on
`cf2666e6` — a sizing note from commit `afd0283a`'s message; that run's
rows are not committed, so it is not a published result). 0 = no
limit (REQ-060): `Budget` skips the token check when its limit is 0.
`Budget` enforces the ceiling for the one-shot and engine arms; the
agent arm is told the ceiling in its brief, but `opencode run` is not
metered mid-run — only the wall clock stops it, and its tokens are
recorded after the run. Every row records input, output
and reasoning tokens (when the provider reports them); the summary
adds per-arm min / median / max total tokens (`summary
["total_tokens_stats"]`) so the owner can pick a limit from the
results. The one-shot and the engine's model calls price by the resolved
model id from `MODEL_PRICES_PER_MILLION` (`src/autobridge/config.py`,
input/output per M: Spark `muse-spark-1.3` $1.25/$4.25, Spark
Contributor `muse-spark-1.3-contributor` $0.10/$0.20, Wonder
`muse-wonder-1.0` $4/$20; any other id falls back to a flat legacy
per-tier rate, frontier $15 per M); the agent arm's cost is what
`opencode` reports for its session. With
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
make bench            # the real comparison: dev ablation + learning curve, scorecard from the ablation
```

`make bench-ablation` is the command that runs the comparison. `make bench`
runs the same pipeline (dev ablation + learning curve) and writes the
scorecard from the ablation results.

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
plus the per-arm table at the end. The owner's keyed rerun of this
exact command is development rehearsal, not the pre-registered run
(that is the beacon run, below).

Arm availability (`describe_arm_availability`, printed at the start of
every ablation run and stored on the summary): `frontier_oneshot` runs
live when `FRONTIER_API_KEY` is set, else replays the committed
recordings (a missing prompt-hash recording fails that scenario
loudly, never invented), else the fake provider (offline rehearsal, 0
tokens). `frontier_agent` runs live only with both the `opencode` CLI
on PATH and `FRONTIER_API_KEY` set — the supported path is running the
bench inside the sandbox. Without the key the harness uses
`FakeOpenCodeDriver` and labels rows `fake-*` so rehearsal numbers are
never mistaken for live ones; with the key but no CLI the agent
preflight fails and the run stops. The three engine arms always run
in-process.

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
scans every dev scenario's bundle for leaks.

The agent arm's sandbox holds the same files
(`schema.json`, `capabilities.json`, `example_spec.yaml`,
`vendor_docs.md`, `visible_tests.json`, `pre_drift_spec.yaml`,
`evidence.md`, `task.md`, written by `write_agent_sandbox`), and its
file-based brief names them. The `run_baseline_via_llm_provider`
fallback (plan §9; the ablation never calls it, it drives
`run_baseline_arm`) renders the same full context for real scenarios;
only unknown/synthetic scenario ids with no ablation case keep the
legacy one-liner (unit tests). Grading is symmetric: a frontier patch
is applied to the pre-drift spec and goes to the verifier like an
engine patch (fixable fixed / wrong shipped); an escalation on an
unfixable scenario counts as held back, on a fixable one as not fixed
(`tests/unit/test_autobridge_bench_req060.py` pins all four).

The learning curve (`run_learning_curve` via `python -m bench.ablation_curve`,
the last step of `make bench-ablation`; flags `--scenario`
(default `page_to_cursor`), `--repeats` (5), `--seed` (42), `--provider
auto|fake|live`, `--out` (`experiments/learning_curve.md`)) injects the
same drift five times: the agent pays every repair; the engine arms show
what the ladder genuinely does (proposing caches nothing, so cold repeats
re-derive at L1 with 0 model tokens; L0 hits only after an approval) —
with the driver/provider provenance printed on the file so rehearsal
rows (fake-labelled) cannot be mistaken for publication numbers. The CLI
passes no verifier URL, so `agent_verified` reads `unverified` and the
warm arm learns nothing (it only learns verified fixes) and repeats cold.
The committed `experiments/learning_curve.md` is an unkeyed rehearsal
(`FakeOpenCodeDriver`, 2,000 fake tokens per repeat; cold/warm rung 1,
`propose`, 0 tokens) written before the `agent_verified` column existed —
not a publication number.

Pre-registration: `experiments/preregistration.md` — arms, metrics,
trials, budgets, the primary hypothesis (`autobridge_cold` ≥
`frontier_agent` verified completion at ≥10× fewer frontier tokens,
measured on the beacon run), and the promise that results are published
whatever they are. The final evaluation is the beacon run (see "Beacon
evaluation" below); the dev-split ablation is the rehearsal.

## Scoring and reports

Scoring (P1.W8.4, `bench/scoring.py`, the matched two-arm path; the ablation grades with `grade_ablation_trial` above) queries the verifier in hidden mode and reports: verified completion, repair success, false-fix/unsafe rate, escalation quality, elapsed, tool calls, model calls, frontier tokens, estimated cost, zero-frontier solves. `bench/report.py` writes `experiments/scorecard.md` listing wins and losses — both are reported.

## `make bench` pipeline (P1.W8.5, REQ-024 B6, REQ-077 item 2)

One command runs the dev-split benchmark (all ablation arms →
`experiments/scorecard.md` → learning curve):

```bash
make bench                                    # dev split: ablation arms, scorecard from the ablation, learning curve
BENCH_SCENARIOS=baseline BENCH_TRIALS=2 make bench
BENCH_SEED=99 make bench                      # reproducible from seeds
BENCH_NO_SCORE=1 make bench                   # ablation JSONL only, skip the scorecard
make bench-ablation                           # all arms on the dev split + learning curve (needs the stack for grading: make local-up first)
```

Other knobs: `BENCH_TOKEN_CEILING` (default 200000, 0 = no limit),
`BENCH_WALL_CLOCK` (default 600 s), `BENCH_ARMS` (`matched` | `ablation`;
the make targets pass `--arms` themselves), `BASELINE_DRIVER`,
`FRONTIER_MODEL` / `OPENCODE_MODEL`. `BENCH_TRIALS` defaults to 5 for
the ablation (the make target sets it). The make targets pass
`--control-api-url http://localhost:8101 --verifier-url http://localhost:8104`,
which win over `CONTROL_API_URL` / `VERIFIER_URL`. The synthetic matched
mode stays behind `--arms matched` for `test_07` / harness tests: from
the CLI both arms are synthetic (see "Harness") and
`_guard_zero_tool_calls` still refuses to publish that empty scorecard.
`BENCH_NO_SCORE=1 make bench` writes the ablation JSONL only.

Steps inside `python -m bench.harness` (matched mode):

1. **Heldout gate** (REQ-007, retired — see "Held-out split (retired)")
   — dev runs skip it.
2. **Matched-budget run** — every scenario × trial through both arms with
   byte-identical `HarnessInputs` (asserted pre-run and audited post-run);
   from the CLI both arms are synthetic (see "Harness").
3. **JSONL output** — `experiments/benchmark_runs/<scenario>--trial-<n>.jsonl`
   plus `_summary.json` (overwritten, never appended — re-runs are idempotent).
   Unless the probe was skipped, `_guard_zero_tool_calls` then exits 1
   when an arm has 0 tool calls across all trials — which the synthetic
   arms always do.
4. **Scoring + report** (`score_and_report`) — every trial × arm scored via
   verifier hidden mode over HTTP (unreachable verifier records losses,
   never raises), then `experiments/benchmark_runs/_scorecard.json` and
   `experiments/scorecard.md` (wins AND losses) via `bench/report.py`.
   Dev scorecards carry the header `Heldout seeds: n/a — dev run on
   public YAML seeds`.

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

## Held-out split (retired)

The private held-out seeds (REQ-007 / REQ-024 A, manifest
`verifier/hidden_cases/heldout/SHA256SUMS`, frozen at `ff372083`) are no
longer part of the evaluation. Private seeds cannot be published without
ending their secrecy, so nobody else can download them and check a run.
The beacon run below proves the same thing — we could not have tuned on
the test — with inputs anyone can fetch and verify. The manifest and the
`make bench-heldout` / `make verify-heldout-reveal` targets stay in the
repo untouched, but no held-out run is planned and there is no held-out
scorecard.

Unseen-vendor generalization is measured separately on LegacyStay
(REQ-019 item 5): the `tests/e2e/test_s1_legacystay.py` generalization guard keeps
that vendor out of engine code.

## Beacon evaluation (REQ-025)

Why: this is the final evaluation. A private held-out split can only
seal *instance data for public fault types* and cannot be checked by
anyone else; here the test's data *and* fault details derive from a
public random value (a League of Entropy drand round) that did not exist
when the code froze. Commit-then-reveal with public randomness — we could
not have prepared for the test even if we wanted to.

Status: only rehearsals on past rounds have run. The real freeze has not
happened — there is no `eval-freeze-<round>` tag, no
`experiments/beacon/<round>/protocol.md` and no
`experiments/beacon/<round>/` result yet — so there are no beacon
results to report. The list below is what a judge can check once it has.

What a judge can check (all of it public):

1. `experiments/beacon/<round>/protocol.md` — the freeze
   (`bench/beacon_run.py:freeze`): one protocol per run (the freeze
   computes the target round first, then writes the round's own file;
   `parse_protocol` still reads the old single `experiments/beacon/protocol.md`
   when present). Contents: round rule (first round at/after a UTC time)
   and target round, N instances (`beacon-faults`), trials
   (`beacon-trials`, default 5), arms (the five above), the commit it was
   frozen at (recorded as both the engine and the generator commit), the
   freeze time, the rehearsal flag, the drand chain, the metrics in prose,
   "one run, no retries, results published whatever they are", the
   pinned frontier model (`beacon-frontier-model`, from `FRONTIER_MODEL`,
   plus `beacon-agent-model`, the agent arm's resolved `meta/<id>` form,
   when it differs), and the frozen budgets (`beacon-token-ceiling`,
   `beacon-wall-clock-s` — one ceiling and one wall clock for every arm,
   the agent included). The pin proves the model was fixed before the random
   value existed: `bench-beacon` (real runs and rehearsals) refuses to
   start when the resolved model differs, a real run requires the line,
   and a protocol without it (older rehearsals) still parses. The run
   uses the frozen budgets (a pre-freeze protocol falls back to
   `BENCH_TOKEN_CEILING` / `BENCH_WALL_CLOCK` / the dev-ablation
   defaults) and prints the values it used (`beacon run: token_ceiling=…
   wall_clock=…`); the agent arm is held to the same ceiling (an agent
   row over it is `budget_exhausted`, never a pass). The run does not
   itself check out or compare the frozen commit.
2. The `eval-freeze-<round>` git tag and its push time — before the round's
   publication time (the beacon carries no timestamp; round `r` is
   published at the chain's `genesis_time + (r − 1) × period`, from the
   chain info; compare the two).
3. Re-fetch the round (`make verify-beacon ROUND=<n>`): the client
   verifies the threshold BLS signature against the LoE mainnet key with
   pinned `py_ecc` (fail closed), re-derives
   `eval_seed = sha256(randomness || "|" || "autobridge-eval-v1")` (per
   instance `sha256(eval_seed || index_be8)`), regenerates the
   instance descriptors through the simulator admin plane (so it needs
   the local stack up), and checks they match the published
   `experiments/beacon/<round>/instances.json`.
4. The per-row grades (`grades.jsonl`, one row per instance × trial ×
   arm, appended as it lands so a mid-run abort loses nothing and a rerun
   resumes the landed rows) + `summary.json` (per arm, per family, and
   overall). Grading is about the act, the same for every arm: a crashed,
   timed-out, truncated, over-budget or never-ran arm reports outcome
   `Error` — its own failure class, never an escalation (it passes no
   instance, not even `escalate` ones; escalation passes only when the arm
   explicitly decided to ask). "No change needed" is a stated answer for
   every arm (an empty patch means no change). "No patch + one question"
   grades the same whether reported Degraded or Blocked. FIAS repairs are
   judged on the produced records like HTTP repairs. Scores are averaged
   per instance first and CIs are computed over instances with a bounded
   interval (`n` names instances), with paired per-instance arm
   differences vs cold and per-family rows in the summary and EVIDENCE.md.
   Metrics: verified completion rate (judged only by the verifier),
   wrong patches proposed as well as released (an unneeded patch on a
   no-change instance counts as wrong; released must be 0 for AutoBridge),
   correct escalations (Blocked with one operator question on an instance
   whose key accepts a question; a no-patch Complete on a throttle is
   verified completion, not an escalation), frontier tokens, estimated cost
   (engine model cost is recorded in-memory, never left unset), wall time. The warm arm
   learns during the run: instances run in the frozen order, and after
   each instance a verified fix the warm arm produced is learned (the way
   an approved fix is in production) and is available to later instances;
   cold never learns. Each instance reports whether warm used a learned
   fix (rung + source instance) — a warm-vs-cold gap shows learning, not
   pre-knowledge. The frontier agent is confined to file tools inside its
   sandbox dir (read/glob/grep/edit; no shell, no fetch, no subagents,
   no outside-directory reads — `opencode.json` denies `bash`,
   `webfetch`, `websearch`, `task`, `skill`, `lsp` and
   `external_directory`) with an allowlisted child env built from scratch
   (`PATH`/`HOME`/`TMPDIR`/locale plus the CLI's own `OPENCODE_*`/`XDG_*`
   settings and the frontier provider's key variables; `PWD` pinned to
   the sandbox dir and `OPENCODE_PERMISSION` set to the same deny config
   so an inherited value can never loosen it). Nothing else crosses — no
   repo checkout, no generator/verifier sources, no verifier/admin ports;
   the preflight proves the permission file and the allowlist before the
   first scored instance, rehearsals included; both
   arms get the stored last-good baseline — the frontier arms' evidence
   shows the baseline sample next to the faulted values, clearly labelled
   baseline vs faulted, and the engine arms compare against the same
    stored baseline the detector uses. Owner-accepted answers: code_list
    "repair" instances accept an escalation with one question (nothing
    reveals a random new code's meaning; a guessed mapping is a wrong
    patch), and `transport_auth/token_expired` accepts an escalation.
    Semantic `minor_major` keys `repair` (the `minor_units` reference
    repair is proven by the preflight); semantic `gross_net` keys
    `degraded_question` (no factor arg in the closed transform set — the
    operator decides the business meaning, and a guessed factor is a
    wrong patch).
   A meridian `dates/cutoff` fault keys `no_patch`: it recomputes folio
   `business_date` values the meridian wire never serves (money and
   instants only), so the connector output is correct and there is
   nothing to repair (REQ-078 item 8).

Protocol commands: `make eval-freeze ROUND_AFTER=<UTC> FAULTS=<N>
[TRIALS=5]` (item 4: writes the round's own `protocol.md`), `make
bench-beacon [ROUND=<n>]` (item 5: refuses an unpublished round or an
existing result dir — no re-rolls; without `ROUND` it resolves the single
unrun protocol of the checkout), `make verify-beacon ROUND=<n>
[OUTDIR=<dir>]` (item 7), `make beacon-eval [FAULTS=25] [DELAY_MIN=20]
[PUSH=yes] [CONFIRM=yes]` (item 6b: one unattended command — checks the
tree is clean and that no ralph loop runs in this checkout's path,
freezes a NEW future round (resuming only an unrun protocol of this
checkout, never another run's results), commits, tags
`eval-freeze-<round>`, pushes the freeze and later the results only with
`PUSH=yes` (to the current branch's upstream; without it both are
committed locally and the script prints how to push to a fork before the
round for a public timestamp; `CONFIRM=yes` skips the one "yes"), waits,
runs, writes `EVIDENCE.md` + `evidence.png` pointing at the round's own
protocol and tag, commits; resumable). Rehearsals on past rounds: `make
bench-beacon REHEARSE=<round> [FAULTS=10] [TRIALS=1]` (item 6: labelled
rehearsal under `experiments/beacon/rehearsals/<round>/`, at least five
before the freeze; that directory is gitignored — practice runs, never
results). Results land in `instances.json`, `grades.jsonl` and
`summary.json` in the round's directory.

### Run it yourself

Prerequisites: the stack up (`make local-up` or `make up`) and
`FRONTIER_API_KEY` for the frontier arms (without it the frontier arms
run on replay/fake drivers and the scorecard labels say so — the engine
arms still run the real path). One command freezes a new future round
and runs it end to end:

```bash
make beacon-eval FAULTS=25
```

Roughly: the freeze targets the first drand round at/after now + 20
minutes; the run then attempts every instance × trial on all five arms
(frontier calls happen on the climbable faults — the engine climbs past
L1 to L2/L3 whenever no deterministic rule fixes the drift, plus whatever
the frontier arms spend). Results land in
`experiments/beacon/<round>/` (`instances.json`, `grades.jsonl`,
`summary.json`, `EVIDENCE.md`); anyone can re-check a published run
with `make verify-beacon ROUND=<n>`.

Grading: every arm attempts the same instances under matched budget —
the same repair task (the pre-drift spec under repair plus the faulted
sync's evidence: outcome, unmet criteria / failed checks, served
response sample), the same file-based bench prompts
(`config/prompts/bench/`), and the same 200k token ceiling (the dev
ablation default, enforced the same way — `Budget` for the one-shot and
engine arms, wall clock only for the agent; `token_ceiling` 0 = no limit
via `BENCH_TOKEN_CEILING`, per-call output cap `FRONTIER_MAX_TOKENS`
(default 131072), wall clock 600 s via `BENCH_WALL_CLOCK`). Before the
first arm (rehearsals included) the run proves the apparatus works: the
harness self-test syncs every vendor's pre-drift spec over its unfaulted
simulator and requires the records judge to pass, checks the candidate
sync wiring (vendor host + spec base path, resolvable credentials),
renders both frontier prompts non-empty and pins the agent child's
working directory — failing loudly otherwise. Arms that produce a candidate spec sync it with the
real runtime against the live beacon shape; produced records are judged by
the verifier (`/beacon/records`: canonical set, no loss/duplicates, exact
money, property-local business dates, canonical status vocabulary, every
canonical field value-compared, plus
contract coverage by the spec). Pagination renames are announced the way
a vendor would: the served vendor docs for the faulted round describe
the new request parameters (the same docs every arm reads — the only
channel that names them; the evidence and diagnostics never do). The reported outcome class is judged by
`/beacon/grade` against the accepted set. Beacon randomness, seeds and
descriptors are public by design; what is computed server-side, after
arms report, is the answer key. FIAS instances carry an in-process engine
spec built from their docs (vocabulary v3, as in the demo), so engine
arms derive and climb in-process like every other vendor: the announced
PS layout (the same handshake the live edge agent reports) is compared
against the spec's mapped wire ids. Framed transport has no live HTTP
sync, so a FIAS candidate syncs in-process over the announced feed
layout — but it is judged by `/beacon/records` like every HTTP repair,
against what the pre-drift spec produces over the unfaulted feed (one
judging regime for all vendors). `wrong_patch_released` counts only
rows whose patch reached a release (records verified) and then failed the
outcome grade — each such row names the patch and the failed checks, and
the summary total always equals the rows. A `Blocked` row names which
step blocked (detection with no signals, the ladder with no matching
rule, …) and why. Token accounting is in/out per model tier on one
basis for every arm: rows carry `frontier_input` / `frontier_output` /
`small_input` / `small_output` (frontier arms spend the frontier tier
only; engine arms split small vs frontier — never a swapped total), and
the summary adds per-arm min / median / max totals.
The judge fails closed (`judge_error`, bounded) instead of raising, so a
grading error is an explicit row label, never a silent 0 or an HTTP 500.
No drift detected is not an escalation (REQ-063): when the detector
reports no signals and the faulted sync is `Complete`, every engine arm
reports `Complete` with no patch and no question — production opens no
repair run, so the ladder is never called — and the row reads "no drift
detected". On a repair instance that still fails, as a silent miss.
An escalation carrying one operator question passes a
`degraded_question` instance whether reported `Degraded` or `Blocked`;
`escalate` instances pass with or without a question; a semantic decoy
also accepts `Complete` with no patch (the connector ignores the decoy,
so its output is correct). Every rehearsal runs a bench-side sanity
gate first (`sanity_unchanged_repairs`): a repair instance whose faulted
canonical output equals the baseline with no drift signals is a
generator bug — the run refuses before spending any arm budget, naming
the instance.
Engine arms that cannot attempt a vendor in-process (no spec source in
the vendor table) are recorded `executed: false` with the reason named —
a fail-closed loss, the same for every arm.
`autobridge_warm` is pre-warmed from the dev split only: `make bench-beacon`
builds its learned knowledge from the dev scenarios (never beacon
instances) before the first instance — the same for real runs and
rehearsals — so the beacon run measures learning. The run prints how
many dev-split fixes the warm cache holds (and `EVIDENCE.md` records it);
`autobridge_cold` starts from an empty cache.
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

Serving guarantee (REQ-078 item 8, REQ-080 item 1): every beacon
record-shape mutation is served on the vendor wire, never a 500.
Meridian parses dates strict-ISO: an unparseable date nulls the derived
UTC fields and rides the wire verbatim as an extra, as do nesting
parent wrappers. A renamed field vanishes from the wire — every
vendor serializer emits the old key only when the record still carries
the field — and only the new key is served (Atlas folio renames ride
the transaction wire under their camelCase name). The item-4 detection
sweep covers these instances: none whose records differ from the
baseline may end `Complete` with 0 signals.

Answer key (REQ-080 item 4): a rename to a name that plausibly denotes
the same field expects a `repair`; a rename to a misleading new name
(one that plausibly denotes a *different* field) expects an escalation
with one operator question — a guessed patch there is not credited.
The L1 `rename_field` rule repairs only unambiguous renames by a
generic, vocabulary-free test (exactly one mapped field emptied or
vanished, exactly one new wire key with values of the expected shape,
and a new name lexically close to the old wire key or the canonical
field name); every other inferred pairing climbs to the model, whose
evidence carries the
new wire keys and a redacted raw vendor-record sample.
