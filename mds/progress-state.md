# Progress

## Current State

REQ-081 code-done in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): all 7 rehearsal-gap items fixed (semantic signal-seeded evidence, equal baseline, validated patches, date input_format, meridian/atlas nesting on wire, multi-path L3, agent permission + re-prompt). Owner keyed rehearsal next.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-081 all code-DONE except owner keyed/live VERIFY remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
  - REQ-078 (reopened 2026-11-02): PWD fix code-done; owner step: keyed rehearsal on the result, then close
  - REQ-080 follow-up (reopened 2026-11-02): engine no longer copies the answer key; distant renames climb; owner step: keyed rehearsal on this commit, then close
  - REQ-081 (2026-11-02): 7 items code-done; owner step: keyed rehearsal on this commit, then close
- Tests: full make test green (incl. UI 350); lint/typecheck green.
- Stack: down (no live stack in this sandbox; live rehearsal not runnable, not faked).

## Next tasks
1. **REQ-081 + REQ-078 + REQ-080 (VERIFY)** — owner: keyed rehearsal with FRONTIER_API_KEY on this commit, report every arm, then close all three.
2. **REQ-074 (VERIFY)** — owner: browser check of Atlas `34cca645` + Harbor `77853663`; then close.
3. **REQ-071/070/069 (VERIFY)** — owner keyed walkthroughs / record-frontier; real beacon freeze waits for them.
4. **REQ-065/063/062/061/060 (VERIFY)** — owner keyed bench-beacon rehearsal / bench-ablation in the sandbox.
5. **REQ-051** — DEFERRED; do not start until the owner says so.
