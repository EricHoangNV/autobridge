# Progress

## Current State

REQ-039 DONE in sbx1 (opencode meta/muse-spark-1.3-contributor (escalation)): the live semantic break runs the
real climb — scheduler/worker repair path for SEMANTIC_VALUE_DRIFT enumerates the engine's own amount readings
from the wire signals + spec (new src/autobridge/repair/semantic.py), wire-filters, climbs via
RepairController.handle/run_frontier_climb (L2 skipped on one explanation, exactly one L3 attempt live/replay/
explicit-skip), judges the proposal against the verifier (recorded, never released) and escalates with one
operator question. Break's repair row carries funnel + L3 messages + verdict; record-climb is now fallback-only;
record-frontier captures the live prompt (rung messages) + in-process prompt for replay. Live verified unkeyed:
seed, break→error, demo 8/8 (twice), test_08 both passes (live legs + recorded leg).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-033 all DONE (REQ-021/024/025/027/028 remaining bits are owner keyed/live runs; REQ-031 remaining:
    owner keyed re-run + 20x reset/seed camera acceptance — 5/5 green in sandbox)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-029 code DONE + audit validate run DONE (owner closes)
  - REQ-034/035/036/037/038 DONE
  - REQ-039 DONE (code + full gates green unkeyed + live verified; owner: keyed re-run + record-frontier capture)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
  - local-up.sh pre-migrates the DB once (fixes fresh-boot "malformed" scheduler_state on shared filesystems)
- Tests: 2669 + 134 + simulators pass / 0 fail (autobridge 90.55%, verifier 91.49%, gate 90%; ui 192 pass); lint + typecheck + openapi-check + ui-typecheck green
- Stack: local-up verified this run (seed, break→error live, demo 8/8 twice, test_08 2 passes); healed after verifying, stack down, .local removed

## Next tasks
1. **REQ-039 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo`, then `make record-frontier SCENARIO=gross_to_net` and commit demo/recordings/gross_to_net.json
2. **REQ-031 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` + reset/seed 20x on camera
3. **REQ-033 (VERIFY)** — owner: keyed full run (covers REQ-033; test_08 composition: seed pass → demo → recorded pass)
4. **REQ-032 (VERIFY)** — owner: keyed full run (covers REQ-032)
5. **REQ-030 (VERIFY)** — owner: keyed `make local-up && make e2e && make demo` (L3 live numbers in step 4)
6. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then merge/close
7. **REQ-027 iteration (VERIFY)** — owner keyed-stack run, then sub-step 6 bench rerun
8. **REQ-021 remaining (owner step)** — record-frontier capture + live step-4 check
9. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; Nov 3 held-out run
10. **REQ-025 remaining (owner/CI steps)** — live frontier numbers; the real frozen run
11. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
12. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
