# Progress

## Current State

REQ-043 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): repair rows exist from the start of the climb (decision `climbing`, rung snapshots stream between rungs, filled in place at the end, crash → `failed`, orphans marked on restart); Repairs list shows `repairing` within one poll; repair page running view with calling model + elapsed seconds; connector rows link to the running repair; `FRONTIER_MAX_TOKENS` (default 131072) configures the L3 cap.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-040 all DONE except IN_PROGRESS REQ-021/024/025/027/028 (remaining bits are owner keyed/live runs)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-034/035/036/037/038/040/041/042/043 DONE (REQ-043 keyed owner step outstanding)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
  - local-up.sh pre-migrates the DB once (fixes fresh-boot "malformed" scheduler_state on shared filesystems)
- Tests: make test 2686 + 134 + 409 pass / 0 fail (autobridge 90.22%, verifier 91.49%, simulators 90.48%; ui 199 pass); lint + typecheck + openapi-check + ui-typecheck green
- Stack: local-up green in sandbox; e2e test_027 (9) + test_03/test_05 (30) pass; make demo 8/8 unkeyed (+ STEP=4 alone); make e2e-ui 14 pass / 5 skip (live-only)

## Next tasks
1. **REQ-043 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` with FRONTIER_API_KEY; confirm step 4 shows the running repair live in the UI, and report whether L3 with 131072 room returns a patch (tokens used + patch or rejection reason)
2. **REQ-041 (VERIFY)** — owner: keyed `make e2e-ui` on Compose (live chart/graph branches)
3. **REQ-042 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` with FRONTIER_API_KEY, then unkeyed
4. **REQ-027 (VERIFY)** — owner: keyed-stack run (make demo 8/8 twice, L3 reasoning line, /knowledge state, UI spec)
5. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
6. **REQ-021 remaining (owner step)** — record-frontier capture + live step-4 check
7. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; Nov 3 held-out run
8. **REQ-025 remaining (owner/CI steps)** — live frontier numbers; the real frozen run
9. **REQ-039 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo`, then `make record-frontier SCENARIO=gross_to_net` and commit demo/recordings/gross_to_net.json
10. **REQ-031 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` + reset/seed 20x on camera
11. **REQ-033 (VERIFY)** — owner: keyed full run (covers REQ-033; test_08 composition: seed pass → demo → recorded pass)
12. **REQ-032 (VERIFY)** — owner: keyed full run (covers REQ-032)
13. **REQ-030 (VERIFY)** — owner: keyed `make local-up && make e2e && make demo` (L3 live numbers in step 4)
14. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
15. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
