# Progress

## Current State

REQ-030 DONE in sbx1 (opencode meta/muse-spark-1.3-contributor (escalation)): sync scheduler (10s default, skip-busy, threaded, WAL), learn-on-approve (propose caches nothing; approve writes entry with approval id/actor/release id), connector state server-side, every step persisted (repair delta/class, verifier report id, hypothesis evidence, ModelCall mode/captured/outcome, pabx events, record-climb + pabx endpoints), knowledge page approval line. Full gates green.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-026 all DONE (REQ-021 remainder is owner steps: record-frontier capture + live step-4 check)
  - REQ-023 codeable DONE (owner picks video cut after REQ-027 lands)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-024 sub-step B code DONE + leak-audit fix (remaining: live frontier numbers + Nov 3 held-out run)
  - REQ-025 sub-step B code DONE + 5 rehearsals (remaining: live frontier numbers, the real frozen run)
  - REQ-027 sub-steps 1-6 DONE (remaining: owner keyed-stack demo verification)
  - REQ-028 code DONE (remaining: owner keyed rerun for live frontier numbers)
  - REQ-029 code DONE + audit validate run DONE (owner closes once REQ-034 decided; now decided + fixed)
  - REQ-034 DONE (sidecar; 4 e2e green both modes, smoke + demo 8/8 green)
  - REQ-035 DONE (demo admin token default + auth-boundary smoke; live verified)
  - REQ-038 DONE (deterministic FIAS replay/flood tests; 50/50 + full suite green)
  - REQ-036 DONE (directives amended + L3 count derived; full suite green)
  - REQ-037 DONE (validated stack-mode marker; stale → Compose + warning; full suite green)
  - REQ-030 DONE (scheduler/state/learn-on-approve/persistence; keyed re-run is owner step)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
- Tests: 2614 + 134 + 408 pass / 0 fail (autobridge 90.54%, verifier 91.49%, simulators 90.48%; ui 161 pass); lint + typecheck + openapi-check + ui-typecheck green
- Stack: local-up verified this run (e2e test_03, matrix, knowledge_flow, 027, fias + demo 8/8 green unkeyed); left up for owner keyed re-run

## Next tasks
1. **REQ-031 → REQ-032 → REQ-033** — owner UI-first demo parts B/C/D (depend on REQ-030)
2. **REQ-030 (VERIFY)** — owner: keyed `make local-up && make e2e && make demo` (L3 live numbers in step 4)
3. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then merge/close
4. **REQ-027 iteration (VERIFY)** — owner keyed-stack run, then sub-step 6 bench rerun
5. **REQ-021 remaining (owner step)** — record-frontier capture + live step-4 check
6. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; Nov 3 held-out run
7. **REQ-025 remaining (owner/CI steps)** — live frontier numbers; the real frozen run
8. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
9. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
