# AutoBridge — benchmark (plan §2.10, §4 W8)

## Harness (matched-budget)

For each scenario × trial the harness runs two arms with **identical** inputs:

* **AutoBridge** via the control API (`POST /connectors` → `/build` → `/sync` → `/verify`).
* **Baseline** inside the same `runtime-sandbox` image with the same docs volume, sandbox URL, credentials (refs only), visible tests, token ceiling and wall-clock.

The harness self-test `assert_inputs_equal` (and `assert_budget_fields_equal` for the six task-named fields: `docs_volume`, `sandbox_url`, `sandbox_image`, `credential_refs`, `visible_tests`, `token_ceiling`, `wall_clock_s`) proves both arms received byte-identical `HarnessInputs` before either arm runs; `verify_matched_trial` is the post-hoc audit used by scoring.

Secrets are never in `HarnessInputs` — only `credential_ref` (env key names) are carried; values are read at runtime from the environment inside each arm (DIRECTIVES §6).

The harness is deterministic from `seed`: same seed → identical inputs and scenario seed. `make bench` is idempotent and reproducible — results go to `experiments/benchmark_runs/<scenario>--trial-<n>.jsonl` with `seed` in the payload, and `check_heldout_sha` guards the heldout split.

## Baseline drivers (P1.W8.2)

### `claude_code_headless` — headless CLI with usage capture

`src/autobridge/bench/baseline_coding_agent.py:ClaudeCodeDriver` drives:

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

`src/autobridge/bench/baseline_coding_agent.py:CodexCliDriver` is the `codex` adapter stub (plan §2.10):

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

* `src/autobridge/bench/baseline_coding_agent.py:BaselineLLMDriver` — wraps any `LLMProvider` (e.g. `AnthropicProvider` live, `FakeProvider` in tests) and implements the same `run(prompt, cwd=None) -> ClaudeCliResult` shape. Token usage comes from the provider's `Usage` and `estimate_cost`, so `frontier_tokens` / `cost_usd` remain comparable. When a `Budget` is supplied (from `HarnessInputs.token_ceiling` / `wall_clock_s`) the call is wrapped in `BudgetedProvider`, so the same ceiling is enforced for both arms.
* `src/autobridge/bench/harness.py:run_baseline_via_llm_provider` — functional entry point with the same semantics, used by the harness when `driver=None` and by `make bench` when `BASELINE_DRIVER=llm`.

Factory:

```python
from autobridge.bench.baseline_coding_agent import get_baseline_driver

driver = get_baseline_driver("claude")   # ClaudeCodeDriver
driver = get_baseline_driver("codex")    # CodexCliDriver (stub)
driver = get_baseline_driver("llm")      # BaselineLLMDriver (fallback, plan §9)
driver = get_baseline_driver("one_shot") # OneShotGenerator (secondary control, P1.W8.3)
```

`make bench` selects via `BASELINE_DRIVER` env (`claude` | `codex` | `llm` | `one_shot`); default is `claude` when the binary is present, otherwise `llm` fallback so the harness is runnable in CI without credentials.

## Secondary diagnostic control — `one_shot_generator` (P1.W8.3)

`src/autobridge/bench/one_shot.py:OneShotGenerator` is the secondary control: a single `LLMProvider.complete` that must produce a `ConnectorSpec` YAML in one pass, with the *same* `HarnessInputs` the other two arms receive (docs volume, sandbox URL, credentials as refs, visible tests, token ceiling, wall-clock — asserted by `assert_inputs_equal` / `assert_budget_fields_equal` three-way). It goes through `Budget` + `BudgetedProvider` so ceilings are identical and every call lands in `ModelCall`; cost is `Decimal` via `estimate_cost`.

Helpers:

* `build_one_shot_prompt(inputs, docs_text)` — prompt contains scenario/trial/seed, docs Volume/ref, sandbox URL/image/runtime_target, credential refs (names only), visible tests, token/wall ceilings, plus loaded vendor docs text; no secret values (DIRECTIVES §6).
* `extract_spec_yaml(text)` — normalizes fenced ```yaml ... ``` or bare YAML.
* `try_parse_spec_yaml(text)` — returns dict when valid YAML, else None.
* `load_docs_text(inputs)` — reads docs file/dir from mounted volume (max 120k chars).
* `run_one_shot_arm(inputs, provider, budget, tier, ...)` → `ArmResult(arm="one_shot")` with usage/cost/spec.
* `run_trial_with_one_shot(config, scenario_id, trial, ...)` — runs all three arms under matched audit, returning a `TrialResult` with extra `one_shot` arm.

If the agentic baseline beats the one-shot, the gain is from tool use/iteration, not just model size. The one-shot's `spec_dict` is available for verifier scoring (W8.4) alongside the other arms.

## Scoring and reports

Scoring (P1.W8.4, `bench/scoring.py`) queries the verifier in hidden mode and reports: verified completion, repair success, false-fix/unsafe rate, escalation quality, elapsed, tool calls, model calls, frontier tokens, estimated cost, zero-frontier solves. `bench/report.py` writes `experiments/scorecard.md` listing wins and losses — both are reported.

## `make bench` pipeline (P1.W8.5)

One command runs the whole benchmark (plan §8 verification ladder:
heldout sha check → matched-budget run → `experiments/scorecard.md`):

```bash
make bench                                    # dev split, seed 42
BENCH_SCENARIOS=baseline BENCH_TRIALS=2 make bench
BENCH_SEED=99 make bench                      # reproducible from seeds
BENCH_NO_SCORE=1 make bench                   # harness JSONL only, skip scoring
make bench-heldout                            # Nov 3 run: BENCH_VISIBILITY=heldout
```

Steps inside `python -m autobridge.bench.harness`:

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

It reads each private file's nonce and seed in memory and its sha256,
then scans every `ralph-logs/*.jsonl` transcript, every blob in the full
git history (`git rev-list --all` + `git cat-file`), every file under
`.worktrees/`, and the project folder for any occurrence of a nonce, a
seed file's sha256, or a seed file's raw bytes. Output is one line per
source with counts (`0 hits in 109 transcripts, 0 in 2 140 blobs …`) and
exit 1 on any hit. Bare seed integers are not searched (small ints occur
everywhere); the exact raw file bytes cover that case with no false
positives. The committed manifest itself is the one legitimate place for
hashes, so it is excluded and reported as excluded. The audit result
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
