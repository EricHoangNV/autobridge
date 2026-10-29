# Progress

## Current State

REQ-025 rehearsal 3 DONE in sbx1 (2026-10-29): round 6594400, 10 instances / 50 grades, every arm V=0.2 E=0.2 with 0 wrong patches (2 escalate-expected score everywhere; 8 repair-expected novel instances end Blocked on all arms — same signature as rehearsals 1-2, losses published as-is); verify-repro byte-match. Remaining: rehearsals 4-5, live frontier numbers, the real frozen run. Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-026 all DONE (REQ-021 remainder is owner steps: record-frontier capture + live step-4 check)
  - REQ-023 codeable DONE (catalogue + per-entry runs + matrix token_expired row; owner picks video cut)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed (re-verification pending owner)
  - REQ-024 owner check on f32b4b4b FIXED (code): end-to-end engine arms, bench move, rehearsal numbers reported
  - REQ-024 sub-step A DONE; sub-step B code DONE (remaining: live frontier numbers + Nov 3 held-out run)
  - REQ-024 leak-audit fix DONE (raw+nonce only, per-kind counts)
  - REQ-025 sub-step A DONE (faultgen, answer keys, reference solver, 1000+ sweeps)
  - REQ-025 sub-step B code DONE + rehearsals 1-3 (rounds 6593463, 6593540, 6594400; remaining: rehearsals 4-5, live frontier numbers, the real frozen run)
  - REQ-027 sub-steps 1-6 DONE (remaining: owner keyed-stack demo verification)
  - REQ-028 code DONE (taxonomy SCHEMA_RENAME end to end, L0 class+delta scoping, oneshot per-vendor docs + arm availability, `ran` label; remaining: owner keyed rerun)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
- Tests: autobridge 90.4% (2534) / verifier 91.5% (134) / simulators 90.5% (396) + ui 160 pass; lint + typecheck + ui-typecheck + openapi-check green
- Stack: local stack up in sbx1 (rehearsal 3 green; e2e 17/17 on touched files with service-env tokens plumbed)

## Next tasks
0. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation` (or `make bench`), record frontier_oneshot/agent numbers in BUILD-NOTES; then merge/close
1. **REQ-027 iteration (VERIFY)** — owner: fresh `.env` from `.env.example`, keyed `make local-up && make e2e && make demo` 8/8, check L3 line shows reasoning split, /knowledge state (atlaspms v1, meridianpms v2/v1-refused, PABX edge v3, reused entry), run new UI spec; then merge
2. **REQ-021 remaining (owner step)** — owner runs `make record-frontier SCENARIO=gross_to_net` and commits the capture; live step-4 check on the owner's host
3. **REQ-024 remaining (owner/CI steps)** — owner re-runs leak audit (expect OK); live frontier numbers need `FRONTIER_API_KEY`; single Nov 3 held-out run
4. **REQ-025 remaining (owner/CI steps)** — rehearsals 4-5 before freeze; live frontier numbers; the real frozen run
5. **REQ-023** — DONE codeable 2026-10-29 (owner picks video cut after REQ-027 lands)
6. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
7. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
