# Learnings

Distilled insights from code reviews, security reviews, product reviews and debugging sessions. Read and write it as `LEARNINGS-INSTRUCTIONS.MD` says.

## 1. `internal: true` Compose networks break host port publishing and service DNS

**Context:** P1.W1.2 — `make up` reported all services healthy, but `make smoke` could not reach the verifier on its host port, and control could not resolve `verifier` by name.

- A service attached only to an `internal: true` network (the verifier on `verifier-net`) gets no published host port, even with an explicit `ports:` mapping.
- Flipping an existing internal network to a plain bridge in place leaves containers without their service-name DNS aliases; only `docker compose down` (removing the networks) and a fresh `up` restored `verifier` resolution.
- **Rule:** Isolate Compose trust zones by network attachment only, never `internal: true` on a network whose services need host ports; after changing a network's `internal` flag, recreate from `docker compose down`, and guard it with a static compose test (`test_no_network_is_internal`).

---

## 2. Bare `on:` / `off:` / `yes:` keys in YAML parse as booleans and break spec key names

**Context:** P1.W1.3 — the plan's `retry: { on: [429, 503] }` example parsed under PyYAML (YAML 1.1) as `{True: [429, 503]}`, failing pydantic validation with a cryptic `invalid_key` error far from the real cause.

- PyYAML coerces unquoted `on/off/yes/no/y/n/true/false` mapping keys (and values) to booleans, so any spec/schema/scenario key spelled that way is unrepresentable without quoting — a trap every future YAML author (simulator scenarios, connector specs) will walk into.
- Renaming the field sidesteps it entirely: `retry.statuses` instead of `retry.on`, with the reason recorded in a code comment so nobody "fixes" it back to match the plan's spelling.

**Rule:** Never use `on/off/yes/no` (or any YAML 1.1 boolean spelling) as a spec, schema or scenario key; pick a synonym and comment why.

## 3. mypy hides `rowcount` on DML `Session.execute`; ruff TC006 wants the cast target quoted

**Context:** P1.W1.6 — `session.execute(update(Job)...).rowcount` failed typecheck (`Result` has no attribute `rowcount`) even though it works at runtime (DML returns a `CursorResult`).

- `cast(CursorResult, ...)` with a runtime import fixed mypy but tripped ruff TC006 (typing-only names belong in quotes); the combination that satisfies both is `cast("CursorResult", ...)` with `from sqlalchemy import CursorResult` inside the `TYPE_CHECKING` block.
- **Rule:** For DML rowcounts, write `cast("CursorResult", session.execute(stmt)).rowcount` and import `CursorResult` under `TYPE_CHECKING` — every future task that writes UPDATE/DELETE through the ORM will need exactly this shape.

---

## 4. E2E exact-text assertions anchor on table headers that restyles remove

**Context:** REQ-002 slice 4 — converting `ReleaseHistory` from a `<table>` to a broadcast `<ol>` broke `tests/e2e/test_06_ui_journey.spec.ts:483` (`getByText("Approved by", { exact: true })`), even though the row text (`approved by e2e-operator`) was unchanged.

- The journey was not matching the row text at all: exact match needs the whole string, and the only element whose full text was exactly `Approved by` was the old `<th>Approved by</th>` column header. Dropping the table silently removed the anchor.
- Unit tests stayed green throughout (they assert testids and row text, never the header), so only the browser run caught it.
- Fixed by adding a quiet two-column label line (`Release` / `Approved by`) above the broadcast list — same grammar as the design's `.clbl` labels, keeps the journey green without reintroducing the table.

- **Rule:** Before converting a table to a list, grep the e2e specs for exact-text assertions that the table headers satisfy; carry each one over as a visible label in the new markup and re-run the journey spec before committing.

## 5. A Dockerfile `COPY` of a scaffold dir that was never committed breaks `make up` on every clean clone

**Context:** P1.W10.3 (2026-10-27) — `make up` failed in a fresh worktree at the `ui` image build: `COPY --from=builder /srv/ui/public ./public: not found`. W7.4's Next.js scaffold never created `ui/public/`, and no prior run caught it because existing checkouts/sandbox images either had the dir incidentally or built with cache.

- Docker `COPY` has no tolerate-missing flag: one absent scaffold path fails the whole `docker compose up --build`, so no service starts and the W10 passing chain (`up → test → e2e → demo`) never runs.
- Fixed by committing the scaffold dir itself (`ui/public/.gitkeep`) rather than editing the Dockerfile — the Dockerfile was written against the standard Next.js layout, the layout was what drifted.

- **Rule:** When a Dockerfile `COPY`s a scaffold path, that path must exist in git (add `.gitkeep` for intentionally-empty dirs); after touching any `COPY` source or scaffold task, verify with a clean-context build (`docker compose build --no-cache <svc>` or a fresh worktree), not an incremental one.
---

## 6. Node resolves modules from the importing file, not the shell cwd — a config outside the package dir needs NODE_PATH

**Context:** REQ-009 (2026-10-28) — `make e2e-ui` failed in a fresh clone with `Cannot find module '@playwright/test'` even with `ui/node_modules` fully installed, while the Makefile comment claimed the journey was "green with mocks when the stack is down".

- The `test:e2e` npm script runs with cwd `ui/`, but the config it loads (`../playwright.config.ts`) lives at the repo root, and Node resolves `import ... from "@playwright/test"` relative to the importing file — walking up from the repo root, where no `node_modules` exists — never consulting the shell's cwd.
- The first symptom (`playwright: command not found`) masked the second: installing the browsers fixed the binary but the config load still crashed. The docs already recorded the workaround (`NODE_PATH=ui/node_modules ...`), but it never made it into the automation.
- Fixed in the `e2e-ui` recipe (`NODE_PATH="$$PWD/node_modules"` after `cd ui`), keeping the npm script as the single flag definition; added a Makefile-contract test so the env var cannot silently drop out.

**Rule:** When a tool loads a config file outside the package dir, set the module path (NODE_PATH) at the invocation site — cwd does not affect module resolution — and cover the invocation, not just the install, with a test.

---

## 7. A proxy that swallows upstream 403s makes "labelled" fault injection silently do nothing

**Context:** REQ-010 (2026-10-28) — `POST /demo/inject-fault` proxied to the simulator admin API without the `X-Admin-Network: admin-net` header that `simulators/common/admin.py` requires on every write, so the admin answered 403 and the fault never landed. The endpoint still returned 200 (`labelled: true`) with the 403 buried in `admin_response._http_status`, so `make demo` steps 3–4 and every consumer looked green while injecting nothing.

- The admin module's own docstring said "real callers (control proxy, e2e harness) send the header" — the harness did, the proxy didn't; the doc described intent, not code.
- The new live test caught it only because it asserts the injection *landed* (`admin_response._http_status == 200`) instead of trusting the 200 envelope — the same class of mistake as asserting on the demo's local-evidence output alone.
- Fixed by sending the header in `inject-fault` and `reset` (plus env-override parity for reset), with unit tests asserting the header on the mocked `httpx.Client` call.

**Rule:** Any proxy endpoint that returns success while delegating the real work must assert the upstream status in at least one test — a 200 envelope around a failed delegation is a silent lie.
---

## 8. Declaring an OpenAPI security scheme makes schemathesis require 401s, even for optional auth

**Context:** REQ-012 (2026-10-28) — optional `AUTOBRIDGE_CONTROL_TOKEN` on control write routes

- Adding `HTTPBearer` security to the 15 POST routes turned 4 previously-green schemathesis cases red (`ST401`, `ST203`, `ST201`): the fuzzer generates unauthenticated cases and expects 401/403 once a scheme is declared, but with the token unset the routes correctly return 2xx.
- Root cause: OpenAPI has no "optional security" that matches conditional runtime enforcement — the declared scheme reads as required to the fuzzer.
- Fixed two ways: documented the `401` response on every guarded route (shared `AUTH_401` so the 401 shape validates), and set the token in the contract test's fixture so enforcement is active while fuzzing; the 2xx paths stay covered by the functional API tests.

**Rule:** When auth is conditional at runtime but declared in OpenAPI, run contract fuzzing with enforcement switched on and document the 401, otherwise the fuzzer will flag the open-when-unset path as an auth failure.

## 9. A published header is a routing hint, never a credential — new shared secrets follow the REQ-012 pattern

**Context:** AR-1-1 (2026-10-28) — simulator admin writes were guarded only by `X-Admin-Network: admin-net`, a value printed in source, docs and every caller, so any vendor-net peer could replay it; the smoke probe (`GET /healthz`, no header, expecting failure) passed even when the write path was wide open.

- Fixed with `SIM_ADMIN_TOKEN`: env-only secret, `X-Admin-Token` header, `hmac.compare_digest`, enforced-when-set / open-when-unset — the exact semantics the reviewer already accepted for `AUTOBRIDGE_CONTROL_TOKEN` (REQ-012), so re-verification has a precedent to check against instead of a new auth model to evaluate.
- The smoke fix that makes the check meaningful is a *write* probe with the old credential (header-only POST must 403 when the token is configured), not a read probe without it; the vacuous probe failed to detect access because it never attempted what the attacker would attempt.
- Consolidating healthz/readyz onto the same gate as writes removed three copies of the check that could drift apart again.

**Rule:** When a finding says "static value is not authorization", introduce the secret with the repo's existing optional-token semantics (env-only, empty default, constant-time compare, 401/403 naming the missing header never the value), forward it in every caller (proxy + harness + e2e helpers), and upgrade the negative probe to replay the *old* credential against the *write* path.

## 10. Close an unverified-persistence hole by quarantining, not by schema migration

**Context:** AR-1-2 (2026-10-28) — build candidates that never passed the verifier were persisted and then approvable/activatable/syncable. The review offered two remediations: gate persistence on a passing verdict, or quarantine unverified candidates. A `verified` column plus approve/sync enforcement would have broken ~30 existing tests that approve gate-less releases and required an Alembic migration.

- Passing `verifier_url`/`scenario_id` through to `create_release` reuses the existing gate (fail-closed on unreachable via `GateBlockedError`); catching it at the call site and persisting *nothing* quarantines the candidate with zero schema change, and all no-verifier tests keep passing because the gate still skips when unconfigured.
- The verifier URL belongs only on services with a verifier-net route: setting it on `worker` (engine-net only) would fail closed on every worker-executed build, which is worse than the narrow race it would close — so verification placement follows the network topology, and the residual is documented instead of papered over.
- Keep `success: True` on a gate-blocked build: the build (docs → draft) worked; it is the *release* that was withheld. Conflating the two would misreport the lifecycle.

**Rule:** When a persistence gate is missing, thread the existing check into the creation call and quarantine by not persisting (blocked error → no row, reason on the parent run) rather than adding enforcement columns; configure the check only where the trust-boundary topology lets it succeed, and document the path that inherently cannot be checked.

## 11. A client-supplied id that names what to activate is an assertion, never a selector

**Context:** AR-1-3 (2026-10-28) — `POST /repairs/{id}/approve` accepted a client `to_release_id` for any `propose` run, so any caller could activate a stale/rolled-back/unverified release under an unrelated repair's audit cover.

- Resolve the target server-side (here: latest release with `provenance_repair_run_id == repair_id`); a supplied id only *confirms* the resolved one and mismatches 422. Enforce the same invariant in the engine function, not just the route, so direct callers cannot substitute either.
- Existing tests that approved unlinked releases needed provenance at creation time, not patched afterward — release rows are immutable (`before_update` raises), so the helper pattern is "create the repair run first, pass `provenance_repair_run_id=` into `create_release`".
- Describing the semantics on the schema field (`RepairDecisionRequest.to_release_id`) and regenerating OpenAPI/TS client keeps the contract visible to API consumers and the UI.

**Rule:** When a request id selects a security-sensitive target, ignore-and-resolve beats validate-and-trust: resolve the only legal target from server state, treat the client's value as an assertion (422 on mismatch), bind it again one layer down, and document the semantics in the schema description.

## 12. A route that fetches a caller-supplied URL is a read proxy — constrain the target, never the content

**Context:** AR-2-2 (2026-10-28) — `POST /connectors/{id}/build` fetched any `docs_url` from the control process (verifier-net + admin-net member) and read any local `docs_path`, so an internal URL or a container/dev filesystem path became server-side readable, with the content flowing into bootstrap evidence and (on L2/L3 paths) model prompts.

- Removal beats confinement when the read has no legitimate server-side caller: the CLI already reads `--docs-file` client-side into `docs_text`, so deleting both server reads broke nothing and left no allowlist directory or env var to maintain; `docs_path` survives as an opaque identifier so existing callers/tests keep their 200s.
- Layer the URL check deterministically-first: scheme + hostname denylist + literal-IP `is_global` are pure and unit-assertable; DNS resolution is a best-effort bonus with a short timeout that fails open (sandbox DNS is unreliable). Tests must only depend on the deterministic layers.
- `parsed.hostname` already strips userinfo, but the *error path* re-echoes the caller URL — redact userinfo there too (§6), including inside the library exception string (`str(exc).replace(raw, redacted)`), or a `http://user:secret@host` 422 leaks the secret it was meant to protect.
- Keep the OpenAPI contract untouched when only server behavior changes (same fields, same 422 shapes): no schema edit means `make openapi-check` passes with zero generated-file churn. And never run `ruff format` on a repo that is not format-clean when `make lint` is check-only — it buries the fix in unrelated hunks.

**Rule:** When a request causes the server to fetch or read, treat the server as the attacker's proxy: delete the primitive if no legitimate caller needs it (identifier-only over file-read), else allowlist the *target class* (scheme/host/routability, DNS as fail-open bonus); scrub caller secrets from every error echo; and keep the diff to behavior, never reformatting.

## 13. A gate verdict is meaningless unless bound to its request — echo-check, then record what ran

**Context:** AR-1-5 (2026-10-28) — the engine's verifier client accepted any `pass` verdict without comparing the response `scenario_id`/`mode` to the request, so a pass for a different scenario would have counted as allowed; and the release path silently skipped verification when the gate module failed to import.

- The real verifier already echoes `scenario_id` and `mode` in every response — binding is a three-line comparison in the client (`scenario_mismatch` / `mode_mismatch` → `GateBlockedError`), not a protocol change. The same echo discipline applies to the best-effort inline check in `build_run.py`, which previously set `verified=True` on verdict alone.
- Recording the verified spec's sha256 on the outcome (`GateOutcome.spec_signature`, same function that fills `ConnectorRelease.signature`) turns "verified" from a boolean into a binding between a verdict and an exact spec dict — evidence that names what ran instead of what was asked for.
- A test mock that returns a fixed scenario silently encodes the bug: existing tests requested `baseline` while the mock claimed `rename_guest_id` and passed. Making the mock echo the request (like the real service) with explicit overrides for the mismatch case keeps old tests green and makes the new invariant testable.
- Skip-on-error and fail-closed differ by one keyword: `except Exception: return` in `_maybe_verify` became `raise ReleaseError(...)`. Any "convenience skip" on a security-relevant path should read as a conscious, logged decision — never a bare return in an except arm.

**Rule:** Every allow-decision from a verifier/gate must assert the response echoes the request (ids, mode, and the signature of exactly what was checked); test doubles must echo too, or they certify the misbinding; and a gate that cannot run must raise, never silently permit.
---

## 14. A success cache without a failure path replays rejected work forever

**Context:** AR-2-3 (2026-10-28) — the L0 `KnowledgeCache` was populated on every `propose` but no reject/rollback path evicted entries, so a human-rejected or regressed patch was re-proposed indefinitely at zero frontier cost with only the downstream verifier gate standing behind it.

- Evict by the artifact that failed, not by the cache key: reject/rollback know the repair run's `candidate_patch` but not the `(vendor, delta)` key it was stored under, so `evict_patch` (canonical-JSON patch equality, all matching keys) catches the same rejected patch cached under several delta keys; `evict(vendor, delta)` covers targeted single-key drops.
- Keep eviction best-effort and out of the decision path: `contextlib.suppress(Exception)` around the eviction with the function-level import means a cache bug can never break `reject_release`/`rollback_release` (which flush the `Approval` first, then evict).
- Rollback needs provenance to find the patch: the superseded release's `provenance_repair_run_id` leads to its `candidate_patch`; releases created outside any repair run (no provenance) simply skip eviction.

**Rule:** Every cache that stores successful outcomes needs a named invalidation call on every failure path that can disprove a cached entry — evict by the failed artifact's identity, make the eviction unable to fail the decision, and follow provenance links when the failure record does not carry the artifact itself.
---

## 15. A documented 5xx still trips contract fuzzers from the inside

**Context:** AR-2-6 (2026-10-28) — making control `/readyz` return the honest 503 when degraded broke the schemathesis contract test, even though the 503 was documented in OpenAPI.

- `case.call_and_validate()` runs schemathesis' default checks (including `not_a_server_error`) *before* returning, so asserting on the response afterwards is too late: the documented 503 must be excluded via `excluded_checks=[..., not_a_server_error]` for that operation, with a follow-up assertion that the body still reports the degraded contract.
- The exclusion must be scoped per-operation (`case.operation.path == "/readyz"`), not global — a blanket exclusion would let a real crash anywhere else in the API pass as "documented".
- Companion pattern that kept the rest of the suite honest: the shared compose health helper now asserts code↔body consistency (`200↔ok`, `503↔degraded`) instead of a fixed 200, so every app's readyz encodes the contract and a future regression (body says degraded, code says 200, or vice versa) fails in unit tests without a live stack.

**Rule:** When an endpoint legitimately returns 5xx, scope the fuzzer's server-error exclusion to that operation and assert the degraded body explicitly; encode code↔body consistency in shared health helpers so the contract is checked for every service, not just the one you changed.

## 16. A LAN-exposure switch must not cover trust-boundary ports

**Context:** AR-1-4 residual (2026-10-28) — one `AUTOBRIDGE_BIND_HOST` variable governed all 15 published ports, so the LAN-demo switch also re-published the verifier and the simulator admin APIs to the whole local network.

- Pin, don't parametrize, the ports that must never leave loopback: a second env var for "internal" ports would preserve the same one-variable-exposes-everything failure mode under a different name. The verifier and sim-admin mappings are now literal `127.0.0.1` with a comment citing the finding.
- The existing loopback test accepted *either* the variable or a literal, so it could not catch the regression — the new test names the exact (service, port) pairs that must be literal, failing on any re-introduction of the variable there.
- Scope the exception list to what the review named: vendor-facing sim ports, control and UI keep the override because a LAN demo legitimately needs them, and nothing in the stack needs the verifier/admin ports from off-host (bench uses verifier-net, smoke/e2e use localhost).

**Rule:** When one environment switch widens exposure, enumerate the ports it must never cover and pin those literally with a named test — an "internal bind host" variable is just the same bug with an extra step.

## 17. An unkeyed hash is an integrity checksum, not a signature — key it or rename it

**Context:** AR-2-4 (2026-10-28) — the edge worker's "signed release" gate recomputed a plain sha256 anyone holding the spec could reproduce, so the check proved the spec matched its checksum, not that an authorized release process approved it.

- Keying (HMAC with a server-side env secret) beats renaming when the gate is load-bearing: renaming to "checksum" would have documented away the only thing stopping an arbitrary spec from executing on the edge worker. The review offered both; the gate's refusal of unsigned releases made it authorization, so it got a key.
- Keep the unkeyed function for content-addressing, key only the authorization path — but here they were the same function (`spec_signature` feeds both drift fingerprints and the edge gate). The resolution: one function with two modes (HMAC when `EDGE_RELEASE_SECRET` is set, legacy sha256 otherwise), with the integrity-only consumers (fingerprint, registry) explicitly documented as non-authorization uses.
- Fail closed on key mismatch, not on key absence: with the secret set, a legacy hash is rejected (that is the whole test — "a self-signed foreign spec is rejected"); with it unset, legacy mode keeps dev/e2e green. Absence-as-legacy is acceptable only because the compose default is loopback-only and `.env.example` says to set a key before exposure.
- **Rule:** When a hash gates execution, HMAC it with an env secret shared by signer and verifier, compare with `compare_digest`, and add the test that matters: the attacker's self-computed unkeyed hash must be rejected while the keyed one passes.
---

## 18. A lockfile per image beats one lockfile for the repo when images must not share code

**Context:** AR-2-5 (2026-10-28) — the verifier and simulator images installed floating `pip install "fastapi>=…"` ranges, and all base images floated on tags; two builds of one commit could resolve different verifier dependencies.

- One `uv.lock` can source many `pip --require-hashes` requirement files: `uv export --package <member>` emits each image's exact closure from the single resolved set, so the engine (54 pkgs), verifier and simulator (23 pkgs each) stay mutually consistent — asserted by a test that shared pins are identical across all three locks.
- Export with `--no-emit-project` and install the project `--no-deps` from local source: otherwise pip would try to fetch the workspace project itself from PyPI (version 0.1.0, which does not exist there). Locked deps + local code keeps images slim, pip-only, and free of cross-package installs (trust boundary §2: no engine code in verifier/simulator images).
- Marker-gated entries (win32-only `colorama`, emscripten-only `httpx2-jsfetch`) are correctly absent from a linux `pip freeze` — a freeze-vs-lock comparison must normalize names (`_`/`-`) and expect marker-skipped packages, or it reports phantom mismatches.
- **Rule:** When sibling images share one lockfile source, export one hash-pinned requirements file per image, test that shared pins agree across them, and validate reproducibility by building twice and diffing `pip freeze` — never by eyeballing version ranges.
