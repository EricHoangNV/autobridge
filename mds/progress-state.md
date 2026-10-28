# Progress

## Current State

REQ-022 registry-out-of-engine owner check DONE (sbx1 2026-10-28). Agent: opencode meta/muse-spark-1.3-contributor (escalation).

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-019 + REQ-017 + REQ-018 all DONE
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-020 codeable work merged (status stale IN_PROGRESS in REQUESTS.MD)
  - REQ-021 matrix full-climb DONE: gross_to_net/invalid_patch_trap climb [0,1,2,3,4] via provider-wired controller path, L3 recorded, oracle rejects, L4 escalate, all other rows 0 frontier calls
  - REQ-021 23:05 check DONE: L3-mislabel fix + raw preview, local-up UI rebuild on source change, knowledge details
  - AR-2-1 … AR-2-6 + AR-1-1 … AR-1-5 code-fixed (re-verification pending owner)
  - REQ-022 sub-steps A + B + C merged + B owner check fixed (edge-sign + fail-fast, thread containment, trust_env=False, strict thread warnings)
  - REQ-022 reset-target owner check DONE: RESET_TARGETS + EXTRA_RESETS registry, endpoint + 3 tests derive from it
  - REQ-022 registry-out-of-engine owner check DONE: demo targets in config/demo_targets.yaml loaded at startup, runtime validation, OpenAPI ids from file, guard green
  - REQ-026 DONE: pytest-timeout 2.4.0 (unit 30 s thread-method, e2e 180 s via conftest mark) + 3 mechanism/mark tests + LEARNINGS #23
- Tests: 2729 pass / 0 fail (autobridge 2305, verifier 99, simulators 325) + ui 156 pass;
  90% gates green (simulators 89.8–90.4 run-to-run random-order noise, pre-existing); lint (incl. import-linter) + typecheck + openapi-check + ui-typecheck green
- Stack: local stack all readyz green (incl. sim-fias-pabx + edge-worker); full make e2e on local stack 145 pass / 2 skip / 0 fail (legacystay guard fixed by this task)
- Known notes (pre-existing, not this task): compose-stack /demo/reset cannot reach sim-fias-pabx (by design: control has no edge-net route; matrix live test is local-stack-only); edge-agent live test flaky under full-suite random order

## Next tasks
0. **REQ-021 remaining (owner step)** — owner runs `make record-frontier SCENARIO=gross_to_net` and commits the capture; live step-4 check on the owner's host
1. **macOS DNS-hermetic fix** — unit tests must not do real DNS (monkeypatch resolver + autouse guard); hermetic bench pipeline test (owner: do next, independent of sub-step D)
2. **REQ-022 sub-step D** — fault scenarios (YAML + verifier checks + e2e): layout change, resync, ack-timeout,
   restart-inflight, cloud-slow, duplicates, oversize/flood, session-lost, stale-command
3. **REQ-022 sub-step E** — demo step + docs (architecture, safety-model) + README + simulators index UI page
4. **P1.W10.3** — [~] OWNER: confirm CI demo + record labelled demo (docs/demo.md)
5. **P1.W2.8** — [~] OWNER: place private seeds in ~/autobridge-heldout/ matching SHA256SUMS
