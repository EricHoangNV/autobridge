# Progress

## Current State

P2.S4.1 done (sbx1 2026-10-27): MCP Guardian — mock HotelToolsV1 → V2 (3 renames + 1 addition), declarative catalog snapshot/diff, pinned-mapping compatibility gateway, pinned-consumer replay Complete via gateway vs Blocked via plain rediscovery, unsafe mappings rejected. New mcp-hotel :8115 on engine-net only. Agent: opencode meta/muse-spark-1.3-contributor (escalation).

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
  - REQ-001 operator name on approvals; REQ-002 broadcast UI (5 slices); REQ-003 trust-boundary wording + contracts; REQ-004 schemathesis contract; REQ-005 model-id literals; REQ-006 money float rejection — all DONE
  - P2.S1.1 LegacyStay third vendor (bootstrap + rename repair, frontier 0); P2.S2.1 Simulated Edge target (signed-only edge worker, isolated edge-net, live negative + signed execution green); P2.S3.1 DiscoveryResult static import (reserved schema, explicit-endpoint lifecycle, 2 live e2e green); P2.S4.1 MCP Guardian (pinned gateway replay Complete vs rediscovery Blocked, unsafe mappings rejected, 2 live e2e green)
- Tests: 2159 pass / 0 fail (autobridge 1826 @ 91.15% gate, verifier 92 @ ~93.8%, simulators 241 @ ~96.7%) + ui 130 pass; make lint (ruff+import-linter, 2 contracts KEPT) + typecheck green; e2e test_s4 6/6 (2 live) green against compose stack this run
- Stack: compose stack up (e2e fixture used the live stack green)

## Next tasks
1. **P1.W10.3** — [~] OWNER: confirm CI green on main with demo step, record labelled demo (docs/demo.md)
2. **P1.W2.8** — [~] OWNER (REQ-007): place private seeds in ~/autobridge-heldout/ matching committed verifier/hidden_cases/heldout/SHA256SUMS
3. Phase 2 (owner decision 2026-10-27, PRD.MD): P2.S1.1 + P2.S2.1 + P2.S3.1 + P2.S4.1 DONE; next P2.S5.1 OpenAI provider, in plan §5 order

## Open Requests
None — REQ-006 was the last open request (resolved 2026-10-27).
