# Progress

## Current State

REQ-084 code-done in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): meridian nesting reachable (customers unwrap + derived-endpoint audit), preflight proves every drawn repair, per-instance redacted L3 traces. Owner keyed rehearsal after REQ-085.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-084 all code-DONE except owner keyed/live VERIFY remainders below; REQ-051 DEFERRED by owner; REQ-082 DEFERRED until after the hackathon
  - Phase 2 (P2.S1.1…P2.S7.1) complete
  - REQ-078/080/081/083 follow-ups (reopened 2026-11-02): code-done; owner step: keyed rehearsal on this commit, then close
- Tests: full make test green (autobridge 3216 @90.03%, verifier 139 @90.35%, simulators 433 @90.18%, UI 350); lint/typecheck green; e2e not run in sandbox (no live stack; in-process coverage instead).
- Stack: down (no live stack in this sandbox by default).

## Next tasks
1. **REQ-085** — owner priority next (one REQ per iteration). Engine L3 evidence shows transform steps; pointers allow add-arg + join repoint.
2. **REQ-084 + REQ-083 + REQ-081 + REQ-078 + REQ-080 (VERIFY)** — owner: keyed rehearsal with FRONTIER_API_KEY after REQ-085, report every arm, then close.
3. **REQ-074 (VERIFY)** — owner: browser check of Atlas `34cca645` + Harbor `77853663`; then close.
4. **REQ-071/070/069 (VERIFY)** — owner keyed walkthroughs / record-frontier; real beacon freeze waits for them.
5. **REQ-065/063/062/061/060 (VERIFY)** — owner keyed bench-beacon rehearsal / bench-ablation in the sandbox.
6. **REQ-051** — DEFERRED; do not start until the owner says so.
