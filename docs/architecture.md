# AutoBridge — Architecture

> Final reference as of P1.W10.1 (plan §4 W10, §2.1, §2.2, §2.3). Earlier
> workstreams extended this page section by section; W10 completed it.

## 1. What AutoBridge does

AutoBridge is a generic **Connect + Heal** engine. It compiles declarative
connectors from vendor docs, executes them on a schedule, detects drift when a
vendor changes, repairs the connector through a deterministic-first ladder, and
verifies every repair behind a separate trust boundary before it ships.

Design principles (plan §1):

1. **Repairs are data, not code** — a connector is a `ConnectorSpec`
   (YAML/JSON) the runtime interprets; a repair is a JSON Patch; transforms
   are a closed set, JSONPath subset only, no generated code.
2. **Three trust boundaries** — `src/autobridge` (engine), `verifier/`,
   `simulators/` are separate packages, containers and Docker networks.
3. **Deterministic first, models last** — ladder L0 → L1 → L2 → L3 → L4;
   every model call goes through `LLMProvider` + `Budget` and lands in
   `ModelCall`; known repair patterns use L0/L1 only (`frontier_tokens == 0`).
4. **Success is not a 200** — every `SyncRun` ends `Complete | Degraded |
   Blocked` with the unmet criterion named.
5. **Money is `Decimal`, dates are property-local business dates** end to end.
6. **Coverage ≥ 90% per Python package**, health contracts on every service,
   e2e scenarios per workstream.

## 2. Services and networks

### 2.1 Service table (plan §2.1)

| Service | Package | Image / app module | Trust zone | Host port | Role |
|---|---|---|---|---|---|
| `control` | `autobridge.control` | `autobridge.control.app:app` | engine | 8101 | Control-plane API (connectors, runs, drift, repairs, SSE) |
| `worker` | `autobridge.engine` | `autobridge.engine.app:app` | engine | 8102 | Executes `BuildRun / SyncRun / RepairRun` jobs |
| `runtime-sandbox` | `autobridge.runtime` | `autobridge.runtime.app:app` | engine (restricted) | 8103 | Interprets `ConnectorSpec`; only service that reaches simulators |
| `verifier` | `verifier` | `verifier.app:app` | verifier | 8104 | Oracle + canonical checks; hidden fixtures at `/fixtures` (ro) |
| `sim-atlas` | `simulators` | `simulators.atlaspms.app:app` | simulator | 8105 | AtlasPMS synthetic vendor (clean OpenAPI) + admin API |
| `sim-harbor` | `simulators` | `simulators.harborpms.app:app` | simulator | 8106 | HarborPMS synthetic vendor (messy docs, quirks) + admin API |
| `sim-legacystay` | `simulators` | `simulators.legacystay.app:app` | simulator | 8111 | LegacyStay synthetic vendor Phase 2 (offset, strict Bearer, throttle, nullable chaos) + admin API |
| `ui` | `ui` | Node stub → Next.js 15 (W7) | user | 8107 | Four screens over the control plane |
| `bench` | `autobridge.bench` | `autobridge.bench.app:app` | engine | 8108 | Matched-budget harness (AutoBridge vs baseline) |

All images are built from `Dockerfile.engine` (engine zone, `APP_MODULE`
switches the app), `verifier/Dockerfile` (context `verifier/` only), and
`simulators/Dockerfile` (context `simulators/` only). Hidden fixtures are
mounted read-only into the verifier container and nowhere else.

### 2.2 Docker networks (plan §2.1)

```
engine-net   — control, worker, runtime-sandbox, ui, bench
vendor-net   — runtime-sandbox ↔ sim-atlas/sim-harbor/sim-legacystay public API (8000)
verifier-net — control ↔ verifier, bench ↔ verifier
admin-net    — control ↔ sim-atlas/sim-harbor/sim-legacystay admin API (8001, W2.2)
```

Isolation is by **attachment only** — no `internal: true`. An internal-only
network silently drops host-port publishing, so the verifier's `/readyz`
would be unreachable from `make smoke` on the host (LEARNINGS #1). Negative
probes asserted in `scripts/smoke.sh` and statically in
`tests/unit/test_autobridge_compose.py`:

- `runtime-sandbox` has no route to `verifier-net` or `admin-net`
- `ui` is on `engine-net` only
- `worker` is on `engine-net` only

Positive probes guard against a blanket outage masquerading as isolation:

- `runtime-sandbox → sim-atlas/sim-harbor/sim-legacystay :8000` must succeed
- `control → verifier :8000` must succeed

### 2.3 Health contracts (plan §3)

Every service exposes:

- `GET /healthz` — process liveness, no dependencies. Returns
  `{"status": "ok"}` with `200` and `application/json`.
- `GET /readyz` — readiness including dependency checks. Returns
  `{"status": "ok"|"degraded", "checks": {"<name>": "ok"|"fail"}}` with
  `200`. The top-level `status` is `ok` only when every check is `ok`.

The stub apps (W1) return `{"default": "ok"}`. Later workstreams wire real
checks: `control` reports DB/verifier/sandbox reachability, simulators
report dataset readiness, etc. `make up` runs `scripts/wait_readyz.sh` which
polls every service's `/readyz` until all are `ok`; `make smoke` asserts
both endpoints plus the network probes above; `tests/e2e/test_00_stack_up.py`
is the e2e acceptance test for the same contract.

## 3. Repository layout (plan §2.2)

```
autobridge/
├── compose.yaml  Dockerfile.engine  .env.example  Makefile  pyproject.toml (uv workspace)
├── docs/            architecture.md  connector-spec.md  safety-model.md  benchmark.md  demo.md
├── schemas/         reservation.schema.json  folio-charge.schema.json
│                    connector-spec.v1.json  runtime-target.json  discovery-result.v0.json (reserved)
├── src/autobridge/
│   ├── spec/        models.py  patch.py  registry.py  entities.py
│   ├── runtime/     interpreter.py  http.py  auth.py  pagination.py  transforms/  checks.py  checkpoint.py  targets.py
│   ├── discovery/   docs_reader.py  classifiers.py  hypotheses.py  probes.py  bootstrap.py
│   ├── drift/       fingerprint.py  classifier.py  signals.py
│   ├── repair/      rules/  registry.py  controller.py  ladder.py
│   ├── llm/         provider.py  budget.py  ledger.py
│   ├── evidence/    store.py  redact.py
│   ├── control/     app.py  routes/  models.py  jobs.py  sse.py  schemas.py
│   ├── engine/      build_run.py  sync_run.py  repair_run.py  monitor.py
│   ├── bench/       harness.py  baseline_coding_agent.py  scoring.py  report.py
│   ├── health.py    config.py
│   └── __init__.py
├── verifier/        pyproject.toml  app.py  oracle.py  canonical_checks.py  hidden_cases/{dev,heldout}
├── simulators/      pyproject.toml  common/  atlaspms/  harborpms/  legacystay/ (Phase 2)  docs/  scenarios/
├── generated/       connectors/<id>/<release>/spec.yaml + build-report.json
├── ui/              Next.js 15, TypeScript, Tailwind; lib/api (openapi-typescript client) — stub in W1
├── experiments/     benchmark_runs/*.jsonl  scorecard.md
├── mcp_stretch/     (Phase 2)
├── tests/           unit/  e2e/ (conftest.py + test_00_stack_up.py in W1)
├── scripts/         wait_readyz.sh  smoke.sh  fake_claude.py
├── mds/             planning + agent bookkeeping (PRD-TRACKING, BUILD-NOTES, progress-*, DIRECTIVES, LEARNINGS, …)
└── alembic/         migrations for the core data model
```

Python is a **uv workspace** with three packages (`autobridge`, `verifier`,
`simulators`) and an import-linter contract that `autobridge` never imports
the other two (`make lint-imports`). Sanctioned exception (DIRECTIVES §2):
the verifier may import the simulators' shared data-generator code
(`simulators/common/`, `simulators/scenarios/`); the engine still may not.
The UI is a separate npm package.

## 4. Core data model (plan §2.3)

Ten tables, all portable (String + CHECK, JSON, `DateTime(timezone=True)`,
no SQLite-only types; proven to compile on Postgres):

- `Connector` — id, name, `vendor_docs_ref`, `base_url`, `credential_ref`
  (env key name, never the value), `target_entities[]`, `runtime_target`,
  `active_release_id`, `health_status`.
- `ConnectorRelease` — **immutable** (model-layer `before_update`/`before_delete`
  events raise `ImmutableReleaseError`); `spec` JSON + `spec_signature` sha256,
  `parent_release_id`, provenance (`build_run_id | repair_run_id`).
- `BuildRun / SyncRun / RepairRun` — job execution records; `SyncRun`
  carries `outcome Complete|Degraded|Blocked + unmet_criteria[]`,
  `data_readiness`, `fingerprint_id`; `RepairRun` stores the ladder trace,
  candidate patch, visible-test results and verifier report.
- `Job` (W1.6) — lease/queue table for the `JobRunner`; `state
  queued|running|succeeded|failed`, `attempts/max_attempts`, `worker_id`,
  `reclaim_orphaned` for crash recovery.
- `EvidenceRecord` — append-only ledger (`kind` is one of 8 plan values,
  `payload` redacted at the append boundary, `parent_ids[]` chain, `run_id`
  is `kind:id`).
- `ModelCall` — per-LLM-call ledger (provider, model, tier, tokens,
  latency, cost, purpose); every model call goes through `BudgetedProvider`.
- `DependencyRegistration` — consumer pause/notify on `release.activated`.
- `DriftEvent / Approval` — drift classification + human approval/rollback.

Migrations live in `alembic/versions/` (`0001_core_data_model`,
`0002_job_queue`). `DATABASE_URL` in `.env` selects SQLite (default) or
Postgres.

## 5. ConnectorSpec v1 (plan §2.4)

A connector is a strict YAML document validated by `src/autobridge/spec/models.py`
and `schemas/connector-spec.v1.json` (the two are pinned equal by test):

- `source.base_url` + `auth { type: bearer|api_key_header|basic|oauth2_client_credentials,
  credential_ref }` + `rate_limit { max_rps, retry { on: [429,503], backoff: exponential } }`
  + `timezone_source`.
- `operations` — keyed by name, each `GET` with `path`, `params`,
  `pagination { strategy: offset|cursor|page, ..., stop_when: [null_cursor, repeated_cursor] }`,
  `checkpoint`.
- `entities` — `Reservation` / `FolioCharge` from an operation, `key`,
  `fields { <canonical>: { path: <JSONPath>, transform: [...] } }`, `checks
  [unique_key, no_loss, date_order]`.
- `capabilities { read: [...], action: [] }` and `health { canary, fingerprint }`.

Transforms are a **closed set** (`date_local`, `datetime_utc`, `money`,
`enum_map`, `flatten`, `coalesce`, `const`, `concat`, `business_date`);
JSONPath is a subset regex; unknown enum values `block` (→ `Blocked`);
money is `Decimal` end to end; dates are property-local business dates.

Patches are RFC 6902 JSON Patches (`src/autobridge/spec/patch.py`:
apply-to-copy + revalidate, diff, reverse via diff-back). Releases are
stored write-once under `generated/connectors/<id>/<release>/` with a
sha256 signature (`src/autobridge/spec/registry.py`).

See `docs/connector-spec.md` (W3.7) for the full reference.

## 6. LLM adapter and budgets (plan §2.9)

`src/autobridge/llm/provider.py` defines the `LLMProvider` Protocol
(`complete(messages, tools?, max_tokens, tier) → Completion`) and the
`Budget(max_frontier_tokens, max_small_tokens, max_calls, max_wall)`
wrapper. `AnthropicProvider` is the W1 implementation (frontier + small
tiers, model ids from `src/autobridge/config.py` env helpers, never literals);
`OpenAIProvider` is a stub until P2.S5.1. `FakeProvider` powers unit tests.
`BudgetedProvider` enforces ceilings and writes `ModelCall` rows; exceeding
a ceiling raises `BudgetExhausted` and the ladder escalates to L4 — never a
silent guess. `src/autobridge/evidence/redact.py` strips credential values
from every payload at the store boundary before it can enter a prompt or an
API response.

## 7. E2E harness (plan §4 W9, P1.W1.10)

`tests/e2e/conftest.py` (complete, P1.W9.1) is the session harness:

- `SERVICE_PORTS` mirrors `compose.yaml` host ports.
- `e2e_stack` (session scope) brings up Compose (`docker compose up -d
  --build --wait`), waits for every `/readyz` to be `ok`, yields, and tears
  down (`docker compose down -v`) unless `E2E_KEEP_STACK=1`. When Docker is
  not available the fixture **skips** the suite so `make test` stays green
  on a laptop. `E2E_SKIP_STACK=1` assumes `make up` already ran.
- `reset_simulator_state` (function scope, autouse) resets both simulators
  via `POST /admin/reset` before and after every test. Tests that run
  against the live stack get a *verified* reset (a failed reset fails the
  test instead of leaking scenario state); fake-transport tests get a
  best-effort reset that never fails.
- `preserve_process_env` (function scope, autouse) snapshots and restores
  `os.environ` around every test, so `*_BASE_URL` / `*_API_KEY` assignments
  in one file cannot leak into another under a random order.
- Tests are independent and random-order safe (`pytest-randomly` shuffles
  every run); no test depends on another's side effects. Trust-boundary
  negative tests pin the stale-allowlist failure mode: guard files that
  enforce the boundary (`builds.py` 422 on reference-spec reads) and the
  W8.5 heldout SHA gate (`check_heldout_sha`, returns None, fixture bytes
  leave only as a hex digest) are allowlisted structurally, everything else
  still fails the suite.

`tests/e2e/test_00_stack_up.py` is the W1 acceptance test: it asserts every
service's `/healthz` and `/readyz` return `200`, `status ok`, valid checks,
`application/json` content type, and that the harness's service list matches
`compose.yaml`. It is the e2e counterpart of `make smoke`.

Run the suite with:

```
make up                # build + wait for readyz
make smoke             # healthz/readyz + network probes (host)
make e2e               # tests/e2e via pytest (needs Docker), random order
make e2e-ui            # Playwright UI journey test_06 (needs `make up` first)
E2E_SKIP_STACK=1 make e2e   # reuse an already-up stack
```

Suite budget (P1.W9.1, enforced by constants in the harness):
`SUITE_RUNTIME_BUDGET_SECONDS = 300` wall-clock on a warm stack excluding
the one-off image build, `WARM_TEST00_BUDGET_SECONDS = 30` for `test_00`
alone. Measured in the sandbox: 66 tests in ~5s warm, cold
`compose up --build --wait` + `test_00` in ~20s. The harness documents this
in its module docstring.

## 8. Verification ladder (plan §8)

```
make test     # unit, ≥90% coverage per package + UI component tests
make up && make smoke   # all services healthz+readyz, negative network checks
make e2e      # tests/e2e/test_00 … test_07 (+ test_s* in Phase 2), random order
make bench    # heldout sha check → matched-budget run → experiments/scorecard.md
make demo     # six demo steps through the control API (W10)
```

CI runs unit (coverage gate) → smoke → e2e → demo from a clean clone
(`.github/workflows/ci.yml`, P1.W9.2 + P1.W10.3): one ordered job — `make test`, then
`make up`, `make smoke`, `make e2e`, then the Playwright UI journey via
`make e2e-ui`, then the six demo steps via `make demo` (live against the
stack, after e2e so fault injection cannot contaminate the suite), with
`make down` unconditional (`if: always()`). The
definition of done for a workstream is: unit ≥ 90% + its e2e file green +
its readyz checks green + its docs page updated (plan §3).

## 9. Control plane + UI (plan §2.11, §4 W7)

The control plane is a FastAPI app (`src/autobridge/control/app.py`) with
32 paths and 29 schemas (OpenAPI at `ui/openapi.json`, typed client in
`ui/lib/api/client.ts` via `openapi-typescript`). Every service has
`/healthz`/`/readyz`; the UI has its own readyz proxy to control.

### 9.1 Endpoints (§2.11)

`POST /connectors`, `GET /connectors`, `GET /connectors/{id}`,
`POST /connectors/{id}/build|sync`, `GET /runs/{id}`, `GET /runs/{id}/events`
(SSE, redacted), `GET /drift`, `POST /repairs/{id}/approve|reject`,
`POST /releases/{id}/rollback`, `GET /operator` (server-configured operator
name from `AUTOBRIDGE_OPERATOR`; approve/reject/rollback always record
`Approval.actor` from it and ignore any client-sent actor; `ReleaseOut`
carries `approved_by`), `POST /demo/inject-fault` (labelled, proxies
to simulator admin on `admin-net`), `GET /metrics/model-usage`,
`GET /bench/scorecards`, plus `healthz`/`readyz`. Schemathesis contract tests
(`tests/unit/test_autobridge_control_contract.py`, P1.W7.9) generate cases
from the live OpenAPI on every run (seed-pinned for reproducible CI): no 5xx
on any generated input, documented responses conform to the schema, and
schema-valid input is accepted. Error responses are documented alongside the
auto-generated validation shape — app-wide `404`/`400`, `409` on dependency
creation, and a `oneOf` string/array `422` on the two routes that raise domain
errors (`POST /connectors`, `POST /demo/inject-fault`); response timestamps
are UTC-aware RFC 3339 (`UtcDatetime`), and `InjectFaultRequest.target`/`seed`
are a `Literal`/`StrictInt` so the schema states exactly what is accepted.

### 9.2 UI screens (Next.js 15, App Router, Tailwind, SWR + SSE)

1. `/connectors` — table: health, active release, last sync outcome, open drift,
   model-usage sparkline.
2. `/connectors/new` — docs upload/URL/paste, sandbox base URL, credential
   env-ref picker (known refs + custom), target entities, create & build two-step.
3. `/connectors/[id]` — spec viewer (YAML), release history, sync runs with
   `Complete|Degraded|Blocked`, fingerprints, dependency registrations.
4. `/repairs/[id]` — drift evidence, ladder trace L0-L4 badges, hypotheses +
   probes, JSON Patch diff old/new side by side, visible tests, verifier
   verdict, model usage, Approve/Reject/Rollback + Inject Fault drawer +
   model-usage view; list at `/repairs`.

Theme (REQ-002, broadcast direction): dark-only on-air tokens in
`ui/app/globals.css` (`--ground/--band/--ink/--ink-2/--rule/--off/--live/--stop`,
radius 0), mapped in `ui/tailwind.config.js`; Archivo (width axis) + Fragment
Mono via `next/font/google`; shell top bar with LIVE, labelled "Demo: inject
fault" button and healthz. Status is square-shape + word, never hue alone.
On `/repairs/[id]` the ladder track advances from the source sync run's live
SSE ladder events (polled record as fallback); the frontier counter ticks only
at L3 and the verdict/action appear only once a patch has passed, all honoring
`prefers-reduced-motion`.

The UI talks to simulators/verifier **only via the control plane**:
`compose.yaml` puts `ui` on `engine-net` alone (no `vendor-net`,
`verifier-net`, `admin-net`); `next.config.mjs` rewrites `/api/*` to
`control:8000` (8101 on the host). `scripts/smoke.sh` negative probes
`ui -/-> sim-atlas:8000, sim-harbor:8000, verifier:8000`; the static
precondition is asserted in `tests/unit/test_autobridge_ui_isolation.py` and
the full journey in `tests/e2e/test_06_ui_journey.spec.ts` (Playwright).
`POST /demo/inject-fault` is the sole bridge to `admin-net` and every request
is labelled `demo: true, labelled: true`.

### 9.3 E2E journey (P1.W7.8, plan §4 W7)

`tests/e2e/test_06_ui_journey.spec.ts` walks the four screens in the browser,
captures every request, and asserts none target
`sim-atlas/sim-harbor/verifier` or ports 8104/8105/8106/8109/8110. When the
Compose stack is up it walks live data; when it is not it intercepts
`/api/*` and `localhost:8101` with mocks so the journey is still green and the
network invariant is still checked. `playwright.config.ts` (root) and
`ui/playwright.config.ts` target that single spec:

```
npx playwright test tests/e2e/test_06_ui_journey.spec.ts
E2E_UI_BASE=http://localhost:3000 npx playwright test
```

`make test` covers the same invariant without a browser via
`tests/unit/test_autobridge_ui_isolation.py` (compose + source + spec
existence checks). `make e2e` runs the pytest suite (`test_00` … `test_05`,
`test_07`, `test_s1`); the Playwright spec is independent — run it with `make e2e-ui`
— and skips gracefully when the UI is not reachable.

## 10. Final state (W10)

All Phase 1 workstreams are complete and documented on their own pages:

- **W2** — `simulators/common` seeded generator, fault switchboard, Atlas/Harbor
  apps with real pagination/auth, scenario catalog (14 YAMLs), verifier oracle +
  regression runner + mutation/invalid-patch traps. See
  [Safety model](safety-model.md) §§2–6.
- **W3** — runtime interpreter (HTTP + retry, auth, pagination, transforms,
  checks, checkpointing), `RuntimeTarget` wiring, reference specs. See
  [ConnectorSpec](connector-spec.md).
- **W4–W6** — drift detection, the 7 deterministic repair rules, ladder L0/L1,
  hypothesis engine, semantic drift + safety. See [Safety model](safety-model.md)
  §§7–11.
- **W7** — control plane + UI (§9 above).
- **W8** — benchmark harness (matched-budget, baseline drivers, scoring via
  verifier hidden mode, `make bench` reproducibility). See
  [Benchmark](benchmark.md).
- **W10** — this docs pass (`connector-spec.md`, `safety-model.md`,
  `benchmark.md`, `demo.md`, README) plus `make demo` driving the six demo
  steps. See [Demo](demo.md).

Trust boundaries, money-as-Decimal, business-date semantics, and the
`/healthz`/`/readyz` contracts were established in W1 and remain load-bearing
for every workstream.
