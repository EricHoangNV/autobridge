# Progress

## Current State

REQ-031 DONE in sbx1 (opencode meta/muse-spark-1.3-contributor (escalation)): both reopened owner checks fixed —
Harbor seeds from its reference release via new verifier-gated POST /connectors/{id}/reference-release (break →
Degraded → escalate → error happens live); scheduler pauses during reset/seed/heal (owner-scoped, stale-expiring)
+ reset waits for in-flight jobs (409 on timeout); one undecided fault keeps exactly one drift + one repair
(dedupe); deterministic live repairs record L0→L4 reasons; step 4 + test_08 assert the live break (record-climb
kept as no-key fallback). Full gates green unkeyed + live verified.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-033 all DONE (REQ-021/024/025/027/028 remaining bits are owner keyed/live runs; REQ-031 remaining:
    owner keyed re-run + 20x reset/seed camera acceptance — 5/5 green in sandbox)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-029 code DONE + audit validate run DONE (owner closes)
  - REQ-034/035/036/037/038 DONE
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
- Tests: 2660 + 134 + 408 pass / 0 fail (autobridge 91%, verifier 91%, simulators 90%; ui 192 pass); lint + typecheck + openapi-check + ui-typecheck green
- Stack: local-up verified this run (seed 5/5, break→error live, demo 8/8, test_08 2 passes, e2e subset green); healed after verifying, stack left up clean

## Next tasks
1. **REQ-031 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` + reset/seed 20x on camera
2. **REQ-033 (VERIFY)** — owner: keyed full run (covers REQ-033; test_08 composition: seed pass → demo → recorded pass)
3. **REQ-032 (VERIFY)** — owner: keyed full run (covers REQ-032)
4. **REQ-030 (VERIFY)** — owner: keyed `make local-up && make e2e && make demo` (L3 live numbers in step 4)
5. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then merge/close
6. **REQ-027 iteration (VERIFY)** — owner keyed-stack run, then sub-step 6 bench rerun
7. **REQ-021 remaining (owner step)** — record-frontier capture + live step-4 check
8. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; Nov 3 held-out run
9. **REQ-025 remaining (owner/CI steps)** — live frontier numbers; the real frozen run
10. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
11. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
