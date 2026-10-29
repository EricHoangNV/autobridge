# Progress

## Current State

REQ-022 DONE (sbx1 2026-10-29): final owner check fixed (e2e heartbeat poll + simulators docs label) + W5.7 guard carve-out for bench reference-spec reads. Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-026 all DONE (REQ-021 remainder is owner steps: record-frontier capture + live step-4 check)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed (re-verification pending owner)
  - REQ-024 sub-step A DONE; sub-step B DONE (codeable B1–B6, rehearsed live 60/60 matched)
  - REQ-024 leak-audit fix DONE (raw+nonce only, per-kind counts)
  - REQ-025 sub-step A DONE (faultgen, answer keys, reference solver, 1000+ sweeps)
- Tests: 2898 pass / 0 fail (autobridge 2397, verifier 122, simulators 379; gates ≥90% hold) + ui 158 pass + e2e 158 pass / 2 skip (keyed local stack)
- Stack: no stack running in sbx1 (local-down after verification)

## Next tasks
0. **REQ-021 remaining (owner step)** — owner runs `make record-frontier SCENARIO=gross_to_net` and commits the capture; live step-4 check on the owner's host
1. **REQ-024 remaining (owner/CI steps)** — owner re-runs leak audit (expect OK); live frontier numbers need `FRONTIER_API_KEY`; single Nov 3 held-out run (`make bench-heldout` + ablation per preregistration)
2. **REQ-025 sub-step B** — beacon client, serving, make eval-freeze/bench-beacon/verify-beacon/beacon-eval, docs; then rehearsals (owner/CI)
3. **REQ-023** — demo plan applied as each lands
4. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
5. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
