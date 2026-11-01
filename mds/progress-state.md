# Progress

## Current State

REQ-068 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): all 8 round-2 findings code-done — simulator net_sum under gross_to_net (honest Approve path) + replay re-keyed + docs §4; L3 entity-first raw records; candidate max+1; L0 learned wording; vv fix; connectors layout + Playwright; PABX plain state/filtered postings/heartbeat-on-flip; small cuts (table gutter, release separator, decision prefix, skipped L2, live-since).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-068 all DONE except IN_PROGRESS/BLOCKED owner keyed/live remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
- Tests: `make test` python gates 2987 + 138 + 415 passed, autobridge 90.05% / verifier 91.04% / simulators 90.50%; UI 312 passed; lint/typecheck/openapi-check green. No live stack in sbx1.
- Stack: no live stack in sbx1; `.local/ACTIVE` marker present

## Next tasks
1. **REQ-068 (VERIFY)** — owner: keyed `make record-frontier SCENARIO=gross_to_net` (L3 prompt hash changed with entity-first records), then rerun the keyed browser walkthrough to close. Real beacon freeze waits for it.
2. **REQ-065 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), rehearsal 5 (expected engine 5/12, ~0 frontier tokens except semantic climbs, 0 wrong); then close. Real freeze waits for it.
3. **REQ-063 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), every row gradable, no-drift rows Complete, escalations carry questions; then close.
4. **REQ-062 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), every row gradable with stated reason; then close.
5. **REQ-061 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox, per-scenario table of what each frontier arm produced and why; then close
6. **REQ-060 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox, per-scenario table and why; then close
