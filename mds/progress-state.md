# Progress

## Current State

REQ-055 items 1, 2, 3(a)-(d) DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): migrated-DB approve fix (0015 + CHECK-aware detection + per-revision approve matrix), honest dry-run (non-money repoint fails, retry carries the message, field notes next to the change), repair-page fixes (no Approve wording while climbing, chapter 3 shows tested proposal, verifier tile shows dry-run, rail wraps). Items 3(e)(f), 4, 5 stay OPEN.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-040 all DONE except IN_PROGRESS REQ-021/024/025/027 (remaining bits are owner keyed/live runs)
  - REQ-028 BLOCKED on owner keyed run (see Blocked line in REQUESTS.MD)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-034/035/036/037/038/040/041/042/043/044/045/046/047/048/049/050/053/054 DONE (keyed owner verify steps outstanding where noted)
  - REQ-055 items 1, 2, 3(a)-(d) DONE (items 3(e)(f), 4, 5 OPEN for next loop)
- Tests: make test green — autobridge 90.03%, verifier 91.49%, simulators 90.48%, ui 253 pass; make lint + typecheck + ui-typecheck + openapi-check green; make demo 8/8 unkeyed on local stack; tests/e2e test_06 8/8 live (sandbox stale-marker DNS teardown errors gone after removing the dead .local/ACTIVE)
- Stack: local stack up in sandbox (make local-up, no FRONTIER_API_KEY)

## Next tasks
1. **REQ-055 items 3(e)(f), 4, 5 (OPEN)** — pinned rail, no-overlap layout check, dry-run gate in UI + retry field meanings, no-$ UI
2. **REQ-055 (VERIFY)** — owner keyed: decks 4 → 5 break → tested proposal whose label matches what it does → Approve → verifying sync Complete → Harbor ● Working
3. **REQ-056/057/058 (OPEN)** — scannable repair page, connector spec viewer, capability-request download
4. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
