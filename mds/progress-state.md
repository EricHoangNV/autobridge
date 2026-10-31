# Progress

## Current State

REQ-064 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): engine detects both silent-corruption shapes (mapped_field_emptied → SCHEMA_RENAME, money_scale_shift → SEMANTIC_VALUE_DRIFT) and climbs L3 with the l3_patch repair context before escalating (structural proposals become verifier-gated candidates; ambiguity escalates with the question) — live deferral, bench arms and repair jobs all climb; unkeyed behaviour identical on all 12 dev scenarios; docs updated.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-064 all DONE except IN_PROGRESS/BLOCKED owner keyed/live remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
- Tests: `make test` 2984 + 138 + 413 passed, autobridge 90.10% / verifier 91.04% / simulators 90.42%; UI 295 passed; lint/typecheck/ui-typecheck/openapi-check green. No live stack in sbx1.
- Stack: no live stack in sbx1; `.local/ACTIVE` marker present

## Next tasks
1. **REQ-064 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), rehearsal 4; then close. Real freeze waits for it.
2. **REQ-063 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), every row gradable, no-drift rows Complete, escalations carry questions; then close.
3. **REQ-062 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), every row gradable with stated reason; then close.
4. **REQ-061 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox, per-scenario table of what each frontier arm produced and why; then close
5. **REQ-060 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox, per-scenario table and why; then close
6. **REQ-052 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
7. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record the table; then close
8. **REQ-021/024/025 (IN_PROGRESS)** — owner keyed/live runs outstanding (record-frontier capture, live frontier numbers, held-out/beacon run)
