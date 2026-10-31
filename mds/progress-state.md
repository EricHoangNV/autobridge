# Progress

## Current State

REQ-066 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): beacon protocol pins the frontier model — freeze() writes beacon-frontier-model (+ beacon-agent-model when the opencode form differs), parse_protocol reads both, run_beacon refuses on mismatch (real run requires the line; older rehearsal protocols still parse and run unpinned with a warning), verify-repro and beacon-eval EVIDENCE.md show the pin, docs/benchmark.md updated.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-066 all DONE except IN_PROGRESS/BLOCKED owner keyed/live remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
- Tests: `make test` python gates 2985 + 138 + 413 passed, autobridge 90% / verifier 91% / simulators 90%; UI 294/295 (1 pre-existing full-suite-only failure in app/repairs/[id]/page.test.tsx, also fails on clean tree); lint/typecheck green. No live stack in sbx1.
- Stack: no live stack in sbx1; `.local/ACTIVE` marker present

## Next tasks
1. **REQ-067 (OPEN)** — owner priority: demo UI walkthrough fixes (10 items, FINDINGS.md + demo-flow §5.2 in tmp/ from main checkout).
2. **REQ-065 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), rehearsal 5 (expected engine 5/12, ~0 frontier tokens except semantic climbs, 0 wrong); then close. Real freeze waits for it.
3. **REQ-063 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), every row gradable, no-drift rows Complete, escalations carry questions; then close.
4. **REQ-062 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), every row gradable with stated reason; then close.
5. **REQ-061 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox, per-scenario table of what each frontier arm produced and why; then close
6. **REQ-060 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox, per-scenario table and why; then close
