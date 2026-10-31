# Progress

## Current State

REQ-067 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): all 10 walkthrough findings fixed — decision-box question wraps, L3 prompts v2 ask for the proposal (fixture re-keyed), names shrink at word boundaries, approved reads shipped/rollback numbers, build copy + r1 automatic label, in-flight counter wording, PABX per-agent postings + resync burst, sync-paused badges, §5.2 wording; docs/demo.md deck-3 presenter steps.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-067 all DONE except IN_PROGRESS/BLOCKED owner keyed/live remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
- Tests: `make test` python gates 2986 + 138 + 413 passed, autobridge 90.11% / verifier 91.04% / simulators 90.43%; UI 303 passed; test_10 22/22 + test_06 7 passed mocked; lint/typecheck green. No live stack in sbx1.
- Stack: no live stack in sbx1; `.local/ACTIVE` marker present

## Next tasks
1. **REQ-067 (VERIFY)** — owner: re-run `make record-frontier SCENARIO=gross_to_net` (prompt hash changed), then rerun the keyed browser walkthrough to close.
2. **REQ-065 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), rehearsal 5 (expected engine 5/12, ~0 frontier tokens except semantic climbs, 0 wrong); then close. Real freeze waits for it.
3. **REQ-063 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), every row gradable, no-drift rows Complete, escalations carry questions; then close.
4. **REQ-062 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), every row gradable with stated reason; then close.
5. **REQ-061 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox, per-scenario table of what each frontier arm produced and why; then close
6. **REQ-060 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox, per-scenario table and why; then close
