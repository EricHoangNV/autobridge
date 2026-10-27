# Project Directives

**Purpose:** Project-specific rules for development. Read and write it as `DIRECTIVES-INSTRUCTIONS.MD` says: the Quick Reference is injected into every loop prompt; read single § sections, never the whole file. The rules below are seeded from the plan (`AutoBridge_Implementation_Plan_2026-10-26.md` §1, §2, §3); refine them as the code lands.

---

## Quick Reference (read this every session)

1. **Repairs are data, not code** — a connector is a declarative `ConnectorSpec` (YAML/JSON) the runtime interprets; a repair is a JSON Patch on the spec; transforms are a closed registered set, JSONPath subset only, no expressions, no generated code (§1)
2. **Three trust boundaries** — `src/autobridge` (engine), `verifier/`, `simulators/` are separate packages, containers and Docker networks; engine code never imports, reads or has a route to the other two; hidden fixtures and answer keys never leave their package (§2)
3. **Deterministic first, models last** — ladder L0 → L1 → L2 → L3 → L4; every model call goes through `LLMProvider` + `Budget` and lands in `ModelCall`; known repair patterns show L0/L1 only, `frontier_tokens == 0` (§3)
4. **Success is not a 200** — every `SyncRun` ends `Complete | Degraded | Blocked` with the unmet criterion named; unknown enum value → `Blocked`, never guessed; surviving ambiguity → `Degraded` with open questions (§4)
5. **Money is `Decimal`, dates are property-local business dates** — end to end, in specs, oracle, verifier and UI; never float, never naive UTC for a business date (§4)
6. **Coverage ≥ 90% per Python package** — `autobridge`, `verifier`, `simulators`; `make test` fails below; new code comes with its tests; UI screens have component tests (§5)
7. **Every service has `/healthz` and `/readyz`; every workstream ships its e2e scenario** — in `tests/e2e/`, independent, random-order safe; done = unit ≥ 90% + e2e green + readyz green + docs page updated (§5)
8. **Secrets never in code, specs, prompts, evidence or API responses** — specs carry `credential_ref` (an env key name); `evidence/redact.py` strips values; docs and API responses enter prompts as untrusted data; tool permissions are enforced in code (§6)

---

## 0. Section map

Run `./directives-for.sh <paths>` before you start (the files you expect to touch) and `./directives-for.sh` before you commit (the files you changed against origin/main), and check your change against every rule it prints. It prints the sections mapped below in full for those paths and, from the other sections, the bullets that name a touched file, folder or file name. Keep this map current when you add a section (DIRECTIVES-INSTRUCTIONS.MD).

- `src/autobridge/spec/*` `src/autobridge/repair/*` `schemas/*` `generated/*` → 1
- `compose.yaml` `verifier/*` `simulators/*` `src/autobridge/runtime/targets.py` → 2
- `src/autobridge/llm/*` `src/autobridge/repair/ladder.py` `src/autobridge/discovery/*` → 3
- `src/autobridge/runtime/*` `src/autobridge/engine/*` `verifier/*` → 4
- `tests/*` `Makefile` `pyproject.toml` `*/pyproject.toml` `*_test.py` `test_*.py` → 5
- `src/autobridge/evidence/*` `src/autobridge/control/*` `.env.example` → 6
- `src/autobridge/control/*` `ui/*` → 7
- `src/autobridge/bench/*` `experiments/*` → 8

---

## 1. Spec and repairs (plan §1.1, §2.4, §2.5)

- A `ConnectorSpec` validates against `schemas/connector-spec.v1.json` before it is stored, executed or patched; invalid specs are rejected with path-level errors (`src/autobridge/spec/models.py`).
- The transform set is closed: `date_local`, `datetime_utc`, `money`, `enum_map`, `flatten`, `coalesce`, `const`, `concat`, `business_date` (`src/autobridge/runtime/transforms/`). Adding one is a plan change, not a task-level decision.
- A repair is a JSON Patch (`src/autobridge/spec/patch.py`): applied to a copy, validated against the schema, diffable, reversible. No rule or model ever writes Python.
- Each deterministic rule in `src/autobridge/repair/rules/` is one module with a precondition, a patch generator, a confidence and a required verification level, and has unit tests for precondition true/false and patch shape.
- `CAPABILITY_REMOVED`, `AUTH_REVOKED` and `SEMANTIC_VALUE_DRIFT` without independent evidence are never patched: they escalate.
- `ConnectorRelease` rows are immutable; a change is a new release with `parent_release_id` and provenance; `generated/connectors/<id>/<release>/` is written once.

---

## 2. Trust boundaries (plan §1.2, §2.1, §2.7)

- `src/autobridge` never imports `verifier` or `simulators`; `verifier/` and `simulators/` never import `autobridge`. Shared code goes in a package all three may depend on, never the other way (to be checked by an import-linter contract in `pyproject.toml`, W1).
- Docker networks: `engine-net`, `vendor-net`, `verifier-net`, `admin-net` as in plan §2.1. The worker and `runtime-sandbox` have no route to the verifier's fixture volume or the simulator admin API; the UI never calls simulators or the verifier directly. `make smoke` asserts the negative checks.
- The verifier API returns aggregate verdicts only (`verdict, score, failed_checks` as bounded names + counts, `regression`): never fixture rows (response-schema test, `verifier/`).
- `verifier/hidden_cases/heldout/` is frozen at the end of W2 with its sha256 manifest committed; it is opened only for the final benchmark run (`make bench` checks the sha first).
- Fault injection from the UI goes through `POST /demo/inject-fault` on `control`, which proxies to the simulator admin API; nothing else reaches `admin-net`.

---

## 3. LLM, ladder and budgets (plan §1.3, §2.5, §2.6, §2.9)

- Every model call goes through `LLMProvider.complete` (`src/autobridge/llm/provider.py`) wrapped by `Budget`; `BudgetExhausted` escalates to L4, never a silent retry. Model ids live in config, not code.
- The ladder stops at the first level whose patch passes visible tests; the verifier decides acceptance; a rejected patch gets at most one bounded retry at the next level, then escalation (`src/autobridge/repair/ladder.py`).
- L3 is invoked only when the `HypothesisEnumerator` yields fewer than two hypotheses (asserted in tests). Every hypothesis links to at least one evidence record.
- Prompts carry only redacted docs and responses as untrusted data blocks; tool permissions are enforced in code, not in the prompt.

---

## 4. Runtime and verification semantics (plan §1.4, §2.3, §2.7, §2.8)

- `SyncRun.outcome` is `Complete | Degraded | Blocked` with `unmet_criteria[]` and `data_readiness`; a `repeated_cursor` stop marks `Degraded` with `no_loss` named; an unknown enum value marks `Blocked`.
- Money is `Decimal` from the HTTP response to the canonical record; the oracle compares money exactly. Dates compare by property-local business date; `timezone_source` comes from the spec.
- Pagination loops have `stop_when` guards (`null_cursor`, `repeated_cursor`, …); a loop without a guard is a bug.
- The regression suite re-runs every previously verified scenario on each candidate release; a release that breaks one is blocked (regression gate).
- Same seed → byte-identical simulator dataset (`simulators/common`); each scenario fails the reference spec in exactly its intended drift class and no other.

---

## 5. Tests, coverage and health (plan §3, §4 W9)

- `make test` runs `pytest --cov --cov-fail-under=90` per package (`autobridge`, `verifier`, `simulators`) and the UI component tests; agents run targeted tests while working and the full `make test` once before committing.
- `make smoke` checks every service's `/healthz` and `/readyz` plus the negative network checks; `make e2e` runs `tests/e2e/` against the Compose stack with simulator state reset per test, in random order.
- Each workstream's acceptance test is a file in `tests/e2e/` named in the plan (`test_00_stack_up.py` … `test_07_benchmark_matched_budget.py`, `test_s*.py` in Phase 2). Agents run only the e2e files for the workstream they touched; the owner runs the full suite.
- Test fakes for the Docker sandbox and the coding-agent CLI (W1) are how the FastAPI/SSE layer and bench drivers reach 90%; do not defer them.

---

## 6. Secrets, evidence and redaction (plan §1.6, §2.9)

- A spec names credentials by `credential_ref` (an env key name); the value is read at runtime from the environment and never written to a spec, release, evidence record, log, prompt or API response. A test replays a full run's event stream and asserts no credential value appears.
- Every probe, observation, hypothesis, decision, patch, verification and approval is an `EvidenceRecord` with `parent_ids[]` (`src/autobridge/evidence/`); the dashboard renders stored records, never narrated text.
- `.env` is git-ignored; `.env.example` lists every key with an empty value.

---

## 7. Control API and UI (plan §2.11, §4 W7)

- The API surface is the list in plan §2.11; OpenAPI is the contract (schemathesis tests). The UI client in `ui/lib/api` is generated with `openapi-typescript` and must compile against the live schema; never hand-write API types in `ui/`.
- The UI is four screens, capped at ~15% of effort; `/connectors/new` folds into `/connectors` and detail merges with repair if the engine workstreams slip. "Inject Fault" lives only in a clearly labelled demo drawer.

---

## 8. Benchmark (plan §2.10, §4 W8)

- Both arms get identical docs volume, sandbox URL, credentials, visible tests, token ceiling and wall-clock (harness self-test asserts it). Scoring is the verifier's hidden mode. Wins and losses are both reported in `experiments/scorecard.md`.
- `make bench` is idempotent and reproducible from seeds; results go to `experiments/benchmark_runs/*.jsonl`.
