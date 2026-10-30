# Progress

## Current State

REQ-059 DONE in sbx1 (Agent: opencode meta/muse-spark-1.3-contributor (escalation)): blank-slate demo — seed builds nothing ("0 connectors · ready to build" + readiness checks); `make demo` 8/8 stand-alone from blank (step 1 builds Atlas from its OpenAPI on v1); Connectors empty leads with "Build a connector" + "0 connectors" header; README + docs/demo.md deck order with PABX v2-refused → v3. Drive-bys: test_08 import.meta load fix, probe_wire_shape on test builders (phantom SCHEMA_RENAME), stale test_08 repair-page assertions after REQ-054/056.

- Completed:
  - P1.W1–W10 all DONE (W10.3 codeable done; owner step: confirm CI demo + record video)
  - REQ-001…REQ-040 all DONE except IN_PROGRESS REQ-021/024/025/027 (remaining bits are owner keyed/live runs)
  - REQ-028 BLOCKED on owner keyed run (see Blocked line in REQUESTS.MD)
  - P2.S1.1 … P2.S7.1 DONE (Phase 2 complete)
  - REQ-034/035/036/037/038/040/041/042/043/044/045/046/047/048/049/050/053/054/055/056/057/058/059 DONE (keyed owner verify steps outstanding where noted)
- Tests: autobridge gate 90.08% / verifier 91.49% / simulators 90.48% / ui 295 pass; make lint + typecheck + ui-typecheck + openapi-check green; test_09 5/5 + test_08 five legs green live from blank; test_10 empty pages 4/4 (mocked + live)
- Stack: local stack up in sandbox (make local-up, no FRONTIER_API_KEY)

## Next tasks
1. **REQ-052 (OPEN)** — benchmark quality (run after REQ-059, before the daily validation)
2. **REQ-028 (VERIFY)** — owner: keyed `BENCH_TRIALS=1 make bench-ablation`, record frontier numbers; then close
3. **REQ-021/024/025/027 (IN_PROGRESS)** — owner keyed/live runs outstanding (record-frontier capture, live frontier numbers, held-out run)
