# Progress

## Current State

REQ-021 matrix full-climb DONE (sbx1 2026-10-28). Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-019 + REQ-017 + REQ-018 all DONE
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-020 codeable work merged (status stale IN_PROGRESS in REQUESTS.MD)
  - REQ-021 matrix full-climb DONE: gross_to_net/invalid_patch_trap climb [0,1,2,3,4] via provider-wired controller path, L3 recorded, oracle rejects, L4 escalate, all other rows 0 frontier calls
  - REQ-021 23:05 check DONE: L3-mislabel fix + raw preview, local-up UI rebuild on source change, knowledge details
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed (re-verification pending owner)
  - REQ-022 sub-steps A + B + C merged + B owner check fixed (edge-sign + fail-fast, thread containment, trust_env=False, strict thread warnings)
  - REQ-026 DONE: pytest-timeout 2.4.0 (unit 30 s thread-method, e2e 180 s via conftest mark) + 3 mechanism/mark tests + LEARNINGS #23
- Tests: 2715 pass / 0 fail (autobridge 2291, verifier 99, simulators 325) + ui 156 pass;
  90% gates green (autobridge 90.33%, verifier 93.62%, simulators 90.36%); lint (incl. import-linter) + typecheck green
- Stack: e2e fixture brings up compose as needed; keyed `make local-up` green per prior state
- Known red (pre-existing, not this task): `test_matrix_live_reset_repeatability` fails live —
  `/demo/reset` returns 6 entries incl. fias but the test expects 4, and control-in-Docker cannot
  reach edge-net fias admin by design; left for the REQ-022 D/E loop

## Next tasks
0. **REQ-021 remaining (owner step)** — owner runs `make record-frontier SCENARIO=gross_to_net` and commits the capture; live step-4 check on the owner's host
1. **REQ-022 sub-step D** — fault scenarios (YAML + verifier checks + e2e): layout change, resync, ack-timeout,
   restart-inflight, cloud-slow, duplicates, oversize/flood, session-lost, stale-command (also owns the
   `/demo/reset` vs matrix-test mismatch noted above); macOS DNS-hermetic fix rides with it (owner: do next, independent of D)
2. **REQ-022 sub-step E** — demo step + docs (architecture, safety-model) + README + simulators index UI page
3. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
4. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
