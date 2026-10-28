# Progress

## Current State

REQ-024 sub-step B code-complete (sbx1 2026-10-28). Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-022 + REQ-017 + REQ-018 + REQ-026 all DONE (REQ-021 remainder is owner steps: record-frontier capture + live step-4 check)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed (re-verification pending owner)
  - REQ-022 sub-steps A–E + all owner checks fixed
  - REQ-024 sub-step A DONE (codeable A1–A5)
  - REQ-024 sub-step B DONE (codeable B1–B6): opencode default driver, 5 ablation arms, metrics + 95% CI, learning curve, preregistration, make bench/bench-ablation wiring; rehearsed live 60/60 matched
- Tests: 2849 pass / 0 fail (autobridge 2394, verifier 109/93.78%, simulators 346/90.46%) + ui 158 pass;
  lint (incl. import-linter) + typecheck green; test_07 + matrix in-process green; compose rehearsal done (stack down after run)
- Stack: no stack running in sbx1 (compose down after the ablation rehearsal)

## Next tasks
0. **REQ-021 remaining (owner step)** — owner runs `make record-frontier SCENARIO=gross_to_net` and commits the capture; live step-4 check on the owner's host
1. **REQ-024 remaining (owner/CI steps)** — live frontier numbers need `FRONTIER_API_KEY`; single Nov 3 held-out run (`make bench-heldout` + ablation per preregistration)
2. **REQ-025** — beacon evaluation (after REQ-024); REQ-023 demo plan applied as each lands
3. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
4. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
