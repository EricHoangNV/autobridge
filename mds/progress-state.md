# Progress

## Current State

REQ-019 done (sbx1 2026-10-28): Atlas serves the method-style hotel PMS surface (getReservations/getReservation/getGuest/getHotels/getTransactions, x-api-key, pageNumber/pageSize, JSON-number money, canceled spelling); reference spec + discovery + runtime (page/total_path) + oracle updated; L1 rule page_to_cursor added (8 rules); LegacyStay guard test added. Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - W1 Foundations: scaffold, compose, ConnectorSpec, schemas, DB models, jobs, evidence, LLMProvider, fakes, e2e harness base
  - W2 Simulators + verifier: generator, switchboard, Atlas/Harbor, scenario catalog, verifier/oracle, regression, e2e test_01
  - W3 Runtime: interpreter, auth, pagination, transforms, SyncRun outcomes, targets, reference specs (test_02)
  - W4 Drift + repair: fingerprint, 12-class classifier, 8 rules, ladder L0/L1, releases, canary (test_03, zero frontier)
  - W5 Hypothesis/bootstrap: docs reader, classifiers, enumerators, probes, L2/L3 wiring, bootstrap (test_04)
  - W6 Semantic + safety: invariants, 5 scenarios, escalation bundles, regression gate, docs/safety-model.md (test_05)
  - W7 API + UI: control plane, SSE, typed client, Next.js 4 screens broadcast redesign, network test (test_06), schemathesis contracts (P1.W7.9)
  - W8 Benchmark: harness, baselines, one-shot, scoring, make bench + heldout gate (test_07)
  - W9 E2E: full harness, make e2e, CI unit→smoke→e2e→demo chain; live/inprocess markers (35 live / 73 inprocess)
  - W10 Packaging: README/docs complete, make demo 6/6 live; P1.W10.3 codeable done (owner step below)
  - REQ-001…REQ-019 + REQ-017 + REQ-018 all DONE (REQ-019: method-style Atlas surface)
  - P2.S1.1 … P2.S7.1 DONE (LegacyStay, Edge target, DiscoveryResult import, MCP Guardian, OpenAI provider, Richer UI, CLI)
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 (residual) all code-fixed (Cycle 2 re-verification pending owner)
- Tests: 2385 pass / 0 fail (autobridge 91.43%, verifier 93.78%, simulators 96.21%) + ui 148 pass; lint + typecheck green
- Stack: local mode verified (sbx1 no-Docker sandbox, 2026-10-28): local-up 12/12 readyz, smoke OK (local), demo 6/6 (5 live), test_01/02/03/04/05 live green; readyz green

## Next tasks
0. **REQ-020** — [ ] OPEN (owner priority 2026-10-28: Meridian after Cloudbeds shape; needs REQ-019 done, REQ-021 item 2 for step 4)
1. **P1.W10.3** — [~] OWNER: confirm CI green on main with demo step, record labelled demo (docs/demo.md)
2. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching committed verifier/hidden_cases/heldout/SHA256SUMS (codeable REQ-007 flow done 2026-10-27)
3. Code Review Cycle 2 re-verification (all FAIL items code-fixed)
4. Phase 2 (owner decision 2026-10-27, PRD.MD): P2.S1.1 … P2.S7.1 DONE — Phase 2 complete

## Open Requests
REQ-020, REQ-021, REQ-022, REQ-023, REQ-024, REQ-025 OPEN (owner priority: REQ-020, then REQ-022, REQ-024 → REQ-025 before Nov 3, REQ-021/023 applied as each lands).
