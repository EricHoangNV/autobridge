# Progress

## Current State

REQ-072 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): restart with an in-flight sync no longer wedges the connector (orphaned sync/build fail with reasons at startup, repairs re-queued for the drain; stale-job bound unblocks the scheduler; builds claim exactly their job); full gates green.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-072 all DONE except IN_PROGRESS/BLOCKED owner keyed/live remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
- Tests: `make test` 3006 + 138 + 415 passed, autobridge/verifier/simulators gates green; UI 324 passed; lint/typecheck/contracts/openapi-check green. No live stack in sbx1.
- Stack: no live stack in sbx1; `.local/ACTIVE` marker present but all pids dead (stale)

## Next tasks
1. **REQ-071 (VERIFY)** — owner: keyed walkthrough of deck 7 (drop visible in Sync runs after the fact); then close. Real beacon freeze waits for it.
2. **REQ-070 (VERIFY)** — owner: keyed walkthrough re-run (deck 5 must reach Approve → r2 → verifying sync → ● Working); optional keyed `make record-frontier SCENARIO=gross_to_net`. Real beacon freeze waits for it.
3. **REQ-069 (VERIFY)** — owner: keyed `make record-frontier SCENARIO=gross_to_net` (l3_patch v2->v3 changed the prompt hash), then rerun the keyed browser walkthrough to close. Real beacon freeze waits for it.
4. **REQ-065 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), rehearsal 5; then close. Real freeze waits for it.
5. **REQ-063 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key); then close.
6. **REQ-062 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key); then close.
7. **REQ-061 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox; then close
8. **REQ-060 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox; then close
