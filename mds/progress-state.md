# Progress

## Current State

AUDIT 2026-10-27 (sbx1): first daily directives audit of origin/main 3e4093b8 (184 commits, all Phase 1) done; 4 gaps fixed, 4 filed as REQ-003..REQ-006 + task P1.W7.9. See mds/AUDIT.md. Agent: opencode meta/muse-wonder-1.0 (escalation).

- Completed:
  - W1 Foundations: scaffold, compose, ConnectorSpec, schemas, DB models, jobs, evidence, LLMProvider, fakes, e2e harness base
  - W2 Simulators + verifier: generator, switchboard, Atlas/Harbor, scenario catalog, verifier/oracle, regression, e2e test_01
  - W3 Runtime: interpreter, auth, pagination, transforms, SyncRun outcomes, targets, reference specs (test_02)
  - W4 Drift + repair: fingerprint, classifier (12 classes), 7 rules, ladder L0/L1, releases, canary (test_03, zero frontier)
  - W5 Hypothesis/bootstrap: docs reader, classifiers, enumerators, probes, L2/L3 wiring, bootstrap (test_04)
  - W6 Semantic + safety: invariants, 5 scenarios, escalation bundles, regression gate, docs/safety-model.md (test_05)
  - W7 API + UI: control plane, SSE, typed client, Next.js 4 screens broadcast redesign (REQ-002), network test (test_06)
  - W8 Benchmark: harness, baselines, one-shot, scoring, make bench + heldout gate (test_07)
  - W9 E2E: full harness, make e2e, CI unit→smoke→e2e→demo chain
  - W10 Packaging: README/docs complete, make demo 6/6 live; P1.W10.3 codeable done (owner step below)
  - REQ-001 operator name on approvals; REQ-002 broadcast UI (5 slices) — both DONE
  - AUDIT fixes: bench import-linter contract KEPT; make lint/CI run lint-imports+typecheck; make test/CI gate UI tests; control readyz honestly probes verifier
- Tests: 2026 pass / 0 fail (autobridge 1715 @ 90.92%, verifier 92 @ 93.78%, simulators 219 @ 96.55%) + ui 128 pass; make lint (ruff+import-linter) + typecheck green; make smoke green live; spot e2e test_00+test_03 13 pass live (full make e2e is owner-run)
- Stack: compose up in sbx1 for audit verification; tear down after merge

## Next tasks
1. **P1.W10.3** — [~] OWNER: confirm CI green on main with demo step, record labelled demo (docs/demo.md)
2. **P1.W2.8** — [~] OWNER: populate verifier/hidden_cases/heldout/, sha256sum > SHA256SUMS (docs/benchmark.md)
3. **P1.W7.9** — [ ] schemathesis OpenAPI contract tests (REQ-004; claimed in W7.1, absent)
4. **REQ-003..006** — [ ] owner decisions: §2 vs plan §2.7 shared code; model-id defaults; money float; (in REQUESTS.MD)
5. Phase 2 (P2.S1.1–S7.1) opens only after the Nov 1 stop-rule checkpoint (plan §5)

## Open Requests
REQ-003..REQ-006 (audit 2026-10-27) — see mds/REQUESTS.MD Open Requests.
