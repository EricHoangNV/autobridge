# Progress

## Current State

REQ-022 sub-step C merged (sbx1 2026-10-28). Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-019 + REQ-017 + REQ-018 all DONE
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-020 codeable work merged (sub-steps A + 5a + 5b + 5c + 6 + signature fix + step 7)
  - REQ-021 codeable work merged (items 1–6 + capabilities/knowledge + record-frontier + schema-aware stamp +
    unscripted demo + full [0..4] climbs + recorded (never crash) rejections)
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed (re-verification pending owner)
  - REQ-022 sub-steps A + B + C merged (FIAS simulator + viewer, edge agent + relay, spec v3 vocabulary)
  - REQ-026 still OPEN (only a bookkeeping commit exists; per-test timeout not implemented)
- Tests: 2691 pass / 0 fail (autobridge 2267, verifier 99, simulators 325) + ui 156 pass; 90% gates green
  (90.66 / 93.62 / 90.36); lint (incl. import-linter) + typecheck + ui-typecheck green
- Stack: Docker available in this sandbox; `make up` + `make smoke` green; e2e files green live
  (fias before/after, meridian, s2, knowledge flow)

## Next tasks
0. **REQ-022 sub-step D** — fault scenarios (YAML + verifier checks + e2e): layout change, resync, ack-timeout,
   restart-inflight, cloud-slow, duplicates, oversize/flood, session-lost, stale-command
1. **REQ-022 sub-step E** — demo step + docs (architecture, safety-model) + README + simulators index UI page
2. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
3. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
