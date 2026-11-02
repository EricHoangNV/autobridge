# Progress

## Current State

REQ-083 code-done in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): all 19 items (harness self-test + preflight, A2 reference-repair proofs, A3 one judging regime, A4 wiring, honest judge/scoring/docs, date-only calendar dates + rename evidence + L2 dry-run gate, projected-wire nesting + meridian format exclusion + IP fictionalization). Owner keyed rehearsal next.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-083 all code-DONE except owner keyed/live VERIFY remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
  - REQ-078/080/081 follow-ups (reopened 2026-11-02): code-done; owner step: keyed rehearsal on this commit, then close
- Tests: full make test green (autobridge 3208 @90.03%, verifier 139 @90.35%, simulators 429 @90.09%, UI 350); lint/typecheck green; e2e test_01/02/03/04/05/fias/meridian/knowledge green on live stack.
- Stack: down (torn down after the e2e run; no live stack in this sandbox by default).

## Next tasks
1. **REQ-083 + REQ-081 + REQ-078 + REQ-080 (VERIFY)** — owner: keyed rehearsal with FRONTIER_API_KEY on this commit, report every arm, then close all four.
2. **REQ-074 (VERIFY)** — owner: browser check of Atlas `34cca645` + Harbor `77853663`; then close.
3. **REQ-071/070/069 (VERIFY)** — owner keyed walkthroughs / record-frontier; real beacon freeze waits for them.
4. **REQ-065/063/062/061/060 (VERIFY)** — owner keyed bench-beacon rehearsal / bench-ablation in the sandbox.
5. **REQ-051** — DEFERRED; do not start until the owner says so.
