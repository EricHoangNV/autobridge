# Directives Audit

Written by the daily audit (`sh ralph-afk.sh --validate <sandbox> 1`): the commit it audited up to, the date, and one line per finding.

Last audited: b773926bc15ef90fc23958bfb963594447bdf5d7
Date: 2026-11-02
Auditor: opencode meta/muse-wonder-1.0 (escalation), sandbox sbx1
Scope: `d2a13125..origin/main` (5 commits: prior audit db9f2db6, REQ-078 reopen a4139ed6, claim 2b305961, fix f710053d, build note b773926b). Every commit's diff checked against DIRECTIVES §§1–8 (read one section at a time) and plan §1 standing rules.

## Findings

- OK (verified, no finding) — REQ-078 reopen item 1: bench `run_engine_arm` now calls the same shared `prepare_climb_inputs` + `build_dry_run_fault` production calls (`src/autobridge/engine/detect_repair.py:1302-1313`); the faulted sync summary rides `DerivedDiagnostics` in every constructor (ablation `derive_from_summaries`, beacon FIAS derive, beacon general path via `derive_from_summaries`). Tests pin it: spy-on-`handle` climb carries evidence/spec subtree/records/dry-run fault, and the L3-blocks test fails on any "(none recorded)" block. Reason rows name the deciding rung (`l3_attempted` → "L3 model escalated: <question>").
- OK (verified) — Item 4 misses: legacystay `gross_net` money-ratio observer now clusters ratios within 1% of the median (cent-rounded wire values); meridian nesting classifies SCHEMA_RENAME (wire-rename/emptied matchers precede ENUM) with a harbor true-vocab guard test still reading ENUM_NEW_VALUE. Detection-sweep + rehearsal-miss tests pass (58/58 in the two touched test files).
- OK (verified) — Item 8 (simulator defects) was already fixed pre-range in `32a482c7` (meridian strict-ISO + extras serializers, atlas folio extras present in tree); item 10 keyed rehearsal remains the tracked owner step on REQ-078 (loop ran unkeyed round 6603643: engine 0.40/0.40/$0/0 tok/0 wrong). Heldout `SHA256SUMS` and `experiments/beacon/` untouched in range; import-linter contracts KEPT; commit trailers correct (owner commit `a4139ed6` carries none, per AGENTS.md owner-session rule).
- REQ-079 (filed, not fixed) — Directive §5 test hermeticity: full `make test` flakes — 3068 passed with 4 then 3 teardown DNS-guard ERRORs (`conftest.py:88`, background thread resolves `localhost` during activation/scheduler tests; all pass standalone/pairwise). Same family as REQ-076 / Cycle-6 probe fix; pre-dates this range (no scheduler/localhost code touched). Fix belongs in a dedicated REQ (find + join the leaking thread; no retries/quarantines).
- NOTED (no REQ) — `bench/ablation.py` `run_engine_arm` swallows `prepare_climb_inputs`/`build_dry_run_fault` exceptions into `evidence=None`; a future break there would silently re-mask L3 evidence in bench rows. Guarded today by the happy-path tests above; left as-is rather than improvising bench failure-labelling in an audit.
- NOTED (already tracked) — REQ-078 VERIFY (keyed rehearsal) and P1.W10.3 remain owner keyed/live steps; REQ-051 owner-deferred. Autobridge coverage margin thin (90.04–90.06% vs 90% gate). No directive text in §§1–8 found stale, contradictory, or over size limits this cycle (the §3 REQ-078 bullet from the prior audit reads consistently with §1: semantic drift is never auto-patched, its model patch rides the escalation).

## Feedback loops run (2026-11-02, sbx1)

- `make lint` (ruff + import-linter): pass, contracts KEPT. `make typecheck` (mypy, 133 files): pass. `make ui-typecheck`: pass.
- `make test`: autobridge 3068 passed @90.04–90.06% but leg fails on the REQ-079 teardown flake (2 runs); verifier 139 @90.80%, simulators 422 @90.48%, UI 350 passed (39 files) run individually — all ≥90% gates.
- `make smoke`: unit leg 39 passed; live leg not runnable in this sandbox (no stack up, control unreachable) — reported, not faked. Live e2e likewise not run (owner runs the full suite); workstreams touched in range (W4 drift, W8 bench) are covered by the unit suites above.
