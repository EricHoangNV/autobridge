# Progress

## Current State

REQ-028 BLOCKED in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): code re-verified — empty-delta L0 guard, drift-class-scoped key, NO_LEARN_CLASSES, scoped one-shot docs, arm availability, `ran` label, warm-never-worse test all present; targeted suites green (2 + 8 + 91 passed); no code changed. Only remainder is the owner keyed bench-ablation (no FRONTIER_API_KEY in sandbox).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-040 all DONE except IN_PROGRESS REQ-021/024/025/027 (remaining bits are owner keyed/live runs)
  - REQ-028 code DONE + re-verified, BLOCKED on owner keyed run (see Blocked line in REQUESTS.MD)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-034/035/036/037/038/040/041/042/043/044/045/046 DONE (REQ-043/044/045/046 keyed owner steps outstanding)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
  - local-up.sh pre-migrates the DB once (fixes fresh-boot "malformed" scheduler_state on shared filesystems)
- Tests: targeted REQ-028 suites 101 pass / 0 fail (2 bench ablation + 8 ladder L0 + 91 rules/knowledge); last full gates per REQ-046: make test 2758 + 134 + 409 pass, cov >=90%, ui 202 pass, BENCH_TRIALS=1 rehearsal 12/12 matched unkeyed
- Stack: sandbox rehearsal only (no live stack this run, no FRONTIER_API_KEY)

## Next tasks
1. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
2. **REQ-046 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` for live frontier numbers (re-record replay captures for the new prompt hashes first); then close
3. **REQ-045 (VERIFY)** — owner: keyed step-4 check (rung entries show prompt id/version; edit one prompt file, restart, next call shows new version) + `make record-frontier SCENARIO=gross_to_net` re-capture
4. **REQ-044 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make demo` with FRONTIER_API_KEY; step 4's L3 answer is an applying patch or explicit escalation; report tokens + answer
5. **REQ-043 (VERIFY)** — owner: keyed step-4 live watch + L3-with-131072 report
6. **REQ-041 (VERIFY)** — owner: keyed `make e2e-ui` on Compose (live chart/graph branches)
7. **REQ-042 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` with FRONTIER_API_KEY, then unkeyed
8. **REQ-027 (VERIFY)** — owner: keyed-stack run (make demo 8/8 twice, L3 reasoning line, /knowledge state, UI spec)
9. **REQ-021 remaining (owner step)** — record-frontier capture + live step-4 check
10. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; Nov 3 held-out run
11. **REQ-025 remaining (owner/CI steps)** — live frontier numbers; the real frozen run
