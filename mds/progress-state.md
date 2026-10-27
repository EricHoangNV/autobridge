# Progress

## Current State

P1.W8.5 bench pipeline done (2026-10-27): `make bench` runs heldout SHA gate → matched-budget run → idempotent JSONL → verifier hidden-mode scoring → `experiments/scorecard.md` (wins AND losses); reproducibility via `canonical_payload`/`assert_results_reproducible` (timing-only exclusions, proven live with identical sha256 across runs); `tests/e2e/test_07_benchmark_matched_budget.py` validates the scorecard schema; `make bench-heldout` + freeze runbook ready for Nov 3 (freeze itself stays P1.W2.8 HUMAN). W8 workstream complete (W8.1–W8.5).

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
- Tests: 1995 pass / 0 fail (autobridge 1684 @ 90.63%, verifier 92 @ 93.78%, simulators 219 @ 96.55%)
- Stack: compose down in sbx1 (test_07 green without Docker via fakes; live verifier path when up) / lint + typecheck green

## Next tasks
1. **P1.W9.1** — tests/e2e/conftest.py complete (independent, random-order safe)
2. **P1.W9.2** — make e2e + CI job (unit → smoke → e2e from clean clone)
