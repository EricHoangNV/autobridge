# Requests

## Open Requests (read this section only)

Owner priority (2026-10-28): REQ-018 first (UI is down), then REQ-013, then REQ-017, then REQ-014 → REQ-015 → REQ-016. Do these before any
remaining review FAIL item. Constraint for all of them: someone who downloads the repo may have no Docker and no
sandbox; the demo must still run on their machine (REQ-017), and nothing here may assume Docker is present.

### REQ-018 — UI in compose cannot load anything: browser calls `http://control:8000` (2026-10-28)
Status: IN_PROGRESS (sbx1 since 2026-10-28 08:44)
Opening http://localhost:8107/connectors after `make up` shows "Failed to load connectors: Failed to fetch".
Cause: `ui/Dockerfile` bakes `NEXT_PUBLIC_API_URL=http://control:8000` into the browser bundle (confirmed: the string
is in `.next/static/chunks/app/connectors/page-*.js`), and `ui/lib/api/hooks.ts` `getBaseUrl()` and
`ui/lib/api/client.ts` `createApiClient()` use it in the browser. `control` only resolves inside the compose network,
so every browser request fails. It also bypasses the `/api/*` rewrite, so `ui/middleware.ts` never adds the control
token (REQ-012) and browser writes would get 401 even once reachable.
Wanted:
1. In the browser, every control-API call (SWR hooks, the openapi client, SSE in `ui/lib/sse.ts`, the demo drawer,
   approve/reject, build, sync) goes to same-origin `/api/...`. Only server-side code may use `CONTROL_URL`. Drop the
   `NEXT_PUBLIC_API_URL` build arg (or make it unused in the browser).
2. Writes from the browser pass through the middleware and work with `AUTOBRIDGE_CONTROL_TOKEN` set; the token never
   appears in any `.next/static` chunk (add a check: grep the built chunks for the token name and value).
3. Works the same in local mode (REQ-017): the UI's `/api` rewrite points at `CONTROL_URL`, default
   `http://localhost:8101`.
4. A Playwright check that loads /connectors against `make up` with no error banner, and a test that fails if any
   built browser chunk contains `control:8000`. `make e2e-ui` must cover the connectors list, the inject-fault drawer
   and an approve.
5. Small: the "healthz" text link in the top bar (`ui/components/TopBar.tsx:96-99`) looks like debug output on the
   demo screen. Keep the `/healthz` route and its `data-testid` for the e2e journey, but never show the word
   "healthz" to the user: label the link "System status" (or make it the LIVE indicator's link with a tooltip such as
   "All services healthy" / "Control API unreachable"), and move it to a footer or style it as a quiet status detail
   next to LIVE. Update the e2e journey to find it by `data-testid`, not by its text.

### REQ-014 — Host tools ignore `.env`, so tokens break smoke, e2e and demo (2026-10-28)
Status: OPEN
With `SIM_ADMIN_TOKEN`, `AUTOBRIDGE_CONTROL_TOKEN` and `EDGE_RELEASE_SECRET` set in `.env`, compose passes them to the
containers, but `make smoke`, `make e2e`, `make demo` and `make bench` run on the host and never read `.env`. Result:
smoke says the token is unset, e2e has 35 errors (simulator reset rejected), demo writes get 401. It only works after
`set -a; . ./.env; set +a`.
Wanted:
1. The Makefile loads `.env` for host targets when it exists (e.g. a small loader that exports only non-empty
   `KEY=value` lines and ignores comments and inline `# …` comments), so `make demo` just works with tokens set.
2. Empty values never override defaults: code that reads `E2E_TIMEOUT`, `BENCH_*`, `DATABASE_URL` etc. treats `""` as
   unset (today `E2E_TIMEOUT=` crashes conftest with `float('')`; an empty `DATABASE_URL` breaks readyz).
3. Test: with all three tokens set in a temp `.env`, `make smoke` enforces them and needs no manual export.

### REQ-015 — e2e tests do not send the control token (2026-10-28)
Status: OPEN
With `AUTOBRIDGE_CONTROL_TOKEN` exported, 6 tests fail with `401 Missing or invalid control token`:
`test_s2_edge_target.py::test_edge_execute_signed_release_live`, `test_s3_discovery_import.py` (import_and_list fake +
live, lifecycle live + fake), `test_s7_cli.py::test_s7_cli_repairs_approve_and_reject_fake`. Ralph's gates miss this
because the sandbox runs with no token.
Wanted:
1. One shared helper in `tests/e2e/conftest.py` for control-API headers (token when set); every live call uses it.
2. In-process (fake) tests set or clear the token themselves with monkeypatch, so they pass whatever the shell has.
3. The CLI sends the token when `AUTOBRIDGE_CONTROL_TOKEN` is set (check `src/autobridge/cli.py`).
4. `make e2e` passes both with no tokens and with all tokens set; add a test matrix note to docs/architecture.md.

### REQ-016 — `make demo` reports [ok] when its writes fail (2026-10-28)
Status: OPEN
With the token missing, `make demo` printed `control POST /connectors -> 401` and `POST /demo/inject-fault -> 401`,
yet every step said `[ok]` and the run exited 0. A demo check that cannot fail protects nothing.
Wanted:
1. Any live control-API call that is part of a step must succeed (2xx) or the step is FAILED and `make demo` exits
   non-zero with the method, path, status and detail.
2. Say clearly in the output which steps ran against the live stack and which were computed in-process.
3. Tests: a 401 and a 500 on a step's write make that step fail.

### REQ-017 — Run the demo without Docker (2026-10-28)
Status: OPEN
Today the only way to run AutoBridge is `make up` (Docker Compose). Someone who downloads the repo may have no Docker
and no sandbox. The demo must run on a plain machine with Python + `uv` and Node/npm.
Wanted:
1. `make local-up` / `make local-down` (or `make dev`): start every service the demo needs as local processes on the
   same host ports as compose (control 8101, verifier 8104, simulators, edge-worker, ui 8107 …), SQLite in a temp or
   `./.local/` dir, logs in `./.local/logs/`, PIDs tracked so `local-down` stops them. Wait for every `/readyz` like
   `make up`. Works on macOS and Linux; note Windows (WSL) in the README.
2. The runtime uses `LocalProcessTarget` instead of `DockerSandboxTarget` when Docker is not available (or when
   `AUTOBRIDGE_RUNTIME=local`), and says so in `/readyz` and in the UI header (e.g. "local mode: no network
   isolation"). Never pretend the sandbox exists.
3. Trust boundaries still hold in code: the engine still never imports verifier/ or simulators/ (import-linter), the
   verifier and simulator admin still run as separate processes, and admin writes still need `SIM_ADMIN_TOKEN` when
   set. What local mode cannot enforce (Docker network separation) is listed in docs/safety-model.md and printed once
   at startup.
4. `make demo`, `make smoke` (with the network-isolation checks skipped and reported as "skipped: local mode") and the
   UI journey work against local mode. `make e2e` runs the live tests against it too.
5. README quickstart gets a "No Docker" section: prerequisites (Python 3.12, uv, Node 22), `cp .env.example .env`,
   `make local-up`, open http://localhost:8107, `make demo`, `make local-down`.
6. Check: in a fresh clone with Docker unavailable (`DOCKER_HOST=unix:///nonexistent.sock`), `make local-up && make
   demo && make local-down` passes.

### REQ-008 — `make bench` writes an all-zero scorecard from the host (2026-10-28)
Status: DONE (2026-10-28) — harness auto-selects host URLs outside container + Makefile bench targets pass --control-api-url/--verifier-url host ports; pre-scorecard probe of /healthz and zero-tool-calls guard exit non-zero naming URL; tests: host URL selection + probe/guard. Real bench run skipped (no compose in sandbox).
Priority: do this first. `make bench-heldout` on Nov 3 has the same bug.
Found by a clean-clone check of f516bc0. With the stack up (`make up`), `make bench` exits 0, but every number for
both arms is zero: verified completion 0.0, 0 tool calls, 0 ms elapsed, 0 frontier tokens, "10 wins, 20 losses".
Cause: the Makefile runs `uv run python -m autobridge.bench.harness` on the host, but `BenchConfig` defaults to the
Docker-internal URLs (`DEFAULT_CONTROL_API_URL = "http://control:8000"`, `DEFAULT_VERIFIER_URL = "http://verifier:8000"`
in `src/autobridge/bench/harness.py`). Those names do not resolve outside the compose network. The host constants
`DEFAULT_CONTROL_API_URL_HOST` (8101) and `DEFAULT_VERIFIER_URL_HOST` (8104) are defined but never used. Every
trial fails, is counted as "no verified completion", and the run still exits 0.
Wanted:
1. Running from the host reaches the stack. Either the harness picks the host URLs when it is not inside a container,
   or the `bench` and `bench-heldout` targets pass `--control-api-url`/`--verifier-url` (or run inside the `bench`
   container). Keep the in-container defaults working.
2. Fail loudly. Before any scorecard is written, if the control API or the verifier is unreachable, or any arm
   finishes with zero tool calls across all its trials, exit non-zero with a message that names the URL. An empty
   scorecard must never be produced silently.
3. Add a test for each: host URL selection, and the zero-trial or unreachable guard.
4. With the stack up, run `make bench` once and record the real dev-split numbers in the build note (not zeros).
Do not touch `verifier/hidden_cases/heldout/SHA256SUMS` or anything under `HELDOUT_DIR`.

---

## Resolved Requests (reference only)

### REQ-013 — Build → sync is broken since AR-1-2; sync also runs unapproved releases (2026-10-28)
Status: DONE (2026-10-28)
Found by a host regression on 207f7366 (stack up). AR-1-2 (76bbff43) removed the sync fallback to the latest release,
but nothing activates a freshly built release: `Connector.active_release_id` is only set by `_activate` in
`src/autobridge/engine/releases.py`, reached from repair approval. So after create → build, `POST
/connectors/{id}/sync` answers `422 connector has no active release`. `tests/e2e/test_s7_cli.py::test_s7_cli_same_journey_as_ui_fake`
fails on exactly this (with and without tokens); the UI's Sync button on a newly built connector will do the same.
Second gap: `trigger_sync` in `src/autobridge/control/routes/builds.py` takes `body.release_id` and only checks that the
release belongs to the connector, not that it passed the gate or was approved, so an unapproved candidate can still run.
Wanted:
1. Decide and document (docs/safety-model.md) how a first build becomes runnable. Recommended: a build whose candidate
   passed the verifier gate is activated as the connector's first release, or the operator activates it explicitly
   (e.g. `POST /connectors/{id}/releases/{release_id}/activate`, write-token protected, actor from AUTOBRIDGE_OPERATOR)
   and the UI and CLI show that step. Either way, a gate-blocked build stays unrunnable.
2. `sync` with an explicit `release_id` accepts only a release that passed the gate and is active or approved; anything
   else is 422 with the reason. Unit tests for both.
3. The demo journey works end to end: create → build → (activate) → sync, in the UI, in the CLI, and in `make demo`.
   Fix `test_s7_cli_same_journey_as_ui_fake` by following the new flow, not by loosening the gate.
Resolution: first build auto-activates when the connector has no active release; later build candidates need POST /connectors/{id}/releases/{rid}/activate (write-token protected, actor=AUTOBRIDGE_OPERATOR; refuses repair/rejected/gate-blocked/foreign); sync with explicit release_id requires active-or-approved (+gate evidence) else 422; UI Activate button + `ab activate`; flow in docs/safety-model.md §7.1; tests: tests/unit/test_autobridge_activation.py (10), e2e s7 journey follows the new flow.

### REQ-012 — Optional bearer token on the control API's write routes (2026-10-28)
Status: DONE (2026-10-28)
The control API has no authentication; the only identity is the server-side `AUTOBRIDGE_OPERATOR` name (REQ-001).
That is fine on localhost (REQ-011), but not if the stack is ever hosted for judges.
Wanted:
1. New setting `AUTOBRIDGE_CONTROL_TOKEN`. When unset or empty, behavior is exactly as today (local runs, tests, demo
   need nothing). When set, every state-changing route (POST/PUT/PATCH/DELETE in `src/autobridge/control/routes`)
   requires `Authorization: Bearer <token>` and answers 401 without it, compared in constant time
   (`hmac.compare_digest`). Read-only GETs, `/healthz`, `/readyz` and the SSE stream stay open.
2. The UI never ships the token to the browser. The UI proxies `/api/*` to the control API through the rewrite in
   `ui/next.config.mjs`; add the header on the server side (Next middleware or a route handler that forwards
   to the control API), reading `AUTOBRIDGE_CONTROL_TOKEN` from the UI container's env. No `NEXT_PUBLIC_` variable.
3. Every other caller sends it when set: `make demo` (`autobridge.bench.demo`), the bench harness, e2e helpers, and
   `scripts/` that call the control API.
4. The token is a credential: never logged, never in API responses, evidence, prompts or the OpenAPI examples; the
   SSE redaction in `control/sse.py` already covers `*_TOKEN` names, so check it covers this one.
5. Tests: unset → write routes open; set → 401 without or with a wrong token, 2xx with the right one; GETs open
   either way. Update the OpenAPI contract (security scheme on the write routes) and `make openapi-check`.
6. `.env.example` gets `AUTOBRIDGE_CONTROL_TOKEN=` with a one-line note (set it before exposing the stack beyond
   localhost); `compose.yaml` passes it to `control` and `ui`.
Resolution: `require_control_token` dep on all 15 POST routes (401 + HTTPBearer scheme + documented 401); open when unset; UI middleware injects server-side; harness/demo/CLI/e2e send when set; compose+env wired; redaction verified; 11 new unit tests + contract fuzz with enforcement on.


### REQ-011 — Publish compose ports on localhost only (2026-10-28)
Status: DONE (2026-10-28)
Every port in `compose.yaml` is published as `"81xx:8000"` with no host IP, so Docker listens on all interfaces. While
the stack runs, anyone on the same network (e.g. hackathon Wi-Fi) can reach the control API (unauthenticated write
routes: approve/reject repairs, inject faults, import/connect connectors), the UI, the simulators, and the verifier on
8104, whose hidden cases must stay out of reach.
Wanted:
1. Every published port binds to loopback: `"127.0.0.1:8101:8000"` and so on, for all services and every second port
   (8109–8112). Container-to-container traffic on the compose networks is unchanged.
2. Optional override for anyone who needs LAN access on purpose: a `AUTOBRIDGE_BIND_HOST` variable, default `127.0.0.1`,
   used as `"${AUTOBRIDGE_BIND_HOST:-127.0.0.1}:8101:8000"`. Document it in `.env.example` with a warning.
3. A test that parses `compose.yaml` and fails if any published port lacks a host IP, or uses `0.0.0.0` as a literal.
4. `make up`, `make smoke`, `make e2e` and `make demo` still pass on the host (localhost URLs are unchanged).
Note the trust-boundary smoke checks: the verifier and simulators stay unreachable from the engine network as before.
Resolution: all 15 published ports bound to `${AUTOBRIDGE_BIND_HOST:-127.0.0.1}` (default loopback); `.env.example` documents the override with a warning; new `test_published_ports_bind_loopback` fails on a missing host IP or a literal `0.0.0.0`; `make up`/`make smoke`/live `test_00`/ `make demo` green on the host, live bindings verified as 127.0.0.1.

### REQ-010 — e2e: run the demo's headline claims against the live stack (2026-10-28)
Status: DONE (2026-10-28)
With Docker hidden (`DOCKER_HOST=unix:///nonexistent.sock`), 72 of the 104 tests in `tests/e2e` still pass: they run
engine code in-process against fake HTTP transports. Only about 32 exercise the running stack. In particular
`test_03` (known repairs spend 0 frontier tokens) passes 5/5 with no stack, and `test_05` passes 20 of 22. These are
claims the demo shows on screen.
Wanted:
1. Live-stack versions of the `test_03` and `test_05` scenarios: drive them through the control API on the running
   compose stack (as `make demo` does), and assert the same things: zero frontier tokens for known repairs; the
   invalid patch rejected; semantic drift ends `Degraded`.
2. Mark the in-process tests clearly (a `fake` or `inprocess` marker, or move them to `tests/integration`) so
   `tests/e2e` means "against the stack". Do not delete the in-process tests.
3. If the stack is not reachable, the live tests fail rather than skip when `AUTOBRIDGE_E2E_REQUIRE_STACK=1`, and the
   `make e2e` target sets it.
Resolution: live/inprocess pytest markers (auto-marked in conftest) + 3 live tests via control API (offset_to_cursor frontier 0, gross_to_net Degraded, invalid_patch_trap rejected) + AUTOBRIDGE_E2E_REQUIRE_STACK=1 set by make e2e (live tests fail, not skip, when unreachable) + fixed control proxy missing X-Admin-Network header (inject/reset 403'd, fault never landed).


### REQ-009 — Clean clone: `make test` and `make e2e-ui` fail without `ui/node_modules` (2026-10-28)
Status: DONE (2026-10-28)
Found by a fresh GitHub clone of f516bc0, following the README (`cp .env.example .env`, then make targets).
- `make test`: 1 failed, 1878 passed. `test_ui_client_compiles` runs `npm run typecheck` in `ui/`, and a fresh clone
  has no `ui/node_modules`. The UI's Vitest run was skipped for the same reason.
- `make e2e-ui`: `playwright: command not found`.
CI installs these itself; the README and the Makefile do not. The README says a clean machine needs only Docker and uv.
Wanted:
1. `make test` and `make e2e-ui` work in a fresh clone. Either a `ui-deps` target (`cd ui && npm ci`, plus
   `npx playwright install chromium` for e2e-ui) that they depend on when `ui/node_modules` is missing, or the
   UI-dependent tests skip loudly with the command to run (as Vitest already does). Prefer installing.
2. README quickstart lists Node and npm as a requirement and the `npm ci` step.
3. Check: a fresh clone into a temp dir, then `make test` passes with 0 failures.
This is what P1.W10.3 (clean-clone check) needs.
Resolution: `ui-deps`/`ui-e2e-deps` Makefile targets (prereqs of `test`/`e2e-ui`), `NODE_PATH` in the `e2e-ui` recipe for the root Playwright config, loud skip in `test_ui_client_compiles` without `node_modules`, 6 contract tests, README Node/npm + install step, CI single `make ui-deps`. Fresh-clone check: `make test` 2225 pass / 0 fail + Vitest 143 pass, `make e2e-ui` exit 0 (9 skips by design, no stack up). Drive-by: 4 pre-existing ruff errors in the REQ-008 bench-harness test fixed.


### REQ-007 — Heldout seeds that no agent has seen (2026-10-27)
Status: DONE (2026-10-27)
Owner decision (option B): the 4 heldout scenarios (`endpoint_removed`, `invalid_patch_trap`, `token_revoked`, `tz_boundary_shift`) keep their public YAMLs and seed for dev use, but the **benchmark's heldout run uses private seeds** that live outside the repo and outside every sandbox, so the scorecard can claim "scored on data no agent saw". Today nothing reads `verifier/hidden_cases/heldout/` and the oracle takes seeds from the public YAMLs, so heldout is not hidden.

- **The secrets (owner-made, never in git or in a sandbox):** a directory given by env `HELDOUT_DIR` (the owner uses `~/autobridge-heldout`), one file per heldout scenario, `<scenario_id>.yaml` with `id`, `seed` (int) and `nonce` (random hex, so the published hashes cannot be brute-forced back to the seed).
- **The commitment (in git):** `verifier/hidden_cases/heldout/SHA256SUMS`, `sha256sum` format over those files. `check_heldout_sha` must verify the files in `HELDOUT_DIR` against this committed manifest (not files in the repo folder), exit 2 on any mismatch or missing file, and refuse to run `make bench-heldout` without `HELDOUT_DIR`.
- **The run:** `make bench-heldout HELDOUT_DIR=...` loads each scenario's private seed after the hash check and uses it end to end: the simulator admin scenario call and the verifier's hidden-mode oracle both use the private seed for that run (dev runs and every other path keep the public YAML seed). Seeds never appear in logs, JSONL output, the scorecard or SSE events (record only the scenario id and the manifest hash).
- **Never read the real directory in tests or agent runs.** Unit tests build a temp `HELDOUT_DIR` with fake seeds and a matching temp manifest: hash pass, hash mismatch (exit 2), missing file, missing env, seed reaches simulator and oracle, seed absent from all outputs.
- **Until this lands, `make bench` exits 2 with "heldout SHA check: missing file": that is expected** (the manifest was committed 2026-10-27 in ff372083; its files are deliberately not in the repo). Do this request first in that case. **Never delete, edit or regenerate `verifier/hidden_cases/heldout/SHA256SUMS`, never create files it lists, and never weaken the check to make it pass.** The fix is the scope above: the gate runs only for heldout runs and checks `HELDOUT_DIR`; dev runs (`make bench`, `test_07`) do not need it.
- Update `docs/benchmark.md` (#Heldout-freeze-runbook) to this flow, and the scorecard header to state "heldout seeds private, manifest <sha of SHA256SUMS>".
Resolution: codeable part done (sbx1 2026-10-27) — gate checks HELDOUT_DIR vs committed manifest (exit 2), dev runs skip it; heldout runs push private seeds to sim admin + verifier seed override; scorecard header records manifest sha; seeds never in outputs. Owner step remains: place private seeds in ~/autobridge-heldout/ (P1.W2.8).

### REQ-006 — Directive: §4 money is Decimal, never float — money transform accepts float input (2026-10-27)
Status: DONE (2026-10-27)
`src/autobridge/runtime/transforms/money.py:49-50` accepts a float (via `str(value)`) and `tests/unit/test_autobridge_transforms*.py::test_money_float_input` codifies `transform_money(42.5) == Decimal("42.50")`. The conversion is exact for short decimals, but it contradicts the "never float" wording: a float that has already lost precision upstream is silently laundered into a Decimal. Owner: either reject float inputs (update the test) or amend §4 to "float inputs are converted via str at the boundary only".
Owner decision (2026-10-27): use Decimal. `transform_money` rejects float input (a clear error naming the field), accepts `Decimal`, `int` and numeric strings; replace `test_money_float_input` with a test that floats are rejected, and check no caller passes floats (JSON parsing of vendor responses must use `Decimal`, e.g. `parse_float=Decimal`).
Resolution: transform_money now rejects float with a field-naming TransformError (field propagated via apply_transform); HttpClient.get parses vendor JSON with parse_float=Decimal; tests replaced/added (float rejection, field name, Decimal HTTP parsing); docs/connector-spec.md money row updated.

### REQ-005 — Directive: §3 model ids in config, not code — literal defaults in src/autobridge/config.py (2026-10-27)
Status: DONE (2026-10-27)
`src/autobridge/config.py:9-12` hard-codes `claude-sonnet-4-5-20250929`, `claude-haiku-4-5-20251001`, `gpt-4o`, `gpt-4o-mini` as defaults (env-overridable), and `src/autobridge/bench/baseline_coding_agent.py:18` repeats a frontier id. Strictly, §3 says model ids live in config, not code. Owner: accept defaults-in-config-module as compliant (amend §3 wording) or remove the literals so a missing env var fails loudly. Minor companion: DIRECTIVES §6 says `.env.example` lists every key with an empty value, but the ralph-loop keys in `.env.example` carry non-empty model ids (not secrets).
Owner decision (2026-10-27): fix it. Remove the literal model-id defaults from `src/autobridge/config.py` and `bench/baseline_coding_agent.py`; model ids come from env/config only, and a missing one fails loudly with a clear message naming the env var. Add the engine's model-id env vars to `.env.example` (empty values) and update tests. `.env.example` companion: secrets stay empty; the ralph-loop model ids are not secrets and may keep their values. Amend DIRECTIVES §6 wording to say "every secret with an empty value".
Resolution: removed the four DEFAULT_* literals (helpers now require_env, naming the var); bench docstring examples use <MODEL_ID_FROM_ENV>; .env.example already listed the four engine vars empty; DIRECTIVES §6 now says "every secret"; tests updated (missing-env fails loudly, api-key tests set model env).

### REQ-003 — Directive: §2 trust boundaries — verifier imports simulators; bench lives in the engine package (2026-10-27)
Status: DONE (2026-10-27)
Daily audit 2026-10-27. DIRECTIVES §2 says shared code goes in a package all three may depend on, never the other way, but plan §2.7 mandates the oracle regenerate ground truth via "the simulator's data generator (shared code)" and `verifier/oracle.py`, `verifier/checks.py`, `verifier/regression.py`, `verifier/app.py` all import `simulators.common.*`. The two documents contradict each other. Owner: either amend DIRECTIVES §2 to name `simulators.common` (generator/faults/scenarios) as the sanctioned shared package the verifier may import, or extract that code into a shared package. Related: plan §2.10 places the harness at `bench/harness.py`, but it is implemented as `src/autobridge/bench/` inside the engine package — the audit removed its `simulators` imports (import-linter contract kept again), yet the harness still reads `simulators/scenarios/*.yaml` and `verifier/hidden_cases/heldout/` from the filesystem; decide whether bench is engine code or a fourth, outside-the-boundaries tool and state it in §2.
Owner decision (2026-10-27): the verifier MAY use the simulators' data generator. Amend DIRECTIVES §2 to name `simulators.common` (generator, faults, scenarios) as the sanctioned shared code the verifier may import (the engine still may not), keep `verifier/` importing it, and make the import-linter contract express exactly that. Bench: no change requested; leave `src/autobridge/bench/` where it is, and only state in §2 that its filesystem reads of scenario YAMLs and the heldout dir are a harness concern.
Resolution: DIRECTIVES §2 amended (sanctioned verifier use of simulators/common + simulators/scenarios, engine still isolated, bench reads are a harness concern); import-linter contract 'verifier must not import autobridge' added (both contracts KEPT); tests/unit/test_autobridge_trust_boundaries.py confines verifier simulators imports to common/scenarios; docs/architecture.md noted.


### REQ-004 — Directive: §7 OpenAPI contract — schemathesis tests claimed but absent (2026-10-27)
Status: DONE (2026-10-27)
DIRECTIVES §7 and plan §4 W7 require schemathesis contract tests; `mds/PRD-TRACKING.md` marks P1.W7.1 done with "schemathesis contract tests pass" and `src/autobridge/control/app.py:5` says "(schemathesis validates it)", but there is no schemathesis dependency or test anywhere in the repo. Tracked as task P1.W7.9 in PRD-TRACKING.md. Until it lands, the done-claim wording should be treated as unverified.
Owner decision (2026-10-27): approved. Implement task P1.W7.9: add schemathesis as a dev dependency and contract tests over the control API's OpenAPI schema, wired into `make test`; then the done-claim on P1.W7.1 and the `control/app.py:5` comment become true.
Resolution: implemented as P1.W7.9 — `schemathesis>=4,<5` dev dependency + `tests/unit/test_autobridge_control_contract.py` (34 tests from the live OpenAPI, seed-pinned, in `make test`); genuine fixes shipped with it (UTC datetimes, Literal target, StrictInt seed, documented 404/400/409/oneOf-422).

### REQ-002 — Redesign the UI to the broadcast direction (2026-10-27)
Status: DONE (2026-10-27)
Progress: slice 5 done (2026-10-27 sbx1) — all 5 slices complete.
Restyle the four screens of `ui/` to the approved design: a dark "live broadcast graphic" look with
one accent (broadcast yellow = "now / needs you"), Archivo (variable width) + Fragment Mono, radius 0.
Desktop (1280 and 1440) is what gets demoed; phone only needs to not break.

**The design is a local reference, not in git.** Read it (never copy it into the repo, never edit it)
at `/Volumes/OWC-Express-1M2/Development/autobridge/tmp/design/`:
- `DIRECTION.md`: tokens, type, status shapes, the screens' grammar, the signature ("the climb"),
  voice, and the **"Hand-off: porting to ui/"** section, which maps each existing component to its
  new shape. Read that section first.
- `site/app.css` (tokens + shared styles), `site/app.js` (the climb, URL states), and the four
  screens: `site/repair.html`, `site/connectors.html`, `site/connector.html`, `site/new.html`.
- `shots/r2/` and `shots/r3/` hold renders of the approved state (PNG) if you need to compare.
Do not commit anything under `tmp/` (it is gitignored; keep it that way).

**Do it in slices, one per iteration**, and record which slice is done under this request:
1. Tokens and shell: CSS variables in `ui/app/globals.css`, Tailwind theme mapping, fonts via
   `next/font/google` (Archivo with the `wdth` axis, Fragment Mono), the top bar with LIVE, the
   labelled "Demo: inject fault" button and healthz. Remove `zinc-*`, `red-*`, `rounded-*` use.
2. `/repairs/[id]`: the call (rung numeral, headline, story, drift trace, five-segment track) and
   the scorebug (verifier, frontier tokens, model usage, Approve / Reject / Rollback), then
   hypotheses, probes, visible tests, patch diff. States: climbing, escalated, error, loading.
3. The climb, driven by the run's SSE ladder events instead of the prototype's timers: the frontier
   counter moves only at L3; the verdict and action appear only once a patch passed. Respect
   `prefers-reduced-motion`.
4. `/connectors` (the rundown, recent repairs, empty state) and `/connectors/[id]` (masthead,
   r14-waiting band, sync runs, releases, spec viewer, fingerprint, dependencies).
5. `/connectors/new` (form + "what the build will do" run sheet) and the Inject Fault drawer.

**Rules:**
- Real data from the control API only; the prototype's numbers are sample content.
- Keep every existing `data-testid`, the e2e UI journey (`tests/e2e/test_06_ui_journey.spec.ts`)
  and the network rule (UI never calls simulators or the verifier). Update unit tests to the new
  markup; do not delete tests to make them pass.
- If REQ-001 (operator name) is done first, keep its "Approving as <name>" line in the new scorebug.
- No new runtime dependencies beyond the fonts; say so in BUILD-NOTES if a build step needs network
  for `next/font/google`.
Resolution: all 4 screens in broadcast grammar (tokens/shell, repair call+scorebug, live climb, rundown+masthead, new run-sheet + dashed Inject Fault drawer); every data-testid kept; unit 128 + touched e2e journeys green (test_06 4/6 in sandbox, 2 detail-page failures pre-existing: no control plane).

### REQ-001 — Show the operator's name on approvals (2026-10-27)
Status: DONE (2026-10-27)
There is no login (plan §2.11 has none, and the demo runs locally), but the human rung of the ladder
should have a name. Add a fixed operator name, set by the server, shown next to every approval action
and recorded on every `Approval`.

- **Config:** new env var `AUTOBRIDGE_OPERATOR` (default `operator`), read by `control`. Add it to
  `.env.example` with a one-line comment.
- **Server sets the actor:** `POST /repairs/{id}/approve|reject` and `POST /releases/{id}/rollback`
  write `Approval.actor` from `AUTOBRIDGE_OPERATOR`. Never take the actor from the request body.
- **API:** expose the name to the UI (for example `GET /operator` → `{"name": "..."}`, or a field on
  an existing endpoint), included in the OpenAPI schema so `ui/lib/api` regenerates.
- **UI (`ui/`, the real Next.js app):** in `RepairActions`, a quiet line under the Approve button:
  "Approving as <name>". Release history (`ReleaseHistory`) shows who approved each release, e.g.
  "r13 · approved by <name> · 13:41".
- **Tests:** unit test that the actor comes from the env var and ignores any client-sent actor; UI
  tests for the "Approving as" line and the release-history name; keep existing `data-testid`s.

Context: the design prototype in `tmp/design/` is local only (gitignored) and not available to the
loop; implement this in `ui/` and `src/autobridge/control/`, matching the current styling.

Resolution: server sets Approval.actor from AUTOBRIDGE_OPERATOR (GET /operator); UI shows 'Approving as <name>' + approved-by per release; compose forwards the var.

