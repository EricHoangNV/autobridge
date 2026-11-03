# Progress

## Current State

REQ-093 code-DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): reference-patch pointer shape-from-new-id fix, regression test, refused round-6608587 protocol removed, benchmark docs noted; full gates green. Owner step: re-freeze a new beacon round, then close.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-091 all code-DONE except owner keyed/live VERIFY remainders below; REQ-051 DEFERRED by owner; REQ-082 + REQ-090 DEFERRED until after the hackathon
  - REQ-093 DONE (this run); Phase 2 (P2.S1.1…P2.S7.1) complete
- Tests: full make test green (autobridge 3238 @90%, verifier 144 @91%, simulators 434 @90%, UI 350); lint/typecheck green; stack down (no containers running).
- Stack: down (no live e2e this run; change is bench-harness-only, covered by fakes + end-to-end preflight unit test).

## Next tasks
1. **REQ-093 (owner)** — re-freeze a new beacon round, then close.
2. **REQ-091 (VERIFY)** — owner: keyed rehearsal with FRONTIER_API_KEY, check the `frontier_agent` arm still answers, then close.
3. **REQ-087/078/080/081/083/084/085/086 (VERIFY)** — owner: keyed rehearsal with FRONTIER_API_KEY, report every arm, then close.
4. **REQ-074 (VERIFY)** — owner: browser check of Atlas `34cca645` + Harbor `77853663`; then close.
5. **REQ-071/070/069 (VERIFY)** — owner keyed walkthroughs / record-frontier; real beacon freeze waits for them.
6. **REQ-065/063/062/061/060 (VERIFY)** — owner keyed bench-beacon rehearsal / bench-ablation in the sandbox.
7. **REQ-051** — DEFERRED; do not start until the owner says so.
