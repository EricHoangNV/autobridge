# Progress

## Current State

REQ-034 DONE in sbx1 (opencode meta/muse-spark-1.3-contributor (escalation)): FIAS admin reachable from control in Compose via `sim-fias-pabx-admin` sidecar (dumb :8001 TCP pipe, admin-net alias, edge-only target + self-dial guard). 4 acceptance e2e green on `make up` AND `make local-up`; `POST /demo/reset` FIAS targets 200. Code Review Cycle 4 AR-4-1 code-fixed; verdict change is the auditor's.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-026 all DONE (REQ-021 remainder is owner steps: record-frontier capture + live step-4 check)
  - REQ-023 codeable DONE (owner picks video cut after REQ-027 lands)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-024 sub-step B code DONE + leak-audit fix (remaining: live frontier numbers + Nov 3 held-out run)
  - REQ-025 sub-step B code DONE + 5 rehearsals (remaining: live frontier numbers, the real frozen run)
  - REQ-027 sub-steps 1-6 DONE (remaining: owner keyed-stack demo verification)
  - REQ-028 code DONE (remaining: owner keyed rerun for live frontier numbers)
  - REQ-029 code DONE + audit validate run DONE (owner closes once REQ-034 decided; now decided + fixed)
  - REQ-034 DONE (sidecar; 4 e2e green both modes, smoke + demo 8/8 green)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
- Tests: 2556 + 134 + 403 pass / 0 fail (autobridge 90.30%, verifier 91.49%, simulators 90.48%; ui 160 pass); lint + typecheck green
- Stack: Compose stack up in sbx1; make smoke green (incl. new control→FIAS-admin check), make demo 8/8; 4 former-red live tests green both modes

## Next tasks
1. **REQ-035** — sim admin: default token / Directive §2 authorization-boundary + smoke wording (owner decision recorded: (a)+(c), code it)
2. **REQ-036** — amend stale DIRECTIVES §§1/2/3/6/7 bullets in place (+ AR-4-3 controller-count fix)
3. **REQ-037** — stale `.local/ACTIVE` marker must not misroute smoke/demo/e2e mode detection
4. **REQ-038** — deterministic FIAS replay test (kill the wall-clock flake)
5. **REQ-030 → REQ-031 → REQ-032 → REQ-033** — owner UI-first demo (after findings above, before benchmark rounds)
6. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then merge/close
7. **REQ-027 iteration (VERIFY)** — owner keyed-stack run, then sub-step 6 bench rerun
8. **REQ-021 remaining (owner step)** — record-frontier capture + live step-4 check
9. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; Nov 3 held-out run
10. **REQ-025 remaining (owner/CI steps)** — live frontier numbers; the real frozen run
11. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
12. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
