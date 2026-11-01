# Progress

## Current State

REQ-069 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): all 6 round-3 findings code-done — full-surface dry-run/verifier gates + l3_patch v3 gate fact + replay re-keyed + docs; candidate-version Approve label; header follows active release; post-rollback page names check + question open; Knowledge rollback wording; PABX readable layout.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-069 all DONE except IN_PROGRESS/BLOCKED owner keyed/live remainders below; REQ-051 DEFERRED by owner
  - Phase 2 (P2.S1.1…P2.S7.1) complete
- Tests: `make test` 2989 + 138 + 415 passed, autobridge 90.06% / verifier 91.04% / simulators 90.50%; UI 317 passed; lint/typecheck/ui-typecheck/openapi-check green. No live stack in sbx1.
- Stack: no live stack in sbx1; `.local/ACTIVE` marker present but all pids dead (stale)

## Next tasks
1. **REQ-069 (VERIFY)** — owner: keyed `make record-frontier SCENARIO=gross_to_net` (l3_patch v2->v3 changed the prompt hash), then rerun the keyed browser walkthrough to close. Real beacon freeze waits for it.
2. **REQ-065 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), rehearsal 5 — exercises the stricter REQ-069 gate (expect engine 5/12, ~0 frontier tokens except semantic climbs, 0 wrong; partial proposals now escalate instead of releasing); then close. Real freeze waits for it.
3. **REQ-063 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), every row gradable, no-drift rows Complete, escalations carry questions; then close.
4. **REQ-062 (VERIFY)** — owner: keyed `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox (needs live stack + key), every row gradable with stated reason; then close.
5. **REQ-061 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox, per-scenario table of what each frontier arm produced and why (also confirms REQ-069 dev numbers); then close
6. **REQ-060 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox, per-scenario table and why; then close
