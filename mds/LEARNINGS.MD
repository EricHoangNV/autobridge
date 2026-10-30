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
---

## 19. A committed artifact pinned only by another workstream's e2e file is unpinned in practice

**Context:** REQ-020 owner check (2026-10-28) — spec v2 changed `spec_signature()` for v1 specs and broke the committed reference releases, but the sandbox gates stayed green through two merges.

- Workstream-scoped e2e selection ("run only your file") means a cross-cutting invariant checked in exactly one e2e file is unchecked by every workstream except that file's own: 5a/5c ran the meridian e2e file, which never touches `generated/connectors/*/reference`, while unit tests store+load in tmp dirs so both sides of the round-trip used the new code and agreed.
- The fix pattern: pin committed bytes in unit tests that `make test` always runs — (1) the committed files load clean, (2) golden hex of each committed signature with a "do not re-sign, fix the code" note, (3) the new feature still moves the value when actually used. An invariant that must survive every future change belongs in the cheapest always-run gate, not in the most relevant-looking file.
- **Rule:** When a change touches shared canonical bytes (signatures, hashes, golden files), add the regression pin to the unit suite even if an e2e file already covers it — e2e files are selected per-workstream and may not run.

## 20. One FrameSplitter per TCP connection, never one per read

**Context:** REQ-022 sub-step A (FIAS PABX link engine, 2026-10-28). The 70 KB oversize-frame test never tripped the 64 KB limit even though every byte arrived.

- `_send_and_wait_ack` created a fresh `FrameSplitter` per call and discarded it on return. A hostile frame split across two reads landed half in the spent splitter (discarded) and half in the next one — neither half ever exceeded the limit, so no `OversizeFrameError` ever fired. The same loss applied to legitimate coalesced peer frames: an ACK processed first caused an early return that dropped a peer command queued behind it in the same chunk (flaky KR/KA test).
- Fix: one splitter per connection, created in `_serve_one_link` and shared by the handshake, the send-and-wait loop and the serve poll; every item in every chunk is dispatched before any ACK completes the wait.
- **Rule:** On a byte-stream protocol, the frame splitter is connection state, not call state. If a function that reads from the socket constructs its own splitter, partial frames and coalesced peer data are silently lost — and size limits become unenforceable.

## 21. A threaded socket server deadlocks on the second lock, and its test host lies about time

**Context:** REQ-022 sub-step A link-engine bring-up (2026-10-28). Three independent hangs/flakes, all in test and simulator glue, none in the protocol itself.

- `threading.Lock` is not reentrant: the stay planner held the lock while calling a posting builder that re-acquired it (via a layout helper), freezing the link thread and every admin snapshot. Audited every `with self.lock` + callee for nesting; builders called under lock must be documented lock-free (or take the layout as a parameter).
- `threading.Thread._stop()` is a method: naming an `Event` attribute `_stop` breaks `Thread.join()` with `TypeError: 'Event' object is not callable`. Name it `_halt`/`_shutdown`.
- The in-process test host's `pump(0.1)` used a fixed 2 s socket timeout, so every quiet poll cost 2 s and the suite took 300 s+. Bound `settimeout` to the remaining poll budget (`max(0.01, min(remaining, 2.0))`).
- **Rule:** When a threaded server hangs, suspect your own locking before the protocol: grep nested acquisition, never shadow `Thread` internals, and make test socket timeouts budget-aware — a fixed recv timeout turns every idle poll into a multi-second stall.

---

## 22. An empty markdown cell is not a dash: ✓-column parsers must exclude ""

**Context:** REQ-022 sub-step C table parsing (2026-10-28). Host-record tables mark applicability with ✓ per record column; empty means "not this record".

- The row filter excluded only `("-", "—", "–")`, so `""` passed and every command inherited every field (KR/KD/KZ all looked identical). The bug was invisible in single-record tables — only the multi-record ✓ tables exposed it — and a manual replication of the loop passed because it bypassed the header condition the real code evaluates.
- Fix: add `""` to the exclusion set; regression test pins per-record field sets (KR full, KD without K#/GA/GD, KZ minimal).
- **Rule:** When filtering table cells by marker, exclude the empty cell explicitly — `"" not in ("-", "—")` is True, and an empty applicability cell is the common case, not the exception.
---

## 23. A per-test timeout turns a hanging socket test from a mystery into a stack trace

**Context:** REQ-026 (2026-10-28), after a hanging FIAS socket test cost ~15 minutes of blind reruns in REQ-022 sub-step A.

- The hang was located only by rerunning with faulthandler; `--timeout=30` failed because `pytest-timeout` was never installed.
- Fix: `pytest-timeout` in dev deps, `timeout = 30` + `timeout_method = "thread"` in `[tool.pytest.ini_options]` (thread method dumps every thread's stack and works on macOS and Linux), 180 s per `tests/e2e` item via `pytest_collection_modifyitems` (stack startup must fit inside it).
- **Rule:** Run single tests with `-x` and rely on the 30 s limit; never wrap pytest in long shell timeouts to find a hang.

## 24. A rejected model answer is still a model call — count it, name it, show it

**Context:** REQ-021 owner check 2026-10-28: demo step 4 called the frontier live (tokens billed) but reported "no frontier call available" with an empty proposal.

- The ladder had recorded the call faithfully (usage tokens, `proposal_rejected` action); the *reader* of the trace (`l3_skipped = action != "frontier_generate"`) misclassified the rejection as a skip.
- Fix: call-ness is `action in ("frontier_generate", "proposal_rejected")`; only an explicit skip action means no call. The called-but-unusable reason names mode + model + tokens with the parse/apply detail and carries the truncated raw reply on the run.
- **Rule:** Never derive "was the model called" from proposal presence — a rejection, an unparsable reply and an empty reply are all billed calls. Reserve "skipped" for paths where no provider call happened, and say why (no provider, missing replay + the capture command).
---

## 25. The sandbox proxy turns every httpx client into a DNS client for the proxy host

**Context:** REQ-022 macOS DNS-hermetic fix (sbx1 2026-10-28)

- Unit tests tripped a new no-real-DNS guard not on the target host but on `gateway.docker.internal`: the sandbox exports `https_proxy=http://gateway.docker.internal:3128`, and any `httpx.Client` built with default `trust_env=True` resolves the *proxy* host via `getaddrinfo` — even for requests that ultimately fail fast. `trust_env=False` in source only changes *which* hostname trips the guard (the target instead of the proxy), so it cannot make a test hermetic by itself.
- A second trap: setting `VERIFIER_URL` in a shared test fixture silently changed build outcomes, because `builds.py` falls back to that env var for the verifier gate — a build that previously skipped the gate suddenly attempted it and persisted no release. Dependency-URL env vars belong in the narrowest fixture (or per-test), never in a shared client fixture.
- How it was fixed: tests point dependency URLs at refused loopback (`http://127.0.0.1:9`, a literal IP needing no resolution) or mock the HTTP/resolver layer; the guard trips on any non-literal `getaddrinfo` host, including `localhost`, and fails at teardown so worker-thread trips swallowed by fail-open handlers are still caught.

**Rule:** Unit tests must never resolve a hostname — use literal-IP refused-loopback URLs or mock the transport, and scope dependency-URL env vars to the tests that need them.

## 26. A stream splitter that reports a signal only on termination loses it exactly on close

**Context:** REQ-022 sub-step D (sbx1 2026-10-28) — the PABX's zeros-then-drop never surfaced as a resync: the host counted `resyncs: 0` and the e2e failed.

- `FrameSplitter.feed` emits `zeros` only when a non-zero byte *ends* the run (or on `flush()`). The PABX sends exactly 3 zero bytes then closes, so the run sat buffered in the splitter while the next `recv` returned `b""` — every close site raised "peer closed" without ever flushing, and the zeros item was discarded with the dead session.
- Fix: a `_flush_zeros` helper consulted at all 4 close sites (`_on_peer_closed`): trailing zeros become `LinkDropped("zeros: peer out of sync")` + `resyncs += 1`; anything else keeps its original close message. Same class of fix as LEARNINGS #20 (one splitter per connection): the splitter's contract hides state the caller must drain.
- **Rule:** Whenever a stream parser reports a condition only on termination (run-end, flush, sentinel), every connection-close path must flush first and map a pending condition to its meaning — otherwise the signal is lost precisely in the case it was designed for (clean close after the signal).

## 27. A renamed wire id corrupts silently; the sync outcome cannot detect it

**Context:** REQ-022 sub-step D (sbx1 2026-10-28) — v3-shaped postings (TA→TM) synced `Complete` with `amount: None`: the mapping omits missing paths and no check requires the amount, so the run looked green while every charge was worthless.

- Detection had to come from the handshake (announced layout vs mapped wire ids), not from the outcome — the e2e asserts the silent `None` first, then the detector + ladder repair, then `Complete` with an exact `Decimal`.
- Two resolution traps on the way: every key/date/time id is digit-shaped too, so money-shape alone cannot pick TM out of P#/RN/DA/TI — exclude ids the spec already consumes elsewhere first; and `$['P#']` (bracket form for `#`) must normalize to the same wire id as `$.TA` or P# looks unconsumed and poisons the candidate set.
- **Rule:** For rename-class drift on event feeds, compare the announced layout against mapped wire ids (never the sync outcome), resolve candidates by transform-shape among spec-unconsumed ids only, and normalize all JSONPath spellings to wire ids before set arithmetic.

## 28. A param-less GET route is executed by every contract-fuzz case — it must be side-effect-safe and DNS-free by construction

**Context:** REQ-022 sub-step E (sbx1 2026-10-28) — adding `GET /demo/simulators` broke `make test` in the schemathesis contract run: 15 `localhost` DNS trips at teardown.

- Schemathesis POST cases usually die at 422 validation before touching the network, but a GET with no parameters always executes — including its server-side probes. The new endpoint probed five simulator admins built from `http://localhost:<port>`, and `localhost` is a real DNS lookup (the hermetic guard trips on it, macOS fork-safety).
- Fix: the shared host-mode admin base now uses literal `http://127.0.0.1:<port>` (same loopback, no resolution) — one line, and every demo route inherits it, not just the new one. Display-only URLs shown to browsers stay `localhost` (human-facing, consistent with README).
- **Rule:** Any GET route the contract fuzzer can call with zero parameters must be safe to execute offline in tests: no hostname resolution (literal IPs), no unmocked network, fail-closed to "down", never an exception. Assume the fuzzer *will* execute it.

---

## 29. A provider-wired repair always climbs the semantic path — zero-call rows must wire no provider

**Context:** REQ-024 sub-step B (ablation engine arms), 2026-10-28

- `RepairController.handle` routes *any* repair with a provider set through `_handle_semantic_climb` (L0 → L1 → L2 → L3 → escalate, proposal never released), regardless of drift class. Wiring a provider "just in case" on a deterministic drift turns an L1 rule repair into an L4 escalation with model tokens.
- The fix: engine arms pass a provider only when the case carries hypotheses (semantic drifts). Deterministic rows run the pure ladder — mirroring the model-usage matrix, where zero-call rows wire no provider and any model call is a test failure.
- **Rule:** When driving the ladder outside the matrix, decide provider-vs-None from the case (hypotheses present or not), never by default. A model call on a deterministic row is a harness bug, not a repair.

---

## 30. Bench case data with vendor names lives in config, not engine source

**Context:** REQ-024 sub-step B (trust-boundary guard `test_engine_never_references_meridian`), 2026-10-28

- The ablation case library first hardcoded scenario ids, spec ids and docs paths in `src/autobridge/bench/ablation.py`; the guard failed on the vendor names — the same failure mode as the LegacyStay reset-registry incident (solved via `config/demo_targets.yaml`).
- The fix: `config/ablation_cases.yaml` holds every vendor-specific string (spec ids, docs paths, field names, scenario ids); engine code only implements generic loaders/builders dispatched by `kind` fields, with all runtime values read from the file. `Dockerfile.engine` already ships `config/`, so nothing else changed.
- **Rule:** Any new bench/demo mapping that names a vendor goes in `config/` data with generic engine dispatch — never as literals in `src/autobridge`, not even as dict keys or function names (the guard strips `#` comments but not docstrings).

## 31. A cloud view fed by heartbeats must be polled, never read once — and a guard carve-out must confine, not exempt

**Context:** REQ-022 final owner check (2026-10-29): `test_s2_edge_target` read `/edge/agents` once and saw no `link_up` right after the fault file's teardown had waited for the PABX link; plus a W5.7 guard failure on `src/autobridge/bench/ablation.py`.

- The agent reports link state via heartbeat every 10 s, so the cloud's view lags the real link by up to one interval. A teardown that waits for the device-side link plus a test that reads the cloud view once still races the next heartbeat. Fix: poll the cloud view with a timeout bounded by ~2 heartbeat intervals, at the start (inherit-settled) and at the assert.
- The W5.7 `generated/connectors` guard flagged the ablation harness reading checked-in reference specs. The reference specs are public build artifacts, not hidden fixtures — but the fix was not to exempt bench/; the carve-out asserts every mentioning line also mentions `reference/`, keeping the bootstrap-honesty property while allowing the harness path (same pattern as the existing `hidden_cases` bench carve-out).
- **Rule:** Any assertion on heartbeat-fed state polls with a timeout of ~2 intervals; any new bench filesystem read gets a confining carve-out (path + structural assertions), never a blanket exemption.

## 32. drand chained verification is sha256(prev_sig || round), standard DST — and old clients have it backwards

**Context:** REQ-025 sub-step B beacon client (2026-10-29)

- Three independent guesses at the signed message all failed against a live round (`prev`, `prev||round`, `sha256(prev)` with the IETF suite). The ground truth is in drand Go `crypto/schemes.go` `NewPedersenBLSChained`: digest = `sha256(previous_signature || round_be64)` (previous FIRST), standard RFC 9380 G2 DST, keys on G1 / sigs on G2. The old drandjs `message()` puts round first — wrong order, do not copy it.
- **Rule:** For any third-party signature scheme, fetch the signer's current source (not ports, not old clients) and verify against a live vector before depending on it; commit the vector as an offline fixture.

## 33. Beacon serving must pin the dataset seed, or live and judged baselines diverge

**Context:** REQ-025 sub-step B live rehearsal (2026-10-29)

- The simulator served `generate_dataset(state seed)` + beacon mutation while the verifier judged `generate_dataset(instance dataset_seed)` + mutation: reservation counts matched by luck, folio counts did not (72 vs 77). The push now pins the sim seed to the instance seed, so served == materialized byte-identically.
- **Rule:** Whenever a harness pushes a seed-derived test to a stateful simulator, the push must also align the simulator's dataset seed — assert served-vs-expected identity in a live rehearsal, not just in unit tests.

## 34. Local-mode simulators must not share one state file

**Context:** REQ-025 sub-step B live rehearsal (2026-10-29)

- All four local HTTP sims read/wrote the same `/tmp/sim_state.json`, so every sim served the last-pushed instance (harbor's admin returned a meridian instance). Compose is isolated per container; local mode was not. Fix: per-sim `SIM_STATE_FILE` in `scripts/local-up.sh` (the env knob already existed for FIAS).
- **Rule:** Local mode must mirror Compose isolation boundaries (networks, volumes, state files); when adding a shared-state seam, give each service its own file and check cross-talk with two concurrent pushes.

## 35. A closed store must degrade readyz, never 500 it

**Context:** Full-suite flake during REQ-025 sub-step B validation (2026-10-29)

- `test_edge_worker_app_health` failed intermittently: edge_target tests shut down the module-global store, and a later readyz hit the closed handle (`depths()` raised → 500). `ping()` already failed safe; only `depths()` was unguarded. Fix: try/except → mark the store check failed (honest degraded, 503 per AR-2-6) + regression test.
- **Rule:** Every readyz dependency read gets a fail-to-degraded path (never an exception); global-state tests must leave the global usable (reset in finally) because random order will find the bad interleaving.

## 36. Bench fixture env must save/restore, never setdefault-and-leak

**Context:** REQ-024 owner check fix (2026-10-29) — full-suite failure after wiring fault serving

- `bench/faults.py` used `os.environ.setdefault` for spec placeholder env (`ATLAS_BASE_URL`, …). Bench tests run before control-API tests in one pytest process, so the leaked values changed later tests' behavior: a sync that used to Block on an unresolvable placeholder (no DNS) suddenly resolved to the fake host and tripped the `_no_real_dns` guard (`test_sync_with_release` ERROR, green in isolation).
- Fix: `bench.faults.fake_env()` context manager — snapshot every touched key, `setdefault`, restore exactly (unset stays unset) — wrapped around each served sync, plus a regression test asserting post-sync env absence.
- **Rule:** Library code that needs fixture env for one operation wraps it in a save/restore context; `setdefault` at import or call time is a process-global leak, and random-order/full-suite runs will find the test that depended on the key being absent.

## 37. mypy unused-ignore verdicts depend on the checked set — gate the union

**Context:** REQ-024 owner check fix (2026-10-29) — moving `bench/` out of `src/autobridge`

- `bench/app.py`'s `# type: ignore[return-value]` comments pass under `mypy src/autobridge bench` (ignores used) but fail as unused under standalone `mypy bench`. Same code, same config — only the checked file set differs (follower import resolution changes the precise types mypy sees).
- Fix: restructured the returns to need no ignore in either mode (`{"status": healthz()["status"]}`, `dict(readyz())`), and extended the gate to `mypy src/autobridge bench` so the harness stays typechecked after the move.
- **Rule:** Gate the exact union you ship (`make typecheck` must name every checked tree); never trust a subset run — an ignore that is "used" in one set can be "unused" in another, and vice versa.

## 38. Spec-shaped fingerprints cannot show vendor behavior flips — observe the wire

**Context:** REQ-027 sub-step 2 (2026-10-29) — every engine arm escalated on page_to_cursor with 0 fixes

- The fingerprint's pagination shape mixes spec fields (strategy, params) with runtime counters (pages_fetched, stop_reason). A page→cursor flip changes the wire envelope (`next_cursor` present, `total` gone) but the spec side is identical on both fingerprints, so `_delta_shows_page_to_cursor` (previous.strategy vs current.strategy) can never fire on the real path — only on hand-built deltas.
- Canonical-types fingerprints cannot show renames either: unmapped wire keys never reach canonical records, so a rename reads as a type change (null) or a removal.
- Fix: `_observe_wire_pagination` / `_observe_wire_schema` in `drift/signals.py` read `raw_pages` envelopes + `records` directly (non-null cursor values only — null leaves are normal on last pages; Harbor's envelope carries both `cursor` and `total`), and L1 rules consume them from `ctx.signals`.
- **Rule:** When the detector compares summaries derived from the spec, any drift that changes vendor behavior without changing spec-shaped fields needs a wire-level observer on the raw responses; null/missing on the wire is normal more often than it is evidence, so observers must require a transmitted value, and ambiguous evidence (several added keys) stays unclaimed.

## 39. A response leaf is not a request param; a strategy flip is vendor-level

**Context:** REQ-027 sub-step 2 (2026-10-29) — two reverted refinements

- Setting `cursor_param` from the observed response leaf (`next_cursor`) broke the follow-up sync: the vendor reads the request param `cursor` while serving the response leaf `next_cursor`. Patch `next_path` from the wire leaf, keep `cursor_param` at its default unless explicit extra says otherwise.
- Restricting the flip patch to ops that exhibited the contradiction under-patched: a small op fits on one page (null cursor, no signal) while its sibling flips. Strategy is a vendor-level contract — evidence on one op plus the same spec strategy on the others flips all of them; the verifier gate confirms.
- **Rule:** Wire evidence names response fields only — never derive request-param names from it; and never narrow a vendor-level patch (strategy, auth) to exhibiting operations.

## 40. Every Alembic migration needs a `_REVISION_EFFECTS` mirror entry — or old DBs 500 on upgrade

**Context:** REQ-027 L3 reasoning-ledger (2026-10-29) — adding migration 0008

- `src/autobridge/control/migrate.py::_detect_revision` decides what schema an unversioned database has by checking each revision's effects; a revision missing from `_REVISION_EFFECTS` is invisible to detection. A `create_all`-built DB (current models, no version row) then stamps 0007 and upgrades to head, re-running 0008's `ADD COLUMN` on a table that already has it → `duplicate column name: reasoning_tokens`, `POST /demo/reset` 500s.
- Caught pre-commit by `test_demo_reset` (file DB via create_all + ensure_migrated), not by the head-cycle test (fresh DBs never exercise detection).
- **Rule:** The migration and its `_REVISION_EFFECTS` entry land in the same commit, and the commit must include a test that upgrades a database built from current `create_all` metadata (detection path), not just `upgrade head` on an empty file.

## 41. Never assign `os.environ` directly in a test — a leak flips sync behavior suite-wide

**Context:** REQ-027 fix-first (2026-10-29) — intermittent `make test` teardown errors (`_no_real_dns` trips on `localhost` in 3 activation tests, only with coverage, only in some random orders)

- `test_get_connector_detail` set `os.environ["HARBOR_API_KEY"]` directly and never deleted it. When random order ran it before the activation tests, their syncs passed auth and attempted real HTTP to `localhost:8106` (DNS trip → teardown error); otherwise the syncs Blocked pre-HTTP on the missing credential and everything was green. Same tests, same code — order decided the gate.
- Diagnosis path: replay the failing `--randomly-seed`, instrument the guard to print a stack on trip (temporary conftest hack, reverted after), read the httpx→sync→build chain, then notice the passing runs never reach HTTP at all (pre-HTTP auth failure, still HTTP 200 so assertions pass).
- **Rule:** `monkeypatch.setenv` (auto-reverted) for every test env var, no exceptions; direct `os.environ` writes are a gate flake waiting for a seed. When a teardown-only failure is order-dependent, suspect leaked process-global state (env, lru_cache, module attrs) before suspecting the failing test.
---

## 42. Detector evidence speaks wire names; specs speak canonical names — resolve, don't assume

**Context:** REQ-027 sub-step 5 (2026-10-29): the live rename chain escalated while the in-process rename test passed.

- The in-process test hand-built deltas with canonical field names (`guest_name`); the live detector derives `missing`/`added` from observed wire keys (`guestName`). The `rename_field` rule looked the wire key up among canonical field names, found nothing, and escalated — a name-space mismatch invisible to every hand-built test.
- Root fix has two halves: (a) the rule resolves a reference through the spec's path top-keys to the single canonical field (ambiguous shared envelopes resolve to nothing); (b) the detector scopes coverage to wire keys the build actually saw, so phantom mappings and perennial unmapped keys never reach the rule.

**Rule:** Any rule consuming detector-derived signals must accept wire-space references and resolve them to canonical spec names, with ambiguity failing closed; unit tests must cover the wire-space form, not only hand-built canonical evidence.

---

## 43. Redacted transcripts must never feed logic assertions in drivers

**Context:** REQ-027 demo steps 3/7 (2026-10-29): redaction added after the last green 8/8 truncated depth > 3 to an ellipsis, so `provenance_repair_run_id` lookups and `refused_vocabularies` predicates silently compared against `"…"` and failed.

- The transcript record (redacted, bounded) and the logic read (raw, complete) are different artifacts. Sharing one body between them breaks the first assertion that looks deeper than the truncation depth — always on reused state, never on a fresh DB, so it escapes first-run verification.
- Fixed with a separate raw GET seam for logic reads (`_control_get_raw`); the transcript keeps the redacted record. Tests stub both seams.

**Rule:** In any driver that both logs API traffic and asserts on it, assertions read unredacted bodies; redaction applies to the logged copy only.

## 44. An empty observation delta has a constant key — never learn or match on it

**Context:** REQ-028 bench rehearsal (2026-10-29): `autobridge_warm` proposed verified patches on `rate_limit_429`/`transient_503` and lost `duplicate_late_records`' escalation. All four drifts derived an all-False fingerprint delta (transport faults leave no observable trace through the sync path), whose canonical signature is one constant string shared by every such drift — so a rename fix learned under one scenario hit verbatim on unrelated vendors' no-patch drifts.

- The L0 key was `(vendor, delta-signature)` with no drift class; the class was stored on the entry but never consulted. Two compounding causes, both fixed: (a) the class is now part of the key; (b) an empty delta (and transport/auth/capability classes, explicitly) never consults or populates L0 — in `run_ladder`, `run_frontier_climb` and the controller populate guard alike.
- Same incident class as #42: the bench derive path lacked the build-probe annotation the live path relies on, so unmapped wire keys flooded `added` and renames classified as REMOVED/TYPE_CHANGE. Scoping the detection copy to baseline-observed wire keys (derived from responses only) fixed classification with no change to the repair inputs.

**Rule:** A cache key must include every dimension the hit decision depends on (vendor, drift class, observed delta); when the observation is empty there is nothing to match, so skip — never store, never look up. And any evidence annotation the live path depends on must be reproducible on the bench path from observation, or the two paths classify the same fault differently.

---

## 45. Fail-closed env flags need a conftest scrub or they leak into unit tests

**Context:** REQ-029 (2026-10-29): `create_release` gained a `CONTROL_URL` proxy fallback and an `AUTOBRIDGE_REQUIRE_VERIFIER_GATE=1` fail-closed branch, both read from ambient env. Unit tests assume gate-skip when no URL is passed — a developer shell with `CONTROL_URL` exported would silently route every gate at a phantom proxy.

- Fixed by deleting `VERIFIER_URL` / `CONTROL_URL` / `AUTOBRIDGE_REQUIRE_VERIFIER_GATE` / `AUTOBRIDGE_CONTROL_TOKEN` in the existing autouse hermeticity fixture (`tests/unit/conftest.py`); tests that need them set them explicitly via `monkeypatch` (fixture setup runs first, so explicit sets win).
- Same file already scrubs DNS; env is the same class of impurity. The dotenv-loader blank-handling test (`test_demo_env_default_treats_blank_as_unset`) already assumed a clean shell and now gets it guaranteed.

**Rule:** Any production behavior keyed off ambient env gets a matching `delenv` in the unit conftest; tests opt in explicitly. Never let the developer's shell change what a unit test asserts.

---

## 46. A forwarding sidecar must never resolve its target to itself

**Context:** REQ-034 (Code Review Cycle 4, AR-4-1): a TCP-forwarding sidecar for the FIAS admin port initially pointed at a hostname that, on one of its own networks, resolved to the sidecar itself via a DNS alias.

- Every accepted connection spawned a handler that connected back into the sidecar's own listener, which spawned another handler — an exponential fork-bomb (37k threads, then `can't start new thread` and container death within seconds).
- The failure mode is total and fast: the container dies before any probe can diagnose it, and the logs show only a thread-exhaustion cascade plus upstream connection resets.
- Fixed two ways: (1) the target name is edge-net-only while the alias lives on admin-net only, so the target can never resolve to self; (2) a `_self_dial_target` guard compares the route's source address against every address the target resolves to (same IP *and* same port) and refuses loudly instead of connecting.

**Rule:** Any proxy/forwarder must prove its target is not itself before connecting — compare resolved target addresses against the local route source for the same port, and fail closed with a loud log when they overlap.

## 47. A 403 is not "unreachable" — smoke probes must name which refusal they got

**Context:** REQ-035 (Code Review Cycle 4, AR-4-2): `scripts/smoke.sh` treated any failed HTTP probe as "down/unreachable", so a vendor-net peer getting `403` from the simulator admin API looked identical to having no route — hiding that the §2 "no route" boundary was actually an authorization boundary all along.

- A negative check has three outcomes, not two: no route (connection failure), reachable-but-rejected (401/403), and accepted (2xx). Collapsing the first two lets a misconfigured open endpoint pass as "isolated" and lets a real boundary look like a network gap.
- Fixed with `probe_admin_denied`: pass on no-route and on 401/403 with distinct messages naming the case, fail only on 2xx without a token. The same shape applies to the write probes (`probe_post … down` already failed only on accept).

**Rule:** Every negative network/admin probe distinguishes "no route" from "reachable but rejected" in its output and fails only when the unauthorized access is ACCEPTED. Never assert refusal with a plain reachability probe.

---

## 48. A protocol test must wait on business-level events, never on wire frames

**Context:** Code Review Cycle 4 AR-4-4 (REQ-038) — `test_replay_postings_resend_same_pno` flaked as `7 == (6 + 2)` only inside the full `make test` under parallel load.

- The PABX link layer retransmits a frame after `ack_wait` with no ACK; the retransmit is byte-identical on the wire but does NOT bump `postings_sent`. A loaded test host stalls past the deadline, so the first two frames received can be one business send plus its link-level copy.
- Asserting an exact business counter after collecting N frames couples two different levels: arrival proves nothing about how many business sends happened.
- Fixed by waiting on the engine's own counter (`postings_sent >= sent_before + 2`) while collecting, then asserting exact equality plus a same-P# / nothing-invented check; same treatment for exact frame counts (`== 5` → `>= 5`) wherever a resend copy can land in the collection batch.

**Rule:** When a protocol retransmits below the business layer, tests assert on business-level counters or idempotency sets, never on wire frame counts or arrival order.

---

## 49. Claim one request with a block-scoped edit, never a bare status-line sed

**Context:** REQ-036 claim — `sed -i 's/^Status: OPEN (audit …)$/.../' mds/REQUESTS.MD` matched the identical status lines of REQ-036 *and* REQ-037, claiming both tasks at once (caught by `git diff` before pushing; REQ-037 reverted).

- Open requests often share byte-identical `Status:` lines (same audit, same date). A global line-pattern replace cannot tell them apart.
- Caught because the claim step checks `git diff --stat`/`grep ^[+-]Status` before committing — one extra minute that saved a cross-loop claim collision.

**Rule:** When claiming a request, scope the edit to its block (match on the `### REQ-NNN` header through its status line, or edit with line numbers from `grep -n`), and always verify with `git diff` that exactly one status line changed before pushing the claim.

---

## 50. Blocking I/O on the event loop wedges the whole control plane — scheduler passes belong on threads

**Context:** REQ-030 sync scheduler (lifespan background task, local stack)

- The first scheduler implementation ran `run_scheduler_pass` (sync httpx calls with retry/backoff budgets, seconds per connector) directly in an `async` lifespan task. Every tick blocked the loop for the whole pass: `/healthz` took seconds, e2e fixtures timed out calling the stack "not reachable", and parallel requests queued behind syncs.
- Root cause: FastAPI runs one event loop; any `def`-sync blocking call inside an `async` task stalls every route, SSE stream and readiness probe until it returns. A 10s cadence with multi-second passes makes the stall permanent, not transient.
- Fixed by running the pass in `asyncio.to_thread` with an overlap guard (a tick whose predecessor is still running is skipped), verified by `/healthz` staying at single-digit milliseconds under a ticking scheduler.

**Rule:** Never run blocking engine I/O (sync HTTP, long DB passes) on the control event loop — offload to a worker thread and guard overlaps; verify with a latency probe under load, not just a green tick.
---

## 51. One SQLite writer at a time: WAL mode plus short transactions for scheduler + API coexistence

**Context:** REQ-030 (scheduler background passes + API writes on one SQLite file, local mode)

- With the default rollback-journal mode, a scheduler pass holding one session across all connector syncs locked API writes out: `POST /connectors` answered 500 `database is locked` whenever a background pass was mid-flight.
- Root cause: in journal mode an open read/write transaction blocks writers; the pass held its transaction for seconds. WAL mode (readers never block writers) plus a 30s busy timeout (writers wait instead of failing) plus committing scheduler snapshots before the sync loop removed the collisions — proven live (38 e2e + demo 8/8 with the scheduler ticking).
- Applied in `engine_from_url` (SQLite only; Postgres untouched) with a unit pin on the pragma values.

**Rule:** Any background writer sharing a SQLite file with request handlers needs WAL + busy timeout and short transactions; a "database is locked" 500 under background load means the pass holds its transaction too long, not that SQLite can't cope.
---

## 52. Fresh SQLite files on shared filesystems can go readonly mid-creation — seed, copy, and repair migrations defensively

**Context:** REQ-030 (sandbox worktree on a virtiofs mount; `make test` collection, `ensure_migrated`)

- Creating a new SQLite file in the worktree intermittently failed on the *second* write (`attempt to write a readonly database`), leaving partial schemas (e.g. only `connectors` + empty `alembic_version`). This poisoned `ensure_migrated` two ways: upgrade re-ran `CREATE TABLE` on existing tables, and a version row older than a create_all-built schema re-added existing columns.
- Fixed in two places: (a) `ensure_migrated` now also repairs the schema-ahead case (version older than detected schema → trust the schema, stamp the detection — the mirror of the existing head-stamp repair); (b) unit tests only touch tmp-dir databases, and the dev flow builds migration targets on local tmp then copies into place.
- The same flake explains "table already exists" during parallel/concurrent startups: treat a stale local `./autobridge.db` as disposable (delete + let lifespan recreate + migrate) rather than debugging it as a schema bug.

**Rule:** On shared/sandbox filesystems, never diagnose a partial SQLite schema as a migration bug first — check for a failed file creation (readonly mid-write), rebuild from a tmp-built copy, and keep the migrator able to repair version skew in both directions.

## 53. The connector record names the host, the docs name the path — join them at build time
- A docs-built Meridian release synced Blocked (endpoint_not_found) on the live stack: the connector-record `base_url` override (normalized per stack mode) replaced the docs `servers` URL wholesale, dropping its `/api/connector/v1` path prefix, while the simulator only serves the prefixed routes.
- Root cause: the REQ-027 owner fix stored the override verbatim, so host and path could never come from different sources.
- Fixed in `bootstrap._with_docs_path`: an override with an empty/root path gains the docs URL's path; an override that already carries a path, a placeholder or an unparseable value is stored verbatim.
- **Rule:** when two sources each own part of an address (record owns routability, docs own the path), merge them explicitly at the boundary instead of letting one clobber the other.

## 54. An empty entity on a healthy sync is not drift — quiet it at the detector, not the classifier
- The seeded PABX edge connector flipped to `error` seconds after seeding: every scheduler sync observed 0 RoomStatus records (idle feed between postings bursts), `zero_records` fired, the classifier fell through to UNKNOWN, and the ladder escalated to L4.
- Root cause: no matcher owns lone `zero_records` on a Complete sync, so the fallthrough escalated a quiet feed. The Blocked+empty pattern (capability_removed) was never at risk — Blocked/Degraded always add their own signals.
- Fixed in `detect_and_repair_after_sync`: all-signals-are-`zero_records` returns drift_found False with reason `empty_entities_on_healthy_sync` (verifying-sync retirement still applies).
- **Rule:** a Complete sync that reached the vendor and contradicts nothing is the absence of evidence, not evidence of drift; fallthrough escalations must not fire on it.

## 55. A designed recovery that never runs on startup is a wedge, not a safety net
**Context:** REQ-032 live verification (local stack restart orphaned a meridian sync job in `running`; connector stuck "sync running" forever because the scheduler skips busy connectors)

- `JobRunner.reclaim_orphaned()` existed, was unit-tested, and the `Job` docstring promised "a restarted worker reclaims via `reclaim_orphaned` and re-runs" — but no lifespan or startup path called it. The orphaned row survived every restart; the busy check kept skipping the connector, so all four seed connectors could never be Working again without a reset.
- Fixed by calling it in the control lifespan (`reclaim_orphaned_jobs()` in `app.py`, best-effort so a broken jobs table never stops serving), verified live: planted a `running` row, restarted, watched it return to `queued` with the retry budget intact.
- **Rule:** every crash-recovery path needs a caller on the startup path and a live restart test that plants the orphan — an untested, unwired reaper is indistinguishable from no reaper.

## 56. The evidence store rejects unknown parents — filter, don't fail, when linking derived records
**Context:** REQ-032 (persisting the hypothesis derivation on the deterministic ladder path; fast-path signals are never stored as evidence rows)

- `EvidenceStore.append` raises `UnknownParentError` for parent ids with no row, and the first version of the derivation persist passed raw signal evidence ids — which don't exist in the fast path — so the whole append died inside a best-effort `try/except` and repairs silently carried no derivation.
- Fixed with `_existing_evidence_ids()`: link only parents that resolve to stored rows (usually none on the fast path), and say so in the stage source instead of inventing a funnel.
- **Rule:** when a store enforces referential integrity, resolve-then-link at the call site; a silent best-effort write that fails closed on integrity is a silent data gap — assert the row exists in tests with realistic (unpersisted-id) inputs.

## 57. Verify the fix against the faulted vendor — healing first scars deterministically
**Context:** REQ-033 UI journey (a `page_to_cursor` repair approved after the sim was healed kept Degrading `no_loss` and escalating to `SEMANTIC_VALUE_DRIFT`, bricking the connector to error until reset)

- The L1 patch flips pagination strategy page→cursor. Verified against the faulted (cursor-shape) sim it syncs `Complete` and the drift retires. Verified after a heal, the cursor-strategy spec runs against the clean page-shape sim, which ignores the cursor param and repeats page 1: `repeated_cursor` stops the loop, the partial fetch trips `no_loss`, and the run escalates with empty evidence.
- The scar is deterministic, not flaky: any heal-while-shape-fixed-release-is-active plus one scheduler tick produces it, and there is no dismiss-drift affordance (reject leaves the drift open), so only a reset recovers.
- The journey now approves and verifies first, heals only where the active spec matches clean sims, and reactivates the base release before healing after a shape fix.
- **Rule:** repairs verify against the fault they were built for — never heal/clear the fault between approve and the verifying sync; when a demo or e2e needs a clean frame afterwards, reactivate the pre-patch release first, then heal, and assert no escalate repair was left behind.

## 58. One undecided fault keeps exactly one drift and one repair — dedupe at detection, not at read
**Context:** REQ-031 owner check (scheduler tick + driven sync doubling drift/repair rows; "after one break, exactly one drift and one repair")

- `detect_and_repair_after_sync` created a `DriftEvent` unconditionally, so any two sequential detections of the same undecided fault (scheduler tick racing a driven sync, or two ticks when the busy-check window was missed) wrote scar rows. Concurrent overlap is already prevented (the scheduler skips busy connectors; the loop never overlaps passes), so the remaining hole was strictly sequential.
- Fixed with `_find_undecided_open_drift`: same (connector, class), status open, engine-derived (source sync set — labelled `record-climb` rows never match), newest repair still without an `Approval`. Decided repairs (approved/rejected/rolled back) and different classes always open new incidents, so approve → verifying-sync → inject-again flows are untouched.
- **Rule:** idempotency for detection-like writes belongs at creation time with a decision-aware key (open + undecided), never as a read-side filter — dashboards, busy-checks and counts all stay correct by construction.

## 59. Trust-boundary guard tests match strings, not intent — even docstrings trip them
**Context:** REQ-031 (`ReferenceReleaseRequest` docstring named the reference-spec path; `test_workspace_has_no_path_to_verifier_or_simulators` failed)

- The W5.7 negative test forbids the literal `generated/connectors` in every file under `src/autobridge` except four named files — a docstring that merely *names* the path the client reads (the server never opens it) still fails the suite. Widening a guard test needs an owner note (REQ-024 precedent), so the fix was rewording, not a carve-out.
- **Rule:** when a guard test scans for forbidden strings, treat the scan as the contract: keep the strings out of engine code entirely (including comments and docstrings); if a carve-out is truly needed, add the owner note in REQUESTS first, never widen silently.

---

## 60. Hooks after early returns crash only on the second render — unit tests with mocked loaded state never see it

**Context:** REQ-043 added a ticking "calling \<model\> · Ns" line to the repair page via a new `useNow(1000)` hook placed after the loading/error early returns, while every existing hook sits before them. Unit tests (SWR mocked with data on first render) stayed green; the live browser crashed with "Application error" on every repair page load (first render returns early, second render calls one more hook).

- The rule is per-render hook-count stability, so the bug is invisible whenever the first render already has data — exactly what mocked SWR tests produce.
- The fix is placement (all hooks before any early return), and the guard is a test that renders loading first, then rerenders with data.
- **Rule:** Any new hook in a component with early returns goes with the other hooks above them, plus a loading-then-data rerender test; never trust a hooks change that only ever rendered loaded state.

## 61. The replay prompt hash covers prompt text and the allowed-pointer list — rekey fixtures, never loosen the test

**Context:** REQ-049 changed `config/prompts/l3_patch.md` (proposal option) and widened `allowed_l3_pointers` (money-check `compare_to`); three replay tests failed with L3 "skipped: no replay recording for prompt \<new hash\>".

- `prompt_hash` covers the fully rendered L3 messages, so both the prompt file text and the evidence blocks (which carry `patch_target.allowed_pointers`) rotate the key. The fixture `tests/fixtures/replay/gross_to_net.json` is keyed by that hash, and its own note documents the rotation procedure ("an edited prompt file changes the hash").
- Fix: render the same three input sets (l2 pair, matrix pair-no-evidence, demo single+evidence) through the loader with old then new prompts, verify the old hashes match the committed keys (sanity), and swap in the new keys keeping the synthetic payloads byte-identical. The old demo key did NOT match a naive replication — the evidence change (new pointers) was the second hash input, which is exactly why the fixture note says re-capture rather than hand-edit.
- **Rule:** when a replay test fails on a hash miss after a prompt or evidence change, re-render the recorded inputs and rekey; never delete the test, never invent a recording, and always verify old-hash == committed-key first so a genuinely changed input (not just the prompt) is caught.

## 62. Prove a resync on idempotency keys, not on counts — the relay folds re-sends by design

**Context:** REQ-050 deck 7 first asserted `/edge/postings` count rises after `fias_link_resync`; live it stayed 138 → 138 and the step failed. The relay stores postings with `INSERT OR IGNORE` on the idempotency key, so a re-handshake that resends un-PA'd postings with the same P# correctly changes nothing — a rising count would have meant duplication, not recovery.

- Stay postings are queued once per process at link start; a quiet link resends nothing new. Fresh PABX activity (new P#) is the only thing that grows the log.
- Fix: snapshot the key set before the break, and after one settle tick assert every pre-break key is still logged exactly once (nothing lost: no missing keys; nothing duplicated: unique count == total count), plus link back up, post-resync sync `Complete`, connector Working, no drift/repair.
- **Rule:** when a store deduplicates by key, assert on the key set (superset + uniqueness), never on a rising count; a count that must not move is the passing signal.
---

## 63. In-place edits to a loaded JSON column never mark the row dirty — deep-copy before mutating

**Context:** REQ-053 item 6 (auto-rollback annotation + dry-run pass stamp on `RepairRun` rows, SQLite via SQLAlchemy 2.x).

- `row.ladder_trace = list(row.ladder_trace or [])` followed by mutating an entry dict and reassigning the list silently persisted nothing: the loaded JSON shares dict objects with the attribute state, so old and new compare equal and the attribute is never marked dirty — `flush()` emits no UPDATE.
- The sibling code (`controller._progress`) works because it builds fresh dicts via `trace_from_attempts`; only the in-place pattern is broken.
- Fixed by `copy.deepcopy` before mutating, then assigning the new list.

**Rule:** When updating a JSON column, always assign a deep-copied structure — never mutate the loaded objects in place.
