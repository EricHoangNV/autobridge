# Progress

## Current State

REQ-078 continuation code-done in sbx1 (Agent: opencode meta/muse-spark-1.3 (escalation)): bench engine arm production path + round-6603643 misses fixed, unkeyed rehearsal run (round 6603643 FAULTS=10 TRIALS=1).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-078 all code-DONE except owner keyed/live VERIFY remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
- Tests: full gates green (make test all green incl. ui 350 passed; simulators 422 passed @90.48%); lint/typecheck green.
- Rehearsal 6603643 (unkeyed, no FRONTIER_API_KEY): engine arms verified 0.40 / esc 0.40 / $0 / 0 tok / 0 wrong prop+rel; frontier arms 0.00. Instance 7 legacystay semantic now detected + scores; instance 3 meridian nesting now SCHEMA_RENAME (needs keyed model patch). Warm == cold. 50 grades, no crashes.
- Stack: down after the rehearsal (was up for the run, all readyz green).

## Next tasks
1. **REQ-078 (VERIFY)** — owner: keyed rehearsal with FRONTIER_API_KEY on f710053 + real beacon freeze/run.
2. **REQ-074 (VERIFY)** — owner: browser check of Atlas `34cca645` + Harbor `77853663`; then close.
3. **REQ-071/070/069 (VERIFY)** — owner keyed walkthroughs / record-frontier; real beacon freeze waits for them.
4. **REQ-065/063/062/061/060 (VERIFY)** — owner keyed bench-beacon rehearsal / bench-ablation in the sandbox.
5. **REQ-051** — DEFERRED; do not start until the owner says so.
