# Progress

## Current State

REQ-020 sub-step 6 done (sbx1 2026-10-28): after-proof + two faults repaired (this entry). Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - W1 Foundations: scaffold, compose, ConnectorSpec, schemas, DB models, jobs, evidence, LLMProvider, fakes, e2e harness base
  - W2 Simulators + verifier: generator, switchboard, Atlas/Harbor, scenario catalog (17), verifier/oracle, regression, e2e test_01
  - W3 Runtime: interpreter, auth, pagination, transforms, SyncRun outcomes, targets, reference specs (test_02)
  - W4 Drift + repair: fingerprint (+body_cursor fields), 12-class classifier, 9 rules (+cursor_next_path_shift), ladder L0/L1, releases, canary (test_03, zero frontier)
  - W5 Hypothesis/bootstrap: docs reader, classifiers, enumerators, probes, L2/L3 wiring, bootstrap (test_04)
  - W6 Semantic + safety: invariants, 5 scenarios, escalation bundles, regression gate, docs/safety-model.md (test_05)
  - W7 API + UI: control plane, SSE, typed client, Next.js 4 screens broadcast redesign, network test (test_06), schemathesis contracts (P1.W7.9)
  - W8 Benchmark: harness, baselines, one-shot, scoring, make bench + heldout gate (test_07)
  - W9 E2E: full harness, make e2e, CI unit→smoke→e2e→demo chain; live/inprocess markers (35 live / 73 inprocess)
  - W10 Packaging: README/docs complete, make demo 6/6 live; P1.W10.3 codeable done (owner step below)
  - REQ-001…REQ-019 + REQ-017 + REQ-018 all DONE (REQ-019: method-style Atlas surface)
  - P2.S1.1 … P2.S7.1 DONE (LegacyStay, Edge target, DiscoveryResult import, MCP Guardian, OpenAI provider, Richer UI, CLI)
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 (residual) all code-fixed (Cycle 2 re-verification pending owner)
  - REQ-020 sub-step A done (sbx1 2026-10-28): sim-meridian + portal + compose/local/smoke + vocabulary_guard before-refusal + e2e before-pin (live green)
  - REQ-020 sub-steps 5a+5b+5c done (sbx1 2026-10-28): spec v2 + runtime + discovery proposes v2 + declarative joins
  - REQ-020 sub-step 6 done (sbx1 2026-10-28): after-proof (gate+Complete+business date, live) + 2 faults repaired via ladder at 0 frontier (this entry)
- Tests: 2496 pass / 0 fail (autobridge 2103, verifier 99, simulators 294) + ui 148 pass; 90% gates green (91 / 94 / 96); lint + typecheck green; e2e meridian after/faults/joins/runtime/before + test_01/03/04 (39) green live
- Stack: Docker available in this sandbox: sim-meridian live verified (after-Complete + both fault wires + Decimal money)

## Next tasks
0. **REQ-020** — [>] IN PROGRESS sbx1 (sub-steps A + 5a + 5b + 5c + 6 merged; remaining: 7 demo)
1. **P1.W10.3** — [~] OWNER: confirm CI green on main with demo step, record labelled demo (docs/demo.md)
2. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching committed verifier/hidden_cases/heldout/SHA256SUMS (codeable REQ-007 flow done 2026-10-27)
3. Code Review Cycle 2 re-verification (all FAIL items code-fixed)
4. Phase 2 (owner decision 2026-10-27, PRD.MD): P2.S1.1 … P2.S7.1 DONE — Phase 2 complete

## Open Requests
REQ-020, REQ-021, REQ-022, REQ-023, REQ-024, REQ-025 OPEN (owner priority: REQ-020, then REQ-022, REQ-024 → REQ-025 before Nov 3, REQ-021/023 applied as each lands).
