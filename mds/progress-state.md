# Progress

## Current State

REQ-049 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): Harbor built from its wiki carries the totals check; the model proposes "accept the vendor's change"; Approve/Deny on the repair page; business-decision labels; Knowledge Decisions section.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-040 all DONE except IN_PROGRESS REQ-021/024/025/027 (remaining bits are owner keyed/live runs)
  - REQ-028 BLOCKED on owner keyed run (see Blocked line in REQUESTS.MD)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-034/035/036/037/038/040/041/042/043/044/045/046/047/048/049 DONE (keyed owner verify steps outstanding where noted)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
  - local-up.sh pre-migrates the DB once (fixes fresh-boot "malformed" scheduler_state on shared filesystems)
- Tests: REQ-049 gates green — make test full pass (autobridge/verifier/simulators ≥90%), ui 228 pass; make lint + typecheck + ui-typecheck green; openapi regen committed; make demo 8/8 unkeyed on local stack (twice + STEP=4 alone); test_09 e2e 5/5 live; replay fixture re-keyed for l3_patch v2 hashes
- Stack: local stack up in sandbox (make local-up, no FRONTIER_API_KEY); keyed deck 4→5 x3 + Aside Green→Red→Approve→Green + record-frontier re-capture are the owner check

## Next tasks
1. **REQ-050 (OPEN)** — deck 6 builds Meridian live (v1 refused, v2 connects), deck 7 PABX live from spec; seed drops Meridian
2. **REQ-049 (VERIFY)** — owner keyed: deck 4→5 three times (valid-proposal rate + labels), Aside run with screenshots, `make record-frontier SCENARIO=gross_to_net`
3. **REQ-048 (VERIFY)** — owner: the same Aside run (demo-break gross_to_net/harbor), with screenshots of the repair page top, Connectors header, and Repairs list during the call
4. **REQ-047 (VERIFY)** — owner: keyed step 4 + screenshots of the repair page
5. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
6. **REQ-046 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` for live frontier numbers (re-record replay captures for the new prompt hashes first); then close
7. **REQ-045 (VERIFY)** — owner: keyed step-4 check + `make record-frontier SCENARIO=gross_to_net` re-capture
8. **REQ-044 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make demo` with FRONTIER_API_KEY; report tokens + answer
9. **REQ-043 (VERIFY)** — owner: keyed step-4 live watch + L3-with-131072 report
10. **REQ-041 (VERIFY)** — owner: keyed `make e2e-ui` on Compose
11. **REQ-042 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` with FRONTIER_API_KEY, then unkeyed
12. **REQ-027 (VERIFY)** — owner: keyed-stack run (make demo 8/8 twice, L3 reasoning line, /knowledge state, UI spec)
13. **REQ-021 remaining (owner step)** — record-frontier capture + live step-4 check
14. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; Nov 3 held-out run
