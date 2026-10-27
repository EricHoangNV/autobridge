# Progress

## Current State

W3.6 RuntimeTarget LocalProcess + DockerSandbox done (2026-10-27): network allowlist + /admin path guard, timeout 1..3600, memory 64..8192, Edge reserved, negative network test (sandbox -/-> verifier/admin), factory + readyz.

- Completed:
  - P1.W1.1: Repo scaffold (uv workspace, Makefile, lint/typecheck/test, import-linter, smoke tests)
  - P1.W1.2: compose.yaml (8 services, 4 networks, healthz/readyz, make up + make smoke)
  - P1.W1.3: ConnectorSpec v1 (models, connector-spec.v1.json, patch, registry)
  - P1.W1.4: Canonical entity schemas (4 schema files, entities.py models + reference examples)
  - P1.W1.5: SQLAlchemy core data model + Alembic migrations (10 tables, immutability, spec_signature)
  - P1.W1.6: DB-backed JobRunner + jobs table (retries, restart recovery, stub executors)
  - P1.W1.7: EvidenceStore + redact.py (chains, redaction at append boundary, JSONL round-trip)
  - P1.W1.8: LLMProvider + Budget + ModelCall ledger (frontier/small budgets, Anthropic + fake, 58 tests)
  - P1.W1.9: RuntimeTarget fakes + coding-agent CLI fakes (96 tests, fake CLI executable)
  - P1.W1.10: e2e conftest + test_00_stack_up (8 e2e pass, skip when no Docker) + docs/architecture.md
  - P1.W2.1: Seeded generator (properties/reservations/folios, late postings, duplicates, nullable, byte-identical)
  - P1.W2.2: Fault switchboard (14 mutations) + admin API on 8001/admin-net, state reset; Atlas/Harbor fault-aware
  - P1.W2.3: AtlasPMS clean OpenAPI 3.1 docs, bearer auth, offset pagination, clear statuses; readyz
  - P1.W2.4: HarborPMS Markdown docs (one wrong example, one undocumented endpoint), cursor pagination with repeated-cursor bug, nested folios, opaque status codes, ambiguous date; readyz
  - P1.W2.5: Scenario catalog 14 YAMLs (dev|heldout + fixable|escalate), loader + isolation test (each scenario exactly one drift signal)
  - P1.W2.6: Verifier service POST /verify with oracle, canonical checks, hidden mode guard
  - P1.W2.7: Verifier regression runner (regression_suite param, baseline + fixable suite), mutation helpers (10 ids, flip/drop helpers), invalid-patch trap set (7 traps, net→total_amount etc. rejected)
  - P1.W2.9: e2e test_01_simulators_and_oracle (admin API → verifier verdicts vs catalog, hidden mode, sim HTTP faults) + docs/safety-model.md first pass
  - P1.W3.1: Runtime interpreter + http (retry/backoff, offset/cursor/page pagination, repeated_cursor flag, ModelCall 0)
  - P1.W3.2: Auth strategies (bearer, api-key header, basic, oauth2 client-credentials via credential_ref, never logged)
  - P1.W3.3: Pagination pagination.py (offset/cursor/page, null_cursor/repeated_cursor/empty_page/no_next/page_limit guards) + engine/sync_run Degraded/no_loss
  - P1.W3.4: Closed transform registry (date_local, money, enum_map, etc.), checks, checkpoint; money Decimal, date boundary, Blocked on unknown enum
  - P1.W3.5: SyncRun outcome Complete|Degraded|Blocked + data_readiness + fingerprint_id per run
  - P1.W3.6: RuntimeTarget LocalProcess + DockerSandbox (network allowlist, timeout, resource limits), Edge reserved; negative network test that DockerSandbox cannot reach verifier or simulator admin
- Tests: 969 pass / 0 fail / 0 skip (autobridge 94.16%, verifier 93.67%, simulators 98.54%; ui –) + 17 e2e pass (8 test_00 + 9 test_01, skip when no Docker)
- Stack: `make up` → all 8 readyz green (runtime_target + interpreter checks) / `make smoke` green / `make e2e` green (17 pass, skip when no Docker) / `make lint` + `make typecheck` green

## Next tasks
1. **P1.W2.8** — HUMAN heldout freeze (agents prepare manifest + sha check; owner freezes SHA256SUMS)
2. **P1.W3.7** — Reference specs for Atlas/Harbor + test_02_reference_sync + docs/connector-spec.md
