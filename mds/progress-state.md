# Progress

## Current State

REQ-048 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): escalation reads as a question — top box, single question, header count, in-flight calls, budget = L3 cap, docs rewritten.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-040 all DONE except IN_PROGRESS REQ-021/024/025/027 (remaining bits are owner keyed/live runs)
  - REQ-028 BLOCKED on owner keyed run (see Blocked line in REQUESTS.MD)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-034/035/036/037/038/040/041/042/043/044/045/046/047/048 DONE (keyed owner verify steps outstanding where noted)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
  - local-up.sh pre-migrates the DB once (fixes fresh-boot "malformed" scheduler_state on shared filesystems)
- Tests: REQ-048 gates green — make test full pass (autobridge 90.17%, verifier 91.49%, simulators 90.48%, ui 219 pass); make lint + typecheck + ui-typecheck green; openapi regen committed; make demo 8/8 unkeyed on local stack; live demo-break verified row/ledger frontier_budget=131072, Harbor error/needs-decision
- Stack: local stack up in sandbox (make local-up, no FRONTIER_API_KEY); keyed Aside run + screenshots are the owner check

## Next tasks
1. **REQ-048 (VERIFY)** — owner: the same Aside run (demo-break gross_to_net/harbor), with screenshots of the repair page top, Connectors header, and Repairs list during the call
2. **REQ-047 (VERIFY)** — owner: keyed step 4 + screenshots of the repair page
3. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
4. **REQ-046 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` for live frontier numbers (re-record replay captures for the new prompt hashes first); then close
5. **REQ-045 (VERIFY)** — owner: keyed step-4 check + `make record-frontier SCENARIO=gross_to_net` re-capture
6. **REQ-044 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make demo` with FRONTIER_API_KEY; report tokens + answer
7. **REQ-043 (VERIFY)** — owner: keyed step-4 live watch + L3-with-131072 report
8. **REQ-041 (VERIFY)** — owner: keyed `make e2e-ui` on Compose
9. **REQ-042 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` with FRONTIER_API_KEY, then unkeyed
10. **REQ-027 (VERIFY)** — owner: keyed-stack run (make demo 8/8 twice, L3 reasoning line, /knowledge state, UI spec)
11. **REQ-021 remaining (owner step)** — record-frontier capture + live step-4 check
12. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; Nov 3 held-out run
13. **REQ-025 remaining (owner/CI steps)** — live frontier numbers; the real frozen run
