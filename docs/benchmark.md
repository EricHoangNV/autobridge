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

## Reproducibility

`make bench` is idempotent and reproducible from seeds (`BENCH_SEED`, `BENCH_SCENARIOS`, `BENCH_TRIALS` env). Heldout SHA is checked before the run (`verifier/hidden_cases/heldout/SHA256SUMS`). `tests/e2e/test_07_benchmark_matched_budget.py` runs one dev scenario through both arms and validates the scorecard schema.
