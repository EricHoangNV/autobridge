# Progress

## Current State

REQ-017 done (sbx1 2026-10-28): Docker-free local mode — `make local-up/down` (same host ports, ./.local db/logs/pids), LocalProcessTarget in local mode, /readyz mode reporting, UI LOCAL badge, smoke skip note, e2e local support, README No-Docker + safety-model §12; verified live incl. DOCKER_HOST=broken cycle. Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - W1 Foundations: scaffold, compose, ConnectorSpec, schemas, DB models, jobs, evidence, LLMProvider, fakes, e2e harness base
  - W2 Simulators + verifier: generator, switchboard, Atlas/Harbor, scenario catalog, verifier/oracle, regression, e2e test_01
  - W3 Runtime: interpreter, auth, pagination, transforms, SyncRun outcomes, targets, reference specs (test_02)
  - W4 Drift + repair: fingerprint, 12-class classifier, 7 rules, ladder L0/L1, releases, canary (test_03, zero frontier)
  - W5 Hypothesis/bootstrap: docs reader, classifiers, enumerators, probes, L2/L3 wiring, bootstrap (test_04)
  - W6 Semantic + safety: invariants, 5 scenarios, escalation bundles, regression gate, docs/safety-model.md (test_05)
  - W7 API + UI: control plane, SSE, typed client, Next.js 4 screens broadcast redesign, network test (test_06), schemathesis contracts (P1.W7.9)
  - W8 Benchmark: harness, baselines, one-shot, scoring, make bench + heldout gate (test_07)
  - W9 E2E: full harness, make e2e, CI unit→smoke→e2e→demo chain; live/inprocess markers (35 live / 73 inprocess)
  - W10 Packaging: README/docs complete, make demo 6/6 live; P1.W10.3 codeable done (owner step below)
  - REQ-001…REQ-013 + REQ-017 + REQ-018 all DONE (REQ-017: Docker-free local run, verified live)
  - P2.S1.1 … P2.S7.1 DONE (LegacyStay, Edge target, DiscoveryResult import, MCP Guardian, OpenAI provider, Richer UI, CLI)
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 (residual) all code-fixed (Cycle 2 re-verification pending owner)
- Tests: 2356 pass / 0 fail (autobridge 91.59%, verifier 93.76%, simulators 96.82%) + ui 147 pass; lint + typecheck green
- Stack: local mode verified (sbx1 no-Docker sandbox, 2026-10-28): local-up 12/12 readyz, smoke OK (local), demo 6/6, test_00/02/s2 live; readyz green

## Next tasks
0. **REQ-014 → REQ-015 → REQ-016** — [ ] OPEN (owner priority 2026-10-28: .env loading, e2e control token, demo fail-loud)
1. **P1.W10.3** — [~] OWNER: confirm CI green on main with demo step, record labelled demo (docs/demo.md)
2. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching committed verifier/hidden_cases/heldout/SHA256SUMS (codeable REQ-007 flow done 2026-10-27)
3. Code Review Cycle 2 re-verification (all FAIL items code-fixed)
4. Phase 2 (owner decision 2026-10-27, PRD.MD): P2.S1.1 … P2.S7.1 DONE — Phase 2 complete

## Open Requests
REQ-014, REQ-015, REQ-016, REQ-019, REQ-020, REQ-021, REQ-022, REQ-023 OPEN (owner priority: REQ-014 → REQ-015 → REQ-016, then REQ-019 → REQ-020, then REQ-022, REQ-021/023 applied as each lands).
