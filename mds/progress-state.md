# Progress

## Current State

REQ-029 DONE in sbx1 (opencode meta/muse-spark-1.3-contributor (escalation)): close-out — REQ-034 decided
(option a) and DONE, so the "owner's call once REQ-034 is decided" condition applies. Both gaps re-verified
in code on current main with targeted suites green (releases + verification_gate + engine_build_run: 104
passed; build_docs_guard + control_api + control_api_extended + cli: 110 passed); no code changes needed.
Remaining: auditor flips the Code Review verdict line in FEEDBACKS.MD; owner keyed
`make local-up && make e2e && make demo` re-run.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-039 all DONE except IN_PROGRESS REQ-021/024/025/027/028 (remaining bits are owner keyed/live runs)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-029 DONE (close-out; auditor owns verdict line)
  - REQ-034/035/036/037/038 DONE
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
  - local-up.sh pre-migrates the DB once (fixes fresh-boot "malformed" scheduler_state on shared filesystems)
- Tests: targeted REQ-029 suites 214 passed / 0 fail (this run); full-gate numbers from REQ-039 run: 2669 + 134 + simulators pass / 0 fail (autobridge 90.55%, verifier 91.49%, gate 90%; ui 192 pass); lint + typecheck + openapi-check + ui-typecheck green
- Stack: not started this run (code verification only, no stack needed)

## Next tasks
1. **REQ-027 (VERIFY)** — owner: keyed-stack run (make demo 8/8 twice, L3 reasoning line, /knowledge state, UI spec)
2. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
3. **REQ-021 remaining (owner step)** — record-frontier capture + live step-4 check
4. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; Nov 3 held-out run
5. **REQ-025 remaining (owner/CI steps)** — live frontier numbers; the real frozen run
6. **REQ-039 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo`, then `make record-frontier SCENARIO=gross_to_net` and commit demo/recordings/gross_to_net.json
7. **REQ-031 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` + reset/seed 20x on camera
8. **REQ-033 (VERIFY)** — owner: keyed full run (covers REQ-033; test_08 composition: seed pass → demo → recorded pass)
9. **REQ-032 (VERIFY)** — owner: keyed full run (covers REQ-032)
10. **REQ-030 (VERIFY)** — owner: keyed `make local-up && make e2e && make demo` (L3 live numbers in step 4)
11. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
12. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
