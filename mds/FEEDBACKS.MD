# Feedbacks

## Latest Review Verdicts (read this section first — stop at the breaker line)

### Security: Cycle 0 — (no review yet)
- (awaiting first security review)

### Code Review: Cycle 5 — PASS (2026-10-29)
- Daily Directives Audit (diff `2cea2072..d2d9c9d2`): Cycle 4's AR-4-1…AR-4-4 are resolved in code and verified live on Compose (FIAS admin sidecar + 4 acceptance e2e green, admin-token demo default + boundary smoke, derived L3 gate count, deterministic FIAS tests 5× green); REQ-030…REQ-033 and REQ-039 claims re-verified. The audit fixed 4 Compose/local mode-split residuals itself (compose `${*_BASE_URL}` placeholders unresolved, seed address check hardcoded to Atlas, smoke token default, state-dependent approve-preview locator) and filed REQ-040 (unscoped newest-RepairRun re-fetch) and REQ-041 (test_s6 chart orphaned by REQ-032) — neither blocks the PASS: no trust-boundary, verifier-gate or secret rule is broken by them.
- test coverage: PASS (`make test`: 2673 autobridge 90.55% + 134 verifier 91.49% + 409 simulators 90.48% + 192 UI, all ≥90%; margins on autobridge/simulators stay thin)
- type safety / lint: PASS (`make lint` incl. import-linter contracts, `make typecheck`, `make ui-typecheck`, `make openapi-check` all green; `make up` + `make smoke` + `make demo` 8/8 live on Compose)

### Product Review: Cycle 0 — (no review yet)
- (awaiting first product review)

---

## Daily Directives Audit — Re-verification of Cycle 4 (Code Review Cycle 5, 2026-10-29)

**Verdict: PASS** — Cycle 4's four findings (AR-4-1…AR-4-4 = REQ-034/035/036/038) are resolved in code and verified live on the Compose stack in this audit, and the REQ-030…REQ-033 / REQ-039 work merged since `2cea2072` holds up against DIRECTIVES §§1–8. The audit fixed four Compose-vs-local mode-split residuals itself (engine services never received `${ATLAS_BASE_URL}`/`${HARBOR_BASE_URL}`, so reference-spec syncs died only in Compose; the demo seed's address check hardcoded Atlas; smoke's positive admin probe lacked the stack's default token; a state-dependent Playwright locator on the repair page) and filed two non-blocking follow-ups: REQ-040 (the sync→repair path re-fetches the newest RepairRun unscoped — a provenance cross-link risk under concurrent syncs) and REQ-041 (REQ-032 orphaned the P2.S6.1 model-usage chart; test_s6 red in both modes). Full findings, evidence and loops run are in `mds/AUDIT.md`.

**Checks performed** (sandbox sbx1 worktree at `d2d9c9d2` + audit fixes): `make lint` PASS (import-linter KEPT), `make typecheck` PASS, `make openapi-check` + `make ui-typecheck` PASS, `make test` PASS (2673/134/409, 90.55%/91.49%/90.48%, UI 192), `make up` + `make smoke` PASS live, `make demo` 8/8, REQ-034 acceptance e2e 4/4 and test_09 5/5 on Compose. `make e2e-ui`: 10 passed / 5 skipped / 3 failed (all REQ-041). Full `make e2e` remains the owner's run; keyed frontier runs (REQ-021/027/028/039 owner steps) were not executed — no `FRONTIER_API_KEY` in the sandbox.

---

## Daily Architecture Review — AutoBridge (Code Review Cycle 4, 2026-10-29 Asia/Ho_Chi_Minh)

**Verdict: FAIL** — no new Critical/High was introduced by the reviewed diff, and every Cycle 1/2 finding (AR-1-1…AR-1-5, AR-2-1…AR-2-6) plus the REQ-029 gaps remain resolved in code. The FAIL is carried by two recurring boundary items from Cycle 3 that are still open at HEAD: the FIAS demo/reset path is unreachable in the Compose topology (AR-4-1/REQ-034), and the simulator admin boundary degrades to a published static header whenever `SIM_ADMIN_TOKEN` is unset — which is the shipped default (AR-4-2/REQ-035). Both contradict DIRECTIVES §2 as written; neither is fixable by weakening the edge isolation or the reset assertions, so both still need an owner decision.

**Reviewed commit / baseline and dirty-state scope.** Baseline: `2cea2072` (Code Review Cycle 3's re-verified head); reviewed HEAD `005d69ed` ("Audit 2026-10-29…", carries `Co-Authored-By: Muse Wonder 1.0` — a review-loop commit, not owner host work). Diff `2cea2072..005d69ed` is 12 files: the audit's four code residuals (`src/autobridge/engine/releases.py`, `bench/demo.py`, `compose.yaml`, `.github/workflows/ci.yml`), their unit tests, and bookkeeping (`mds/AUDIT.md`, `mds/REQUESTS.MD`, progress files, this file). Main checkout was clean (no staged/unstaged/untracked source changes) at review time; nothing was run or written there. Thursday daily review: diff + fixed critical-boundary checks (import/network isolation, verifier response shape, ladder budget/L3 gate, release approval/rollback, redaction), not the weekly full sweep. Review standard: DIRECTIVES Quick Reference and §§2/4/8 (`./directives-for.sh` for the touched paths).

### Findings (ordered by severity)

#### AR-4-1 — FIAS admin API unreachable from control in Compose; `/demo/reset` + `/demo/inject-fault` fail for `sim-fias-pabx` (High, recurring = REQ-034, confirmed by code inspection)

- Evidence: `sim-fias-pabx` is attached to `edge-net` only (`compose.yaml:279-291`); control is deliberately not on `edge-net` (compose header comment `compose.yaml:20-22`; `scripts/smoke.sh` asserts control -/-> sim-fias-pabx), while the demo proxy resolves admin targets as `http://{id}:8001` (`src/autobridge/control/routes/demo.py:93-102`). The hostname cannot resolve from control, so both demo endpoints fail for the FIAS target in Compose mode (Cycle 3 verified this live on `make up`; this cycle re-verified the topology in code — live rerun is forbidden here).
- Affected flow: demo reset/inject for FIAS, and the four live e2e tests named in REQ-034 (`test_03…zero_frontier`, `test_05…semantic_drift`, `test_model_usage_matrix…reset_repeatability`, `test_fias_faults…proxies_to_fias`) stay red on `make up` while passing in local mode — a mode split that hides exactly the topology the demo is supposed to prove.
- Why safeguards don't prevent it: the isolation is intentional (DIRECTIVES §2, REQ-022); the gap is the missing sanctioned admin path, not a weak check. Remediation (DIRECTIVES §2, owner decision per REQ-034): (a) admin-net-only route for the FIAS admin listener without exposing its data plane, (b) route via the edge relay, or (c) documented mode split with a named verified reset path. Validation: the four tests green on both `make up` and `make local-up` with FIAS state provably back to baseline.

#### AR-4-2 — Simulator admin auth degrades to the published static header when `SIM_ADMIN_TOKEN` is empty (the default); smoke counts 403 as "unreachable" (Medium, recurring = REQ-035, confirmed)

- Evidence: `simulators/common/admin.py:56-83` (`_require_admin_network`) returns after the `X-Admin-Network: admin-net` header check when the env token is empty; `compose.yaml` and `scripts/local-up.sh` pass `${SIM_ADMIN_TOKEN:-}` and `.env.example` ships it empty, so a fresh-clone stack accepts the header — a string printed in this repo — from any vendor-net peer that can reach `sim:8001` (Cycle 3 showed `runtime-sandbox → sim-atlas:8001/healthz` answering 403, i.e. reachable). `scripts/smoke.sh:49-63` treats any HTTP error, including that 403, as "unreachable", so the negative check cannot distinguish "route blocked" from "rejected but reachable".
- Impact: bounded (demo simulators, loopback-published ports), but DIRECTIVES §2 claims "no route to the simulator admin API"; as shipped it is an authorization boundary with an empty default secret. Remediation (DIRECTIVES §2, owner decision per REQ-035): non-empty labelled demo token by default, or move the listener off the vendor-reachable interface, or amend the directive/smoke wording; in all cases smoke must fail when an unauthorized write is *accepted*. Validation: fresh-clone (`cp .env.example .env`) smoke + a POST probe asserting rejection semantics, not mere reachability.

#### AR-4-3 — L3 gate count is fabricated (`enumerator_yield_count=1`) when the caller passes none (Low, recurring = REQ-036 item 5, confirmed)

- Evidence: `src/autobridge/repair/controller.py:245-255` calls `run_frontier_climb(..., enumerator_yield_count=1 if enumerator_yield_count is None else enumerator_yield_count)`; the ladder then asserts `< 2` (`src/autobridge/repair/ladder.py:259`) on a number nobody counted. The L3 gate therefore holds vacuously on this path rather than by enumeration. Impact is cost/discipline, not safety: the climb is budget-wrapped and its proposals are never auto-released. Remediation (DIRECTIVES §3): derive the count from the surviving hypotheses (as `run_frontier_climb` already filters) or require the caller to pass it; amend the §3 bullet to name the gating count. Validation: unit test asserting L3 is skipped when ≥2 hypotheses survive on the controller path.

#### AR-4-4 — Flaky FIAS replay unit test makes `make test` non-deterministically red (Low, new, confirmed by execution)

- Evidence: `tests/unit/test_simulators_fias_pabx.py::test_replay_postings_resend_same_pno` failed in this review's full `make test` run (`assert 7 == (6 + 2)` on `postings_sent`, `tests/unit/test_simulators_fias_pabx.py:665`) and passed on immediate rerun (file suite 42/42 green). The test races wall-clock deadlines (8 s pumps) against counter snapshots, so CI red/green depends on machine load. Remediation (DIRECTIVES §5): wait on the counter/condition instead of fixed deadlines, or poll `postings_sent` until it reaches the expected value before asserting. Validation: 20 consecutive runs of the file green.

### Status of prior findings (re-verified at `005d69ed`)

- **Resolved, still fixed:** AR-1-1 (token auth in `simulators/common/admin.py` — subject to AR-4-2's empty-default caveat), AR-1-2/REQ-029 (gate-before-persist + new `_require_verified_release` fail-closed activation at `src/autobridge/engine/releases.py:72-87,319`, covering approve and rollback; sync no longer falls back to an unapproved release, `src/autobridge/control/routes/builds.py:290-316`), AR-1-3 (provenance-bound approval), AR-1-4/REQ-012 (control-token guards on write routes; actor server-attributed from `AUTOBRIDGE_OPERATOR`, `src/autobridge/control/routes/repairs.py:99-101`), AR-1-5, AR-2-1 (`Retry-After` capped, `src/autobridge/runtime/http.py:58-60,312-315`), AR-2-2 (docs_url allowlist + docs_path 422-as-identifier, `builds.py:27-115,194-205`), AR-2-3/REQ-030 (`evict`/`evict_patch` in `src/autobridge/repair/knowledge.py:173-203`), AR-2-4/2-5/2-6 (HMAC edge signatures; lockfile-pinned images; `/readyz` 503 via `src/autobridge/health.py:39-57`). Verifier responses remain aggregate-only (`verifier/app.py:101-113` + leak guard); import-linter contracts kept (see checks).

### Checks performed (isolated worktree `/tmp/autobridge-review4` at `005d69ed`; nothing run in the main checkout; no Compose/e2e/bench per the workspace rule)

- `make lint` — PASS (ruff + import-linter, 2 contracts kept)
- `make typecheck` — PASS (mypy, 118 files)
- `make test` — FAIL (first run: 1 flaky failure, AR-4-4; simulators coverage 90.50%, gate met; targeted rerun of the failing test and its file PASS — recorded as flaky, not green)
- `make ui-typecheck` — PASS
- `make openapi-check` — PASS (50 paths, generated client in sync)
- NOT RUN: `make smoke`, `make e2e`, `make bench`, any Docker/network probe — forbidden in this review. AR-4-1/AR-4-2 live behaviour rests on Cycle 3's `make up` verification plus this cycle's code re-verification.

### Cost and idle-work evidence

The reviewed diff adds no scheduler, timer or model call: demo pricing now reads model ids from config (`bench/demo.py`, DIRECTIVES §3) instead of a hardcoded id, and CI gained the two contract gates. No path in the diff triggers a paid call; the production repair path remains deterministic-first with L2/L3 only through `BudgetedProvider` (`src/autobridge/repair/ladder.py:203`), subject to AR-4-3's fabricated gate count. Idle work is unchanged from Cycle 3 (UI SWR polling + health checks); nothing in this diff grows it.

### Verification gaps and confidence limits

Not executed: live Compose reachability for AR-4-1/AR-4-2 (workspace rule; relies on Cycle 3's live run at unchanged topology), e2e/Playwright, bench runs, heldout contents (never opened). Not re-swept this cycle: MCP gateway, Edge target runtime, Postgres behaviour, two-worker contention — unchanged by the diff and covered at code level in Cycles 2–3 only. `make test` per-package counts were not re-captured after the AR-4-4 flake; coverage figure above is the simulators total printed by the failing run. Findings are code-confirmed unless labelled otherwise.

---

## Daily Directives Audit — Re-verification of Cycle 2 (Code Review Cycle 3, 2026-10-29)

**Verdict: FAIL** — Cycle 2's findings (AR-1-1…AR-1-5, AR-2-1…AR-2-6) and the two REQ-029 gaps are re-verified fixed in code at `2cea2072`, and the audit fixed four further residuals itself (Compose `/readyz` reporting `local` mode, a hardcoded frontier model id in demo cost pricing, activation of unverified pre-gate releases under `AUTOBRIDGE_REQUIRE_VERIFIER_GATE=1`, and CI never running `make openapi-check`/`make ui-typecheck`). The FAIL rests on one material open item: in the Compose stack, control cannot reach the FIAS simulator's admin API (edge-net only by design), so `POST /demo/reset` and `POST /demo/inject-fault` fail for `sim-fias-pabx` and four live e2e tests fail on `make up` (REQ-034). A lower residual (REQ-035): the simulator admin listener is reachable from vendor-net peers and is protected only by a token that defaults to empty. Full findings, evidence and the loops run are in `mds/AUDIT.md`; the open items are REQ-034…REQ-037 in `mds/REQUESTS.MD`.

---

## Daily Architecture Review — AutoBridge (Code Review Cycle 2, full-codebase sweep, 2026-10-28 Asia/Ho_Chi_Minh)

**Verdict: FAIL** — the two material failures from Cycle 1 (AR-1-1 simulator-admin boundary, AR-1-2 unverified release path) are re-verified open, and the full sweep adds a vendor-controlled unbounded stall and a server-side request/file-read primitive in the build route. No new Critical was found; the FAIL rests on the same release-integrity and trust-boundary properties as Cycle 1.

**Scope correction and baseline.** Cycle 1 reviewed only the diff `c8b81130..c51824cd` plus fixed boundary checks. That was the wrong scope for a first review: with no prior reviewed baseline, the whole codebase is the baseline. This cycle is the full sweep across all primary scope (`src/autobridge` engine incl. `bench/`, `cli.py`, `edge/`; `verifier/`; `simulators/`; `mcp_stretch/`; contracts, Compose/Dockerfiles/CI/`ralph-afk.sh`; tests read-only; targeted docs) against `origin/main` at `3d723296`. The only commit since Cycle 1 is Cycle 1's own report, so every Cycle 1 finding was re-checked against unchanged code and remains open; they are not re-argued here, only their status is recorded. Review standard: DIRECTIVES Quick Reference and §§1–8.

### Status of Cycle 1 findings (re-verified 2026-10-28, code unchanged)

- **AR-1-1 (High) — open.** Static `X-Admin-Network: admin-net` header (`simulators/common/admin.py:58,69,75`), admin listener on `0.0.0.0:8001` (`simulators/run.py:35`), vacuous smoke probe (`scripts/smoke.sh:56-58`).
- **AR-1-2 (High) — open.** Unverified candidate persists/approves/activates/syncs (`src/autobridge/engine/build_run.py:88-124,335-355`; `src/autobridge/control/routes/builds.py:128,175-185`; compose sets no `VERIFIER_URL`).
- **AR-1-3 (Medium) — open.** Client-chosen `to_release_id` at approval (`src/autobridge/control/routes/repairs.py:72-73`).
- **AR-1-4 (Medium) — open.** No control-API token (REQ-012); loopback default from REQ-011 confirmed, bind override re-exposes verifier/sim-admin ports.
- **AR-1-5 (Low) — open.** Verdict not bound to requested scenario; silent gate skip on import failure (`src/autobridge/engine/verification.py:54-71`; `src/autobridge/engine/releases.py:84-87`).

### New findings from the full sweep (ordered by severity)

#### AR-2-1 — Vendor-controlled `Retry-After` is uncapped; a slow/hostile vendor stalls jobs unboundedly (Medium, new, confirmed)

- Evidence: `src/autobridge/runtime/http.py:256-258` sleeps for whatever numeric `Retry-After` the vendor returns (`>= 0` is the only check; date form ignored, seconds unbounded). Attempts are bounded (`max_attempts` ≤ 10, `src/autobridge/spec/models.py:281`), but each delay is not: `Retry-After: 86400` × 10 attempts is a multi-day stall. Jobs execute synchronously inside control requests (`JobRunner.run_once()`, `src/autobridge/control/routes/builds.py:148-153`) and `JobRunner._execute` has no wall-clock deadline (`src/autobridge/control/jobs.py:355-398`), so the stall also ties up the control request and the single inline worker path. A compromised simulator/vendor needs no credentials to trigger this — one 429/503 response suffices.
- Remediation (DIRECTIVES §4): cap the honoured `Retry-After` (e.g. min(value, 60s)) and add a per-job wall deadline that ends the run `Blocked`/`Degraded` with the criterion named. Validation: unit test with `Retry-After: 99999` asserting the sleep is capped and the run terminates within the deadline.

#### AR-2-2 — Build route fetches arbitrary URLs and reads arbitrary local files server-side (Medium, new, confirmed)

- Evidence: `POST /connectors/{id}/build` fetches `body.docs_url` with no allowlist (`src/autobridge/control/routes/builds.py:85-92`) and reads `body.docs_path` from the control process's filesystem (`:93-103`); the only guard is a substring check for `generated/connectors`. Fetched/read content becomes `docs_text` and flows into bootstrap, stored evidence payloads, and (on L2/L3 paths) model prompts.
- Affected flow: control sits on `verifier-net` + `admin-net`, so internal service URLs are reachable from exactly the process that must never become a read proxy for them; in host/dev mode the same route can read any file the developer's process can read — including `HELDOUT_DIR` contents or `.env`-adjacent files — and carry it into the engine zone, contradicting the "engine never reads heldout fixtures" boundary (DIRECTIVES §2). Error paths limit raw exfiltration (HTTP errors surface as 422 with the exception string), so this is rated Medium, not High; the file-read path has no equivalent limiter beyond the substring guard.
- Remediation (DIRECTIVES §§2, 6, 8): remove `docs_path` server-side reads (accept uploaded text only), or confine both to an explicit docs directory/allowlist of vendor doc hosts; reject non-http(s) schemes and private/loopback/link-local targets for `docs_url`. Validation: unit tests — `docs_url=http://verifier:8000/healthz` and `docs_path` pointing outside the docs root both 422.

#### AR-2-3 — L0 knowledge cache is never invalidated on reject/rollback (Low, new, confirmed)

- Evidence: the cache is a process-wide singleton (`src/autobridge/repair/knowledge.py:159`), populated whenever the ladder proposes (`src/autobridge/repair/controller.py:106-118`), and replayed at L0 with `verification_level="visible"` after only an applies-cleanly check (`src/autobridge/repair/ladder.py:832-867`). No reject/rollback path evicts entries (`grep` finds no `clear()` caller outside the cache module itself). A patch whose release was rejected by a human or rolled back after a regression can therefore be re-proposed indefinitely at zero frontier cost; only the downstream verifier gate (see AR-1-2) stands behind it.
- Remediation (DIRECTIVES §3): evict on reject/rollback for the (vendor, delta) key, or record the approving release id in the entry and require it to still be active. Validation: unit test — propose, reject, re-run same delta, expect L0 miss.

#### AR-2-4 — Edge "signed release" signature is an unkeyed hash anyone can compute (Low, new, confirmed)

- Evidence: `release_signature` is plain SHA-256 over canonical spec JSON (`src/autobridge/runtime/targets.py:634-648`), and the edge worker accepts `{spec, signature}` and recomputes the same hash (`src/autobridge/edge/app.py:92-100`). Any caller who can reach the edge endpoint can "sign" an arbitrary spec; the mechanism detects tampering/corruption, not an unauthorized release. Impact is bounded today (specs are data-only, edge allowlist is enforced, edge port is loopback-published), so this is a naming/design-integrity issue rather than an exploit.
- Remediation (DIRECTIVES §1): either rename to integrity checksum in docs/API, or key the signature (HMAC with a server-side secret delivered by env) if execution authorization is ever load-bearing. Validation: test that a self-signed foreign spec is rejected (if HMAC adopted).

#### AR-2-5 — Verifier and simulator images build from floating dependencies, bypassing the lockfile (Low, new, confirmed)

- Evidence: `verifier/Dockerfile:16` and `simulators/Dockerfile:14` run `pip install "fastapi>=0.115" …` with open ranges, and all base images are floating tags (`python:3.12-slim`, `node:20-alpine`). The verifier — the root of every acceptance decision — is therefore not reproducible from `uv.lock`/`package-lock.json`: two builds of the same commit can resolve different verifier dependencies. The engine image installs the project (`Dockerfile.engine:16`) but likewise from range-pinned metadata, not the lock.
- Remediation (DIRECTIVES §5): build all images from the lockfiles (`uv sync --frozen` / `pip install --require-hashes` export) and pin base images by digest. Validation: CI step rebuilding the verifier image twice and diffing `pip freeze`.

#### AR-2-6 — Readiness is advisory-only: `/readyz` returns HTTP 200 when degraded, and stub services are always ready (Low, new, confirmed)

- Evidence: `src/autobridge/control/app.py:72-100` correctly detects DB/verifier failure but returns the `readyz()` dict with HTTP 200 in all cases (`src/autobridge/health.py:22-34`); `worker`, `bench`, and `runtime-sandbox` readiness is a fixed pass or a construction self-check that cannot observe the DB or the job pipeline (`src/autobridge/engine/app.py:22-24`). `make smoke` asserts only HTTP 200, so a stack with a dead verifier reports every service "ready" — an operator watching only status codes is misled precisely in the AR-1-2 condition (verifier down).
- Remediation (DIRECTIVES §5): return 503 when any check fails and give `worker` a DB check. Validation: unit test asserting 503 with the verifier check failing; smoke asserting the body, not just the code.

### What the full sweep checked and found sound

- **Spec/patch/transforms (§1):** strict pydantic models (`extra="forbid"`), JSONPath subset enforced by regex with no filters/expressions (`src/autobridge/spec/models.py:64-91`), closed 9-transform registry raising on unknown names (`src/autobridge/runtime/transforms/__init__.py:24-62`), RFC 6902 patch applied to a copy then schema-validated (`src/autobridge/spec/patch.py:63-80`). `enum_extend_requires_evidence` cannot self-satisfy: `evidence_confirmed` is set nowhere in production code (tests only).
- **Runtime semantics (§4):** `parse_float=Decimal` at the HTTP boundary, float rejected by the money transform, `ZoneInfo` business dates from the spec's `timezone_source`, every pagination loop capped (`max_pages`, interpreter `range(max_pages)`), `repeated_cursor` → `Degraded`/`no_loss`, unknown enum → `Blocked`. MCP gateway keeps money as `Decimal` and validates business dates without timezone conversion (`mcp_stretch/gateway.py:133-151`).
- **Drift/repair (§§3–4):** 12-class classifier with `CAPABILITY_REMOVED`/`AUTH_REVOKED` non-fixable and `SEMANTIC_VALUE_DRIFT` escalation-gated; L3 gated on `<2` hypotheses in code; `BudgetExhausted` → L4 bundle with exactly one question (validated in `src/autobridge/repair/escalation.py:60-72`); deterministic-first production path — the deployed repair/build handlers pass no provider, so paid calls in the stock stack occur only in bench arms, which are budget-wrapped (`src/autobridge/bench/harness.py:837-844`). Baseline sync makes zero model calls by construction.
- **Boundaries (§2):** import-linter contracts kept; verifier returns aggregate verdicts in both modes with a hidden-mode leak guard; oracle regenerates from seed with validated private-seed override; heldout manifest gating in `bench/harness.py:502-530`; UI reaches only the control proxy (`ui/next.config.mjs:4-7`); `generated/` releases are write-once (`src/autobridge/spec/registry.py:90-116`); release rows are mapper-enforced immutable with a `(connector_id, version)` unique constraint; row ids are uuid4, so SSE/run endpoints are not enumerable. `ralph-afk.sh` passes provider keys and `GH_TOKEN` into sandboxes as bare `-e NAME` (never on a command line).
- **Jobs/DB (§9 spot):** atomic conditional claim, bounded attempts, orphan reclaim keyed by worker id; portable JSON columns in Alembic 0001–0004 (SQLite now, no SQLite-only constructs found in the migrations read).

### Checks performed (isolated worktree `/tmp/autobridge-review2` at `3d723296`; nothing run in the main checkout; no Compose/e2e/bench per the workspace rule)

- `make lint` — PASS (ruff + import-linter, 2 contracts kept)
- `make typecheck` — PASS (mypy, 93 files)
- `make test` — PASS (autobridge 1890 tests, 91.40%; verifier 97 tests, 93.76%; simulators 241 tests, 96.66%; UI Vitest 143 tests; all ≥90% gates)
- `make ui-typecheck` — PASS
- `make openapi-check` — PASS (40 paths, generated client in sync)
- NOT RUN: `make smoke`, `make e2e`, `make bench`, any Docker/network probe — forbidden in this review. AR-1-1/AR-2-1/AR-2-2 are code-evidenced, not executed against the owner's stack.

### Cost and idle-work evidence

Same conclusion as Cycle 1, now from the full sweep: no scheduler/cron/timer invokes a model; jobs run inline from control requests; the production repair path is deterministic-only (no provider wired), so idle cost is UI SWR polling (5–10 s, list endpoints capped at 100 rows) plus health checks. The one unbounded *time* cost is AR-2-1 (vendor `Retry-After`), which is a wall-clock/availability exposure, not token spend. Per-connector vs global budget interaction under concurrent drift remains unexercised — the deployed handlers construct no `Budget` at all, which is safe for cost but means L2/L3 spend bounding is currently only exercised in tests and bench.

### Verification gaps and confidence limits

Not executed: live Docker network reachability (AR-1-1), the AR-2-2 SSRF against a running stack, e2e/Playwright, bench runs, heldout contents (never opened; manifest logic read only). Not fully read line-by-line: every simulator app variant (atlas/harbor sampled via shared `common/`), all UI components, and `tests/e2e/` bodies beyond the new files. Postgres behaviour, two-worker-on-one-SQLite contention under load, and MCP replay semantics were reviewed at code level only. Findings are code-confirmed unless labelled deployment-dependent (AR-1-4).

---

## Daily Architecture Review — AutoBridge (Code Review Cycle 1, 2026-10-28 Asia/Ho_Chi_Minh)

**Verdict: FAIL** — two material failures are established by code evidence in the inspected scope: (1) the simulator admin trust boundary is bypassable by any vendor-net peer using a published static header, and the smoke negative check that claims to prove isolation cannot detect it (AR-1-1); (2) a candidate release that was never verified (verifier unreachable, or the verifier gate never configured) can be persisted, approved without any re-verification, and activated — and can even be synced without approval (AR-1-2). PASS criteria are therefore not met for the release-integrity and trust-boundary properties. This verdict covers the inspected scope only; coverage gaps are listed at the end.

**Reviewed baseline and scope.** No prior Code Review exists (Cycle 0), so the baseline is the current `origin/main` at `c51824cd` ("REQ-011: bind all compose ports to loopback"). The main checkout was behind `origin/main` by 5 commits (clean except untracked `.pnpm-store/`); the reviewed diff is `c8b81130..c51824cd` (REQ-010 live e2e markers/admin header, REQ-011 loopback port binding; touches `compose.yaml`, `src/autobridge/control/routes/demo.py`, e2e/unit tests, `.env.example`, `Makefile`, `pyproject.toml`). Because the diff touches a trust boundary, the fixed critical-boundary checks were run in addition to the diff review. Recent loop commits carry `Co-Authored-By: Muse Spark 1.3` trailers (unattended ralph output); the baseline commit itself is an owner host commit with no trailer. Review standard: DIRECTIVES Quick Reference plus §§2, 5, 6, 7 as printed by `./directives-for.sh` for the touched paths.

### Findings (ordered by severity)

#### AR-1-1 — Simulator admin API is protected only by a published static header; the smoke "isolation" probe cannot detect access (High, new, confirmed by code inspection)

- Evidence: `simulators/run.py:35` binds the admin app to `0.0.0.0:8001` in the same dual-homed container that sits on both `vendor-net` and `admin-net` (`compose.yaml`, sim-atlas/sim-harbor/sim-legacystay networks). The only application check is a constant header value: `simulators/common/admin.py:58,69,75` require `X-Admin-Network: admin-net` — a string printed in the committed source and now also sent by `src/autobridge/control/routes/demo.py` (inject-fault and reset handlers). `runtime-sandbox` shares `vendor-net` with every simulator, so it has an L2/L3 route to `sim-atlas:8001`; sending the known header is the only remaining step to `POST /admin/scenario` or `POST /admin/reset`.
- Why the safeguard fails: `scripts/smoke.sh:56-58` probes `GET http://sim-atlas:8001/healthz` from `runtime-sandbox` and expects failure. The admin app exposes only `/admin/*` routes, so `/healthz` returns 404 with or without network isolation, and the probe sends no header and no POST. The check passes even when the admin API is fully reachable, so the DIRECTIVES §2 claim "worker/runtime-sandbox have no route to the simulator admin API" is asserted but not enforced or tested.
- Affected flow / trigger: from any vendor-net peer (runtime-sandbox, a compromised simulator, or a sandboxed bench workload with vendor-net egress): `curl -H 'X-Admin-Network: admin-net' -X POST http://sim-atlas:8001/admin/reset`. Impact: fault injection and scenario resets outside the labelled `POST /demo/inject-fault` proxy — fabricated drift, corrupted sync runs, and a channel to manipulate scenario state during benchmark/verification runs. Reachability was established by inspection (compose networks + `0.0.0.0` bind); it was not executed against the owner's stack per the workspace rule.
- Remediation (DIRECTIVES §2): bind the admin listener to the admin-net interface only, or split admin into its own container attached solely to `admin-net`; remove the static header as an authorization mechanism (keep it, if at all, only as a routing hint). Fix the smoke probe to attempt `POST /admin/reset` with the header from `runtime-sandbox` and require 403/connection failure. Validation: updated `make smoke` negative check plus a unit test asserting the admin bind address/network.

#### AR-1-2 — An unverified candidate can persist, be approved, activated, and synced (High, new, confirmed by code inspection)

- Evidence: `src/autobridge/engine/build_run.py:88-124` treats verifier failure/unreachability as "skipped" (`verified` stays `False`; with no `verifier_url`, local visible tests are relabelled as verification), and `:335-355` then creates the `ConnectorRelease` whenever `build_out["success"]` is true — the `verified` flag is recorded but never gates persistence. The deployed wiring makes the skip the default: `compose.yaml` sets no `VERIFIER_URL` for `control`/`worker`, so `src/autobridge/control/routes/builds.py:128` yields `verifier_url=None` unless the API client itself supplies one. Approval does not close the gap: `src/autobridge/engine/releases.py:264-311` (`approve_release`) performs no verification and checks no verification report, and `src/autobridge/control/routes/builds.py:175-185` lets a sync fall back to the *latest* release when no active release exists, i.e. an unapproved candidate is executable.
- Affected flow / trigger: start a build while the verifier is down (or simply run the stock compose stack without `VERIFIER_URL`), then approve the resulting repair/build candidate — or call sync before any approval. The active/executed spec has never passed the verifier or the regression gate, contradicting the release-integrity property ("verifier decides acceptance; regression gate before persistence", DIRECTIVES §§1, 4). The gate-before-persist logic in `src/autobridge/engine/releases.py:_maybe_verify` is correct where invoked, but the build path does not invoke it (`create_release` is called without `verifier_url` at `build_run.py:348-353`).
- Remediation (DIRECTIVES §§1, 4): make release persistence conditional on a passing verifier verdict bound to the candidate spec signature, or quarantine unverified candidates so approve/sync reject them with 409; set `VERIFIER_URL=http://verifier:8000` in compose for `control`; remove the sync fallback to unapproved releases (422 when no active release). Validation: unit test — build with a failing/unreachable fake verifier persists no activatable release and approve returns 409/422; e2e `test_05_safety_and_semantic.py` extension.

#### AR-1-3 — Approval is not bound to the repair's candidate release (Medium, new, confirmed)

- Evidence: `src/autobridge/control/routes/repairs.py:72-73` accepts a client-supplied `body.to_release_id` for any `RepairRun` whose decision is `propose`, falling back to the provenance-linked release only when the client omits it. `approve_release` (`src/autobridge/engine/releases.py:286-300`) validates connector consistency only when the client also supplies `from_release_id`/`connector_id`; the target's connector is otherwise inferred from the target itself. Actor attribution itself is correct (`AUTOBRIDGE_OPERATOR`, client actor ignored, `repairs.py:86-88`).
- Impact: within the accepted no-auth control-plane model, any caller who can reach the control API can activate an arbitrary existing release (stale, rolled-back, or built for a different repair) under the audit cover of an unrelated proposed repair. Combined with AR-1-2, an unverified release can be activated with a misleading provenance trail.
- Remediation (DIRECTIVES §7, plan §2.11): ignore client `to_release_id` (or require it to equal the release whose `provenance_repair_run_id` matches and whose parent is the current active release); return 422 on mismatch. Validation: unit test approving with a foreign `to_release_id` expects 422 and no activation.

#### AR-1-4 — Control API remains unauthenticated; REQ-011 mitigates the default, REQ-012 is still open (Medium, recurring-by-design, deployment-dependent)

- Evidence: no `AUTOBRIDGE_CONTROL_TOKEN` or bearer check exists in `src/autobridge/control/` (REQ-012 in `mds/REQUESTS.MD` is still OPEN). The reviewed diff correctly binds every published port to `127.0.0.1` by default with a tested override (`compose.yaml`, `tests/unit/test_autobridge_compose.py:167-183`). However, setting `AUTOBRIDGE_BIND_HOST=0.0.0.0` re-publishes not only the control API and UI but also the verifier (8104) and the simulator admin ports (8109–8112) — the latter still "protected" only by the AR-1-1 header.
- Assessment: on the default loopback binding this matches the stated deployment assumption (no public deployment, network placement as the control) and is not by itself a FAIL driver. It becomes one in combination with AR-1-1 if the stack is ever LAN-exposed. Remediation: implement REQ-012 before any non-loopback exposure; consider excluding verifier/simulator-admin ports from the bind override entirely. Validation: REQ-012's own test list (401 without token, open GETs, `make openapi-check`).

#### AR-1-5 — Engine gate does not bind the verdict to the requested scenario; gate skip is silent on import failure (Low, new, confirmed)

- Evidence: `src/autobridge/engine/verification.py:54-71,161-166` parses `scenario_id` from the verifier's response and never compares it with the requested scenario (nor the mode or spec signature); a pass verdict for a different scenario would be accepted as `allowed`. Separately, `src/autobridge/engine/releases.py:84-87` swallows any exception while importing the gate module and proceeds without verification. Today the verifier is trusted and same-hosted, so exploitability is low; the weakness matters if the verifier endpoint is ever redirected via the client-supplied `verifier_url` (see AR-1-2).
- Remediation (DIRECTIVES §4): assert response `scenario_id`/`mode` match the request and record the spec signature alongside the verification report; fail closed (raise) instead of returning silently in `_maybe_verify`. Validation: unit test with a mismatched-scenario fake verifier response expects `GateBlockedError`.

### What was checked and held up

- Import isolation: `make lint` import-linter contracts KEPT (`autobridge` ↛ verifier/simulators; verifier exception limited to `simulators/common` + `simulators/scenarios`); verifier responses in both modes are aggregate-only (`verifier/app.py` response model + hidden-mode leak guard at `:356-361`).
- Ladder/budgets: L2/L3 go through `BudgetedProvider`, the L3 `<2` hypotheses gate is asserted in code (`src/autobridge/repair/ladder.py:180-184`), escalation classes `CAPABILITY_REMOVED`/`AUTH_REVOKED` are non-fixable in `src/autobridge/drift/classifier.py:59`; probes are GET-only by construction (`src/autobridge/discovery/probes.py:58-59,186`). Money is `Decimal` at the HTTP boundary (`src/autobridge/runtime/http.py:305`, `parse_float=Decimal`); `repeated_cursor` → `Degraded`/`no_loss` and unknown enum → `Blocked` are implemented in `src/autobridge/engine/sync_run.py`. Job claiming is an atomic conditional update with a bounded retry budget (`src/autobridge/control/jobs.py:308-344`).
- Release mechanics where invoked: schema validation before persist, gate before persist in `create_release`, immutable new-version rollback, server-attributed approval actor.

### Checks performed (isolated worktree `/tmp/autobridge-review` at `c51824cd`; nothing run in the main checkout, no Compose/e2e/bench per the workspace rule)

- `make lint` — PASS (ruff + import-linter, 2 contracts kept)
- `make typecheck` — PASS (mypy, 93 files)
- `make test` — PASS (autobridge 1890 tests, 91.40%; verifier 97 tests, 93.76%; simulators 241 tests, 96.66%; UI Vitest 143 tests; all ≥90% gates)
- `make ui-typecheck` — PASS
- `make openapi-check` — PASS (40 paths, generated client in sync)
- NOT RUN: `make smoke`, `make e2e`, `make bench`, Docker/network probes — forbidden in this review (owner's live stack); AR-1-1 reachability is therefore code-evidenced, not executed.

### Cost and idle-work evidence

No scheduler, cron, or timer-driven model call was found: jobs execute inline via `JobRunner.run_once()` from control requests (`src/autobridge/control/routes/builds.py:148-153`), the `worker` service is a health-only stub (`src/autobridge/engine/app.py`), and no background loop invokes L2/L3. Baseline sync therefore makes zero model calls by construction, consistent with the new e2e `test_03_known_repairs_zero_frontier.py` (read, not run). Idle work is bounded to UI SWR polling (5–10 s intervals, `ui/lib/api/hooks.ts:23-33`, list endpoints capped at 100 rows) — acceptable, but it grows linearly with open screens, not connectors. Under repeated drift, spend is bounded by `Budget` per repair attempt; per-connector/global budget interaction under 10× concurrent drift was not exercised (see gaps).

### Verification gaps and confidence limits

Not inspected or not executable in this review: live network reachability (AR-1-1 not executed against Docker), e2e/UI Playwright behaviour, bench matched-budget execution and heldout contents (never opened; manifest gating reviewed in code only), MCP gateway and Edge target runtime semantics beyond the fixed boundary checks, Postgres behaviour (SQLite reviewed), and concurrency of two workers on one database beyond the claim-path reading. Findings AR-1-1/1-2/1-3 are code-confirmed; their live exploitability in the owner's running stack was not tested, by design of the workspace rule.

---

# Detailed Review History (reference only — read specific entries when you need context)

(none yet)
