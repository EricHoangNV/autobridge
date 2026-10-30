# Progress

## Current State

REQ-058 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): refused build drafts the capability request (Download Markdown, deterministic, no model call); vocabulary-check carries refusal facts; simulator pick resets every field; developers-portal link per pick. REQ-057 DONE (connector spec viewer + fingerprints). REQ-056 DONE (plain scan layer on repair page). REQ-055 DONE (only the owner keyed check remains, scheduled in the overnight run).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-040 all DONE except IN_PROGRESS REQ-021/024/025/027 (remaining bits are owner keyed/live runs)
  - REQ-028 BLOCKED on owner keyed run (see Blocked line in REQUESTS.MD)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-034/035/036/037/038/040/041/042/043/044/045/046/047/048/049/050/053/054/055/056/057/058 DONE (keyed owner verify steps outstanding where noted)
- Tests: autobridge gate 2882 passed / verifier 134 pass 91.49% / simulators 409 pass 90.48% / ui 294 pass; make lint + typecheck + ui-typecheck + openapi-check green; test_10 layout 18/18 + test_06 green (mocked)
- Stack: local stack up in sandbox (make local-up, no FRONTIER_API_KEY)

## Next tasks
1. **REQ-059 (OPEN)** — blank-slate demo (REQ-058 unblocked it: pick-reset fix is in)
2. **REQ-052 (OPEN)** — benchmark quality (run after REQ-059, before the daily validation)
3. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
