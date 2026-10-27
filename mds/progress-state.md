# Progress

## Current State

W1.5 Core data model done (2026-10-27): 10 plan §2.3 tables in `control/models.py` (portable types, String+CHECK vocabs, Decimal money, tz-aware timestamps, credential_ref with no secret column, ConnectorRelease immutability via mapper events) + Alembic scaffold and `0001_core_data_model` (SQLite-safe batch FK) + 26 unit tests incl. migration cycle and Postgres DDL compile.

- Completed:
  - P1.W1.1: Repo scaffold (uv workspace, Makefile, lint/typecheck/test, import-linter, smoke tests)
  - P1.W1.2: compose.yaml (8 services, 4 networks, healthz/readyz, make up + make smoke)
  - P1.W1.3: ConnectorSpec v1 (models, connector-spec.v1.json, patch, registry)
  - P1.W1.4: Canonical entity schemas (4 schema files, entities.py models + reference examples)
  - P1.W1.5: SQLAlchemy core data model + Alembic migrations (10 tables, immutability, spec_signature)
- Tests: 224 pass / 0 fail / 0 skip (autobridge 100%, verifier 100%, simulators 100%; ui –)
- Stack: compose untouched by W1.5, as W1.2 left it / `make test` green / `make lint` green / `make typecheck` green

## Next tasks
1. **P1.W1.6** — DB-backed JobRunner
2. **P1.W1.7** — EvidenceStore + redact.py
3. **P1.W1.8** — LLMProvider + Budget + ModelCall ledger
