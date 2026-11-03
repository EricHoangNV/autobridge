# Progress

## Current State

REQ-088 (AR-7-1 frontier_agent sandbox confinement) code-DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): deny-all permission config shared between sandbox file + OPENCODE_PERMISSION env, allowlist child env, preflight proofs; full gates green. Owner step: keyed rehearsal, then close.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-088 all code-DONE except owner keyed/live VERIFY remainders below; REQ-051 DEFERRED by owner; REQ-082 DEFERRED until after the hackathon
  - Phase 2 (P2.S1.1…P2.S7.1) complete
- Tests: full make test green (autobridge 3231 @90%, verifier 144 @91%, simulators 434 @90%, UI 350); lint/typecheck green; smoke unit leg green, live leg + e2e not runnable in sandbox (no stack up).
- Stack: down (no live stack in this sandbox by default).

## Next tasks
1. **REQ-089 (AR-7-2)** — control-plane writes open when the token is empty; owner priority next.
2. **REQ-087/078/080/081/083/084/085/086 (VERIFY)** — owner: keyed rehearsal with FRONTIER_API_KEY, report every arm, then close.
3. **REQ-074 (VERIFY)** — owner: browser check of Atlas `34cca645` + Harbor `77853663`; then close.
4. **REQ-071/070/069 (VERIFY)** — owner keyed walkthroughs / record-frontier; real beacon freeze waits for them.
5. **REQ-065/063/062/061/060 (VERIFY)** — owner keyed bench-beacon rehearsal / bench-ablation in the sandbox.
6. **REQ-051** — DEFERRED; do not start until the owner says so.
