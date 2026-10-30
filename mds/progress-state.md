# Progress

## Current State

REQ-046 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): frontier arms get exactly the engine's context (schema, capabilities, different-vendor example, vendor docs, visible tests, answer format); bench prompts in `config/prompts/bench/` files with the REQ-045 loader rules; scorecard records each arm's prompt id/version/hash + shared context hash; fair-context test asserts one hash per case across engine + both frontier arms; docs + prereg amendment describe what each arm is given.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-040 all DONE except IN_PROGRESS REQ-021/024/025/027/028 (remaining bits are owner keyed/live runs)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-034/035/036/037/038/040/041/042/043/044/045/046 DONE (REQ-043/044/045 keyed owner steps outstanding; REQ-046 owner step: keyed bench-ablation for live frontier numbers)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
  - local-up.sh pre-migrates the DB once (fixes fresh-boot "malformed" scheduler_state on shared filesystems)
- Tests: make test 2758 + 134 + 409 pass / 0 fail (autobridge 90%, verifier 91%, simulators 90%; ui 202 pass); lint + typecheck green; test_07 e2e pass; BENCH_TRIALS=1 rehearsal 12/12 matched unkeyed
- Stack: sandbox rehearsal only (no live stack this run); verifier down → grades degrade to unverified, ReplayMissing fails loudly on the new prompt hash until owner re-records

## Next tasks
1. **REQ-046 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` for live frontier numbers (re-record replay captures for the new prompt hashes first); then close
2. **REQ-045 (VERIFY)** — owner: keyed step-4 check (rung entries show prompt id/version; edit one prompt file, restart, next call shows new version) + `make record-frontier SCENARIO=gross_to_net` re-capture
3. **REQ-044 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make demo` with FRONTIER_API_KEY; step 4's L3 answer is an applying patch or explicit escalation; report tokens + answer
4. **REQ-043 (VERIFY)** — owner: keyed step-4 live watch + L3-with-131072 report
5. **REQ-041 (VERIFY)** — owner: keyed `make e2e-ui` on Compose (live chart/graph branches)
6. **REQ-042 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` with FRONTIER_API_KEY, then unkeyed
7. **REQ-027 (VERIFY)** — owner: keyed-stack run (make demo 8/8 twice, L3 reasoning line, /knowledge state, UI spec)
8. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
9. **REQ-021 remaining (owner step)** — record-frontier capture + live step-4 check
10. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; Nov 3 held-out run
11. **REQ-025 remaining (owner/CI steps)** — live frontier numbers; the real frozen run
