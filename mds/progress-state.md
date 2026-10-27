# Progress

## Current State

W1.7 EvidenceStore done (2026-10-27): append-only `EvidenceStore` over the W1.5 `EvidenceRecord` table (kinds, `parent_ids[]` chains, JSONL dump/load) + `redact.py` (`redact_prompt` strips every credential-ref value, recursive payload redaction) + 24 unit tests incl. planted-secrets prompt test.

- Completed:
  - P1.W1.1: Repo scaffold (uv workspace, Makefile, lint/typecheck/test, import-linter, smoke tests)
  - P1.W1.2: compose.yaml (8 services, 4 networks, healthz/readyz, make up + make smoke)
  - P1.W1.3: ConnectorSpec v1 (models, connector-spec.v1.json, patch, registry)
  - P1.W1.4: Canonical entity schemas (4 schema files, entities.py models + reference examples)
  - P1.W1.5: SQLAlchemy core data model + Alembic migrations (10 tables, immutability, spec_signature)
  - P1.W1.6: DB-backed JobRunner + jobs table (retries, restart recovery, stub executors)
  - P1.W1.7: EvidenceStore + redact.py (chains, redaction at append boundary, JSONL round-trip)
- Tests: 262 pass / 0 fail / 0 skip (autobridge 99.76%, verifier 100%, simulators 100%; ui –)
- Stack: compose untouched by W1.7, as W1.2 left it / `make test` green / `make lint` green / `make typecheck` green

## Next tasks
1. **P1.W1.8** — LLMProvider + Budget + ModelCall ledger
2. **P1.W1.9** — Test fakes (RuntimeTarget, coding-agent CLI)
3. **P1.W1.10** — e2e conftest + test_00_stack_up + docs/architecture.md
