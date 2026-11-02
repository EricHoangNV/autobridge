# Progress

## Current State

REQ-080 follow-up code-done in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): answer-key vocabulary removed from the engine; L1 repairs inferred renames only on a generic vocabulary-free test (exactly-one key pair, value shape, lexical closeness >= 0.75). Owner keyed rehearsal next.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-080 all code-DONE except owner keyed/live VERIFY remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
  - REQ-078 (reopened 2026-11-02): PWD fix code-done; owner step: keyed rehearsal on the result, then close
  - REQ-080 follow-up (reopened 2026-11-02, done this run): engine no longer copies the answer key; distant renames climb; owner step: keyed rehearsal on this commit, then close
- Tests: 3109+139+422 pass / 0 fail (autobridge 90%, verifier 91%, simulators 91%, UI 350); lint/typecheck green.
- Stack: down (no live stack in this sandbox; live rehearsal not runnable, not faked).

## Next tasks
1. **REQ-078 + REQ-080 (VERIFY)** — owner: keyed rehearsal with FRONTIER_API_KEY on this commit, report every arm, then close both.
2. **REQ-074 (VERIFY)** — owner: browser check of Atlas `34cca645` + Harbor `77853663`; then close.
3. **REQ-071/070/069 (VERIFY)** — owner keyed walkthroughs / record-frontier; real beacon freeze waits for them.
4. **REQ-065/063/062/061/060 (VERIFY)** — owner keyed bench-beacon rehearsal / bench-ablation in the sandbox.
5. **REQ-051** — DEFERRED; do not start until the owner says so.
