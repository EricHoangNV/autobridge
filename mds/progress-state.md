# Progress

## Current State

REQ-078 PWD remainder done in sbx1 (Agent: opencode meta/muse-spark-1.3 (escalation)): child `PWD` pinned to the sandbox dir in all three agent CLI drivers; keyed rehearsal is the owner step.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-079 all code-DONE except owner keyed/live VERIFY remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
  - REQ-078 (reopened 2026-11-02): PWD fix code-done (this run); owner step: keyed rehearsal on the result, then close
- Tests: 3072+139+422 pass / 0 fail (autobridge 90.03%, verifier 90.80%, simulators 90.48%, UI 350); lint/typecheck green.
- Stack: down (no live stack in this sandbox; live rehearsal not runnable, not faked).

## Next tasks
1. **REQ-078 (VERIFY)** — owner: keyed rehearsal with FRONTIER_API_KEY on this commit, report every arm, then close.
2. **REQ-080** — renamed field as model evidence; misleading rename expects a question (open, owner priority after REQ-078).
3. **REQ-074 (VERIFY)** — owner: browser check of Atlas `34cca645` + Harbor `77853663`; then close.
4. **REQ-071/070/069 (VERIFY)** — owner keyed walkthroughs / record-frontier; real beacon freeze waits for them.
5. **REQ-065/063/062/061/060 (VERIFY)** — owner keyed bench-beacon rehearsal / bench-ablation in the sandbox.
6. **REQ-051** — DEFERRED; do not start until the owner says so.
