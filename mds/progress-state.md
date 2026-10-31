# Progress

## Current State

REQ-061 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): frontier agent arm runs the same derived model (`meta/<FRONTIER_MODEL>`, always `-m`), stdin closed on all CLI drivers, exit-1 reason from stdout error events + tail, live preflight gating agent rows.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-061 all DONE except IN_PROGRESS/BLOCKED owner keyed/live remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
- Tests: `make test` 2940 + 134 + 409 passed, autobridge 90.11% / verifier 91.49% / simulators 90.48%; UI 295 passed; lint/typecheck green. Live agent check: preflight ok (17878 tokens) + page_to_cursor row ran=True with real patch.
- Stack: no live stack in sbx1; `.local/ACTIVE` marker present

## Next tasks
1. **REQ-061 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox, per-scenario table of what each frontier arm produced and why; then close
2. **REQ-060 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox, per-scenario table and why; then close
3. **REQ-052 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
4. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record the table; then close
5. **REQ-021/024/025 (IN_PROGRESS)** — owner keyed/live runs outstanding (record-frontier capture, live frontier numbers, held-out/beacon run)
