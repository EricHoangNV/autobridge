# Progress

## Current State

REQ-041 DONE in sbx1 (opencode meta/muse-spark-1.3-contributor (escalation)): per-run
model-usage chart restored on the repair page (appendix, live ModelCall rows);
test_s6 live branches assert shape, test_06 mock-ordering bug fixed; gates green.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-040 all DONE except IN_PROGRESS REQ-021/024/025/027/028 (remaining bits are owner keyed/live runs)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-034/035/036/037/038/040/041/042 DONE
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
  - local-up.sh pre-migrates the DB once (fixes fresh-boot "malformed" scheduler_state on shared filesystems)
- Tests: make test 2678 + 134 + 409 pass / 0 fail (autobridge 90%, verifier 91%, simulators 90%; ui 193 pass); lint + typecheck + openapi-check + ui-typecheck green
- Stack: prod-build UI + mocked control e2e-ui 10 pass / 9 skip (live-only); Compose not in sandbox, owner runs keyed gates

## Next tasks
1. **REQ-041 (VERIFY)** — owner: keyed `make e2e-ui` on Compose (live chart/graph branches)
2. **REQ-042 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` with FRONTIER_API_KEY, then unkeyed
3. **REQ-027 (VERIFY)** — owner: keyed-stack run (make demo 8/8 twice, L3 reasoning line, /knowledge state, UI spec)
4. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
5. **REQ-021 remaining (owner step)** — record-frontier capture + live step-4 check
6. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; Nov 3 held-out run
7. **REQ-025 remaining (owner/CI steps)** — live frontier numbers; the real frozen run
8. **REQ-039 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo`, then `make record-frontier SCENARIO=gross_to_net` and commit demo/recordings/gross_to_net.json
9. **REQ-031 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` + reset/seed 20x on camera
10. **REQ-033 (VERIFY)** — owner: keyed full run (covers REQ-033; test_08 composition: seed pass → demo → recorded pass)
11. **REQ-032 (VERIFY)** — owner: keyed full run (covers REQ-032)
12. **REQ-030 (VERIFY)** — owner: keyed `make local-up && make e2e && make demo` (L3 live numbers in step 4)
13. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
14. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
