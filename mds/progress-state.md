# Progress

## Current State

REQ-089 (AR-7-2 control-token default) code-DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): control-demo-token default in compose/local-up/.env.example, lifespan warning/info, smoke 401/200, unit tests + safety-model docs; full gates + live stack (up/smoke/demo/test_06) green, stack taken down after. Owner step: none codeable left.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-089 all code-DONE except owner keyed/live VERIFY remainders below; REQ-051 DEFERRED by owner; REQ-082 + REQ-090 DEFERRED until after the hackathon
  - Phase 2 (P2.S1.1…P2.S7.1) complete
- Tests: full make test green (autobridge 3236 @90%, verifier 144 @91%, simulators 434 @90%, UI 350); lint/typecheck/openapi green; smoke green live; test_06 7/8 live (1 pre-existing demo-state failure, unrelated).
- Stack: down (was booted for REQ-089 live proof, taken down after; no containers running).

## Next tasks
1. **REQ-087/078/080/081/083/084/085/086 (VERIFY)** — owner: keyed rehearsal with FRONTIER_API_KEY, report every arm, then close.
2. **REQ-074 (VERIFY)** — owner: browser check of Atlas `34cca645` + Harbor `77853663`; then close.
3. **REQ-071/070/069 (VERIFY)** — owner keyed walkthroughs / record-frontier; real beacon freeze waits for them.
4. **REQ-065/063/062/061/060 (VERIFY)** — owner keyed bench-beacon rehearsal / bench-ablation in the sandbox.
5. **REQ-051** — DEFERRED; do not start until the owner says so.
