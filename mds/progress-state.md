# Progress

## Current State

P1.W6.2 5 scenarios wired end to end done (2026-10-27): gross_to_net→Degraded money_invariant via gross_sum (10% low), tz_boundary_shift→Degraded business_date_invariant (30% shifted), status_vocab_change→Blocked unknown_enum, endpoint_removed→Blocked endpoint_not_found escalate no patch, token_revoked→Blocked auth_revoked escalate no patch; classifier ENUM before CAPABILITY, HttpError→Blocked; reference specs updated with money+ business_date invariants; simulator gross_sum independent evidence.

P1.W6.1 SEMANTIC_VALUE_DRIFT via invariants done (2026-10-27): money_invariant (positive Decimal 2dp, missing→Degraded), business_date_invariant (posted_at→business_date via cutoff, late posting mismatch→Degraded), count_invariant (strict vs expected, over-count→Degraded); unmet_criteria names failing invariant, signals→invariant_failure, classifier→SEMANTIC_VALUE_DRIFT; schema regen, docs updated.

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
  - P1.W2.1: Seeded generator (properties, reservations, folios, late postings, duplicates, nullable, byte-identical)
  - P1.W2.2: Fault switchboard (14 mutations) + admin API on 8001/admin-net, state reset; Atlas/Harbor fault-aware
  - P1.W2.3: AtlasPMS clean OpenAPI 3.1 docs, bearer auth, offset pagination, clear statuses; readyz
  - P1.W2.4: HarborPMS Markdown narrative docs (one wrong example, one undocumented endpoint), cursor pagination with repeated-cursor bug, nested folios, opaque status codes, property-local dates, ambiguous date; readyz
  - P1.W2.5: Scenario catalog 14 YAMLs (dev|heldout + fixable|escalate), loader + isolation test (each scenario exactly one drift signal)
  - P1.W2.6: Verifier service POST /verify with oracle, canonical checks, hidden mode guard
  - P1.W2.7: Verifier regression runner (regression_suite param, baseline + fixable suite), mutation helpers (10 ids, flip/drop helpers), invalid-patch trap set (7 traps, net→total_amount etc. rejected)
  - P1.W2.9: e2e test_01_simulators_and_oracle (admin API → verifier verdicts vs catalog, hidden mode, sim HTTP faults) + docs/safety-model.md first pass
  - P1.W3.1: Runtime interpreter + http (retry/backoff, offset/cursor/page pagination, repeated_cursor flag, ModelCall 0)
  - P1.W3.2: Auth strategies (bearer, api-key header, basic, oauth2 client-credentials via credential_ref, never logged)
  - P1.W3.3: Pagination pagination.py (offset/cursor/page, null_cursor/repeated_cursor/empty_page/no_next/page_limit guards) + engine/sync_run Degraded/no_loss
  - P1.W3.4: Closed transform registry (date_local, money, enum_map, etc.), checks, checkpoint; money Decimal, date boundary, Blocked on unknown enum
  - P1.W3.5: SyncRun outcome Complete|Degraded|Blocked + data_readiness + fingerprint id written per run
  - P1.W3.6: RuntimeTarget LocalProcess + DockerSandbox (network allowlist, timeout, resource limits), Edge reserved; negative network test that DockerSandbox cannot reach verifier or simulator admin
  - P1.W3.7: Reference specs for Atlas/Harbor under generated/connectors/ that sync Complete with ModelCall 0 via test_02 + docs/connector-spec.md
  - P1.W4.1: Fingerprinting (keys/types/pagination_shape delta) + drift signals from sync & canary (fingerprint.py 98% + signals.py 92% + sync_run delegation)
  - P1.W4.2: Drift classifier for 12 classes (classifier.py with priority matchers, evidence ids, fingerprint delta analysis, 54 tests from catalog)
  - P1.W4.3: 7 deterministic rules in repair/rules/ (rename_field, offset_to_cursor, cursor_to_offset, retry_backoff_only, enum_extend_requires_evidence, nested_path_shift, type_coerce_string_number), each with precondition/patch generator/confidence/verification level, and unit tests for precondition true/false and patch shape; repair/registry.py
  - P1.W4.4: RepairController + ladder L0 knowledge cache (vendor+delta) and L1 rules wired; rate_limit/transient backoff, token refresh/escalate, CAPABILITY_REMOVED/AUTH_REVOKED escalate; frontier_tokens==0
  - P1.W4.5: ConnectorRelease creation from verified patch, approve/reject/rollback with active flip and bit-identical sync proof; 29 tests
  - P1.W4.6: Canary monitor + DependencyRegistration pause on release.activated (demo-dashboard dummy consumer); 30 tests
  - P1.W4.7: 4 known repairs verified with frontier_tokens==0 (rename_guest_id→rename_field, offset_to_cursor→offset_to_cursor, cursor_repeat_token→offset_to_cursor guard, nested_path_shift→nested_path_shift) + ledger + release + verifier pass
  - P1.W5.1: Docs reader (OpenAPI + Markdown → normalized InterfaceDescription with provenance per field, 42 tests, 93%)
  - P1.W5.2: Classifiers (auth/pagination/date semantics/status vocabulary from docs+first probes, 45 tests)
  - P1.W5.3: Hypothesis model + enumerator families (date 4 created/arrival/business/UTC, pagination 3 per op offset/cursor/page, amount 3 per money field gross/net/tax_inclusive, status 2 clear/opaque; every hypothesis ≥1 evidence Provenance; 52 tests, 94% on hypotheses.py)
  - P1.W5.4: ProbeSelector (GET-only probes, EIG scoring, ProbeBudget with 4 ceilings, budget-exhausted escalation; 75 tests, 98% on probes.py)
  - P1.W5.5: Budgeted L2/L3 wiring (L3 assert <2 hypotheses, BudgetExhausted→L4 escalation bundle, surviving ambiguity→Degraded; 42 tests)
  - P1.W5.6: Bootstrap (docs→draft spec→compile→visible tests→verify Harbor connector from docs alone; 49 tests)
  - P1.W5.7: Bootstrap unfamiliar API e2e (test_04: negative verifier/simulators path, Harbor oracle+sync, date ambiguity probe vs Degraded, L3 assert, BudgetExhausted→L4, visible tests, Decimal)
   - P1.W6.1: Semantic invariants via checks (money_invariant, business_date_invariant, count_invariant) → Degraded with named unmet_criteria + SEMANTIC_VALUE_DRIFT; 30 tests
   - P1.W6.2: 5 scenarios wired end to end (gross_to_net Degraded money, tz_shift Degraded business_date, status Blocked unknown_enum, endpoint/token Blocked escalate no patch; HttpError→Blocked, classifier reorder, gross_sum evidence; 8 tests)
- Tests: 1608 pass / 0 fail / 0 skip (autobridge 90.54%, verifier 93.78%, simulators 96.55%; ui –) + 39 e2e pass (8 test_00 + 9 test_01 + 4 test_02 + 5 test_03 + 13 test_04, 21 skipped when no Docker)
- Stack: `make up` → all 8 readyz green / `make smoke` green / `make e2e` green (39 pass, 21 skipped) / `make lint` + `make typecheck` green

## Next tasks
1. **P1.W6.3** — Escalation bundle format and storage on RepairRun
2. **P1.W6.4** — Regression gate on every candidate release
