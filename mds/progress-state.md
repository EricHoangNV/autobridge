# Progress

## Current State

REQ-027 sub-steps 1-4 MERGED (sbx1 2026-10-29): (1) mode-correct simulator addresses; (2) detector carries wire-observed evidence from raw_pages/records only; (3) the live chain (every sync detects + repairs deterministically); (4) demo steps 3-4 drive the chain with no hand-built inputs — step 3 proposes page_to_cursor rung 1 with 0 tokens (approve -> resync Complete); step 4 climbs detector-derived SEMANTIC_VALUE_DRIFT to L3 with real redacted evidence, decoy rejected, L4 escalated. Live step 4 records the built-spec gap (no inferred money_invariant). Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-026 all DONE (REQ-021 remainder is owner steps: record-frontier capture + live step-4 check)
  - REQ-023 codeable DONE (catalogue + per-entry runs + matrix token_expired row; owner picks video cut)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed (re-verification pending owner)
  - REQ-024 owner check on f32b4b4b FIXED (code): end-to-end arms, bench move, rehearsal numbers reported (all-escalate, 0 frontier tokens — losses published)
  - REQ-024 sub-step A DONE; sub-step B code DONE (remaining: live frontier numbers + Nov 3 held-out run)
  - REQ-024 leak-audit fix DONE (raw+nonce only, per-kind counts)
  - REQ-025 sub-step A DONE (faultgen, answer keys, reference solver, 1000+ sweeps)
  - REQ-025 sub-step B code DONE (client, serving, make targets, docs, 2 live rehearsals + live repair proof; engine-arm caveat now removed)
  - REQ-027 sub-steps 1-4 DONE (addresses; detector wire evidence; live chain; demo steps on the chain; sub-steps 5-6 remain)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
- Tests: autobridge 91% (2492) / verifier 91% (134) / simulators 90% (396) gates hold + ui 158 pass
- Stack: no live stack in sbx1 (unit + in-process serving only; e2e matrix in-process 20/20 green, live-stack error pre-existing on base)

## Next tasks
0. **REQ-027 sub-steps 5-6** — live e2e per scenario (needs keyed stack) + honest bench rerun (6)
1. **REQ-021 remaining (owner step)** — owner runs `make record-frontier SCENARIO=gross_to_net` and commits the capture (prompt changed: re-record required); live step-4 check on the owner's host
2. **REQ-024 remaining (owner/CI steps)** — owner re-runs leak audit (expect OK); live frontier numbers need `FRONTIER_API_KEY`; single Nov 3 held-out run (`make bench-heldout` + ablation per preregistration)
3. **REQ-025 remaining (owner/CI steps)** — 3+ more rehearsals before freeze (`make bench-beacon REHEARSE=<round>`); live frontier numbers; the real frozen run (`make beacon-eval`)
4. **REQ-023** — DONE codeable 2026-10-29 (owner picks video cut)
5. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
6. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
