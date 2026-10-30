# Progress

## Current State

REQ-054 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): repair page rearranged around the story — headline + storyline 1→2→3, 3 chapters, sticky decision rail per state (propose/climbing/escalated±proposal/terminal), Now→Proposed op tables with Now from the active spec, fact-once, reduced-motion signature. UI only, no new API.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-040 all DONE except IN_PROGRESS REQ-021/024/025/027 (remaining bits are owner keyed/live runs)
  - REQ-028 BLOCKED on owner keyed run (see Blocked line in REQUESTS.MD)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-034/035/036/037/038/040/041/042/043/044/045/046/047/048/049/050/053/054 DONE (keyed owner verify steps outstanding where noted)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
  - local-up.sh pre-migrates the DB once (fixes fresh-boot "malformed" scheduler_state on shared filesystems)
- Tests: REQ-054 gates green — make test all green incl. 90% per-package gates, ui 244 pass; make lint + typecheck + ui-typecheck + openapi-check green; make demo 8/8 unkeyed on local stack; tests/e2e test_06 8/8 live; e2e-ui full runner blocked by pre-existing sandbox module-loader failure (UI covered by vitest + live test_06)
- Stack: local stack up in sandbox (make local-up, no FRONTIER_API_KEY); owner Aside run of decks 3 + 5 screenshots is the check

## Next tasks
1. **REQ-054 (VERIFY)** — owner: Aside screenshots at 1440 of deck 3 (propose → approved) and deck 5 (climbing → escalated with proposal → approved)
2. **REQ-053 (VERIFY)** — owner keyed: deck 5 break → Approve the model's tested proposal → Green; Aside screenshots decks 3/5
3. **REQ-049 (VERIFY)** — owner keyed: deck 4→5 three times (valid-proposal rate + labels), Aside run, `make record-frontier SCENARIO=gross_to_net`
4. **REQ-048 (VERIFY)** — owner: Aside run (demo-break gross_to_net/harbor), screenshots of repair page top, Connectors header, Repairs list
5. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
