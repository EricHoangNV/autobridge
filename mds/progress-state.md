# Progress

## Current State

REQ-053 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): all 10 items code-complete — UI build path fixed (vocabulary check-first, simulator credential/entities suggestions, probe_wire_shape), approved repairs terminal (one waiting source), every proposal dry-run (retry once, then question-only), auto-rollback on failed verifying sync with no second escalation.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-040 all DONE except IN_PROGRESS REQ-021/024/025/027 (remaining bits are owner keyed/live runs)
  - REQ-028 BLOCKED on owner keyed run (see Blocked line in REQUESTS.MD)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-034/035/036/037/038/040/041/042/043/044/045/046/047/048/049/050 DONE (keyed owner verify steps outstanding where noted)
  - REQ-053 DONE (keyed owner steps outstanding: deck-5 Approve-to-green, Aside screenshots)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
  - local-up.sh pre-migrates the DB once (fixes fresh-boot "malformed" scheduler_state on shared filesystems)
- Tests: REQ-053 gates green — make test all green incl. 90% per-package gates, ui 237 pass; make lint + typecheck + ui-typecheck + openapi-check green; make demo 8/8 unkeyed on local stack; BENCH_TRIALS=1 rehearsal engine arms unchanged (cold/warm 6/6 + 6/6 held, 0 wrong); tests/e2e test_09 5/5, matrix + 03 + 05 + knowledge + 027 + meridian/fias suites green live; e2e-ui blocked by pre-existing sandbox module-loader failure (UI covered by vitest + live demo)
- Stack: local stack up in sandbox (make local-up, no FRONTIER_API_KEY); owner Aside run of decks 6 and 7 is the check

## Next tasks
1. **REQ-054 (NEXT per owner order)** — repair page rearranged around the story (UI only, no new API)
2. **REQ-053 (VERIFY)** — owner keyed: deck 5 break → Approve the model's tested proposal → Green; Aside screenshots decks 3/5
3. **REQ-049 (VERIFY)** — owner keyed: deck 4→5 three times (valid-proposal rate + labels), Aside run, `make record-frontier SCENARIO=gross_to_net`
4. **REQ-048 (VERIFY)** — owner: Aside run (demo-break gross_to_net/harbor), screenshots of repair page top, Connectors header, Repairs list
5. **REQ-047 (VERIFY)** — owner: keyed step 4 + screenshots of the repair page
6. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
7. **REQ-046 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` for live frontier numbers (re-record replay captures first); then close
8. **REQ-045 (VERIFY)** — owner: keyed step-4 check + `make record-frontier SCENARIO=gross_to_net` re-capture
9. **REQ-044 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` with FRONTIER_API_KEY, then unkeyed
10. **REQ-043 (VERIFY)** — owner: keyed step-4 live watch + L3-with-131072 report
11. **REQ-041 (VERIFY)** — owner: keyed `make e2e-ui` on Compose
12. **REQ-042 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` with FRONTIER_API_KEY, then unkeyed
