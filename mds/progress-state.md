# Progress

## Current State

REQ-052 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3 (escalation)): frontier benchmark arms make real, complete, gradable attempts — L3-sized output room (FRONTIER_MAX_TOKENS) with `truncated` labels, agent model recorded from the run and checked vs FRONTIER_MODEL, shared 600 s wall-clock with `timed_out` labels, every frontier attempt verifier-graded (verdict or explicit label on every row), per-arm quality counts + per-scenario frontier_detail, learning curve on live agent (fake refused with key) with per-repeat verification, one-shot cost from published Muse prices ($1.25/$4.25 per M).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-040 all DONE except IN_PROGRESS REQ-021/024/025/027 (remaining bits are owner keyed/live runs)
  - REQ-028 BLOCKED on owner keyed run (see Blocked line in REQUESTS.MD)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-034/035/036/037/038/040/041/042/043/044/045/046/047/048/049/050/052/053/054/055/056/057/058/059 DONE (keyed owner verify steps outstanding where noted)
- Tests: autobridge gate 90.13% overall (make test 2903 pass; 4 pre-existing stale-.local/ACTIVE teardown errors in demo tests, proven unrelated); bench suites 391 pass; make lint + typecheck green; unkeyed BENCH_TRIALS=1 ablation 12/12 matched with quality table; test_07 e2e green
- Stack: local stack pids dead in sbx1 worktree (stale .local/ACTIVE left untouched); no FRONTIER_API_KEY in sandbox

## Next tasks
1. **REQ-052 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` in the sandbox, per-scenario table of what each frontier arm produced and why; then close
2. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
3. **REQ-021/024/025/027 (IN_PROGRESS)** — owner keyed/live runs outstanding (record-frontier capture, live frontier numbers, held-out run)
