# Progress

## Current State

REQ-056 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): plain scan layer on the repair page (lookup tables, chapter plain headlines + closed tech toggles, ladder chips, always-on dry-run line, rail plain line). REQ-055 DONE (only the owner keyed check remains, scheduled in the overnight run).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-040 all DONE except IN_PROGRESS REQ-021/024/025/027 (remaining bits are owner keyed/live runs)
  - REQ-028 BLOCKED on owner keyed run (see Blocked line in REQUESTS.MD)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-034/035/036/037/038/040/041/042/043/044/045/046/047/048/049/050/053/054/055/056 DONE (keyed owner verify steps outstanding where noted)
- Tests: autobridge gate 2880 passed 90.09% / verifier 134 pass 91.49% / simulators 409 pass 90.48% / ui 266 pass; make lint + typecheck + ui-typecheck + openapi-check green; make demo 8/8 unkeyed twice after reset; test_05 24 passed live; test_06 journey + s6 + layout (16/16) Playwright green (mocked and live-branch)
- Stack: local stack up in sandbox (make local-up, no FRONTIER_API_KEY)

## Next tasks
1. **REQ-057 (OPEN)** — connector spec viewer + grouped fingerprints + hash tooltips (owner order: 057 → 058 → 059 → 052)
2. **REQ-058 (OPEN)** — refused build → Download capability request + simulator-pick reset + docs link
3. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
