# Progress

## Current State

REQ-091 (AR-7-9 agent confinement) code-DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): default-deny permissions, pristine per-run CLI home, re-prompt in a fresh sandbox copy, preflight proofs, tests + benchmark docs; full gates green (UI flaked once full-suite-only, green on rerun). Owner step: keyed rehearsal, then close.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-091 all code-DONE except owner keyed/live VERIFY remainders below; REQ-051 DEFERRED by owner; REQ-082 + REQ-090 DEFERRED until after the hackathon
  - Phase 2 (P2.S1.1…P2.S7.1) complete
- Tests: full make test green (autobridge 3237 @90.01%, verifier 144 @90.60%, simulators 434 @90.07%, UI 350); lint/typecheck green; stack down (no containers running).
- Stack: down (no live e2e this run; change is bench-harness-only, covered by fakes + end-to-end preflight unit test).

## Next tasks
1. **REQ-091 (VERIFY)** — owner: keyed rehearsal with FRONTIER_API_KEY, check the `frontier_agent` arm still answers, then close. The real beacon freeze waits for this one.
2. **REQ-087/078/080/081/083/084/085/086 (VERIFY)** — owner: keyed rehearsal with FRONTIER_API_KEY, report every arm, then close.
3. **REQ-074 (VERIFY)** — owner: browser check of Atlas `34cca645` + Harbor `77853663`; then close.
4. **REQ-071/070/069 (VERIFY)** — owner keyed walkthroughs / record-frontier; real beacon freeze waits for them.
5. **REQ-065/063/062/061/060 (VERIFY)** — owner keyed bench-beacon rehearsal / bench-ablation in the sandbox.
6. **REQ-051** — DEFERRED; do not start until the owner says so.
