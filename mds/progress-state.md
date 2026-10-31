# Progress

## Current State

REQ-060 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): frontier arms get the engine's repair task (pre-drift spec + faulted-sync evidence), patch/escalate symmetric grading, token_ceiling 0 = no limit, live-CLI parsing via session row + fixture, learning-curve live fix, docs rewritten.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-060 all DONE except IN_PROGRESS/BLOCKED owner keyed/live remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
- Tests: `make test` 2928 + 134 + 409 passed, autobridge 90.11% / verifier 91.49% / simulators 90.48%; UI 295 passed; lint/typecheck green. Unkeyed `BENCH_TRIALS=1` ablation rehearsal 12/12 matched, `context_matched: True` on all 5 arms. E2E not runnable in this sandbox (no stack up) — owner runs them.
- Stack: no live stack in sbx1; `.local/ACTIVE` marker present

## Next tasks
1. **REQ-060 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox, per-scenario table of what each frontier arm produced and why; then close
2. **REQ-052 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
3. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
4. **REQ-021/024/025/027 (IN_PROGRESS)** — owner keyed/live runs outstanding (record-frontier capture, live frontier numbers, held-out/beacon run)
