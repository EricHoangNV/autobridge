# Progress

## Current State

REQ-029 CODE DONE in sbx1 (2026-10-29): fail-closed worker gate via control `/internal/verify` proxy + require-flag (compose + local-up), passed gates mint `verification_report_id`; docs_path-only builds 422, CLI fails fast. Remaining: Muse Wonder audit validate run (queued next). Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-026 all DONE (REQ-021 remainder is owner steps: record-frontier capture + live step-4 check)
  - REQ-023 codeable DONE (owner picks video cut after REQ-027 lands)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed (re-verification pending owner)
  - REQ-024 sub-step B code DONE + leak-audit fix (remaining: live frontier numbers + Nov 3 held-out run)
  - REQ-025 sub-step B code DONE + 5 rehearsals (remaining: live frontier numbers, the real frozen run)
  - REQ-027 sub-steps 1-6 DONE (remaining: owner keyed-stack demo verification)
  - REQ-028 code DONE (remaining: owner keyed rerun for live frontier numbers)
  - REQ-029 code DONE (remaining: auditor validate run)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
- Tests: autobridge 90.30% / verifier 91.49% / simulators 90.50% + ui 160 pass; lint + typecheck + ui-typecheck green; openapi schema+client regenerated
- Stack: local stack restarted on new code (e2e subsets 12/12 + 11/11; `make demo` 8/8; proxy verified live via curl); `docker compose config` OK

## Next tasks
0. **REQ-029 (AUDIT)** — Muse Wonder validate run; auditor owns the FEEDBACKS verdict line
1. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then merge/close
2. **REQ-027 iteration (VERIFY)** — owner keyed-stack run (`make demo` 8/8 twice, L3 reasoning line, /knowledge state, UI spec), then sub-step 6 bench rerun
3. **REQ-021 remaining (owner step)** — `make record-frontier SCENARIO=gross_to_net` + live step-4 check
4. **REQ-024 remaining (owner/CI steps)** — leak-audit re-run; live frontier numbers; single Nov 3 held-out run
5. **REQ-025 remaining (owner/CI steps)** — live frontier numbers; the real frozen run (`make beacon-eval`)
6. **REQ-023** — DONE codeable (owner picks video cut after REQ-027 lands)
7. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
8. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
