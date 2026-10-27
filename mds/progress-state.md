# Progress

## Current State

W1.3 ConnectorSpec v1 done (2026-10-27): pydantic models + committed JSON Schema + JSON Patch apply/validate/reverse + write-once disk release registry, 81 new unit tests.

- Completed:
  - P1.W1.1: Repo scaffold (uv workspace, Makefile, lint/typecheck/test, import-linter, smoke tests)
  - P1.W1.2: compose.yaml (8 services, 4 networks, healthz/readyz, make up + make smoke)
  - P1.W1.3: ConnectorSpec v1 (models, connector-spec.v1.json, patch, registry)
- Tests: 138 pass / 0 fail / 0 skip (autobridge 100%, verifier 100%, simulators 100%; ui –)
- Stack: `make up` green (all 8 readyz) / `make smoke` green (incl. negative network checks) / `make test` green / `make lint` green / `make typecheck` green

## Next tasks
1. **P1.W1.4** — Canonical entity schemas
2. **P1.W1.5** — SQLAlchemy core data model + Alembic migrations
3. **P1.W1.6** — DB-backed JobRunner
