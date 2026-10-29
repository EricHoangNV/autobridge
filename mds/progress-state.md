# Progress

## Current State

Directives audit Cycle 5 DONE in sbx1 (opencode meta/muse-wonder-1.0 (escalation)): audited
`2cea2072..d2d9c9d2` (REQ-034…039, REQ-030…033) against DIRECTIVES §§1–8. Cycle 4 AR-4-1…AR-4-4
verified resolved live; Code Review verdict moved to PASS in FEEDBACKS.MD. Audit fixed 4
Compose/local mode-split residuals (compose *_BASE_URL placeholders, seed address check,
smoke token default, approve-preview locator) and filed REQ-040 + REQ-041.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-039 all DONE except IN_PROGRESS REQ-021/024/025/027/028 (remaining bits are owner keyed/live runs)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-034/035/036/037/038 DONE; REQ-040/041 OPEN (filed by this audit)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
  - local-up.sh pre-migrates the DB once (fixes fresh-boot "malformed" scheduler_state on shared filesystems)
- Tests: make test 2673 + 134 + 409 pass / 0 fail (autobridge 90.55%, verifier 91.49%, simulators 90.48%; ui 192 pass); lint + typecheck + openapi-check + ui-typecheck green
- Stack: Compose up in sbx1; make smoke green, make demo 8/8, REQ-034 acceptance 4/4, test_09 5/5; e2e-ui 10 pass / 5 skip / 3 fail (all REQ-041)

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
11. **REQ-041** — restore or retire the S6 model-usage chart; make e2e-ui green on Compose
12. **REQ-040** — thread the persisted RepairRun id through the controller; delete the unscoped re-fetch
13. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
14. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
