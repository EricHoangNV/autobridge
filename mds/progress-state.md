# Progress

## Current State

REQ-020 signature regression fixed (sbx1 2026-10-28). Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-019 + REQ-017 + REQ-018 all DONE
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-020 sub-steps A + 5a + 5b + 5c + 6 merged; owner-check signature fix merged (v1 `spec_signature` byte-identical to pre-v2; committed references verify; 4 unit regression pins)
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed (re-verification pending owner)
- Tests: 2500 pass / 0 fail (autobridge 2107, verifier 99, simulators 294) + ui 148 pass; 90% gates green (91 / 94 / 96); lint + typecheck green; e2e test_02 + meridian-runtime-v2 (9) green
- Stack: Docker available in this sandbox (sim-meridian live verified earlier)

## Next tasks
0. **REQ-020** — [>] IN PROGRESS sbx1 (remaining: 7 demo; owner order: REQ-021 next, then step 7 → REQ-022 → REQ-024 → REQ-025)
1. **REQ-021** — OPEN, next: full reset, pinned vocabulary, rung ledger, matrix test, Muse frontier provider, capabilities/knowledge APIs + page
2. **P1.W10.3** — [~] OWNER: confirm CI green on main with demo step, record labelled demo (docs/demo.md)
3. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
