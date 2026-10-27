# Progress

## Current State

P1.W7.5 Screen /connectors/new done (2026-10-27): NewConnectorForm with docs upload/URL/paste, sandbox base URL, credential env-ref picker (known refs + custom), target entities, create+build two-step via control plane, 12 component tests + page test, next build 8 routes.

P1.W7.4 Next.js 15 scaffold + /connectors screen done (2026-10-27): Next.js 15 App Router (TS, Tailwind, SWR polling + SSE), Vitest + Testing Library, readyz proxy to control, /connectors table with health/active release/last sync outcome/open drift/sparkline, healthz/readyz route handlers, 15 component tests pass, `next build` 7 routes.

P1.W7.3 OpenAPI → ui typed client done (2026-10-27): live FastAPI schema exported to `ui/openapi.json` (30 paths, 28 schemas) via `scripts/export_openapi.py`, `ui/lib/api/schema.d.ts` via openapi-typescript 7.13.0, typed `ui/lib/api/client.ts` via openapi-fetch with 25 wrappers + SSE helper, `make openapi-check`/`ui-typecheck` green, 5 new tests; 90.34% coverage.

P1.W7.2 SSE run events done (2026-10-27): `src/autobridge/control/sse.py` streaming stored EvidenceRecords with credential redaction, `GET /runs/{id}/events` via sse_starlette, full-stream redaction test proving no secret value appears; 90.34% coverage.

P1.W7.1 FastAPI control plane done (2026-10-27): all §2.11 endpoints (connectors CRUD, build/sync jobs, runs/events SSE, drift, repairs approve/reject, releases rollback, demo inject-fault labelled proxy, metrics/model-usage, bench/scorecards) with healthz/readyz DB check, OpenAPI contract 15 endpoints, secrets redacted; 90.28% coverage.

P1.W6.5 Safety model complete + e2e test_05 done (2026-10-27): 5 scenarios via fake+live admin-oracle, regression gate, escalation bundle single '?', Decimal+04:00, secrets redacted, frontier 0; docs/safety-model.md §§3,7-12.

P1.W6.4 Regression gate done (2026-10-27): verify_spec POST /verify with regression_suite, GateBlockedError, engine releases gate via verifier_url; 29 tests.

- Completed:
  - P1.W1.1: Repo scaffold
  - P1.W1.2: compose.yaml + healthz/readyz + networks + smoke
  - P1.W1.3: ConnectorSpec v1 + patch + registry
  - P1.W1.4: Canonical entity schemas
  - P1.W1.5: SQLAlchemy core data model + Alembic
  - P1.W1.6: JobRunner + jobs table
  - P1.W1.7: EvidenceStore + redact
  - P1.W1.8: LLMProvider + Budget + ModelCall
  - P1.W1.9: RuntimeTarget fakes + CLI fakes
  - P1.W1.10: e2e conftest + test_00 + arch docs
  - P1.W2.1: Seeded generator
  - P1.W2.2: Fault switchboard + admin API
  - P1.W2.3: AtlasPMS docs + bearer + offset
  - P1.W2.4: HarborPMS docs + quirks
  - P1.W2.5: Scenario catalog 14 YAMLs
  - P1.W2.6: Verifier POST /verify + oracle
  - P1.W2.7: Regression runner + invalid-patch trap
  - P1.W2.9: e2e test_01 oracle
  - P1.W3.1: Runtime interpreter + http
  - P1.W3.2: Auth strategies
  - P1.W3.3: Pagination guards + Degraded/no_loss
  - P1.W3.4: Transform registry + checks + checkpoint
  - P1.W3.5: SyncRun outcome + data_readiness + fingerprint
  - P1.W3.6: RuntimeTarget Local/Docker + negative test
  - P1.W3.7: Reference specs + test_02
  - P1.W4.1: Fingerprinting + drift signals
  - P1.W4.2: Drift classifier 12 classes
  - P1.W4.3: 7 deterministic repair rules + registry
  - P1.W4.4: RepairController + ladder L0/L1 + Budget
  - P1.W4.5: Releases approve/reject/rollback
  - P1.W4.6: Canary monitor + DependencyRegistration
  - P1.W4.7: 4 known repairs zero frontier
  - P1.W5.1: Docs reader
  - P1.W5.2: Classifiers
  - P1.W5.3: Hypothesis enumerators
  - P1.W5.4: ProbeSelector + Budget
  - P1.W5.5: Budgeted L2/L3 wiring
  - P1.W5.6: Bootstrap docs→spec→verify
  - P1.W5.7: Bootstrap e2e test_04
  - P1.W6.1: Semantic invariants
  - P1.W6.2: 5 scenarios end to end
  - P1.W6.3: Escalation bundle
  - P1.W6.4: Regression gate
  - P1.W6.5: Safety model + test_05
  - P1.W7.1: FastAPI control plane (§2.11) — all endpoints + OpenAPI + redaction
  - P1.W7.2: SSE run events (`control/sse.py`, `GET /runs/{id}/events` from EvidenceRecords with redaction)
  - P1.W7.3: OpenAPI → ui typed client via openapi-typescript (export script + schema.d.ts + client.ts + tsc check)
  - P1.W7.4: Next.js 15 scaffold + /connectors screen (health, active release, last sync outcome, open drift, sparkline) — App Router, TS, Tailwind, SWR polling + SSE, Vitest + RTL, readyz
  - P1.W7.5: /connectors/new screen (docs upload/URL/paste, sandbox base URL, credential env-ref picker, target entities, create & start build) — App Router, 12 component tests, next build 8 routes
- Tests: 1722 pass / 0 fail (autobridge 90.34%, verifier 93.78%, simulators 96.55%; ui tsc green) + 59 e2e pass (8 test_00 + 9 test_01 + 4 test_02 + 5 test_03 + 13 test_04 + 20 test_05, 23 skipped when no Docker) — ui: 28 Vitest pass + next build green
- Stack: `make up` → all 8 readyz green / `make smoke` green / `make e2e` green / `make lint` + `make typecheck` + `make ui-typecheck` green

## Next tasks
1. **P1.W7.6** — Screen /connectors/[id] (spec YAML, releases, sync runs, fingerprints, dependencies)
2. **P1.W7.7** — Screen /repairs/[id] + Inject Fault drawer + model-usage view
3. **P1.W7.8** — Network test that UI never calls simulators/verifier; Playwright test_06_ui_journey
