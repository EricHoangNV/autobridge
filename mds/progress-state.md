# Progress

## Current State

REQ-085 code-done in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): L3 evidence renders each field's transform steps (name + args) next to its path(s) plus entity joins; add-arg/replace-arg and join-repoint patches pass the pointer gate. Owner keyed rehearsal next.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-085 all code-DONE except owner keyed/live VERIFY remainders below; REQ-051 DEFERRED by owner; REQ-082 DEFERRED until after the hackathon
  - Phase 2 (P2.S1.1…P2.S7.1) complete
  - REQ-078/080/081/083/084/085 follow-ups (reopened 2026-11-02): code-done; owner step: keyed rehearsal on this commit, then close
- Tests: full make test green (autobridge/verifier/simulators ≥90% gates, UI 350); lint/typecheck green; e2e not run in sandbox (no live stack; in-process coverage instead).
- Stack: down (no live stack in this sandbox by default).

## Next tasks
1. **REQ-078/080/081/083/084/085 (VERIFY)** — owner: keyed rehearsal with FRONTIER_API_KEY, report every arm, then close.
2. **REQ-074 (VERIFY)** — owner: browser check of Atlas `34cca645` + Harbor `77853663`; then close.
3. **REQ-071/070/069 (VERIFY)** — owner keyed walkthroughs / record-frontier; real beacon freeze waits for them.
4. **REQ-065/063/062/061/060 (VERIFY)** — owner keyed bench-beacon rehearsal / bench-ablation in the sandbox.
5. **REQ-051** — DEFERRED; do not start until the owner says so.
