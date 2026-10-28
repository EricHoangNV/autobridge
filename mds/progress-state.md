# Progress

## Current State

AR-2-4 done (sbx1 2026-10-28): edge release signatures HMAC-keyed via EDGE_RELEASE_SECRET (sign: control/worker, verify: edge-worker), 9 new tests. Agent: opencode meta/muse-spark-1.3-contributor (escalation).

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
  - REQ-001…REQ-012 all DONE (operator name, broadcast UI, trust wording, schemathesis, model-id literals, money float, heldout seeds codeable, bench host URLs, UI deps, live e2e, loopback ports, control write token)
  - P2.S1.1 … P2.S7.1 DONE (LegacyStay, Edge target, DiscoveryResult import, MCP Guardian, OpenAI provider, Richer UI, CLI)
  - AR-2-1: capped vendor Retry-After + per-job delay budget, 5 new tests
  - AR-1-1: SIM_ADMIN_TOKEN replaces published header as admin credential, 9 new tests
  - AR-1-2: gate-before-persist on build path + no sync fallback to unapproved releases, 7 new tests
  - AR-1-3: approval bound to repair run's candidate release (route 422 + engine ReleaseError), 3 new tests
  - AR-2-2: docs_url allowlist + identifier-only docs_path (route + engine), userinfo redaction, 18 new tests
  - AR-1-5: gate verdict bound to scenario/mode + spec signature recorded + fail-closed gate import, 5 new tests
  - AR-2-3: L0 cache evict(vendor,delta) + evict_patch(); reject/rollback eviction, 4 new tests
  - AR-2-6: readyz_response 503-on-degraded (control) + worker job-store check + smoke body assert, 6 new tests
  - AR-1-4 residual: verifier :8104 + sim-admin :8109/:8110/:8112 pinned literal loopback, 1 new compose test
  - AR-2-4: HMAC-keyed edge release signatures (EDGE_RELEASE_SECRET shared by control/worker/edge-worker; legacy checksum when unset), 9 new tests
- Tests: 2304 pass / 0 fail (autobridge 92%, verifier 93.76%, simulators 96.69%) + ui 143 pass; lint + typecheck + ui-typecheck green; no compose stack in sandbox (unit-covered only)
- Stack: compose up with loopback-bound ports; all readyz green 2026-10-28

## Next tasks
1. **P1.W10.3** — [~] OWNER: confirm CI green on main with demo step, record labelled demo (docs/demo.md)
2. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching committed verifier/hidden_cases/heldout/SHA256SUMS (codeable REQ-007 flow done 2026-10-27)
3. Code Review Cycle 2 FAIL items still open: AR-2-5 (AR-1-1, AR-1-2, AR-1-3, AR-1-4 residual, AR-1-5, AR-2-1, AR-2-2, AR-2-3, AR-2-6, AR-2-4 fixed, pending re-verification)
4. Phase 2 (owner decision 2026-10-27, PRD.MD): P2.S1.1 … P2.S7.1 DONE — Phase 2 complete

## Open Requests
None open (REQ-012 DONE 2026-10-28).
