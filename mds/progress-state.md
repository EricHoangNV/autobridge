# Progress

## Current State

REQ-027 sub-steps 1-4 + owner check-1 fixes MERGED to main (sbx1 2026-10-29). Demo steps 3-4 drive the live chain with zero hand-built inputs; docs builds store the mode-correct base_url verbatim (vendor = connector name); zero-response syncs open no drift; stack-level e2e added (skips without Docker). OPEN NEXT: owner knowledge-page check (vocabulary on profiles incl. refused builds, learned entries via API approvals, inject-again leg, UI e2e) — needs keyed stack + browser. Agent: opencode meta/muse-spark-1.3-contributor (escalation).

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
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
- Tests: autobridge 91% (2494) / verifier 91% (134) / simulators 90% (396) gates hold + ui 158 pass
- Stack: no live stack in sbx1 (unit + in-process serving only; e2e live files skip/error without Docker — pre-existing)

## Next tasks
0. **REQ-027 knowledge-page check (OPEN)** — vocabulary on connector profiles (incl. refused v1), learned entries via API approvals, inject-again leg with times_reused, UI e2e assertion; needs keyed stack
1. **REQ-027 sub-steps 5-6** — live e2e per scenario (needs keyed stack) + honest bench rerun (6)
2. **REQ-021 remaining (owner step)** — owner runs `make record-frontier SCENARIO=gross_to_net` and commits the capture (prompt changed: re-record required); live step-4 check on the owner's host
3. **REQ-024 remaining (owner/CI steps)** — owner re-runs leak audit (expect OK); live frontier numbers need `FRONTIER_API_KEY`; single Nov 3 held-out run (`make bench-heldout` + ablation per preregistration)
4. **REQ-025 remaining (owner/CI steps)** — 3+ more rehearsals before freeze (`make bench-beacon REHEARSE=<round>`); live frontier numbers; the real frozen run (`make beacon-eval`)
5. **REQ-023** — DONE codeable 2026-10-29 (owner picks video cut)
6. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
7. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
