# Progress

## Current State

REQ-079 done in sbx1 (Agent: opencode meta/muse-spark-1.3 (escalation)): two cross-test leaks fixed, full `make test` green with no teardown trips.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-079 all code-DONE except owner keyed/live VERIFY remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
  - REQ-079: scheduler-loop shutdown joins in-flight pass; run_beacon placeholder env save/restored; raw os.environ sets now monkeypatch
- Tests: 3069 pass / 0 fail (autobridge 90.05%, verifier 139 @90.80%, simulators 422 @90.48%, UI 350); lint/typecheck/ui-typecheck/openapi-check green.
- Stack: down (no live stack in this sandbox; live legs reported not runnable, not faked).

## Next tasks
1. **REQ-078 (VERIFY)** — owner: keyed rehearsal with FRONTIER_API_KEY + real beacon freeze/run.
2. **REQ-080** — renamed field as model evidence; misleading rename expects a question (open, owner priority after REQ-078).
3. **REQ-074 (VERIFY)** — owner: browser check of Atlas `34cca645` + Harbor `77853663`; then close.
4. **REQ-071/070/069 (VERIFY)** — owner keyed walkthroughs / record-frontier; real beacon freeze waits for them.
5. **REQ-065/063/062/061/060 (VERIFY)** — owner keyed bench-beacon rehearsal / bench-ablation in the sandbox.
6. **REQ-051** — DEFERRED; do not start until the owner says so.
