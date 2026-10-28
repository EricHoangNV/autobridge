# Progress

## Current State

REQ-022 sub-step A merged (sbx1 2026-10-28). Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-019 + REQ-017 + REQ-018 all DONE
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-020 sub-steps A + 5a + 5b + 5c + 6 + signature fix + step 7 merged (codeable work complete)
  - REQ-021 items 1–6 + capabilities/knowledge + record-frontier + schema-aware stamp +
    unscripted demo + full [0..4] climbs + recorded (never crash) rejections
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed (re-verification pending owner)
  - REQ-022 sub-step A DONE: FIAS PABX simulator + document viewer + wiring (B agent, C vocabulary, D scenarios, E demo remain)
- Tests: 2622 pass / 0 fail (autobridge 2195, verifier 99, simulators 328) + ui 156 pass; 90% gates green
  (91 / 94 / 90); lint (incl. import-linter) + typecheck + ui-typecheck green
- Stack: Docker available in this sandbox; `make up` + `make smoke` green (incl. sim-fias-pabx :8118, all new probes)

## Next tasks
0. **REQ-022 sub-step B** — edge agent (outbound-only, durable outbox/inbox, delivery guarantees, link supervision) + EdgePOS removal
1. **REQ-022 sub-steps C–E** — FIAS spec vocabulary (before/after) → fault scenarios → demo step + docs
2. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
3. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
