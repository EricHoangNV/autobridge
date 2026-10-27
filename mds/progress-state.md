# Progress

## Current State

W1.4 Canonical entity schemas done (2026-10-27): reservation + folio-charge schemas (Decimal-as-string 2dp, property-local business dates, closed status vocab), runtime-target (edge reserved), discovery-result.v0 (reserved), engine pydantic models + reference examples + schema helpers, 60 new unit tests.

- Completed:
  - P1.W1.1: Repo scaffold (uv workspace, Makefile, lint/typecheck/test, import-linter, smoke tests)
  - P1.W1.2: compose.yaml (8 services, 4 networks, healthz/readyz, make up + make smoke)
  - P1.W1.3: ConnectorSpec v1 (models, connector-spec.v1.json, patch, registry)
  - P1.W1.4: Canonical entity schemas (4 schema files, entities.py models + reference examples)
- Tests: 198 pass / 0 fail / 0 skip (autobridge 100%, verifier 100%, simulators 100%; ui –)
- Stack: compose untouched by W1.4, as W1.2 left it / `make test` green / `make lint` green / `make typecheck` green

## Next tasks
1. **P1.W1.5** — SQLAlchemy core data model + Alembic migrations
2. **P1.W1.6** — DB-backed JobRunner
3. **P1.W1.7** — EvidenceStore + redact.py
