# Progress

## Current State

W1.9 RuntimeTarget + coding-agent CLI fakes done (2026-10-27): `host_from_url`/`_host_allowed` + `BaseTarget`/`LocalProcess`/`DockerSandbox` + `FakeRuntimeTarget`/`FakeDockerSandbox`/`FakeLocalProcess` with network allowlist/timeout/fault injection + `ClaudeCodeDriver`/`FakeClaudeDriver`/`parse_claude_output`/`build_fake_claude_output` + `scripts/fake_claude.py` executable fake; 96 new unit tests.

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
- Tests: 399 pass / 0 fail / 0 skip (autobridge 99.49%, verifier 100%, simulators 100%; ui –)
- Stack: compose untouched by W1.9, as W1.2 left it / `make test` green / `make lint` green / `make typecheck` green

## Next tasks
1. **P1.W1.10** — e2e conftest + test_00_stack_up + docs/architecture.md
2. **P1.W2.1** — simulators/common seeded generator (properties, reservations, folios)
