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
| `sim-atlas` | `simulators` | `simulators.atlaspms.app:app` | simulator | 8105 | AtlasPMS synthetic vendor (method-style REST, x-api-key, page pagination) + admin API |
| `sim-harbor` | `simulators` | `simulators.harborpms.app:app` | simulator | 8106 | HarborPMS synthetic vendor (messy docs, quirks) + admin API |
| `sim-legacystay` | `simulators` | `simulators.legacystay.app:app` | simulator | 8111 | LegacyStay synthetic vendor Phase 2 (offset, strict Bearer, throttle, nullable chaos) + admin API |
| `sim-meridian` | `simulators` | `simulators.meridianpms.app:app` | simulator | 8116 | MeridianPMS synthetic vendor REQ-020 (Mews-style POST/JSON, body auth, Limitation cursor, UTC instants) + admin API |
| `sim-fias-pabx` | `simulators` | `simulators.fias_pabx.app:app` | simulator | 8118 | FIAS PABX simulator REQ-022 sub-step A (on-premise exchange over TCP: dials the host, admin API; edge-net only — control reaches its admin via the sim-fias-pabx-admin sidecar, REQ-034) |
| `mcp-hotel` | `mcp_stretch` | `mcp_stretch.app:app` | engine | 8115 | MCP Guardian Phase 2 (mock HotelTools V1→V2 + compatibility gateway, pinned-consumer replay) |
| `ui` | `ui` | Node stub → Next.js 15 (W7) | user | 8107 | Screens over the control plane (connectors, repairs, knowledge, simulators) |
| `bench` | `bench` | `bench.app:app` | engine | 8108 | Matched-budget harness (AutoBridge vs baseline) |

All images are built from `Dockerfile.engine` (engine zone, `APP_MODULE`
switches the app), `verifier/Dockerfile` (context repo root, but only
`verifier/` + `simulators/` copied in), `simulators/Dockerfile` (context
`simulators/` only), and `ui/Dockerfile` (context `ui/` only). Hidden
fixtures are mounted read-only into the verifier container and nowhere else.

Reproducible builds (AR-2-5): every base image is digest-pinned
(`python:3.12-slim@sha256:…`, `node:20-alpine@sha256:…`), Python images
install from hash-pinned requirements locks (`requirements.engine.lock`,
`verifier/requirements.lock`, `simulators/requirements.lock` — regenerate
with `make lockfiles` after changing any `pyproject.toml`) via
`pip install --require-hashes`, and the UI installs with `npm ci` from the
committed `package-lock.json`. CI fails if the locks drift from `uv.lock`
and rebuilds the verifier image twice, diffing `pip freeze`
(`scripts/check-image-repro.sh`).

Every published port binds loopback only
(`"${AUTOBRIDGE_BIND_HOST:-127.0.0.1}:8101:8000"`, REQ-011), so the stack is
unreachable from the local network by default; set `AUTOBRIDGE_BIND_HOST`
(e.g. to `0.0.0.0`) only on a trusted network. Two exceptions never follow
that override (AR-1-4): the verifier (`127.0.0.1:8104`) and the simulator
admin APIs (`127.0.0.1:8109/8110/8112`) stay literal loopback-only, so a
LAN-exposed stack cannot re-publish the acceptance root or the simulator
mutation surface. Container-to-container traffic
on the compose networks is unchanged.

### 2.2 Docker networks (plan §2.1)

```
engine-net   — control, worker, runtime-sandbox, ui, bench, mcp-hotel
vendor-net   — runtime-sandbox ↔ sim-atlas/sim-harbor/sim-legacystay/sim-meridian public API (8000)
verifier-net — control ↔ verifier, bench ↔ verifier
admin-net    — control ↔ sim-atlas/sim-harbor/sim-legacystay/sim-meridian admin API (8001, W2.2), plus the sim-fias-pabx-admin sidecar (REQ-034: a dumb :8001 pipe to the FIAS admin, aliased sim-fias-pabx on admin-net only)
edge-net     — edge-worker ↔ edge-relay (cloud command queue, :8000) + edge-worker ↔ sim-fias-pabx (REQ-022 B: the agent listens on TCP :5010, the PABX dials out to the host; public docs :8000, admin :8001 loopback-only). The agent is outbound-only: no inbound port from the cloud.
```

Isolation is by **attachment only** — no `internal: true`. An internal-only
network silently drops host-port publishing, so the verifier's `/readyz`
would be unreachable from `make smoke` on the host (LEARNINGS #1). Negative
probes asserted in `scripts/smoke.sh` and statically in
`tests/unit/test_autobridge_compose.py`:

- `runtime-sandbox` has no route to `verifier-net` or `admin-net`
- `ui` is on `engine-net` only
- `worker` is on `engine-net` only
- REQ-022 B: `edge-worker` (the on-premise agent) and `sim-fias-pabx` live on `edge-net` only; `edge-relay` bridges edge-net/engine-net as a dumb authenticated queue; `control`, `runtime-sandbox`, `ui` and the cloud sims have no route to edge-net (REQ-034: control reaches the FIAS admin `:8001` through the `sim-fias-pabx-admin` sidecar on `admin-net`; the `:8000` data plane stays edge-only so `control -/-> sim-fias-pabx:8000` still holds)

Positive probes guard against a blanket outage masquerading as isolation:

- `runtime-sandbox → sim-atlas/sim-harbor/sim-legacystay/sim-meridian :8000` must succeed
- `control → verifier :8000` must succeed

### 2.3 Health contracts (plan §3)

Every service exposes:

- `GET /healthz` — process liveness, no dependencies. Returns
  `{"status": "ok"}` with `200` and `application/json`.
- `GET /readyz` — readiness including dependency checks. Returns
  `{"status": "ok"|"degraded", "checks": {"<name>": "ok"|"fail"}}` with
  `200` when every check passes and `503` when any check fails (AR-2-6),
  so an operator watching only status codes is not misled by a degraded
  service. The top-level `status` is `ok` only when every check is `ok`.
  Liveness probes must use `/healthz`; readiness gates poll `/readyz`.

The stub apps (W1) return `{"default": "ok"}`. Later workstreams wire real
checks: `control` reports DB/verifier reachability, `worker` reports job-store
(DB) reachability, simulators report dataset readiness, etc. `make up` runs `scripts/wait_readyz.sh` which
polls every service's `/readyz` until all are `ok`; `make smoke` asserts
both endpoints' codes *and* bodies plus the network probes above; `tests/e2e/test_00_stack_up.py`
is the e2e acceptance test for the same contract.

## 3. Repository layout (plan §2.2)

```
autobridge/
├── compose.yaml  Dockerfile.engine  .env.example  Makefile  pyproject.toml (uv workspace)
├── docs/            architecture.md  connector-spec.md  safety-model.md  benchmark.md  demo.md
├── schemas/         reservation.schema.json  folio-charge.schema.json
│                    connector-spec.v2.json  runtime-target.json  discovery-result.v0.json (reserved)
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
│   ├── health.py    config.py
│   └── __init__.py
├── bench/           harness.py  ablation.py  faults.py  beacon_run.py  baseline_coding_agent.py  scoring.py  report.py  demo.py (REQ-024 owner check: moved out of src/autobridge; imports the engine like the tests do, never verifier)
├── verifier/        pyproject.toml  app.py  oracle.py  canonical_checks.py  hidden_cases/{dev,heldout}
├── simulators/      pyproject.toml  common/  atlaspms/  harborpms/  legacystay/ (Phase 2)  docs/  scenarios/
├── generated/       connectors/<id>/<release>/spec.yaml + build-report.json
├── ui/              Next.js 15, TypeScript, Tailwind; lib/api (openapi-typescript client) — stub in W1
├── experiments/     benchmark_runs/*.jsonl  scorecard.md
├── mcp_stretch/     (Phase 2 S4: catalog.py, gateway.py, replay.py, app.py — MCP Guardian mock + gateway)
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
  events raise `ImmutableReleaseError`); `spec` JSON + `spec_signature`
  (HMAC over canonical JSON when `EDGE_RELEASE_SECRET` is set, unkeyed sha256
  integrity checksum otherwise — AR-2-4),
  `parent_release_id`, provenance (`build_run_id | repair_run_id`).
- `BuildRun / SyncRun / RepairRun` — job execution records; `SyncRun`
  carries `outcome Complete|Degraded|Blocked + unmet_criteria[]`,
  `data_readiness`, `fingerprint_id`; `RepairRun` stores the ladder trace,
  candidate patch, visible-test results and verifier report. Successful
  patches are cached for L0 lookup keyed by (vendor, fingerprint delta);
  reject/rollback evict the run's candidate patch so a rejected or
  regressed patch is never re-proposed at L0 (AR-2-3).
- `Job` (W1.6) — lease/queue table for the `JobRunner`; `state
  queued|running|succeeded|failed`, `attempts/max_attempts`, `worker_id`,
  `reclaim_orphaned` for crash recovery.
- `EvidenceRecord` — append-only ledger (`kind` is one of 8 plan values,
  `payload` redacted at the append boundary, `parent_ids[]` chain, `run_id`
  is `kind:id`).
- `ModelCall` — per-LLM-call ledger (provider, model, tier, tokens,
  latency, cost, purpose); every model call goes through `BudgetedProvider`.
  `reasoning_tokens` is stored as a sub-count of output tokens, so a
  reasoning model that spends its budget thinking and returns an empty
  message is diagnosed, not mislabelled.
- `DependencyRegistration` — consumer pause/notify on `release.activated`.
- `DriftEvent / Approval` — drift classification + human approval/rollback.

Migrations live in `alembic/versions/` (`0001_core_data_model`,
`0002_job_queue`). `DATABASE_URL` in `.env` selects SQLite (default) or
Postgres.

### 4.1 Wire-observed detector evidence (REQ-027 sub-step 2, plan §2.5)

The fingerprint compares spec-shaped summaries, so a page→cursor flip never
shows as a strategy change there (both sides read the strategy from the
spec). The detector therefore also attaches what the vendor actually served,
derived only from the observed `raw_pages` envelopes and `records` — never
from scenario ids, fault YAML or answer keys:

- pagination contradiction: a transmitted (non-null) cursor leaf with no
  total anywhere contradicts a `page`/`offset` spec; a cursor-family spec
  whose `next_path` reads null on every page while the wire carries a
  non-null cursor leaf names the renamed leaf. Null leaves are normal (last
  page, request echo) and never fire alone.
- schema observation: spec-mapped top-level keys absent from every wire
  record, plus the uncovered wire keys with truncated samples and types.
- items-path shift: the spec's `items_path` extracts nothing while the
  envelope carries candidate arrays (`$.key` or `$.parent.key`, depth ≤ 2).

`classify` maps these to the canonical classes (`PAGINATION_CHANGE`,
`SCHEMA_RENAME` for an unambiguous single↔single rename) and records the
provenance in `classification.details["wire_evidence"]` (signal kind,
operation, source `raw_pages` vs `records`). L1 rules read the evidence from
`ctx.signals` (`repair/rules/wire.py`); a response leaf and a request param
are different names, so `cursor_param` is never taken from the wire.
Ambiguous evidence (several added keys) stays unclaimed: no guessing, the
drift falls back to the previous classification and escalates.

### 4.2 Sync detects and repairs inline (REQ-027 sub-step 3, plan §2.5)

Every worker sync (and `POST /connectors/{id}/sync`, which runs the worker
inline) feeds its summary to `engine/detect_repair.py`: signals from the
observed responses, `classify`, a persisted `DriftEvent` (`open`), then
`RepairController.handle_from_signals` with detector-derived inputs only —
no drift class, delta, hypotheses or yield count from anywhere else. A
`Complete` sync with wire-contradiction signals is still drift (silent
shape change, e.g. records dropping to zero with no invariant failure).
The ladder persists the `RepairRun` (rung, rule id, zero model usage,
ladder trace); a proposed patch becomes a candidate release
(`provenance_repair_run_id` set, verifier-gated when `VERIFIER_URL` is
configured, fail-closed otherwise) awaiting operator approval, after which
the next sync runs the new spec. The L0 key is a spec-previous/wire-current
delta (`pagination_details`/`types_details`, names only — never samples),
so repeats hit L0 while a different drift does not; a `None` delta is never
cached, so nothing is ever keyed content-free. Detection never fails the
sync: its errors are row data on the result, not exceptions.

## 5. ConnectorSpec v1/v2 (plan §2.4)

A connector is a strict YAML document validated by `src/autobridge/spec/models.py`
and `schemas/connector-spec.v2.json` (v1 file kept for reference; the two are
pinned equal by test — v1 documents still validate):

- `source.base_url` + `auth { type: bearer|api_key_header|basic|oauth2_client_credentials,
  credential_ref }` (v2 adds `body_fields {<body path>: <CREDENTIAL_REF>}`) + `rate_limit { max_rps, retry { on: [429,503], backoff: exponential } }`
  + `timezone_source`.
- `operations` — keyed by name, each `GET` with `path`, `params`,
  `pagination { strategy: offset|cursor|page, ..., stop_when: [null_cursor, repeated_cursor] }`,
  `checkpoint` (v2 adds `POST` with a JSON `body` template and
  `body_cursor` pagination with `cursor_path`/`count_path` in, `next_path` out).
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
`OpenAIProvider` is the P2.S5.1 Chat Completions implementation behind the
same `LLMProvider` contract (model ids from env, `system` messages passed
through inline, Anthropic-style tool specs normalized to OpenAI function
shape). `tests/e2e/test_s5_provider_parity.py` proves one dev scenario
reaches an identical ladder outcome on both providers. `FakeProvider` powers unit tests.
`BudgetedProvider` enforces ceilings and writes `ModelCall` rows; exceeding
a ceiling raises `BudgetExhausted` and the ladder escalates to L4 — never a
silent guess. `src/autobridge/evidence/redact.py` strips credential values
from every payload at the store boundary before it can enter a prompt or an
API response. Every engine prompt (system + user template) lives in its own
file under `config/prompts/` (`src/autobridge/llm/prompts.py` loader with
front-matter: id, version, purpose, tier, max_tokens, inputs, answer
format); control and worker load and validate all prompts at boot (a
missing file or bad front-matter fails startup naming the file), and each
model call records the prompt id/version/content hash on its `ModelCall`
row and rung entry — the repair page shows them next to the call. No
prompt text remains in Python; to improve a prompt, edit
`config/prompts/<id>.md` and restart.

## 7. E2E harness (plan §4 W9, P1.W1.10)

`tests/e2e/conftest.py` (complete, P1.W9.1) is the session harness:

- `SERVICE_PORTS` mirrors `compose.yaml` host ports.
- `e2e_stack` (session scope) brings up Compose (`docker compose up -d
  --build --wait`), waits for every `/readyz` to be `ok`, yields, and tears
  down (`docker compose down -v`) unless `E2E_KEEP_STACK=1`. When Docker is
  not available the fixture **skips** the suite so `make test` stays green
  on a laptop — unless `AUTOBRIDGE_E2E_REQUIRE_STACK=1` is set (`make e2e`
  sets it), in which case live tests **fail** instead of skipping so CI
  never passes vacuously (REQ-010). `E2E_SKIP_STACK=1` assumes `make up`
  already ran.
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

Markers (REQ-010): every test in `tests/e2e` is auto-marked at collection —
`live` if it takes the `e2e_stack` fixture (runs against the Compose
stack), `inprocess` otherwise (engine code in-process against fake
transports, green without Docker). `pytest -m live` runs the stack journey;
`pytest -m inprocess` runs the offline journey. The live-stack versions of
the headline claims live next to the tests they mirror:
`test_offset_to_cursor_live_zero_frontier_via_control_api` in
`test_03_known_repairs_zero_frontier.py` (fault injection and reset through
the control API, ladder + oracle local, frontier 0, patched spec syncs
`Complete` against the still-drifted sim) and
`test_semantic_drift_live_gross_to_net_degraded_via_control_api` /
`test_invalid_patch_live_rejected_via_stack_verifier` in
`test_05_safety_and_semantic.py` (semantic drift ends `Degraded` via
escalation; the trapped patch is rejected by the stack verifier).
`POST /demo/inject-fault` and `POST /demo/reset` send the
`X-Admin-Network: admin-net` routing hint plus `X-Admin-Token` from
`SIM_ADMIN_TOKEN` (AR-1-1) — without authorization the fault never
lands (403).

Token matrix (REQ-015): `make e2e` passes both with no tokens and with all
tokens set (`AUTOBRIDGE_CONTROL_TOKEN`, `SIM_ADMIN_TOKEN`,
`EDGE_RELEASE_SECRET`). Live control-API calls share one helper —
`control_headers()` in `tests/e2e/conftest.py` — that forwards
`AUTOBRIDGE_CONTROL_TOKEN` as `Authorization: Bearer` when set; the
`ab` CLI does the same via `auth_headers()` in `src/autobridge/cli.py`.
In-process (fake) tests pin the token themselves with `monkeypatch`
(`delenv` by default) so they pass whatever the shell has.

## 8. Verification ladder (plan §8)

```
make test     # unit, ≥90% coverage per package + UI component tests
make up && make smoke   # all services healthz+readyz, negative network checks
make e2e      # tests/e2e/test_00 … test_07 (+ test_s* in Phase 2), random order
make bench    # heldout sha check → matched-budget run → experiments/scorecard.md
make demo     # eight demo steps through the control API (W10)
```

CI runs unit (coverage gate) → smoke → e2e → demo from a clean clone
(`.github/workflows/ci.yml`, P1.W9.2 + P1.W10.3): one ordered job — `make test`, then
`make up`, `make smoke`, `make e2e`, then the Playwright UI journey via
`make e2e-ui`, then the eight demo steps via `make demo` (live against the
stack, after e2e so fault injection cannot contaminate the suite), with
`make down` unconditional (`if: always()`). The
definition of done for a workstream is: unit ≥ 90% + its e2e file green +
its readyz checks green + its docs page updated (plan §3).

## 9. Control plane + UI (plan §2.11, §4 W7)

The control plane is a FastAPI app (`src/autobridge/control/app.py`) with
53 paths and 62 schemas (OpenAPI at `ui/openapi.json`, typed client in
`ui/lib/api/client.ts` via `openapi-typescript`). Every service has
`/healthz`/`/readyz`; the UI has its own readyz proxy to control.

### 9.1 Endpoints (§2.11)

`POST /connectors`, `GET /connectors`, `GET /connectors/{id}`,
`POST /connectors/{id}/build|sync`, `GET /runs/{id}`, `GET /runs/{id}/events`
(SSE, redacted), `GET /drift`, `POST /repairs/{id}/approve|reject`,
`GET /repairs/{id}/approve-preview` (read-only: what Approve/Reject will do,
derived from the same rows the approve path acts on — candidate release,
active release, pause-on-change dependents, scheduler interval,
learn-on-approve — so the UI text follows the server; REQ-032),
`RepairRunOut.approval` (latest approval row, so the repairs list shows
fix-ready / needs-your-decision / approved / applied / rejected without
extra round trips), `POST /releases/{id}/rollback`, `GET /repairs/{id}/ledger` (rung-by-rung
ledger with linked model calls), `GET /operator` (server-configured operator
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

Optional write token (REQ-012): when `AUTOBRIDGE_CONTROL_TOKEN` is set, every
state-changing route (`POST`/`PUT`/`PATCH`/`DELETE`) requires
`Authorization: Bearer <token>` (constant-time compare, 401 + documented
`401` response otherwise, `HTTPBearer` scheme in the OpenAPI); when unset or
empty the API behaves exactly as before. Read-only `GET`s, `/healthz`,
`/readyz` and the SSE stream stay open. The UI never ships the token to the
browser — `ui/middleware.ts` injects the header server-side on `/api/*`
writes from the UI container's env. The bench harness, `make demo` driver,
`ab` CLI and e2e helpers send it from their own env when set; `compose.yaml`
passes it to `control` and `ui`, `.env.example` documents it. The token is a
credential: never logged, never in responses, and covered by the SSE
redaction (`*_TOKEN` names).

Phase 2 S3 adds the reserved `DiscoveryResult` static import (7 paths,
`discovery_records` table via migration `0004_discovery_records.py`):
`POST /discovery/import` validates every record against the reserved
`DiscoveryResult` contract (closed `Literal` status/entity vocabularies plus
the committed `schemas/discovery-result.v0.json`, parity-pinned by unit
tests; the request path uses the model so it works in the container image,
which ships `src/` without `schemas/`), `GET /discovery` lists the
`discovered / needs_confirmation / authorized / connected / healing` states
from the static fixture
(`tests/e2e/fixtures/discovery_s3_static.json`), and five explicit endpoints
(`request-confirmation`, `authorize`, `connect`, `mark-healing`, `resolve`)
own the only legal transitions — anything else is a 422, never guessed.
Acceptance: `tests/e2e/test_s3_discovery_import.py` (fakes + live).

Phase 2 S4 adds the MCP Guardian (`mcp_stretch/`, engine zone, served as
`mcp-hotel :8115` on `engine-net` only): a mock `HotelToolsV1` → `V2` tool
migration (3 renamed tools + 1 addition), a declarative catalog
snapshot/diff (`GET /catalog/diff`), a compatibility gateway over a pinned
V1→V2 mapping table (`POST /gateway/invoke`, `POST /gateway/validate` —
mappings are data validated by `validate_mapping`, never generated code),
and pinned-consumer workflow replay (`POST /replay/run|compare`). Every
invoke ends `Complete | Degraded | Blocked` with the criterion named
(unknown tool → `Blocked`, never guessed); money is `Decimal` (exact 2dp
strings), dates are property-local business dates, and the mock carries no
credentials. The pinned consumer stays `Complete` through the gateway while
plain exact-name rediscovery goes `Blocked` on the first renamed tool;
unsafe mappings (dropped money param, money routed to non-money) are
rejected with 422. Acceptance: `tests/e2e/test_s4_mcp_gateway.py`
(fakes + live).

REQ-021 item 6 adds what-the-engine-knows endpoints (derived, never
hand-kept): `GET /capabilities` is the vocabulary per version (HTTP methods,
auth types, pagination strategies from the spec-model Literals, minus the
v2-only set; 9 transforms; checks; stop conditions; every L1 rule with what
it fixes; the v1 -> v2 diff; plus an MCP `tools/list`-style agent surface),
and `GET /knowledge` is learned knowledge (one entry per approved repair
group — vendor, drift signature, patch, learning run/rung/tokens/cost,
times reused, tokens saved — plus per-connector learned profiles from
successful builds). Both are cleared by `POST /demo/reset` like everything
else. CLI: `autobridge capabilities|knowledge` (text or `--output json`).
Acceptance: `tests/e2e/test_knowledge_flow.py` (empty -> L1 -> approve ->
entry -> same drift -> L0 hit, reused + 1) plus unit pins.

REQ-030 adds the sync scheduler, server-side connector state, learn-on-approve
and full persistence of every step. `AUTOBRIDGE_SYNC_INTERVAL_S` (default 10 in
local and Compose demo mode, off when unset) ticks every active connector
through the same path as `POST /sync` (detect -> classify -> ladder ->
verifier gate, never approves): a lifespan background task runs one pass per
interval on a worker thread (blocking sync I/O stays off the event loop; a
tick whose predecessor is still running is skipped). A connector is skipped
while a sync/build/repair job for it is queued/running or its latest repair
still awaits an operator decision; per-connector failures are recorded, never
raised. SQLite runs in WAL mode with a busy timeout so background passes and
API writes interleave instead of locking each other out. `GET /connectors`
and `GET /connectors/{id}` report `state` (`working` = last sync `Complete`,
no open drift; `degrading` = drift detected, repair running, verified fix
waiting, or verifying sync after approval; `error` = a waiting repair used a
model or escalated, or the last sync `Degraded`/`Blocked`), plus a one-line
`activity` and `last_sync_at` — the UI renders these, never its own
classification. A fix is learned only once it is approved: proposing caches
nothing (a waiting proposal can never hit L0), `approve_release` writes the
entry after activation recording approval id, actor and release id, and the
Knowledge page shows "learned from approval <id> by <actor>"; reject/rollback
evict (AR-2-3). A `Complete` sync on an approved repair's release retires its
drift events (`repaired`) — that is what returns a connector to `working`.
Persistence additions (migration `0009_req030_persistence`): repair rows carry
`drift_class` + `fingerprint_delta` (wire before/after), the verifier report
id is written back onto the repair row, hypothesis derivations persist as
`kind=hypothesis` evidence owned by the repair run, `ModelCall` rows carry
structured `mode` (`live`/`replay`), `captured_at` and `outcome` (ranked /
proposal / proposal_rejected / no_patch / unparsable / hypothesis), and
`pabx_link_events` holds scheduler link snapshots (`link`) plus restart
reports (`restart`). Labelled demo endpoints persist what the demo proves:
`POST /demo/record-climb` (the instrumented semantic climb with rung trace,
hypotheses, model-call box and verifier verdict, read back by the demo's
assertions; since REQ-039 the break's own repair carries the climb and
record-climb runs only as the no-key/no-recording fallback), `POST /demo/pabx-restart` and `GET /demo/pabx-events` (restart
report and snapshots for the PABX panel; postings counters are the edge
connector's `SyncRun` series). Acceptance:
`tests/unit/test_autobridge_req030_scheduler_state.py` (interval, skips,
busy, state machine, learn/evict, record-climb, pabx) plus `make demo` 8/8
with the scheduler ticking.

### 9.2 UI screens (Next.js 15, App Router, Tailwind, SWR + SSE)
1. `/connectors` — table: health, connector state (`● Working` / `▲ Degrading`
   / `■ Error` with the server's activity line and a ticking last-sync age,
   REQ-032), active release, last sync outcome, open drift,
   model-usage sparkline; plus the `DiscoveryList` section showing statically
   imported vendors and their lifecycle state (P2.S3.1).
2. `/connectors/new` — docs upload/URL/paste, sandbox base URL, credential
   env-ref picker (known refs + custom), target entities, create & build two-step.
3. `/connectors/[id]` — state badge + activity in the masthead (REQ-032),
   spec viewer (YAML), release history, sync runs with
   `Complete|Degraded|Blocked`, fingerprints, dependency registrations;
   edge (PABX) connectors also get a link panel (link state, arriving
   postings count, announced record layout, last restart result).
4. `/repairs/[id]` — the story in one sentence (headline from facts) plus the
   storyline 1 happened → 2 did → 3 recommends (jump links; yellow mark
   on the step that needs the operator), then three chapters and a sticky
   decision rail, all from persisted rows, never recomputed in the browser
   (REQ-032, rearranged REQ-054): "1 What happened" (connector/vendor,
   detecting sync + outcome + unmet criterion, wire before/after from the
   fingerprint delta, ▸ affected records and evidence); "2 What the engine
   did" (rung timeline L0–L4 from the ledger with the rung that held lit
   and unused rungs reading "Not needed", proof tiles — one token-counts
   tile (calls and tokens only, no dollar amounts, REQ-055 item 5),
   verifier verdict, hypotheses/probes with "The rule needed no
   enumeration" when empty — per-call ledger rows with each call's proposal
   and dry-run verdict ("passed dry-run" / "rejected by dry-run", REQ-055
   item 4b) only when calls landed,
   hypothesis funnel stages from the derivation record, probes,
   ▸ model usage across runs, ▸ evidence graph); "3 What the engine
   recommends" (one-line decision, per-operation Now → Proposed table
   grouped by operation with Now read from the active release spec,
   touched sections, raw JSON Patch one click away with copy; rejected
   proposals; never the escalation question). The rail merges Approve
   (release rN), what Approve does (from
   `GET /repairs/{id}/approve-preview`), Reject + what it does, ▸ roll
   back, and "Approving as …"; the rail keeps only the decision (the
   question on one line with its full text in the tooltip, the proposal
   label + dry-run result, Approve / Deny, rollback) with the
   Now → Proposed table and raw JSON living in chapter 3, the "If you
   click Approve / Deny" consequences collapsed behind ▸, and the rail
   itself pinned while scrolling (`position: sticky` with a viewport-capped
   scroll, REQ-055 item 3e); climbing rails show "repairing at L<n>"
   with model/elapsed/token counter and no actions; escalated rails show
   the question + rationale and the tested proposal with Approve: \<label\>
   / Deny (question only + Deny without a proposal); terminal rails show
   the outcome with actions disabled. Below 1100 px the rail moves after
   chapter 3 with a sticky bottom bar; reduced motion draws the storyline
   bars full with no pulse. The demo "inject fault" drawer stays dashed at
   the bottom, clearly labelled. The list
   at `/repairs` shows connector, drift class, rung, rule, model calls
   and status (fix ready / needs your decision / approved / applied /
   rejected, from `RepairRunOut.approval`), newest first. P2.S6.1 keeps two
   read-only views on this page with no new API surface: a per-run model-usage chart
   (`ModelUsageChart`, stacked frontier/small bars from
   `GET /metrics/model-usage` `entries[].by_run`, repair-linked runs
   highlighted) and an evidence graph (`EvidenceGraph`, SVG nodes for drift
   events + evidence records with drift→evidence and evidence→parent edges,
   dangling parents as stub nodes; nodes size to their content with the
   full label in a tooltip, REQ-055 item 3f). REQ-021 item 3 adds the rung ledger
   section (`RungLedger` from `GET /repairs/{id}/ledger`).
5. `/knowledge` — "What AutoBridge knows" (REQ-021 item 6): vocabulary per
   version plus learned knowledge (entries with reuse counts, connector
   profiles); linked from the top bar.
6. `/simulators` — "System status" index (REQ-022 E): one row per registry
   simulator (display name, API style, `/developers` docs link, public
   port), live up/down probed server-side by `GET /demo/simulators`
   through the control plane (the browser never calls a simulator), the
   active fault scenario if any, and the connectors using it (each with its
   live state badge, REQ-032); linked from
   the top bar's "System status" link. Generated from the one registry
   (`config/demo_targets.yaml`), never a hand-kept list.

Theme (REQ-002, broadcast direction): dark-only on-air tokens in
`ui/app/globals.css` (`--ground/--band/--ink/--ink-2/--rule/--off/--live/--stop/--ok`,
radius 0), mapped in `ui/tailwind.config.js`; Archivo (width axis) + Fragment
Mono via `next/font/google`; shell top bar with LIVE, labelled "Demo: inject
fault" button and healthz. Status is square-shape + word, never hue alone.
On `/repairs/[id]` the ladder track advances from the source sync run's live
SSE ladder events (polled record as fallback); the frontier counter ticks only
at L3 and the verdict/action appear only once a patch has passed, all honoring
`prefers-reduced-motion`.

The UI talks to simulators/verifier **only via the control plane**:
`compose.yaml` puts `ui` on `engine-net` alone (no `vendor-net`,
`verifier-net`, `admin-net`); the browser calls same-origin `/api/*` only
(REQ-018) — `next.config.mjs` rewrites `/api/*` to the server-only
`CONTROL_URL` (`http://control:8000`, passed as a compose build arg because
Next.js evaluates `rewrites()` at build time; `http://localhost:8101` for
local dev), and `ui/middleware.ts` injects the
control token server-side so it never ships to the browser. No browser code
may reference `NEXT_PUBLIC_API_URL` or a control hostname
(`ui/lib/api/base.ts` is the single base-URL helper); `scripts/smoke.sh`
negative probes `ui -/-> sim-atlas:8000, sim-harbor:8000, verifier:8000`; the static
precondition is asserted in `tests/unit/test_autobridge_ui_isolation.py` and
the full journey in `tests/e2e/test_06_ui_journey.spec.ts` (Playwright).
`POST /demo/inject-fault` is the sole bridge to `admin-net` and every request
is labelled `demo: true, labelled: true`.

### 9.3 `ab` CLI for CI (P2.S7.1, plan §5 S7)

The thin `ab` wrapper (`src/autobridge/cli.py`, installed as the `ab`
console script) drives the same journey as the UI over the control API —
`ab connectors create`, `ab build <id> [--wait]`, `ab sync <id> [--wait]`,
`ab runs get`, `ab drift list/get`, `ab repairs list/get/approve/reject`,
`ab health` — with `--output text|json` (JSON for CI) and `--control-url`
(flag, else `$AUTOBRIDGE_CONTROL_URL`, else `http://localhost:8101`).
It owns no state machine, applies no patches and imports no engine,
verifier or simulator code; exit codes are `0` success, `1` API/connection
failure, `2` usage. Only `credential_ref` env-key names travel — no flag
accepts a credential value (pinned by
`tests/e2e/test_s7_cli.py::test_s7_cli_never_takes_credential_values`).
`--wait` polls `GET /jobs/{id}` to a terminal state. Acceptance:
`tests/e2e/test_s7_cli.py` (fakes + live).

### 9.4 E2E journey (P1.W7.8, plan §4 W7)
`tests/e2e/test_06_ui_journey.spec.ts` walks the screens in the browser,
captures every request, and asserts none target
`sim-atlas/sim-harbor/verifier` or ports 8104/8105/8106/8109/8110. When the
Compose stack is up it walks live data; when it is not it intercepts
`/api/*` and `localhost:8101` with mocks so the journey is still green and the
network invariant is still checked. `playwright.config.ts` (root) and
`ui/playwright.config.ts` target both UI specs:

```
npx playwright test tests/e2e/test_06_ui_journey.spec.ts
E2E_UI_BASE=http://localhost:3000 npx playwright test
```

P2.S6.1 adds `tests/e2e/test_s6_richer_ui.spec.ts`: same mock-when-down
pattern, asserting the per-run chart renders stacked frontier/small rows and
the evidence graph renders drift/evidence nodes with parent-link edges, plus
screenshots of both views (`test-results/s6/*.png`, asserted written and
non-empty). Run from the repo root as
`E2E_UI_BASE=http://localhost:3000 NODE_PATH=ui/node_modules ui/node_modules/.bin/playwright test --config=playwright.config.ts test_s6_richer_ui.spec.ts`
(the spec lives in `tests/e2e`, outside `ui/` module resolution).

`make test` covers the same invariant without a browser via
`tests/unit/test_autobridge_ui_isolation.py` (compose + source + spec
existence checks). `make e2e` runs the pytest suite (`test_00` … `test_05`,
`test_07`, `test_s1`); the Playwright spec is independent — run it with `make e2e-ui`
— and skips gracefully when the UI is not reachable.

## 10. Final state (W10)

All Phase 1 workstreams are complete and documented on their own pages:

- **W2** — `simulators/common` seeded generator, fault switchboard, Atlas/Harbor
  apps with real pagination/auth, scenario catalog (27 YAMLs: HTTP family +
  the REQ-022 D FIAS on-premise family), verifier oracle +
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
  `benchmark.md`, `demo.md`, README) plus `make demo` driving the eight
  demo steps. See [Demo](demo.md).

Trust boundaries, money-as-Decimal, business-date semantics, and the
`/healthz`/`/readyz` contracts were established in W1 and remain load-bearing
for every workstream.
