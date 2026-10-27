# Progress

## Current State

REQ-001 operator name complete (2026-10-27): server sets `Approval.actor` from `AUTOBRIDGE_OPERATOR` (default `operator`), `GET /operator` exposes it, `ReleaseOut.approved_by` populated; UI `RepairActions` shows "Approving as <name>", `ReleaseHistory` shows approver per release; W7.8 Playwright journey 6/6 green (mock stabilization); compose forwards the var.

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
  - P1.W7.1: FastAPI control plane + OpenAPI + redaction
  - P1.W7.2: SSE run events with redaction
  - P1.W7.3: OpenAPI → ui typed client
  - P1.W7.4: Next.js scaffold + /connectors screen
  - P1.W7.5: /connectors/new screen
  - P1.W7.6: /connectors/[id] screen
  - P1.W7.7: /repairs/[id] screen
  - P1.W7.8: UI journey + network isolation
  - P1.W8.1: Bench harness matched-budget
  - P1.W8.2: Baseline drivers (claude/codex/llm fallback)
  - P1.W8.3: one_shot_generator secondary control
  - P1.W8.4: bench scoring + report via verifier hidden mode
  - P1.W8.5: make bench idempotent + heldout sha + test_07 + docs/benchmark.md
  - P1.W9.1: e2e harness complete (verified reset, env isolation, budget, random order)
  - P1.W9.2: make e2e + CI job (unit → smoke → e2e from clean clone)
  - P1.W10.1: README + docs/ pages + .env.example
  - P1.W10.2: make demo six steps via control API + local evidence
  - REQ-001: operator name on approvals (server-set actor, GET /operator, UI lines, compose var)
- Tests: 2026 pass / 0 fail (autobridge 1715 @ 90.83%, verifier 92 @ 93.78%, simulators 219 @ 96.55%) + ui 105 + e2e test_06 6/6 (mocked, chromium)
- Stack: compose up in sbx1 (control verified live: GET /operator default + override) / lint + typecheck green

## Next tasks
1. **P1.W10.3** — HUMAN clean-clone check + demo recording (codeable part done: make demo is idempotent)
2. **P1.W2.8** — HUMAN heldout freeze (manifest + sha check ready; owner freezes)
