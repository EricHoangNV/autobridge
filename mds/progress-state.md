# Progress

## Current State

W1.6 JobRunner done (2026-10-27): DB-backed `control/jobs.py` (`submit_build/sync/repair`, atomic claim, retries to `max_attempts`, `reclaim_orphaned` for worker restart) + `Job` model and `0002_job_queue` migration + 14 unit tests incl. kill-mid-job restart and flaky-handler retry.

- Completed:
  - P1.W1.1: Repo scaffold (uv workspace, Makefile, lint/typecheck/test, import-linter, smoke tests)
  - P1.W1.2: compose.yaml (8 services, 4 networks, healthz/readyz, make up + make smoke)
  - P1.W1.3: ConnectorSpec v1 (models, connector-spec.v1.json, patch, registry)
  - P1.W1.4: Canonical entity schemas (4 schema files, entities.py models + reference examples)
  - P1.W1.5: SQLAlchemy core data model + Alembic migrations (10 tables, immutability, spec_signature)
  - P1.W1.6: DB-backed JobRunner + jobs table (retries, restart recovery, stub executors)
- Tests: 238 pass / 0 fail / 0 skip (autobridge 99.73%, verifier 100%, simulators 100%; ui –)
- Stack: compose untouched by W1.6, as W1.2 left it / `make test` green / `make lint` green / `make typecheck` green

## Next tasks
1. **P1.W1.7** — EvidenceStore + redact.py
2. **P1.W1.8** — LLMProvider + Budget + ModelCall ledger
3. **P1.W1.9** — Test fakes (RuntimeTarget, coding-agent CLI)
