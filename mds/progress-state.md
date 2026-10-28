# Progress

## Current State

REQ-021 sub-step A merged (sbx1 2026-10-28). Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-019 + REQ-017 + REQ-018 all DONE
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-020 sub-steps A + 5a + 5b + 5c + 6 merged; owner-check signature fix merged
  - REQ-021 sub-step A merged (live Muse frontier provider: MuseProvider + env settings + per-model
    pricing + compose/.env wiring + SSE redaction pin; 16 new unit tests)
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed (re-verification pending owner)
- Tests: 2516 pass / 0 fail (autobridge 2123, verifier 99, simulators 294) + ui 148 pass; 90% gates green
  (91 / 94 / 96); lint + typecheck green
- Stack: Docker available in this sandbox (sim-meridian live verified earlier)

## Next tasks
0. **REQ-021** — [>] IN PROGRESS sbx1 (remaining: B full reset, C pinned vocabulary, D rung ledger,
   E matrix e2e, F capabilities/knowledge, G replay; then REQ-020 step 7 → REQ-022 → REQ-024 → REQ-025)
1. **P1.W10.3** — [~] OWNER: confirm CI green on main with demo step, record labelled demo (docs/demo.md)
2. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
