# Progress

## Current State

REQ-022 sub-step E DONE (sbx1 2026-10-28). Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-021 + REQ-017 + REQ-018 + REQ-026 all DONE (REQ-021 remainder is owner steps: record-frontier capture + live check)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed (re-verification pending owner)
  - REQ-022 sub-steps A + B + C + D merged, all owner checks fixed (edge-sign, fail-fast, reset-target, registry-out-of-engine, DNS-hermetic, PS/PA race, matrix reset-targets)
  - REQ-022 sub-step E DONE: demo step 8 (firmware layout change L1 repair, restart 0 lost/0 duplicated) + GET /demo/simulators + UI /simulators index (System status link) + docs (demo.md step 8, architecture eight steps + §9.2 item 6); registry display metadata + public-port cross-checks; host admin base literal 127.0.0.1 (hermetic DNS)
- Tests: 2784 pass / 0 fail (autobridge 2329/90.49%, verifier 109, simulators 346) + ui 158 pass;
  lint (incl. import-linter) + typecheck + ui-typecheck + openapi-check green
- Stack: local stack 2026-10-28, all readyz green; smoke ok; `make demo` live 8/8 green; `make e2e` 157 pass / 2 skip / 1 fail (documented edge-agent random-order flake, green alone); Playwright journey 7 pass / 1 skip (pre-existing skip)
- Known notes (pre-existing, not this task): compose-stack /demo/reset cannot reach sim-fias-pabx (by design); edge-agent live test flaky under full-suite random order

## Next tasks
0. **REQ-021 remaining (owner step)** — owner runs `make record-frontier SCENARIO=gross_to_net` and commits the capture; live step-4 check on the owner's host
1. **REQ-024** — held-out integrity audit + engine-vs-frontier ablation (owner order: after REQ-022)
2. **REQ-025** — beacon evaluation (after REQ-024); REQ-023 demo plan applied as each lands
3. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
4. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
