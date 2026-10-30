# Progress

## Current State

REQ-044 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): L3 asks a well-posed question (spec patch target + exact JSON Pointers + redacted raw records, {patch|escalate}+rationale answer, one retry with apply error, model escalation resolves L4 with the model's question labelled on the repair page); replay fixtures/tests updated.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-040 all DONE except IN_PROGRESS REQ-021/024/025/027/028 (remaining bits are owner keyed/live runs)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-034/035/036/037/038/040/041/042/043/044 DONE (REQ-043/044 keyed owner steps outstanding)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
  - local-up.sh pre-migrates the DB once (fixes fresh-boot "malformed" scheduler_state on shared filesystems)
- Tests: make test 2698 + 134 + 409 pass / 0 fail (autobridge 90.09%, verifier 91.49%, simulators ≥90%; ui 201 pass); lint + typecheck + openapi-check + ui-typecheck green
- Stack: local-up green in sandbox (taken down after); e2e matrix (21) + test_05 (24) pass live; make demo 8/8 unkeyed (step 4 L3 explicit skip naming new prompt hash until owner re-captures)

## Next tasks
1. **REQ-045** — OPEN (owner priority after REQ-044): every engine prompt in `config/prompts/` files, L2/L3 prompts well-posed; moves REQ-044's L3 prompt verbatim (new prompt hashes again → re-capture)
2. **REQ-046** — OPEN (must land before any benchmark rehearsal): frontier arms get the engine's context, bench prompts in files
3. **REQ-044 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make demo` with FRONTIER_API_KEY; step 4's L3 answer is an applying patch or explicit escalation (never "does not apply"); report tokens + answer; then `make record-frontier SCENARIO=gross_to_net` and commit the capture
2. **REQ-043 (VERIFY)** — owner: keyed step-4 live watch + L3-with-131072 report
3. **REQ-041 (VERIFY)** — owner: keyed `make e2e-ui` on Compose (live chart/graph branches)
4. **REQ-042 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` with FRONTIER_API_KEY, then unkeyed
5. **REQ-027 (VERIFY)** — owner: keyed-stack run (make demo 8/8 twice, L3 reasoning line, /knowledge state, UI spec)
6. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
7. **REQ-021 remaining (owner step)** — record-frontier capture + live step-4 check
8. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; Nov 3 held-out run
9. **REQ-025 remaining (owner/CI steps)** — live frontier numbers; the real frozen run
