# Progress

## Current State

REQ-063 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): beacon grading fixed — no-drift runs Complete with no patch (ladder not called), L4 escalations carry the ladder's question (Blocked+question passes degraded_question), semantic decoy accepts Complete/no-patch, generator silent no-ops fixed (cutoff selection, format pool, meridian rename exclusions, fias rename>=1) + bench sanity gate failing loudly on every rehearsal.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-063 all DONE except IN_PROGRESS/BLOCKED owner keyed/live remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
- Tests: `make test` 2958 + 138 + 413 passed, autobridge 90.14% / verifier 91.04% / simulators 90.42%; UI 295 passed; lint/typecheck green. No live stack in sbx1.
- Stack: no live stack in sbx1; `.local/ACTIVE` marker present

## Next tasks
1. **REQ-063 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), every row gradable, no-drift rows Complete, escalations carry questions; then close. Real freeze waits for a clean rehearsal.
2. **REQ-062 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), every row gradable with stated reason; then close.
3. **REQ-061 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox, per-scenario table of what each frontier arm produced and why; then close
4. **REQ-060 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox, per-scenario table and why; then close
5. **REQ-052 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
6. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record the table; then close
7. **REQ-021/024/025 (IN_PROGRESS)** — owner keyed/live runs outstanding (record-frontier capture, live frontier numbers, held-out/beacon run)
