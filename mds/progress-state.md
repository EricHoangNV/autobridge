# Progress

## Current State

REQ-009 done (sbx1 2026-10-28): clean-clone UI deps — `ui-deps` (`npm ci`) prereq of `make test`, `ui-e2e-deps` (playwright chromium) prereq of `make e2e-ui`, `NODE_PATH` in the `e2e-ui` recipe for the root Playwright config; typecheck test skips loudly without `node_modules`; README + CI updated. Fresh-clone check: `make test` 0 failures, `make e2e-ui` exit 0. Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - W1 Foundations: scaffold, compose, ConnectorSpec, schemas, DB models, jobs, evidence, LLMProvider, fakes, e2e harness base
  - W2 Simulators + verifier: generator, switchboard, Atlas/Harbor, scenario catalog, verifier/oracle, regression, e2e test_01
  - W3 Runtime: interpreter, auth, pagination, transforms, SyncRun outcomes, targets, reference specs (test_02)
  - W4 Drift + repair: fingerprint, classifier (12 classes), 7 rules, ladder L0/L1, releases, canary (test_03, zero frontier)
  - W5 Hypothesis/bootstrap: docs reader, classifiers, enumerators, probes, L2/L3 wiring, bootstrap (test_04)
  - W6 Semantic + safety: invariants, 5 scenarios, escalation bundles, regression gate, docs/safety-model.md (test_05)
  - W7 API + UI: control plane, SSE, typed client, Next.js 4 screens broadcast redesign (REQ-002), network test (test_06), schemathesis contract tests (P1.W7.9)
  - W8 Benchmark: harness, baselines, one-shot, scoring, make bench + heldout gate (test_07)
  - W9 E2E: full harness, make e2e, CI unit→smoke→e2e→demo chain
  - W10 Packaging: README/docs complete, make demo 6/6 live; P1.W10.3 codeable done (owner step below)
  - REQ-001 operator name; REQ-002 broadcast UI (5 slices); REQ-003 trust-boundary wording; REQ-004 schemathesis; REQ-005 model-id literals; REQ-006 money float rejection; REQ-007 private heldout seeds (codeable); REQ-008 bench host URLs + loud failure; REQ-009 clean-clone UI deps — all DONE
  - P2.S1.1 LegacyStay third vendor (bootstrap + rename repair, frontier 0); P2.S2.1 Simulated Edge target (signed-only edge worker, isolated edge-net, live negative + signed execution green); P2.S3.1 DiscoveryResult static import (reserved schema, explicit-endpoint lifecycle, 2 live e2e green); P2.S4.1 MCP Guardian (pinned gateway replay Complete vs rediscovery Blocked, unsafe mappings rejected, 2 live e2e green); P2.S5.1 OpenAI provider (identical ladder outcome on both providers, e2e test_s5 4 pass + 1 owner-opt-in skip); P2.S6.1 Richer UI (per-run chart + evidence graph, test_s6 3 pass, test_06 6/6 green, no new API surface); P2.S7.1 CLI (same journey as UI via `ab`, unit 26 + e2e test_s7 4/4 green, no new API surface)
- Tests: 2225 pass / 0 fail (autobridge 91.40%, verifier 93.76%, simulators 96.66%) + ui 143 pass; make lint (2 contracts KEPT) + typecheck green; fresh-clone make test + make e2e-ui green
- Stack: compose available; fresh-clone e2e-ui run exit 0 with 9 skips by design (no stack up — live journey needs `make up`)

## Next tasks
1. **P1.W10.3** — [~] OWNER: confirm CI green on main with demo step, record labelled demo (docs/demo.md)
2. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching committed verifier/hidden_cases/heldout/SHA256SUMS (codeable REQ-007 flow done 2026-10-27)
3. **REQ-010** — OPEN: live-stack versions of test_03/test_05 via control API + mark in-process tests (other loop may claim)
4. Phase 2 (owner decision 2026-10-27, PRD.MD): P2.S1.1 + P2.S2.1 + P2.S3.1 + P2.S4.1 + P2.S5.1 + P2.S6.1 + P2.S7.1 DONE — Phase 2 complete

## Open Requests
REQ-010 OPEN (live-stack e2e); REQ-009 DONE 2026-10-28 (owner step tracked in P1.W10.3).
