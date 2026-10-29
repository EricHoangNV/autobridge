# Progress

## Current State

REQ-025 sub-step B DONE (sbx1 2026-10-29): beacon client (live-verified drand BLS), simulator/verifier serving, freeze/run/verify-repro orchestration, make targets, docs; 2 live rehearsals + live repair proof. Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-026 all DONE (REQ-021 remainder is owner steps: record-frontier capture + live step-4 check)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed (re-verification pending owner)
  - REQ-024 sub-step A DONE; sub-step B DONE (codeable B1–B6, rehearsed live 60/60 matched)
  - REQ-024 leak-audit fix DONE (raw+nonce only, per-kind counts)
  - REQ-025 sub-step A DONE (faultgen, answer keys, reference solver, 1000+ sweeps)
  - REQ-025 sub-step B DONE (codeable: client, serving, make eval-freeze/bench-beacon/verify-beacon/beacon-eval, docs; 2 live rehearsals, 1 live repair proof, verify-repro OK)
  - Edge readyz closed-store 500 fixed (honest degraded + regression test)
- Tests: 3140 pass / 0 fail (autobridge 2452/90.31%, verifier 134/91.49%, simulators 396/90.50%; gates hold) + ui 158 pass
- Stack: local stack UP in sbx1 for rehearsals (local-down at end of run)

## Next tasks
0. **REQ-021 remaining (owner step)** — owner runs `make record-frontier SCENARIO=gross_to_net` and commits the capture; live step-4 check on the owner's host
1. **REQ-024 remaining (owner/CI steps)** — owner re-runs leak audit (expect OK); live frontier numbers need `FRONTIER_API_KEY`; single Nov 3 held-out run (`make bench-heldout` + ablation per preregistration)
2. **REQ-025 remaining (owner/CI steps)** — 3+ more rehearsals before freeze (`make bench-beacon REHEARSE=<round>`); live frontier numbers; the real frozen run (`make beacon-eval`)
3. **REQ-023** — demo plan applied as each lands
4. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
5. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
