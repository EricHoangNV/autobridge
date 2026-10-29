# Progress

## Current State

REQ-027 knowledge-page check CODE DONE in sbx1 (2026-10-29, unmerged): profiles record vocabulary incl. refused builds, demo steps approve via API, inject-again leg shows times_reused 0→1 at L0/0 tokens, UI renders refused + new post-demo UI spec. Needs owner keyed-stack run (`make demo` 8/8 + UI spec). Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-026 all DONE (REQ-021 remainder is owner steps: record-frontier capture + live step-4 check)
  - REQ-023 codeable DONE (catalogue + per-entry runs + matrix token_expired row; owner picks video cut)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed (re-verification pending owner)
  - REQ-024 owner check on f32b4b4b FIXED (code): end-to-end engine arms, bench move, rehearsal numbers reported (all-escalate, 0 frontier tokens — losses published)
  - REQ-024 sub-step A DONE; sub-step B code DONE (remaining: live frontier numbers + Nov 3 held-out run)
  - REQ-024 leak-audit fix DONE (raw+nonce only, per-kind counts)
  - REQ-025 sub-step A DONE (faultgen, answer keys, reference solver, 1000+ sweeps)
  - REQ-025 sub-step B code DONE (client, serving, make targets, docs, 2 live rehearsals + live repair proof; engine-arm caveat now removed)
  - REQ-027 sub-steps 1-4 + check-1 DONE and MERGED (addresses; detector wire evidence; live chain; demo steps; build verbatim base_url/vendor; no-response guard; live e2e file)
  - REQ-027 knowledge-page check + sim-keys defaults CODE DONE (profiles + refused, API approvals, inject-again L0 reuse, UI e2e; local-up/compose/.env.example demo keys + guard test; step-7 MERIDIAN_API_KEY fix) — awaiting owner keyed-stack verification + merge; L3 output-room + Harbor money-invariant left open
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
- Tests: autobridge 90.5% (2499) / verifier 91.5% (134) / simulators 90.5% (396) + ui 159 pass; lint + typecheck + ui-typecheck green
- Stack: no live stack in sbx1 (unit + in-process serving only; e2e live files skip/error without Docker — pre-existing)

## Next tasks
0. **REQ-027 knowledge-page check (VERIFY)** — owner: fresh `.env` from `.env.example`, no manual key exports, keyed `make local-up && make demo` 8/8, check /knowledge (atlaspms v1, meridianpms v2/v1-refused, PABX edge v3, reused entry), run new UI spec; then merge. Then L3 output-room/reasoning-tokens + Harbor money-invariant inference
1. **REQ-027 sub-steps 5-6** — live e2e per scenario (needs keyed stack) + honest bench rerun (6)
2. **REQ-021 remaining (owner step)** — owner runs `make record-frontier SCENARIO=gross_to_net` and commits the capture; live step-4 check on the owner's host
3. **REQ-024 remaining (owner/CI steps)** — owner re-runs leak audit (expect OK); live frontier numbers need `FRONTIER_API_KEY`; single Nov 3 held-out run
4. **REQ-025 remaining (owner/CI steps)** — 3+ more rehearsals before freeze; live frontier numbers; the real frozen run
5. **REQ-023** — DONE codeable 2026-10-29 (owner picks video cut after REQ-027 lands)
6. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
7. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
