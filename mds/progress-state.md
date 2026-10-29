# Progress

## Current State

REQ-037 DONE in sbx1 (opencode meta/muse-spark-1.3-contributor (escalation)): one shared stdlib-only `bench/stack_mode.py` validates `.local/ACTIVE` (recorded pid alive, or control `/readyz` `runtime_mode: local`); smoke, demo and e2e conftest delegate to it. Stale marker → Compose branch as without the marker + warning naming the file; genuinely-local behaviour unchanged. Full gates green.

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
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
- Tests: 2578 + 134 + 408 pass / 0 fail (autobridge 90.35%, verifier 91.49%, simulators 90.48%; ui 160 pass); lint + typecheck green
- Stack: not started this run (no live stack in sandbox; stub-readyz + planted-marker CLI checks instead)

## Next tasks
1. **REQ-030 → REQ-031 → REQ-032 → REQ-033** — owner UI-first demo (findings fixed, before benchmark rounds)
2. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then merge/close
3. **REQ-027 iteration (VERIFY)** — owner keyed-stack run, then sub-step 6 bench rerun
4. **REQ-021 remaining (owner step)** — record-frontier capture + live step-4 check
5. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; Nov 3 held-out run
6. **REQ-025 remaining (owner/CI steps)** — live frontier numbers; the real frozen run
7. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
8. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
