# Progress

## Current State

REQ-024 sub-step A merged (sbx1 2026-10-28 23:03). Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-022 + REQ-017 + REQ-018 + REQ-026 all DONE (REQ-021 remainder is owner steps: record-frontier capture + live step-4 check)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed (re-verification pending owner)
  - REQ-022 sub-steps A–E + all owner checks fixed
  - REQ-024 sub-step A DONE (codeable A1–A5): leak-audit + isolation-check scripts, make verify-heldout-reveal,
    scorecard leak_audit field, docs Held-out protocol, 17 new unit tests
- Tests: 2801 pass / 0 fail (autobridge 2346/90.49%, verifier 109/93.78%, simulators 346) + ui 158 pass;
  lint (incl. import-linter) + typecheck green; W8 e2e test_07 green against live compose stack (up/down for run)
- Stack: no stack running in sbx1 (compose down after the e2e run)

## Next tasks
0. **REQ-021 remaining (owner step)** — owner runs `make record-frontier SCENARIO=gross_to_net` and commits the capture; live step-4 check on the owner's host
1. **REQ-024 sub-step B (IN_PROGRESS sbx1)** — opencode baseline driver (default), ablation arms c/d/e, metrics + 95% intervals, learning curve, preregistration, dev-split rehearsal
2. **REQ-025** — beacon evaluation (after REQ-024); REQ-023 demo plan applied as each lands
3. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
4. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
