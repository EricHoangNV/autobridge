# AutoBridge — Implementation Plan
## Generic Connect + Heal Engine: 10-day hackathon build (Oct 26 – Nov 4, 2026)

Companion to `AutoBridge_Plan_2026-10-26_v2.md`. That document locks the thesis; this one says how to build it.

**Mandate (from v2 §2):** frontier models build the engine, they are not required to run it; prove advantage over a frontier coding agent under matched budgets; make the challenge genuinely hard (incomplete docs, semantic drift, unsafe patches, forced escalation).

**What we optimize for:** a demo that runs from real stored evidence, and an engine whose core (connector compiler/runtime, drift classifier, repair registry, hypothesis engine, verifier, provenance model) is production-worthy and reusable as a subsystem of a larger product. The engine is domain-independent; hospitality appears only as the demo data domain (reservations, folio charges).

**Decisions locked:**
- Provider-agnostic LLM adapter; Anthropic implemented first (frontier tier + small tier), OpenAI stubbed behind the same interface.
- Dashboard: Next.js + TypeScript, four utilitarian screens, typed client generated from the control plane's OpenAPI schema.
- Team: solo founder orchestrating AI coding agents. Workstreams are ordered by dependency, not by calendar.
- Repo: `/Volumes/OWC-Express-1M2/Development/autobridge`, Python package `autobridge`.
- Scope split: **Phase 1 = must-have** (W1–W10), **Phase 2 = stretch** (S1–S7) with entry conditions.

---

## 1. Design principles that drive every choice

1. **Repairs are data, not code.** A connector is a declarative `ConnectorSpec` (YAML/JSON). The runtime *interprets* it. A repair is a JSON Patch against the spec: diffable, reversible, testable, and verifiable without executing untrusted code. This is the single biggest lever for "zero frontier calls on known cases" and for production safety.
2. **Three trust boundaries, physically separated:** engine/agent workspace; verifier (hidden fixtures, oracle); simulators + fault injector (answer key). Separate Python packages, separate containers, separate Docker networks.
3. **Deterministic first, models last.** Repair ladder L0 knowledge → L1 deterministic rules → L2 small model → L3 frontier model → L4 human. Every run carries a `ModelUsageLedger`. Known cases must show L0/L1 only.
4. **Success is not a 200.** Every sync run ends in `Complete | Degraded | Blocked` with the unmet criterion named. Verification checks counts, keys, money, dates, business-date boundaries.
5. **Scalable design, simple implementation.** SQLite + SQLAlchemy (Postgres-ready), DB-backed job queue behind a `JobRunner` interface (Temporal/Celery-ready), `RuntimeTarget` abstraction (`cloud` now, `edge` reserved), `LLMProvider` adapter, OpenAPI-typed UI client.
6. **Evidence everywhere.** Every probe, hypothesis, decision, patch, verification result, and approval is an `EvidenceRecord` with a provenance chain. The dashboard renders stored events, never a narrated story.

---

## 2. Target architecture

### 2.1 Services (Docker Compose)

| Service | Package | Trust zone | Role |
|---|---|---|---|
| `control` | `autobridge.control` (FastAPI) | engine | Control plane API: connectors, runs, drift, repairs, approvals, SSE |
| `worker` | `autobridge.engine` | engine | Executes BuildRun / SyncRun / RepairRun jobs; runs the ladder |
| `runtime-sandbox` | `autobridge.runtime` | engine (restricted net) | Interprets ConnectorSpecs; the only service allowed to reach simulators |
| `verifier` | `verifier/` (own pyproject) | verifier | Hidden fixtures + oracle; HTTP API returns aggregate verdicts only |
| `sim-atlas`, `sim-harbor` | `simulators/` (own pyproject) | simulator | Synthetic vendors + admin fault-injection API (separate port/network) |
| `ui` | `ui/` (Next.js) | user | Four screens over `control` |
| `bench` | `autobridge.bench` | engine | Runs AutoBridge and the coding-agent baseline under matched budgets |
| `mcp-*` (Phase 2) | `mcp_stretch/` | engine | Mock MCP server v1/v2 + compatibility gateway |

Docker networks: `engine-net` (control, worker, runtime-sandbox, ui), `vendor-net` (runtime-sandbox ↔ simulator public API), `verifier-net` (control ↔ verifier), `admin-net` (control ↔ simulator admin API; the UI's "Inject Fault" goes through control). The worker never has a route to the verifier's fixture volume or the simulator admin API.

### 2.2 Repository layout

```
autobridge/
├── README.md  compose.yaml  .env.example  Makefile  pyproject.toml (uv workspace)
├── docs/            architecture.md  connector-spec.md  safety-model.md  benchmark.md  demo.md
├── schemas/         reservation.schema.json  folio-charge.schema.json  connector-spec.v1.json
│                    discovery-result.v0.json (reserved)  runtime-target.json
├── src/autobridge/
│   ├── spec/        models.py (pydantic ConnectorSpec)  patch.py (JSON Patch apply/validate)  registry.py
│   ├── runtime/     interpreter.py  http.py  auth.py  pagination.py  transforms/  checks.py  checkpoint.py
│   │                targets.py (RuntimeTarget: LocalProcess, DockerSandbox; Edge reserved)
│   ├── discovery/   docs_reader.py (OpenAPI + Markdown)  classifiers.py (auth/pagination/date/status)
│   │                hypotheses.py  probes.py  evidence.py  bootstrap.py (docs → draft spec)
│   ├── drift/       fingerprint.py  classifier.py  signals.py
│   ├── repair/      rules/ (one module per rule)  registry.py  controller.py  ladder.py
│   ├── llm/         provider.py (Protocol)  anthropic.py  openai.py (stub)  budget.py  ledger.py
│   ├── evidence/    records.py  store.py (SQLite/JSONL)  redact.py
│   ├── control/     app.py  routes/  models.py (SQLAlchemy)  jobs.py (JobRunner)  sse.py  schemas.py
│   ├── engine/      build_run.py  sync_run.py  repair_run.py  monitor.py
│   └── bench/       harness.py  baseline_coding_agent.py  scoring.py  report.py
├── verifier/        pyproject.toml  app.py  oracle.py  canonical_checks.py  mutation_tests.py
│                    hidden_cases/ (dev/ and heldout/ — heldout frozen at W2, sha256 committed)
├── simulators/      pyproject.toml  common/ (seeded data gen, fault switchboard)  atlaspms/  harborpms/
│                    legacystay/ (Phase 2)  docs/ (published docs served to agents)  scenarios/*.yaml
├── generated/       connectors/<connector_id>/<release_id>/spec.yaml + build-report.json
├── ui/              Next.js 15, TypeScript, Tailwind; lib/api (openapi-typescript client)
├── experiments/     benchmark_runs/*.jsonl  scorecard.md
├── mcp_stretch/     (Phase 2)
└── tests/           unit/  e2e/  (verifier and simulators carry their own unit tests)
```

### 2.3 Core data model (SQLAlchemy; SQLite now, Postgres later)

- `Connector` — id, name, vendor_docs_ref, base_url, credential_ref (env key name, never the value), target_entities[], runtime_target, active_release_id, health_status (`healthy|drift|investigating|review_required|blocked`).
- `ConnectorRelease` — immutable: id, connector_id, version, spec (JSON), parent_release_id, provenance (build_run_id | repair_run_id), verification_report_id, created_at, signature (sha256 of spec; real signing later).
- `BuildRun` — state, inputs (docs ref, targets), ladder trace, model_usage, evidence_ids[], draft_spec, result.
- `SyncRun` — release_id, started/finished, records observed/emitted per entity, checkpoint, invariant results, outcome `Complete|Degraded|Blocked` + unmet_criteria[], data_readiness (freshness, completeness), fingerprint_id.
- `DriftEvent` — class (see §2.5), severity, evidence_ids[], source_sync_run_id, status (`open|investigating|repaired|escalated|dismissed`).
- `RepairRun` — drift_event_ids[], ladder_level_reached, candidate_patch (JSON Patch), rule_id | hypothesis_ids, visible_test_results, verifier_report_id, decision (`propose|escalate|reject`), model_usage.
- `Approval` — repair_run_id, actor, decision, from_release → to_release, rollback_of (nullable).
- `EvidenceRecord` — id, kind (`probe|observation|hypothesis|decision|patch|verification|approval|model_call`), payload (redacted), parent_ids[], run_id, created_at.
- `ModelCall` — run_id, provider, model, tier (`small|frontier`), input/output tokens, latency, cost_estimate, purpose.
- `DependencyRegistration` (reusability hook) — consumer_id, connector_id, entity, policy (`pause_on_release_change|notify`). Release activation emits `release.activated`; registered consumers receive a pause signal. Demo registers one dummy consumer.
- `DiscoveryResult` (reserved; Phase 2 static import only) — schema from v2 §12A.

### 2.4 ConnectorSpec v1 (the product)

```yaml
spec_version: 1
connector: { id: harborpms, vendor: HarborPMS, runtime_target: cloud }
source:
  base_url: ${HARBOR_BASE_URL}
  auth: { type: bearer, credential_ref: HARBOR_API_KEY }       # also: api_key_header, basic, oauth2_client_credentials
  rate_limit: { max_rps: 5, retry: { on: [429, 503], backoff: exponential, max_attempts: 5 } }
  timezone_source: { kind: static, value: America/New_York }   # or: { kind: field, path: $.property.tz }
operations:
  list_reservations:
    method: GET
    path: /v2/bookings
    params: { updated_since: "{{checkpoint.reservations}}" }
    pagination: { strategy: cursor, cursor_param: cursor, next_path: $.meta.next_cursor, items_path: $.data, stop_when: [null_cursor, repeated_cursor] }
    checkpoint: { field: $.updated_at, mode: max }
entities:
  Reservation:
    from: list_reservations
    key: $.id
    fields:
      reservation_id: { path: $.id }
      arrival_date:   { path: $.stay.from, transform: [date_local, { tz: source }] }
      status:         { path: $.state, transform: [enum_map, { map: { CI: checked_in, CO: checked_out, RES: confirmed }, on_unknown: block }] }
      total_amount:   { path: $.totals.gross, transform: [money, { scale: 2 }] }
      currency:       { path: $.totals.currency }
    checks:
      - { kind: unique_key }
      - { kind: no_loss, compare_to: $.meta.total_count }
      - { kind: date_order, before: arrival_date, after: departure_date }
capabilities:
  read: [Reservation, FolioCharge]
  action: []                     # separately permissioned; empty in the demo
health:
  canary: { every: 60s, operation: list_reservations, max_pages: 1 }
  fingerprint: { keys: true, types: true, pagination_shape: true }
```

Rules: transforms are a closed, registered set (`date_local`, `datetime_utc`, `money`, `enum_map`, `flatten`, `coalesce`, `const`, `concat`, `business_date`); JSONPath subset only; no expressions, no code. Unknown enum value → `Blocked`, never guessed. Every spec validates against `connector-spec.v1.json`.

### 2.5 Repair ladder and escalation (engine core)

```
DriftEvent ──► L0 KnowledgeLookup   profile cache keyed by vendor + fingerprint delta
            ──► L1 RuleRegistry      match(evidence) → candidate patch(es) + required verification level
            ──► L2 SmallModel        optional: rank/complete candidates, propose hypotheses     [budgeted]
            ──► L3 FrontierModel     hypothesis generation for UNKNOWN / SEMANTIC classes       [budgeted]
            ──► L4 Escalate          evidence bundle + precise question for a human
Each level emits EvidenceRecords. The ladder stops at the first level producing a patch that passes
visible tests; the verifier decides acceptance. A patch rejected by the verifier triggers at most one
bounded retry at the next level, then escalation.
```

Drift classes: `AUTH_EXPIRED`, `AUTH_REVOKED`, `RATE_LIMIT`, `TRANSIENT`, `SCHEMA_RENAME`, `SCHEMA_REMOVED`, `SCHEMA_TYPE_CHANGE`, `PAGINATION_CHANGE`, `ENUM_NEW_VALUE`, `SEMANTIC_VALUE_DRIFT` (schema-valid, invariant fails), `CAPABILITY_REMOVED`, `UNKNOWN`.

Phase 1 deterministic rules (each a tested module with precondition, patch generator, confidence, verification level): `rename_field` (fuzzy key match + value-shape match), `offset_to_cursor`, `cursor_to_offset`, `retry_backoff_only` (no patch), `enum_extend_requires_evidence` (produces a hypothesis, not a patch), `nested_path_shift`, `type_coerce_string_number`. Explicitly non-repairable (escalate): `CAPABILITY_REMOVED`, `AUTH_REVOKED`, `SEMANTIC_VALUE_DRIFT` without independent evidence.

### 2.6 Hypothesis engine (discovery + semantic drift)

- `Hypothesis(id, claim, candidate_mapping_fragment, prior, evidence_for[], evidence_against[])`.
- `HypothesisEnumerator`: deterministic generators for known ambiguity families (date semantics: created/arrival/business/UTC; pagination: offset/cursor/page; amount semantics: gross/net/tax-inclusive; status vocabulary). L3 adds hypotheses only when the enumerator yields fewer than two.
- `ProbeSelector`: chooses the allowed GET probe (with params) that best splits surviving hypotheses, using simple expected information gain over observable distinguishing features (e.g. fetch a record near a timezone boundary and compare `date` with `created_at`).
- Budget: max probes, max requests, max wall-clock, max tokens per run. Surviving ambiguity → `Degraded` with open questions, never a silent choice.

### 2.7 Verifier (separate trust boundary)

- HTTP API: `POST /verify {release_id, spec, scenario_id, mode: visible|hidden}` → `{verdict, score, failed_checks: [bounded names + counts], regression: pass|fail}`. Never returns fixture rows.
- Oracle: regenerates ground truth from the scenario seed via the simulator's data generator (shared code, hidden seeds); compares canonical output: record set equality by key, money exact (`Decimal`), dates by property-local business date, status mapping, no duplicates, no loss.
- Regression suite: every previously verified scenario for the connector re-runs on each candidate release.
- Invalid-patch challenge: a seeded "plausible but wrong" patch set (e.g. map `net` to `total_amount` after gross→net drift) must be rejected.
- Mutation tests on the spec: flip a transform or drop a check → verifier must fail.
- Dev/held-out split: `hidden_cases/dev` for tuning; `hidden_cases/heldout` frozen at W2 (sha256 committed), opened only for the final benchmark run.

### 2.8 Simulators and scenarios

- `simulators/common`: seeded generator for properties, reservations, folios with realistic edge cases (late postings across the business date, duplicates / at-least-once delivery, nullable fields); fault switchboard applying named mutations; admin API `POST /admin/scenario {id, seed}`.
- **AtlasPMS**: clean OpenAPI 3.1, bearer, offset pagination, clear statuses. Purpose: first zero-inference compiled connector.
- **HarborPMS**: Markdown narrative docs with one wrong example and one undocumented endpoint, cursor pagination with a repeated-cursor bug, nested folios, opaque status codes, property-local dates, `date` field ambiguity. Purpose: discovery + repair showcase.
- **LegacyStay** (Phase 2): rate limits, misleading examples, nullable chaos. Purpose: unseen generalization.
- Scenario catalog (`scenarios/*.yaml`): `baseline`, `rename_guest_id`, `offset_to_cursor`, `cursor_repeat_token`, `rate_limit_429`, `transient_503`, `token_expired`, `token_revoked`, `status_vocab_change`, `gross_to_net`, `tz_boundary_shift`, `duplicate_late_records`, `endpoint_removed`, `invalid_patch_trap`. Each tagged `dev|heldout` and `fixable|escalate`.

### 2.9 LLM adapter and budgets

- `LLMProvider` Protocol: `complete(messages, tools?, max_tokens, tier) -> Completion(text, tool_calls, usage)`. `AnthropicProvider` implemented (frontier tier and small tier; model ids in config, not code). `OpenAIProvider` stubbed behind the same interface.
- `Budget(max_frontier_tokens, max_small_tokens, max_calls, max_wall)` enforced by a wrapper that raises `BudgetExhausted` → ladder escalates to L4.
- All calls logged to `ModelCall` with purpose. Prompts carry only redacted docs/responses; credentials never enter prompts (`evidence/redact.py`).
- Docs and API responses are injected as untrusted data blocks; tool permissions are enforced in code, not in the prompt.

### 2.10 Benchmark harness

- `bench/harness.py`: for each scenario × trial, run (a) AutoBridge via the control API and (b) the coding-agent baseline inside the same `runtime-sandbox` image with the same docs volume, sandbox URL, credentials, visible tests, token ceiling and wall-clock. Baseline driver is pluggable: `claude_code_headless` first (headless CLI with usage capture); `codex_cli` adapter stub. Secondary diagnostic control: `one_shot_generator`.
- Scoring via verifier hidden mode. Metrics: verified completion, repair success, false-fix/unsafe rate, escalation quality, elapsed, tool calls, model calls, frontier tokens, estimated cost, zero-frontier solves. Output: `experiments/benchmark_runs/*.jsonl` + `scorecard.md` from `report.py`. Wins and losses both reported.

### 2.11 Control plane API and UI

Endpoints: `POST /connectors`, `GET /connectors`, `GET /connectors/{id}`, `POST /connectors/{id}/build`, `POST /connectors/{id}/sync`, `GET /runs/{id}`, `GET /runs/{id}/events` (SSE), `GET /drift`, `POST /repairs/{id}/approve|reject`, `POST /releases/{id}/rollback`, `POST /demo/inject-fault` (labelled; proxies to simulator admin), `GET /metrics/model-usage`, `GET /bench/scorecards`, plus `GET /healthz` and `GET /readyz` on every service. OpenAPI → `ui/lib/api` via `openapi-typescript`.

UI (Next.js App Router, TypeScript, Tailwind, SWR polling + SSE on run pages):
1. `/connectors` — table: health, active release, last sync outcome, open drift, model-usage sparkline.
2. `/connectors/new` — docs upload/URL, sandbox base URL, credential env-ref picker, target entities, start build.
3. `/connectors/[id]` — spec viewer (YAML), release history, sync runs with Complete/Degraded/Blocked, fingerprints, dependency registrations.
4. `/repairs/[id]` — showcase: drift evidence, ladder trace with level badges, hypotheses + probes, JSON Patch diff (old/new spec side by side), visible tests, verifier verdict, model usage, Approve / Reject / Rollback. "Inject Fault" lives in a clearly labelled demo drawer.

UI is capped at roughly 15% of effort. If behind, fold `/connectors/new` into a form on `/connectors` and merge detail + repair.

---

## 3. Quality bar (applies to every workstream)

- **Unit test coverage above 90%** per Python package (`autobridge`, `verifier`, `simulators`), enforced by `pytest --cov --cov-fail-under=90` in `make test` and CI. UI: component tests for the four screens (Vitest + Testing Library); the typed client must compile against the live OpenAPI schema.
- **Every service exposes a health contract:** `GET /healthz` (process up) and `GET /readyz` (dependencies reachable: DB, verifier, simulators, sandbox). `make up` waits on `readyz` for all services; `make smoke` asserts them plus the negative network checks.
- **The e2e suite is a deliverable, built as part of development** (workstream W9). Each workstream ships its own e2e scenario script into `tests/e2e/` as its acceptance test. `make e2e` runs the whole suite against the Compose stack. The demo script is a subset of the e2e suite.
- **Definition of done for a workstream** = unit tests ≥ 90% + its e2e scenario green + its `readyz` checks green + its docs page updated.

---

## 4. Phase 1 — Must-have

Exit gates (v2 §5): (a) a working compiled declarative connector; (b) zero frontier calls for known repair patterns; (c) independent verification of at least one genuinely ambiguous novel case; (d) rejection of at least one unsafe patch; (e) a reproducible matched-budget head-to-head benchmark.

Dependency order: W1 → W2 → W3 → W4 → W5 → W6. W7 after W3. W8 after W6. W9 grows with every workstream. W10 last.

### W1 Foundations
- **Deliverables:** uv workspace + `pyproject.toml`; `compose.yaml` with all services, networks, `healthz/readyz`; `ConnectorSpec` v1 pydantic models + `connector-spec.v1.json`; canonical entity JSON schemas; SQLAlchemy models + Alembic migrations; DB-backed `JobRunner` behind an interface; `EvidenceStore`; `LLMProvider` Protocol + `AnthropicProvider` + `Budget` + `ModelCall` ledger; `redact.py`; `Makefile` targets `up / down / test / smoke / e2e / bench / demo`; test fakes for the sandbox and the coding-agent CLI (needed to hit coverage later).
- **Passing criteria:** spec round-trips YAML → model → JSON-schema validation; invalid specs are rejected with path-level errors; `Budget` raises `BudgetExhausted` at the ceiling; redaction strips every credential-ref value from a fixture prompt; `JobRunner` executes, retries, and persists state across a worker restart.
- **Service confirmation:** `make up` → all `readyz` green; `make smoke` passes; `tests/e2e/test_00_stack_up.py`.

### W2 Simulators + verifier (oracle)
- **Deliverables:** `simulators/common` seeded generator + fault switchboard + admin API; AtlasPMS (OpenAPI 3.1, bearer, offset); HarborPMS (Markdown docs with one wrong example and one undocumented endpoint, cursor pagination with a repeated-token bug, nested folios, opaque statuses, property-local dates); scenario catalog tagged `dev|heldout` and `fixable|escalate`; verifier service with oracle, canonical checks, regression runner, mutation tests, invalid-patch trap; heldout directory sha256 committed.
- **Passing criteria:** same seed → byte-identical dataset; each scenario applied to the reference spec fails in exactly the intended drift class and no other; verifier hidden mode never returns fixture rows (response-schema test); mutating any transform or check in a passing spec makes the verifier fail; the invalid-patch set is rejected.
- **Service confirmation:** `sim-*` and `verifier` `readyz`; `tests/e2e/test_01_simulators_and_oracle.py` applies every scenario via the admin API and checks verifier verdicts against the catalog's expected outcome.

### W3 Runtime / interpreter
- **Deliverables:** `interpreter.py`; HTTP client with retry/backoff; auth strategies (bearer, api-key header, basic, oauth2 client-credentials); pagination (offset / cursor / page) with `stop_when` guards; closed transform registry; checks; checkpointing; `SyncRun` outcome `Complete|Degraded|Blocked` + `data_readiness`; `RuntimeTarget.LocalProcess` and `RuntimeTarget.DockerSandbox` (network allowlist, timeout, resource limits).
- **Passing criteria:** Atlas and Harbor reference specs sync `Complete` with `ModelCall` count 0; `repeated_cursor` stops the loop and marks `Degraded` with `no_loss` named; unknown enum value → `Blocked`; money is `Decimal` end to end; property-local date transform handles a timestamp crossing the business-date boundary; `DockerSandbox` cannot reach the verifier or simulator admin (negative network test).
- **Service confirmation:** `runtime-sandbox` `readyz`; `tests/e2e/test_02_reference_sync.py`.

### W4 Drift detection + deterministic repair + releases
- **Deliverables:** fingerprinting; drift classifier (12 classes); the 7 repair rules; `RepairController` + ladder L0/L1 wiring; `ConnectorRelease` immutability; approve / reject / rollback; canary monitor; `DependencyRegistration` pause signal.
- **Passing criteria:** `rename_guest_id`, `offset_to_cursor`, `cursor_repeat_token`, `nested_path_shift` → verified new release with `frontier_tokens == 0`; `rate_limit_429` / `transient_503` → no patch, backoff only; `token_expired` → refresh if permitted, else escalate; rollback restores the prior release and the prior sync output is bit-identical; activating a release emits `release.activated` and the registered consumer pauses; every rule has unit tests for precondition true/false and patch shape.
- **Service confirmation:** `tests/e2e/test_03_known_repairs_zero_frontier.py` asserts the ledger per scenario.

### W5 Hypothesis engine + bootstrap
- **Deliverables:** docs reader (OpenAPI + Markdown); auth/pagination/date/status classifiers; `HypothesisEnumerator` families (date, pagination, amount, status); `ProbeSelector`; evidence ledger; budgeted L2/L3; `bootstrap.py`: docs → draft spec → compile → visible tests → verify.
- **Passing criteria:** Harbor connector produced from docs alone with no reference-spec access (negative test: the workspace has no path to `verifier/` or `simulators/`); `date` ambiguity resolved within the probe budget or reported `Degraded` with open questions; every hypothesis links to at least one evidence record; L3 is invoked only when the enumerator yields fewer than two hypotheses (asserted); budget exhaustion → L4 escalation bundle, never a silent guess.
- **Service confirmation:** `tests/e2e/test_04_bootstrap_unfamiliar_api.py`.

### W6 Semantic drift + safety
- **Deliverables:** `SEMANTIC_VALUE_DRIFT` detection via invariants; scenarios `gross_to_net`, `tz_boundary_shift`, `status_vocab_change`, `endpoint_removed`, `token_revoked`; escalation bundle format; regression gate on every candidate release.
- **Passing criteria:** `gross_to_net` → sync `Degraded` with the money invariant named, then either a verified repair backed by independent evidence or an escalation with an evidence bundle; the plausible-but-wrong patch is rejected by the verifier; `endpoint_removed` and `token_revoked` escalate with no patch attempted; a release that breaks any previously verified scenario is blocked by the regression gate.
- **Service confirmation:** `tests/e2e/test_05_safety_and_semantic.py`.

### W7 Control API + UI
- **Deliverables:** FastAPI endpoints (§2.11); SSE run events; OpenAPI → TypeScript client; four Next.js screens; labelled Inject Fault drawer; model-usage view.
- **Passing criteria:** OpenAPI contract tests (schemathesis) pass; the UI never calls simulators or the verifier directly (network test); credentials never appear in any API response or event payload (redaction test over a full run's event stream); the v2 §7.4 user journey completes in the browser under Playwright.
- **Service confirmation:** `control` and `ui` `readyz`; `tests/e2e/test_06_ui_journey.spec.ts` (Playwright).

### W8 Benchmark
- **Deliverables:** `bench/harness.py`; `claude_code_headless` baseline driver with token/usage capture; `one_shot_generator` secondary control; `scoring.py`; `report.py` → `experiments/scorecard.md`.
- **Passing criteria:** both arms receive identical docs volume, sandbox URL, credentials, visible tests, token ceiling and wall-clock (asserted by a harness self-test); heldout sha matches the W2 commit before the run; the scorecard reports every required metric and lists losses; `make bench` is idempotent and reproducible from seeds.
- **Service confirmation:** `tests/e2e/test_07_benchmark_matched_budget.py` runs one dev scenario through both arms and validates the scorecard schema.

### W9 E2E suite (continuous deliverable)
- **Deliverables:** `tests/e2e/` with the scenario scripts above plus a `conftest.py` that brings up Compose, waits on `readyz`, resets simulator state per test, and tears down; `make e2e`; CI job running unit (coverage gate) → smoke → e2e.
- **Passing criteria:** full suite green from a clean clone; tests are independent (random-order run passes); suite runtime budget documented.

### W10 Packaging + demo
- **Deliverables:** README; `docs/`; `.env.example`; `make demo` driving the six demo steps via the API; labelled demo recording.
- **Passing criteria:** `git clone && make up && make test && make e2e && make demo` succeeds on a clean machine with only Docker and `uv` installed.

---

## 5. Phase 2 — Stretch

Entry only after W1–W6 are green and the v2 stop rule holds (REST core, monitoring, and one verified autonomous repair stable by end of Nov 1). Items are ordered by value. Each has an entry condition, its own e2e scenario, the same coverage bar, and is isolated so it cannot destabilize Phase 1.

| # | Stretch item | Entry | Scope | Passing criteria / e2e |
|---|---|---|---|---|
| S1 | LegacyStay third vendor | W6 + W8 | Unseen generalization vendor, heldout only (rate limits, misleading examples, nullable chaos) | Bootstrap + at least one repair verified with no vendor-specific rule added; `test_s1_legacystay.py` |
| S2 | Simulated Edge target | W7 | `RuntimeTarget.Edge`: toy edge worker on an isolated Docker network runs a verified release against a sim POS reachable only there; verify centrally, execute locally | Control plane cannot reach the sim POS (negative test); edge worker executes only signed releases; `test_s2_edge_target.py` |
| S3 | `DiscoveryResult` static import | W7 | Reserved schema + `POST /discovery/import`; connectors list shows `discovered / needs confirmation / authorized / connected / healing` from a static fixture | Schema validation; state transitions only via explicit endpoints; `test_s3_discovery_import.py` |
| S4 | MCP Guardian | all W1–W8 + stop rule | Mock `HotelToolsV1` → `V2`, catalog snapshot/diff, compatibility gateway, pinned-consumer workflow replay, comparison against plain tool rediscovery | Pinned consumer keeps working through the gateway; unsafe mapping rejected by replay tests; `test_s4_mcp_gateway.py` |
| S5 | OpenAI provider | W8 | Fill the stub behind `LLMProvider` | One dev scenario reaches an identical ladder outcome on both providers; `test_s5_provider_parity.py` |
| S6 | Richer UI | all W1–W8 | Model-usage charts per run, evidence graph view | Playwright screenshot tests; no new API surface |
| S7 | CLI | W7 | Thin `ab` wrapper over the control API for CI | `ab build / sync / repairs approve` drive the same journey as the UI; `test_s7_cli.py` |

Slip order (v2 §8): oracle/protocol → runtime → deterministic repair → hypotheses/escalation → benchmark → UI → Phase 2. Hard dates: Phase 2 stop-rule checkpoint end of Nov 1; feature freeze Nov 2; heldout benchmark run Nov 3; submission Nov 4.

---

## 6. Demo script mapping (3–4 minutes, real traces)

1. **Reusable artifact:** open the Atlas release spec, run a sync, show `ModelUsageLedger = 0`.
2. **Unfamiliar API:** start a Harbor build from Markdown docs; show hypotheses, probes, evidence, the resulting spec, and any L3 call with its token count.
3. **Zero-token recovery:** inject `offset_to_cursor`; drift → L1 rule → patch diff → verifier pass → approve; ledger shows 0 frontier tokens.
4. **Hard semantic failure:** inject `gross_to_net`; sync shows `Degraded` with the failing money invariant; ladder reaches L3 or escalates; the invalid patch is rejected by the verifier; approve the correct patch or show the escalation bundle.
5. **Head-to-head:** open `/bench/scorecards`; read the numbers as they are.
6. **Reproducibility:** seed, release ids, `make demo`.

---

## 7. Reusability hooks for a larger product

- A `SourceAdapter` is any `ConnectorRelease` executed by a `RuntimeTarget`. Downstream consumers depend on releases through `DependencyRegistration` and receive pause/notify on change, so a connector repair can never silently feed changed data into a dependent workflow.
- `SyncRun.outcome` (`Complete|Degraded|Blocked`) plus `data_readiness` lets consumers reason about freshness and completeness instead of HTTP status.
- `EvidenceRecord` chains (`probe → observation → hypothesis → decision → patch → verification → approval`) are domain-neutral and importable by any higher-level workflow or verification engine.
- `capabilities.read` / `capabilities.action` keeps write permissions a separate, later approval.
- `RuntimeTarget`, `DeploymentManifest` (allowlist, secret refs), and `HealthPolicy` are first-class in the spec, so cloud/edge placement is configuration.
- `DiscoveryResult` schema is reserved; an `InterfaceProfile` can key the L0 knowledge cache later.
- `LLMProvider` + `Budget` + `ModelCall` ledger are reusable cost governance for any agentic component.

---

## 8. Verification ladder (how anyone confirms the system works)

Run in order; each layer assumes the previous one is green.

1. `make test` — unit tests for `autobridge`, `verifier`, `simulators`, and the UI; fails if any package is under 90% coverage.
2. `make up && make smoke` — Compose stack boots; every service `healthz` + `readyz` green; negative network checks (sandbox ↛ verifier fixtures, sandbox ↛ simulator admin, ui ↛ simulators).
3. `make e2e` — `tests/e2e/test_00` … `test_07` plus any Phase 2 `test_s*`; simulator state reset between tests; passes in random order.
4. `make bench` — heldout sha check, matched-budget run, `experiments/scorecard.md` produced.
5. `make demo` — six demo steps driven through the control API; output is the same evidence the UI renders.
6. Clean-clone check — steps 1–5 on a fresh machine with only Docker and `uv` installed.

---

## 9. Open risks

- Headless coding-agent usage/token capture may require parsing CLI output; the fallback is routing the baseline through the same `LLMProvider` with an equivalent agent loop, documented as such.
- Hypothesis probe selection can overfit Harbor; keep enumerators generic and test on LegacyStay if S1 lands.
- 90% coverage is cheap on the interpreter and rules; on the FastAPI/SSE layer and bench drivers it depends on the sandbox and CLI fakes built in W1 and W8. Do not defer them.
- Solo bandwidth: W7 screens 1–3 are the first thing to simplify if the engine workstreams slip.
