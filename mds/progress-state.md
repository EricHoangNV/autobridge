# Progress

## Current State

REQ-033 DONE in sbx1 (opencode meta/muse-spark-1.3-contributor (escalation)): README "How to run the demo"
recording script, docs/demo.md "Recording from the UI" section, new live-only Playwright spec
test_08_recording_journey (6 journeys: seed frame, zero-token UI approve, L0 re-hit + reused, semantic gap +
recorded funnel/call/question, PABX panel), registered first in playwright.config.ts, `make e2e-ui` sources
load_dotenv.sh for the control token. Full gates green unkeyed.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-026 all DONE (REQ-021 remainder is owner steps: record-frontier capture + live step-4 check)
  - REQ-023 codeable DONE (owner picks video cut after REQ-027 lands)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-024 sub-step B code DONE + leak-audit fix (remaining: live frontier numbers + Nov 3 held-out run)
  - REQ-025 sub-step B code DONE + 5 rehearsals (remaining: live frontier numbers, the real frozen run)
  - REQ-027 sub-steps 1-6 DONE (remaining: owner keyed-stack run, then sub-step 6 bench rerun)
  - REQ-028 code DONE (remaining: owner keyed rerun for live frontier numbers)
  - REQ-029 code DONE + audit validate run DONE (owner closes once REQ-034 decided; now decided + fixed)
  - REQ-034 DONE (sidecar; 4 e2e green both modes, smoke + demo 8/8 green)
  - REQ-035 DONE (demo admin token default + auth-boundary smoke; live verified)
  - REQ-038 DONE (deterministic FIAS replay/flood tests; 50/50 + full suite green)
  - REQ-036 DONE (directives amended + L3 count derived; full suite green)
  - REQ-037 DONE (validated stack-mode marker; stale → Compose + warning; full suite green)
  - REQ-030 DONE (scheduler/state/learn-on-approve/persistence; keyed re-run is owner step)
  - REQ-031 DONE (seed/break/heal + UI docs build; keyed re-run is owner step)
  - REQ-032 DONE (live states + self-explaining repair page; keyed re-run is owner step)
  - REQ-033 DONE (README/demo docs/UI e2e journeys; keyed re-run is owner step)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
- Tests: 2640 + 134 + 408 pass / 0 fail (autobridge 90%, verifier 91%, simulators 90%; ui 192 pass); lint + typecheck + openapi-check + ui-typecheck green
- Stack: local-up verified this run (6/6 journeys green unkeyed + recorded half after demo step 4); left up with journey + step-4 rows (harbor shows recorded drift — reseed before recording)

## Next tasks
1. **REQ-033 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` (covers REQ-033; note e2e-ui composition: test_08 runs first on the seed, test_027/test_s6 need post-demo/s6 state)
2. **REQ-032 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` (covers REQ-032)
3. **REQ-031 (VERIFY)** — owner: keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` (covers REQ-030/031)
4. **REQ-030 (VERIFY)** — owner: keyed `make local-up && make e2e && make demo` (L3 live numbers in step 4)
5. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then merge/close
6. **REQ-027 iteration (VERIFY)** — owner keyed-stack run, then sub-step 6 bench rerun
7. **REQ-021 remaining (owner step)** — record-frontier capture + live step-4 check
8. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; Nov 3 held-out run
9. **REQ-025 remaining (owner/CI steps)** — live frontier numbers; the real frozen run
10. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
11. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
