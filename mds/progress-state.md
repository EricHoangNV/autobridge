# Progress

## Current State

REQ-042 DONE (code) in sbx1 (opencode meta/muse-spark-1.3-contributor (escalation)): no model
call inside a request or held transaction — semantic live climbs queue as background repair
jobs; control drains them committing before/after each call; demo step 4 polls. REQ-040's
prescribed fix (threaded repair_run_id) implemented + tested here, left OPEN for its flip.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-039 all DONE except IN_PROGRESS REQ-021/024/025/027/028 (remaining bits are owner keyed/live runs)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-034/035/036/037/038/042 DONE; REQ-040/041 OPEN
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
  - local-up.sh pre-migrates the DB once (fixes fresh-boot "malformed" scheduler_state on shared filesystems)
- Tests: make test 2676 + 134 + 409 pass / 0 fail (autobridge 90.35%, verifier 91.49%, simulators 90.48%; ui 192 pass); lint + typecheck + openapi-check + ui-typecheck green
- Stack: live-stack e2e not runnable in sandbox (no Compose); owner runs keyed gates

## Next tasks
1. **REQ-042 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` with FRONTIER_API_KEY, then unkeyed
2. **REQ-027 (VERIFY)** — owner: keyed-stack run (make demo 8/8 twice, L3 reasoning line, /knowledge state, UI spec)
3. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
4. **REQ-021 remaining (owner step)** — record-frontier capture + live step-4 check
5. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; Nov 3 held-out run
6. **REQ-025 remaining (owner/CI steps)** — live frontier numbers; the real frozen run
7. **REQ-039 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo`, then `make record-frontier SCENARIO=gross_to_net` and commit demo/recordings/gross_to_net.json
8. **REQ-031 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` + reset/seed 20x on camera
9. **REQ-033 (VERIFY)** — owner: keyed full run (covers REQ-033; test_08 composition: seed pass → demo → recorded pass)
10. **REQ-032 (VERIFY)** — owner: keyed full run (covers REQ-032)
11. **REQ-030 (VERIFY)** — owner: keyed `make local-up && make e2e && make demo` (L3 live numbers in step 4)
12. **REQ-041** — restore or retire the S6 model-usage chart; make e2e-ui green on Compose
13. **REQ-040** — fix implemented + tested under REQ-042; verify and flip (two-connector no-cross-link test exists)
14. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
15. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
