# Progress

## Current State

Daily Directives Audit Cycle 6 DONE in sbx1 (Agent: opencode meta/muse-wonder-1.0 (escalation)): audited `d2d9c9d2..8f1b2dca` (89 commits, REQ-040…REQ-059) against DIRECTIVES §§1–8 — PASS (Code Review Cycle 6). One finding fixed by the audit: `bench/stack_mode.py`'s readyz probe resolved `localhost` via DNS, so a stale `.local/ACTIVE` marker made `make test` fail with 5 teardown errors; the probe now uses the literal `127.0.0.1` and the affected files pass with the marker planted.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-059 all DONE except IN_PROGRESS/BLOCKED owner keyed/live remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
- Tests: `make test` 2903 passed, autobridge 90.10% (thin margin over the 90% gate); UI 295 passed; lint/typecheck/ui-typecheck/openapi-check green. Live smoke/e2e not runnable in this sandbox (no stack up) — owner runs them.
- Stack: no live stack in sbx1; stale `.local/ACTIVE` marker present (now harmless for unit tests after the probe fix)

## Next tasks
1. **REQ-052 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox, per-scenario table of what each frontier arm produced and why; then close
2. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
3. **REQ-021/024/025/027 (IN_PROGRESS)** — owner keyed/live runs outstanding (record-frontier capture, live frontier numbers, held-out/beacon run)
