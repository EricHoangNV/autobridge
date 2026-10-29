# Progress

## Current State

REQ-027 L3 output-room + reasoning-ledger CODE DONE in sbx1 (2026-10-29, unmerged): L3 frontier call 4096→8192 max_tokens, reasoning_tokens split end to end (Usage → ModelCall/archive ledger via migration 0008 → rung ledger API → demo table/reason → replay recordings → repair-page UI). Harbor money-invariant: docs state no gross totals, labelled gap kept (no guessing). Needs owner keyed-stack run. Agent: opencode meta/muse-spark-1.3-contributor (escalation).

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
  - REQ-027 knowledge-page check + sim-keys defaults MERGED (profiles + refused, API approvals, inject-again L0 reuse, UI e2e; demo keys + step-7 MERIDIAN_API_KEY fix)
  - REQ-027 L3 output-room + reasoning-ledger CODE DONE (8192 budget, per-call split in ledger/API/demo/UI, migration 0008; Harbor gap kept labelled) + fix-first pair CODE DONE (framed override protocol-decided; knowledge-profile poll in steps 7/8) + gate-flake fix (HARBOR_API_KEY leak) — awaiting owner keyed-stack verification (demo 8/8 twice) + merge
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
- Tests: autobridge 90.5% (2509) / verifier 91.5% (134) / simulators 90.5% (396) + ui 160 pass; lint + typecheck + ui-typecheck green
- Stack: no live stack in sbx1 (unit + in-process serving only; e2e live files skip/error without Docker — pre-existing)

## Next tasks
0. **REQ-027 iteration (VERIFY)** — owner: fresh `.env` from `.env.example`, keyed `make local-up && make e2e && make demo` 8/8, check L3 line shows reasoning split, /knowledge state (atlaspms v1, meridianpms v2/v1-refused, PABX edge v3, reused entry), run new UI spec; then merge
1. **REQ-027 sub-steps 5-6** — live e2e per scenario (needs keyed stack) + honest bench rerun (6)
2. **REQ-021 remaining (owner step)** — owner runs `make record-frontier SCENARIO=gross_to_net` and commits the capture; live step-4 check on the owner's host
3. **REQ-024 remaining (owner/CI steps)** — owner re-runs leak audit (expect OK); live frontier numbers need `FRONTIER_API_KEY`; single Nov 3 held-out run
4. **REQ-025 remaining (owner/CI steps)** — 3+ more rehearsals before freeze; live frontier numbers; the real frozen run
5. **REQ-023** — DONE codeable 2026-10-29 (owner picks video cut after REQ-027 lands)
6. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
7. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
