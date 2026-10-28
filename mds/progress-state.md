# Progress

## Current State

REQ-022 sub-step B merged (sbx1 2026-10-28). Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-019 + REQ-017 + REQ-018 all DONE
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-020 sub-steps A + 5a + 5b + 5c + 6 + signature fix + step 7 merged (codeable work complete)
  - REQ-021 items 1–6 + capabilities/knowledge + record-frontier + schema-aware stamp +
    unscripted demo + full [0..4] climbs + recorded (never crash) rejections
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed (re-verification pending owner)
  - REQ-022 sub-step A DONE: FIAS PABX simulator + document viewer + wiring
  - REQ-022 sub-step B DONE: outbound-only edge agent + edge-relay queue + signed command bundle + EdgePOS removal (C vocabulary, D scenarios, E demo remain)
- Tests: 2666 pass / 0 fail (autobridge 2242, verifier 99, simulators 325) + ui 156 pass; 90% gates green
  (90.69 / 93.62 / 90.36); lint (incl. import-linter) + typecheck + ui-typecheck green
- Stack: Docker available in this sandbox; `make up` + `make smoke` green (edge-relay :8120, agent↔PABX link up, all new probes); local mode verified (`make local-up` + smoke + S2 green)

## Next tasks
0. **REQ-022 sub-step C** — FIAS spec vocabulary: protocol fias_tcp + event sources + record mapping + command templates (before: v1 refuses Blocked; after: bootstraps)
1. **REQ-022 sub-steps D–E** — fault scenarios + verifier checks → demo step + docs + simulators index
2. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
3. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
