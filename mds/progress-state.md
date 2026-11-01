# Progress

## Current State

REQ-075 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3 (escalation)): persisted per-connector money
sketches (`connector_value_baselines` + migration 0016) feed `baseline_summary` on the live detection
path; bench/beacon derive through the same shared sketch; 13 new unit tests.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-075 all DONE except owner keyed/live VERIFY remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
- Tests: `make test` green (autobridge 3020 passed @90% gate; verifier 138 passed @91.04%; UI 348 passed);
  lint/typecheck/contracts/ui-typecheck/openapi-check green. No live stack in sbx1.
- Stack: no live stack in sbx1; smoke live leg + e2e not runnable here (reported, not faked)

## Next tasks
1. **REQ-076** — UI full-suite flake root cause (NewConnectorForm REQ-050 test). Owner priority next.
2. **REQ-074 (VERIFY)** — owner: browser check of Atlas `34cca645` + Harbor `77853663`; then close.
3. **REQ-071/070/069 (VERIFY)** — owner keyed walkthroughs / record-frontier; real beacon freeze waits for them.
4. **REQ-065/063/062/061/060 (VERIFY)** — owner keyed bench-beacon rehearsal / bench-ablation in the sandbox.
