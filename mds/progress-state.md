# Progress

## Current State

W1.10 e2e harness + stack acceptance + architecture docs done (2026-10-27): `tests/e2e/conftest.py` session fixture (compose up/wait/teardown, skip when no Docker, E2E_SKIP_STACK support) + `tests/e2e/test_00_stack_up.py` 8 healthz/readyz checks + `docs/architecture.md` first pass (9 sections, verification ladder, e2e budget).

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
- Tests: 399 pass / 0 fail / 0 skip (autobridge 99.49%, verifier 100%, simulators 100%; ui –) + 8 e2e pass
- Stack: `make up` → all 8 readyz green / `make smoke` green / `make e2e` green (8 pass) / `make lint` + `make typecheck` green

## Next tasks
1. **P1.W2.1** — simulators/common seeded generator (properties, reservations, folios)
2. **P1.W2.2** — fault switchboard + admin API POST /admin/scenario on separate port/network
