# Progress

## Current State

AUDIT 2026-10-29 DONE in sbx1 (opencode meta/muse-wonder-1.0 (escalation)): audited 3e4093b8..2cea2072 (282 commits, REQ-030/031 reverted in-range then re-filed as requests, net code zero). AR-1-1…AR-2-6 + REQ-029 gaps re-verified fixed in code; audit fixed 4 residuals (Compose readyz docker pin, demo cost model from config, unverified-activation guard under require-flag, CI openapi-check + ui-typecheck). Code Review Cycle 3 verdict: FAIL on REQ-034 (FIAS admin unreachable from control in Compose; 4 live e2e red on make up) — see mds/AUDIT.md, REQ-034…REQ-037 filed.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-026 all DONE (REQ-021 remainder is owner steps: record-frontier capture + live step-4 check)
  - REQ-023 codeable DONE (owner picks video cut after REQ-027 lands)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed, re-verified by audit (residuals REQ-034/REQ-035)
  - REQ-024 sub-step B code DONE + leak-audit fix (remaining: live frontier numbers + Nov 3 held-out run)
  - REQ-025 sub-step B code DONE + 5 rehearsals (remaining: live frontier numbers, the real frozen run)
  - REQ-027 sub-steps 1-6 DONE (remaining: owner keyed-stack demo verification)
  - REQ-028 code DONE (remaining: owner keyed rerun for live frontier numbers)
  - REQ-029 code DONE + audit validate run DONE (Cycle 3 stays FAIL on REQ-034/035; owner closes)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
- Tests: autobridge 90.33% / verifier 91.49% / simulators 90.50% + ui 160 pass; lint + typecheck + ui-typecheck + openapi-check green (after audit fixes)
- Stack: Compose stack up in sbx1; make smoke green (full probes), make demo 8/8; e2e spot test_00/test_01/test_02/test_04/test_07/test_027 green, 4 live tests red via REQ-034

## Next tasks
0. **REQ-034 (OWNER DECISION)** — FIAS admin route in Compose (admin-net sidecar vs relay-mediated reset vs documented mode split); unblocks Compose e2e gates for REQ-027/029 + P1.W10.3 CI confirmation
1. **REQ-035** — sim admin: default token / listener placement / Directive §2 + smoke wording (owner decision, then code)
2. **REQ-036** — owner amends stale DIRECTIVES §§1/2/3/6/7 bullets in place
3. **REQ-037** — stale `.local/ACTIVE` marker must not misroute smoke/demo/e2e mode detection
4. **REQ-030 → REQ-031 → REQ-032 → REQ-033** — owner UI-first demo (after audit findings above, before benchmark rounds; REQUESTS priority 2026-10-29 18:40)
5. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then merge/close
6. **REQ-027 iteration (VERIFY)** — owner keyed-stack run (`make demo` 8/8 twice, L3 reasoning line, /knowledge state, UI spec), then sub-step 6 bench rerun
7. **REQ-021 remaining (owner step)** — `make record-frontier SCENARIO=gross_to_net` + live step-4 check
8. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; single Nov 3 held-out run
9. **REQ-025 remaining (owner/CI steps)** — live frontier numbers; the real frozen run (`make beacon-eval`)
10. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
11. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
