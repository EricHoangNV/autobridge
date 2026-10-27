# AutoBridge — Task Tracking Manifest

> **How to use (agents):** Scan this manifest, pick the next open task (`[ ]` or `[~]`).
> Read **only** the plan section it points to (`AutoBridge_Implementation_Plan_2026-10-26.md` §tag, index in `PRD.MD`) — do **not** read the full plan.
> When a task is done: flip its status **here**, and append the build note to `BUILD-NOTES.md`.
> **Never edit the plan or `PRD.MD`** from a task run — they are the stable design docs; they change only when the owner (re)plans.
>
> Status: `[x]` done · `[ ]` open · `[>]` claimed by a ralph loop ("in progress: sbx1 since …" or "sbx-mini") · `[~]` partial/blocked. Completed-task build notes live in `BUILD-NOTES.md`.
> Task ids: `P<phase>.W<workstream>.<n>` for Phase 1 (e.g. `P1.W2.3`), `P<phase>.S<item>.<n>` for Phase 2 (e.g. `P2.S1.1`).
> Workstreams are in dependency order (plan §4): W1 → W2 → W3 → W4 → W5 → W6; W7 after W3; W8 after W6; W9 grows with every workstream; W10 last. Phase 2 opens only after W1–W6 are green and the stop rule holds (plan §5).
> `HUMAN` = needs the owner; agents do the codeable part, then leave it `[~]` with the exact manual step.
> A workstream is done when its unit tests are ≥ 90%, its e2e file is green, its `readyz` checks are green and its docs page is updated (plan §3).


### Task Manifest

### Phase 1 — Must-have (plan §4)

#### W1 — Foundations (plan §4 W1, §2.2, §2.3, §2.9)
- [x] P1.W1.1 Repo scaffold: uv workspace `pyproject.toml` with packages `autobridge` (`src/autobridge`), `verifier/`, `simulators/`; ruff + mypy + pytest-cov config with the 90% gate per package; import-linter contract that `autobridge` never imports `verifier` or `simulators` (DIRECTIVES §2); `Makefile` targets `up / down / test / smoke / e2e / bench / demo / lint / typecheck`; one passing smoke test per package; `.env.example` complete
- [x] P1.W1.2 `compose.yaml`
- [x] P1.W1.3 `ConnectorSpec` v1: pydantic models (`src/autobridge/spec/models.py`), `schemas/connector-spec.v1.json`, the closed transform name set, JSONPath-subset validation; YAML → model → JSON-schema round-trip; invalid specs rejected with path-level errors; `spec/patch.py` JSON Patch apply + validate + reverse; `spec/registry.py` for releases on disk under `generated/connectors/`
- [x] P1.W1.4 Canonical entity schemas `schemas/reservation.schema.json`, `schemas/folio-charge.schema.json`; `schemas/runtime-target.json`; `schemas/discovery-result.v0.json` reserved (plan §2.3 last bullet); tests that the reference examples validate
- [x] P1.W1.5 SQLAlchemy models for the core data model (plan §2.3: `Connector`, `ConnectorRelease`, `BuildRun`, `SyncRun`, `DriftEvent`, `RepairRun`, `Approval`, `EvidenceRecord`, `ModelCall`, `DependencyRegistration`) + Alembic migrations; SQLite now, Postgres-ready (no SQLite-only types); `ConnectorRelease` immutability enforced at the model layer
- [x] P1.W1.6 `JobRunner` interface + DB-backed implementation (`src/autobridge/control/jobs.py`): executes `BuildRun / SyncRun / RepairRun` jobs, retries, persists state across a worker restart (test kills and restarts the worker mid-job)
- [x] P1.W1.7 `EvidenceStore` (SQLite/JSONL, `src/autobridge/evidence/`) with `EvidenceRecord` kinds and `parent_ids[]` chains; `redact.py` that strips every credential-ref value from a fixture prompt (test with planted secrets)
- [x] P1.W1.8 `LLMProvider` Protocol (`src/autobridge/llm/provider.py`), `AnthropicProvider` with frontier and small tiers (model ids in config), `OpenAIProvider` stub behind the same interface, `Budget(max_frontier_tokens, max_small_tokens, max_calls, max_wall)` raising `BudgetExhausted` at the ceiling, `ModelCall` ledger with purpose; a fake provider for tests
- [x] P1.W1.9 Test fakes for the Docker sandbox (`RuntimeTarget` fake) and the coding-agent CLI (fake `claude -p` with usage output) so the control/SSE layer and bench drivers can reach 90% later (plan §9)
- [x] P1.W1.10 `tests/e2e/conftest.py` first cut (compose up, wait on `readyz`, teardown) + `tests/e2e/test_00_stack_up.py`; `docs/architecture.md` first pass

#### W2 — Simulators + verifier / oracle (plan §4 W2, §2.7, §2.8)
- [x] P1.W2.1 `simulators/common`: seeded generator for properties, reservations, folios with late postings across the business date, duplicates / at-least-once delivery, nullable fields; same seed → byte-identical dataset (test)
- [x] P1.W2.2 Fault switchboard applying named mutations + admin API `POST /admin/scenario {id, seed}` on a separate port/network; state reset endpoint for e2e
- [x] P1.W2.3 AtlasPMS: clean OpenAPI 3.1 docs served from `simulators/docs/`, bearer auth, offset pagination, clear statuses; `readyz`
- [x] P1.W2.4 HarborPMS: Markdown narrative docs with one wrong example and one undocumented endpoint, cursor pagination with the repeated-cursor bug, nested folios, opaque status codes, property-local dates, ambiguous `date` field; `readyz`
- [x] P1.W2.5 Scenario catalog `simulators/scenarios/*.yaml` (plan §2.8 list) tagged `dev|heldout` and `fixable|escalate`; test that each scenario applied to the reference spec fails in exactly its intended drift class and no other
- [x] P1.W2.6 Verifier service (`verifier/app.py`): `POST /verify {release_id, spec, scenario_id, mode}` → `{verdict, score, failed_checks, regression}`; oracle regenerating ground truth from the scenario seed; canonical checks (record set by key, money exact as `Decimal`, business-date dates, status mapping, no duplicates, no loss); response-schema test that hidden mode never returns fixture rows
- [x] P1.W2.7 Regression runner (every previously verified scenario re-runs on a candidate release), mutation tests (flip a transform or drop a check → fail), invalid-patch trap set (e.g. `net` → `total_amount` after gross→net) rejected
- [ ] P1.W2.8 HUMAN — `verifier/hidden_cases/dev` and `heldout` split; heldout frozen at the end of W2 with `SHA256SUMS` committed (owner freezes; agents prepare the manifest and the `make bench` sha check)
- [x] P1.W2.9 `tests/e2e/test_01_simulators_and_oracle.py`: applies every scenario via the admin API and checks verifier verdicts against the catalog's expected outcome; `docs/safety-model.md` first pass

#### W3 — Runtime / interpreter (plan §4 W3, §2.4)
- [x] P1.W3.1 `src/autobridge/runtime/interpreter.py` + `http.py`: executes a `ConnectorSpec` operation with retry/backoff from `rate_limit.retry`; `ModelCall` count stays 0 on a reference sync
- [x] P1.W3.2 Auth strategies `auth.py`: bearer, api-key header, basic, oauth2 client-credentials; credentials read by `credential_ref` from the environment, never logged
- [x] P1.W3.3 Pagination `pagination.py`: offset / cursor / page with `stop_when` guards (`null_cursor`, `repeated_cursor`, …); `repeated_cursor` stops the loop and marks `Degraded` with `no_loss` named
- [x] P1.W3.4 Closed transform registry (`runtime/transforms/`: `date_local`, `datetime_utc`, `money`, `enum_map`, `flatten`, `coalesce`, `const`, `concat`, `business_date`), `checks.py` (`unique_key`, `no_loss`, `date_order`), `checkpoint.py`; money `Decimal` end to end; a timestamp crossing the business-date boundary lands on the right local date; unknown enum value → `Blocked`
- [>] P1.W3.5 `SyncRun` outcome `Complete | Degraded | Blocked` + `unmet_criteria[]` + `data_readiness` (freshness, completeness) + fingerprint id written per run (`src/autobridge/engine/sync_run.py`) — in progress: sbx1 since 2026-10-27 04:25
- [ ] P1.W3.6 `RuntimeTarget` (`runtime/targets.py`): `LocalProcess` and `DockerSandbox` (network allowlist, timeout, resource limits), `Edge` reserved; negative network test that `DockerSandbox` cannot reach the verifier or the simulator admin
- [ ] P1.W3.7 Reference specs for Atlas and Harbor under `generated/connectors/`; `tests/e2e/test_02_reference_sync.py`: both sync `Complete` with `ModelCall` count 0; `docs/connector-spec.md`

#### W4 — Drift detection + deterministic repair + releases (plan §4 W4, §2.5)
- [ ] P1.W4.1 Fingerprinting (`drift/fingerprint.py`: keys, types, pagination shape) and `drift/signals.py` from sync runs and the canary
- [ ] P1.W4.2 Drift classifier (`drift/classifier.py`) for the 12 classes of plan §2.5 with evidence ids; tests per class from the scenario catalog
- [ ] P1.W4.3 The 7 deterministic rules in `repair/rules/` (`rename_field`, `offset_to_cursor`, `cursor_to_offset`, `retry_backoff_only`, `enum_extend_requires_evidence`, `nested_path_shift`, `type_coerce_string_number`), each with precondition, patch generator, confidence, verification level, and unit tests for precondition true/false and patch shape; `repair/registry.py`
- [ ] P1.W4.4 `RepairController` + `ladder.py` with L0 knowledge lookup (profile cache keyed by vendor + fingerprint delta) and L1 rules wired; `rate_limit_429` / `transient_503` → no patch, backoff only; `token_expired` → refresh if permitted else escalate; `CAPABILITY_REMOVED` / `AUTH_REVOKED` escalate with no patch attempted
- [ ] P1.W4.5 Releases: `ConnectorRelease` creation from a verified patch, approve / reject / rollback endpoints in the engine layer; rollback restores the prior release and the prior sync output is bit-identical (test)
- [ ] P1.W4.6 Canary monitor (`engine/monitor.py`, `health.canary` from the spec) and `DependencyRegistration`: activating a release emits `release.activated` and a registered consumer with `pause_on_release_change` pauses (demo registers one dummy consumer)
- [ ] P1.W4.7 `tests/e2e/test_03_known_repairs_zero_frontier.py`: `rename_guest_id`, `offset_to_cursor`, `cursor_repeat_token`, `nested_path_shift` → verified new release with `frontier_tokens == 0`, asserted on the ledger per scenario

#### W5 — Hypothesis engine + bootstrap (plan §4 W5, §2.6)
- [ ] P1.W5.1 Docs reader (`discovery/docs_reader.py`): OpenAPI 3.x and Markdown narrative docs → a normalized interface description with provenance (which doc line)
- [ ] P1.W5.2 Classifiers (`discovery/classifiers.py`): auth / pagination / date semantics / status vocabulary from docs + first probes
- [ ] P1.W5.3 `Hypothesis` model and `HypothesisEnumerator` families: date (created/arrival/business/UTC), pagination (offset/cursor/page), amount (gross/net/tax-inclusive), status vocabulary; every hypothesis links to at least one evidence record
- [ ] P1.W5.4 `ProbeSelector` (`discovery/probes.py`): picks the allowed GET probe that best splits surviving hypotheses by expected information gain; budget of probes, requests, wall-clock, tokens per run
- [ ] P1.W5.5 Budgeted L2 / L3 wiring in the ladder: L3 only when the enumerator yields fewer than two hypotheses (asserted); `BudgetExhausted` → L4 escalation bundle, never a silent guess; surviving ambiguity → `Degraded` with open questions
- [ ] P1.W5.6 `discovery/bootstrap.py` + `engine/build_run.py`: docs → draft spec → compile → visible tests → verify; Harbor connector produced from docs alone with no reference-spec access
- [ ] P1.W5.7 `tests/e2e/test_04_bootstrap_unfamiliar_api.py` with the negative test that the workspace has no path to `verifier/` or `simulators/`; `date` ambiguity resolved within the probe budget or reported `Degraded`

#### W6 — Semantic drift + safety (plan §4 W6)
- [ ] P1.W6.1 `SEMANTIC_VALUE_DRIFT` detection via invariants (schema-valid but a money / date / count invariant fails) with the failing invariant named in `unmet_criteria`
- [ ] P1.W6.2 Scenarios `gross_to_net`, `tz_boundary_shift`, `status_vocab_change`, `endpoint_removed`, `token_revoked` wired end to end: `gross_to_net` → `Degraded` with the money invariant named, then a verified repair backed by independent evidence or an escalation; `endpoint_removed` and `token_revoked` escalate with no patch attempted
- [ ] P1.W6.3 Escalation bundle format (evidence bundle + one precise question for a human) and its storage on `RepairRun`
- [ ] P1.W6.4 Regression gate on every candidate release: a release that breaks any previously verified scenario is blocked; the plausible-but-wrong patch is rejected by the verifier
- [ ] P1.W6.5 `tests/e2e/test_05_safety_and_semantic.py`; `docs/safety-model.md` complete

#### W7 — Control API + UI (plan §4 W7, §2.11)
- [ ] P1.W7.1 FastAPI control plane (`src/autobridge/control/app.py`, `routes/`, `schemas.py`): every endpoint of plan §2.11 incl. `GET /metrics/model-usage`, `GET /bench/scorecards`, `POST /demo/inject-fault` (labelled, proxies to the simulator admin); schemathesis contract tests pass
- [ ] P1.W7.2 SSE run events (`control/sse.py`, `GET /runs/{id}/events`) from stored `EvidenceRecord`s; redaction test over a full run's event stream (no credential value appears)
- [ ] P1.W7.3 OpenAPI → `ui/lib/api` typed client via `openapi-typescript`; the client compiles against the live schema in CI
- [ ] P1.W7.4 Next.js 15 scaffold (`ui/`: App Router, TypeScript, Tailwind, SWR polling + SSE), Vitest + Testing Library, `readyz`; screen `/connectors` (health, active release, last sync outcome, open drift, model-usage sparkline)
- [ ] P1.W7.5 Screen `/connectors/new` (docs upload/URL, sandbox base URL, credential env-ref picker, target entities, start build) — fold into `/connectors` if behind
- [ ] P1.W7.6 Screen `/connectors/[id]` (spec viewer YAML, release history, sync runs with Complete/Degraded/Blocked, fingerprints, dependency registrations)
- [ ] P1.W7.7 Screen `/repairs/[id]` (drift evidence, ladder trace with level badges, hypotheses + probes, JSON Patch diff old/new side by side, visible tests, verifier verdict, model usage, Approve / Reject / Rollback) + the labelled Inject Fault demo drawer + model-usage view
- [ ] P1.W7.8 Network test that the UI never calls simulators or the verifier directly; `tests/e2e/test_06_ui_journey.spec.ts` (Playwright) completing the v2 §7.4 user journey

#### W8 — Benchmark (plan §4 W8, §2.10)
- [ ] P1.W8.1 `bench/harness.py`: per scenario × trial runs AutoBridge via the control API and the baseline inside the same `runtime-sandbox` image with the same docs volume, sandbox URL, credentials, visible tests, token ceiling and wall-clock; harness self-test asserts both arms get identical inputs
- [ ] P1.W8.2 Baseline drivers: `claude_code_headless` (headless CLI with usage/token capture, using the W1 fake in tests) and a `codex_cli` adapter stub; fallback documented (route the baseline through `LLMProvider` with an equivalent agent loop, plan §9)
- [ ] P1.W8.3 `one_shot_generator` secondary diagnostic control
- [ ] P1.W8.4 `bench/scoring.py` (verified completion, repair success, false-fix/unsafe rate, escalation quality, elapsed, tool calls, model calls, frontier tokens, estimated cost, zero-frontier solves) via verifier hidden mode; `bench/report.py` → `experiments/scorecard.md` listing wins and losses
- [ ] P1.W8.5 `make bench` idempotent and reproducible from seeds, heldout sha check before the run; `tests/e2e/test_07_benchmark_matched_budget.py` runs one dev scenario through both arms and validates the scorecard schema; `docs/benchmark.md`

#### W9 — E2E suite (continuous deliverable, plan §4 W9)
- [ ] P1.W9.1 `tests/e2e/conftest.py` complete: brings up Compose, waits on `readyz`, resets simulator state per test, tears down; tests independent (random-order run passes); suite runtime budget documented
- [ ] P1.W9.2 `make e2e` runs the whole suite against the Compose stack; CI job running unit (coverage gate) → smoke → e2e from a clean clone

#### W10 — Packaging + demo (plan §4 W10, §6)
- [ ] P1.W10.1 README complete, `docs/` pages complete (`architecture`, `connector-spec`, `safety-model`, `benchmark`, `demo`), `.env.example` complete
- [ ] P1.W10.2 `make demo` driving the six demo steps of plan §6 through the control API; its output is the same evidence the UI renders
- [ ] P1.W10.3 HUMAN — Clean-clone check (`git clone && make up && make test && make e2e && make demo` on a machine with only Docker and `uv`) and the labelled demo recording

### Phase 2 — Stretch (plan §5; entry only after W1–W6 green and the stop rule holds at end of Nov 1)

- [ ] P2.S1.1 LegacyStay third vendor (entry: W6 + W8): heldout-only unseen-generalization vendor (rate limits, misleading examples, nullable chaos); bootstrap + at least one repair verified with no vendor-specific rule added; `tests/e2e/test_s1_legacystay.py`
- [ ] P2.S2.1 Simulated Edge target (entry: W7): `RuntimeTarget.Edge`, toy edge worker on an isolated Docker network running a verified, signed release against a sim POS reachable only there; control plane cannot reach the sim POS (negative test); `tests/e2e/test_s2_edge_target.py`
- [ ] P2.S3.1 `DiscoveryResult` static import (entry: W7): reserved schema + `POST /discovery/import`; connectors list shows `discovered / needs confirmation / authorized / connected / healing` from a static fixture; state transitions only via explicit endpoints; `tests/e2e/test_s3_discovery_import.py`
- [ ] P2.S4.1 MCP Guardian (entry: all W1–W8 + stop rule): `mcp_stretch/` mock `HotelToolsV1` → `V2`, catalog snapshot/diff, compatibility gateway, pinned-consumer workflow replay, comparison against plain tool rediscovery; `tests/e2e/test_s4_mcp_gateway.py`
- [ ] P2.S5.1 OpenAI provider (entry: W8): fill the stub behind `LLMProvider`; one dev scenario reaches an identical ladder outcome on both providers; `tests/e2e/test_s5_provider_parity.py`
- [ ] P2.S6.1 Richer UI (entry: all W1–W8): model-usage charts per run, evidence graph view; Playwright screenshot tests; no new API surface
- [ ] P2.S7.1 CLI (entry: W7): thin `ab` wrapper over the control API for CI; `ab build / sync / repairs approve` drive the same journey as the UI; `tests/e2e/test_s7_cli.py`
