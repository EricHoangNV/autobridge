# Progress

## Current State

REQ-022 sub-step D DONE (sbx1 2026-10-28). Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-021 + REQ-017 + REQ-018 + REQ-026 all DONE (REQ-021 remainder is owner steps: record-frontier capture + live check)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed (re-verification pending owner)
  - REQ-022 sub-steps A + B + C merged, all owner checks fixed (edge-sign, fail-fast, reset-target, registry-out-of-engine, DNS-hermetic)
  - REQ-022 sub-step D DONE: 10 fault YAMLs + fias_checks + test_fias_faults.py (13 tests, live green); layout repair [0,1] 0 frontier tokens; zeros-resync + inflight-fail host fixes
- Tests: 2771 pass / 0 fail (autobridge 2317/90.36%, verifier 109/93.78%, simulators 345/90.43%) + ui 156 pass;
  lint (incl. import-linter) + typecheck + openapi-check green
- Stack: local stack 2026-10-28, all readyz green; `make e2e` 157 pass / 2 skip / 1 fail (documented edge-agent random-order flake, green alone)
- Known notes (pre-existing, not this task): compose-stack /demo/reset cannot reach sim-fias-pabx (by design); edge-agent live test flaky under full-suite random order

## Next tasks
0. **REQ-021 remaining (owner step)** — owner runs `make record-frontier SCENARIO=gross_to_net` and commits the capture; live step-4 check on the owner's host
1. **REQ-022 sub-step E** — demo step (PABX firmware change + restart-without-loss) + docs (architecture, safety-model) + README + simulators index UI page
2. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
3. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
