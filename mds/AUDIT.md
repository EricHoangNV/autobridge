# Directives Audit

Written by the daily audit (`sh ralph-afk.sh --validate <sandbox> 1`): the commit it audited up to, the date, and one line per finding.

Last audited: 8f1b2dca151651a9e787f1fd0a7e4215f2ca93ce
Date: 2026-10-30
Auditor: opencode meta/muse-wonder-1.0 (escalation), sandbox sbx1
Scope: `d2d9c9d2..origin/main` (89 commits: REQ-040…REQ-059 incl. deferred REQ-051, UI-first demo REQ-053/054/056/057/058/059, fair-benchmark REQ-046, frontier quality REQ-052, live semantic climb REQ-039/042/043/047/048/049). Every commit's diff checked against DIRECTIVES §§1–8 (read one section at a time) and plan §1 standing rules.

## Findings

- FIXED — Directive §5/`make test` hermeticity: with a stale `.local/ACTIVE` marker present (dead local stack, the state REQ-037 handles), `make test` failed with 5 teardown errors — unit tests calling demo/stack code sent `bench/stack_mode.py:probe_runtime_mode` to `http://localhost:8101`, performing a real DNS lookup for `localhost` that `tests/unit/conftest.py`'s DNS guard records and fails at teardown. Reproduced in this sandbox (2903 passed, 5 errors, coverage 90.10%); the probe now rewrites `://localhost` to the literal `://127.0.0.1` before probing, and the affected files pass with the stale marker planted (77 passed). This was the "pre-existing stale-.local errors" noted in earlier progress entries — now fixed at the source instead of worked around by deleting the marker.
- OK (verified, no finding) — §1: closed 9-transform registry unchanged in range, repairs JSON Patch only (REQ-049 model proposals applied as patches to a copy, never engine-written), `SEMANTIC_VALUE_DRIFT` proposals reach a release only via the operator's Approve on an escalated repair (`approve_business_decision`: verifier-gated candidate, never learned into L0), releases immutable. §2: import-linter contracts KEPT, `bench/` never imports `verifier`, heldout `SHA256SUMS` untouched in range, FIAS data plane unchanged. §3: every model call through `LLMProvider` + `Budget` (REQ-042: no model call inside a request or held transaction — targeted tests green; REQ-043: repair row exists while climbing, crash/restart → `failed`), L3 gate count derived, known repairs assert `frontier_tokens == 0`. §4: SyncRun Complete|Degraded|Blocked, money Decimal, honest dry-run (REQ-055: a skipped/cannot-evaluate check counts as not passed). §6: `credential_ref`-only specs, no secret values in the range diff, REQ-058 capability request assembled deterministically in the UI with no model call and credential names only. §7: 8 UI routes unchanged, generated OpenAPI client (`make openapi-check` green), REQ-056 plain-language layer is fixed lookup text with no runtime model call. §8: REQ-046/052 bench arms share one context hash per case (tests green), truncation/timeout/no-output labelled, pre-registration amended publicly before the run.
- OK (re-verified) — Cycle 5 findings REQ-040 and REQ-041 are resolved in code: repair provenance threaded by id (two-connector regression tests green) and `ModelUsageChart` restored on the repair page (UI 295 passed).
- NOTED (already tracked, not new) — P1.W10.3 [~] and REQ-021/024/025/027/028/052 remainders are owner keyed/live steps (frontier captures, keyed bench-ablation, held-out/beacon runs); REQ-051 is owner-deferred. Autobridge coverage margin is thin (90.10% vs the 90% gate).

## Feedback loops run (2026-10-30, sbx1)

- `make lint` (ruff + import-linter): pass, contracts KEPT. `make typecheck` (mypy, 130 files): pass. `make ui-typecheck`: pass. `make openapi-check`: pass.
- `make test`: 2903 passed with 5 stale-marker teardown errors before the audit fix (coverage 90.10% autobridge, gate reached); after the fix the affected files pass with the stale marker planted (77 passed). Full re-run after the fix was not repeated (the fix touches only `bench/stack_mode.py`'s probe URL; targeted suites + lint + typecheck green).
- UI Vitest: 295 passed. Targeted unit suites for REQ-040/042/043/052/055/059: 70 passed.
- `make smoke` / live e2e: not runnable in this sandbox (no stack up; smoke's unit leg passes, live leg reports control unreachable) — reported, not faked. The owner runs the full e2e.
