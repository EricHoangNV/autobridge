# Progress

## Current State

REQ-050 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): seed is AtlasPMS only; Meridian builds live (v1 refused with reason, v2 connects, both attempts on its page); the PABX builds live from its spec (no base-URL typing, no runtime choice, engine decides edge); deck 7 break is `fias_link_resync` proved on relay idempotency keys with no repair.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-040 all DONE except IN_PROGRESS REQ-021/024/025/027 (remaining bits are owner keyed/live runs)
  - REQ-028 BLOCKED on owner keyed run (see Blocked line in REQUESTS.MD)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-034/035/036/037/038/040/041/042/043/044/045/046/047/048/049/050 DONE (keyed owner verify steps outstanding where noted)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
  - local-up.sh pre-migrates the DB once (fixes fresh-boot "malformed" scheduler_state on shared filesystems)
- Tests: REQ-050 gates green — make test all green incl. 90% per-package gates, ui 231 pass; make lint + typecheck + ui-typecheck + openapi-check green; make demo 8/8 unkeyed on local stack + STEP=7/8 alone; tests/e2e/test_09 5/5 live; e2e-ui blocked by pre-existing sandbox module-loader failure (UI covered by vitest + live demo)
- Stack: local stack up in sandbox (make local-up, no FRONTIER_API_KEY); owner Aside run of decks 6 and 7 is the check

## Next tasks
1. **REQ-050 (VERIFY)** — owner: Aside run of decks 6 (v1 refused → v2 connects) and 7 (PABX live build + link-drop resync), with screenshots
2. **REQ-049 (VERIFY)** — owner keyed: deck 4→5 three times (valid-proposal rate + labels), Aside run with screenshots, `make record-frontier SCENARIO=gross_to_net`
3. **REQ-048 (VERIFY)** — owner: the same Aside run (demo-break gross_to_net/harbor), with screenshots of the repair page top, Connectors header, and Repairs list during the call
4. **REQ-047 (VERIFY)** — owner: keyed step 4 + screenshots of the repair page
5. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
6. **REQ-046 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` for live frontier numbers (re-record replay captures for the new prompt hashes first); then close
7. **REQ-045 (VERIFY)** — owner: keyed step-4 check + `make record-frontier SCENARIO=gross_to_net` re-capture
8. **REQ-044 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` with FRONTIER_API_KEY, then unkeyed
9. **REQ-043 (VERIFY)** — owner: keyed step-4 live watch + L3-with-131072 report
10. **REQ-041 (VERIFY)** — owner: keyed `make e2e-ui` on Compose
11. **REQ-042 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` with FRONTIER_API_KEY, then unkeyed
12. **REQ-027 (VERIFY)** — owner: keyed-stack run (make demo 8/8 twice, L3 reasoning line, /knowledge state, UI spec)
13. **REQ-021 remaining (owner step)** — record-frontier capture + live step-4 check
14. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; Nov 3 held-out run
