# Progress

## Current State

REQ-078 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3 (escalation)): simulator defects fixed + beacon rehearsal run (round 6599930 FAULTS=10 TRIALS=1).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-078 all DONE except owner keyed/live VERIFY remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
- Tests: full gates green (autobridge 90.05%; verifier 139 passed @90.80%; simulators 422 passed @90.48%; ui 350 passed);
  lint/typecheck green; W2 e2e file live green (9 passed).
- Rehearsal 6599930: engine arms verified 0.40 / esc 0.40 / $0 / 0 tok / 0 wrong prop+rel; frontier arms 0.00
  (no FRONTIER_API_KEY in sbx1, fake drivers fail-closed); sanity gate clean, 50 grades, no crashes. Warm == cold.
- Stack: down after the rehearsal (was up for the live run, all readyz green).

## Next tasks
1. **REQ-078 (VERIFY)** — owner: keyed rehearsal with FRONTIER_API_KEY + real beacon freeze/run.
2. **REQ-074 (VERIFY)** — owner: browser check of Atlas `34cca645` + Harbor `77853663`; then close.
3. **REQ-071/070/069 (VERIFY)** — owner keyed walkthroughs / record-frontier; real beacon freeze waits for them.
4. **REQ-065/063/062/061/060 (VERIFY)** — owner keyed bench-beacon rehearsal / bench-ablation in the sandbox.
5. **REQ-051** — DEFERRED; do not start until the owner says so.
