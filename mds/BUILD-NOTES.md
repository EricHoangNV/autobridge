# AutoBridge — Build Notes

> Companion to `PRD-TRACKING.md`. Implementation detail (files, decisions, gotchas) for completed tasks,
> keyed by task id. Append here when you finish a task; keep the manifest line itself terse.
>
> Entry template:
>
> ```
> ### P1.W1.1
> - **Files:** `path` (what), `path` (what)
> - **Decisions:** why A over B
> - **Checks:** test counts / coverage for the package; e2e file run; readyz
> - **Gotchas:** anything the next agent would trip over (link LEARNINGS #N when there is one)
> ```

### P1.W1.1
- **Files:** `pyproject.toml` (uv workspace root, hatchling src layout, ruff+my+pytest+import-linter), `verifier/pyproject.toml`, `simulators/pyproject.toml`, `Makefile` (up/down/test/smoke/e2e/bench/demo/lint/typecheck), `src/autobridge/{__init__.py,health.py,config.py,control/app.py,evidence/redact.py,llm/provider.py}` + 10 stub `__init__.py`, `verifier/{__init__.py,app.py}`, `simulators/{__init__.py,common/__init__.py,common/generator.py,atlaspms/__init__.py,harborpms/__init__.py,legacystay/__init__.py}`, `tests/unit/test_*.py` (4 files), `uv.lock`
- **Decisions:** `[[tool.importlinter.contracts]]` array-of-tables for import-linter 2.15 (expects list not dict); `requires-python <3.15` to avoid types-jsonpatch unsat; `packages = ["src/autobridge"]` vs `["verifier"]` per hatchling; Makefile uses single-shell `if/else` for compose.yaml missing case
- **Checks:** `make test` — 33 pass (autobridge 100%, verifier 100%, simulators 100% via --cov-fail-under=90 per package); `make lint` — All checks passed; `make typecheck` — Success 19 files; `uv run lint-imports` — 1 kept, 0 broken; `make smoke` — 33 passed; `make up/down/e2e/bench/demo` — exit 0 with skip message
- **Gotchas:** `test_redact_replaces` initially asserted `aabbcc` with `["bb","cc"]` → `aa****` but actual is `aa******` (sequential replace expands string); fixed to `["bb"]` → `aa***cc`. `docs/.gitkeep` was incorrectly created (docs already has .md files) — removed before commit. `uv sync` needs `--all-packages --extra dev` to get ruff/mypy/import-linter.

### P1.W1.2
- **Files:** `compose.yaml` (8 services — control, worker, runtime-sandbox, verifier, sim-atlas, sim-harbor, ui, bench — on engine-net/vendor-net/verifier-net/admin-net, host ports 8101–8108, healthchecks), `Dockerfile.engine` (one engine image serving control/worker/runtime-sandbox/bench via `$APP_MODULE`), `verifier/Dockerfile` (build context `verifier/` only; hidden fixtures mounted ro at `/fixtures`), `simulators/Dockerfile` (context `simulators/` only), `ui/Dockerfile` + `ui/server.js` + `ui/package.json` (Node stub, W7 replaces with Next.js), `src/autobridge/{engine,runtime,bench}/app.py` + `simulators/{atlaspms,harborpms}/app.py` (health-only stubs), `scripts/wait_readyz.sh`, `scripts/smoke.sh`, `Makefile` (`up` = build+wait+readyz, `smoke` = unit health + stack smoke, `uv run --extra dev` everywhere), `tests/unit/test_autobridge_compose.py`, `tests/unit/test_simulators_apps.py`
- **Decisions:** Isolation is by network attachment only — no `internal: true` (LEARNINGS #1). `bench` sits on engine-net + verifier-net (W8 scoring calls the verifier's hidden mode). Simulators are dual-homed (vendor-net + admin-net); W2 must bind the admin API to the admin-net interface only, otherwise the sandbox could reach port 8001 over vendor-net — the current negative check passes only because nothing listens on 8001 yet.
- **Checks:** `make test` — 57 pass (autobridge 100%, verifier 100%, simulators 100%, 90% gate per package); `make lint` / `make typecheck` / `make lint-imports` (1 kept) green; `docker compose down && make up` — all 8 readyz green; `make smoke` green incl. positive probes (sandbox→sims, control→verifier) and negative probes (sandbox↛verifier, sandbox↛sim:8001, ui↛sims/verifier, worker↛verifier). No `tests/e2e/test_00_stack_up.py` — that file is P1.W1.10.
- **Gotchas:** `internal: true` on verifier-net silently dropped the verifier's host port and, after an in-place flip, its service DNS alias (LEARNINGS #1) — guarded by `test_no_network_is_internal`. Agent: opencode meta/muse-wonder-1.0 (escalation).

### P1.W1.3
- **Files:** `src/autobridge/spec/models.py` (strict ConnectorSpec v1: closed 9-transform set, JSONPath-subset regex, credential_ref-only auth, per-strategy pagination, cross-ref validators, YAML/dict loaders, schema-file validation, error formatter), `src/autobridge/spec/patch.py` (apply-to-copy + revalidate, diff, reverse-as-diff-back, PatchError/SpecValidationError), `src/autobridge/spec/registry.py` (write-once generated/connectors/<id>/<release>/{spec.yaml,build-report.json}, sha256 signature, traversal guards, half-write cleanup), `src/autobridge/spec/__init__.py` (public exports), `schemas/connector-spec.v1.json` (generated from model_json_schema, pinned by test), `tests/unit/test_autobridge_spec.py` (81 tests), `pyproject.toml` + `uv.lock` (jsonschema>=4.23)
- **Decisions:** `retry.statuses` not plan's `retry.on` (YAML 1.1 parses bare `on:` as True — LEARNINGS #2); `enum_map` hard-requires `on_unknown: block`; operations `GET`-only for v1; normalizer returns plain data for full path locs; reverse via diff-back; empty transform list = no transform (dump round-trips)
- **Checks:** `make test` — 138 pass (autobridge 100%, verifier 100%, simulators 100%); `make lint` / `make typecheck` / `make lint-imports` (1 kept) green; no e2e file for W1.3 (compose untouched). Agent: opencode meta/muse-spark-1.3 (escalation).
- **Gotchas:** single-quote JSONPath branch was missing its closing `\]` — masked because `re.match` prefix-matches; always assert full-string matches (`fullmatch` or `$` anchor with end-anchored test strings) when testing validators. `re` `\'` inside a character class is a literal quote, which made the broken pattern look right on inspection.

### P1.W1.4
- **Files:** `schemas/reservation.schema.json`, `schemas/folio-charge.schema.json` (Decimal-as-string 2dp, property-local business dates, closed 5-status vocab, no extras), `schemas/runtime-target.json` (local_process/docker_sandbox/edge oneOf, edge reserved), `schemas/discovery-result.v0.json` (reserved, 5 S3 states), `src/autobridge/spec/entities.py` (Reservation/FolioCharge/RuntimeTarget/DiscoveryResult models, Money + BusinessDate validated types, REFERENCE_* examples, validate_against_schema/validate_entity/schema_path), `src/autobridge/spec/__init__.py` (exports), `tests/unit/test_autobridge_entities.py` (60 tests)
- **Decisions:** hand-written schemas (interop contracts; patterns explicit) with round-trip + field/required-set consistency guards instead of W1.3-style exact generation pin; canonical money requires exponent exactly -2; schema pattern is shape-only, calendar check lives in model/verifier; same-day stay ok, negative folio amounts ok (refunds), guest_name/description nullable
- **Checks:** `make test` — 198 pass (181 autobridge @100% incl. entities.py @100%, 4 verifier @100%, 13 simulators @100%); `make lint` / `make typecheck` green; no e2e file for W1.4 (workstream e2e is P1.W1.10; compose untouched). Agent: opencode meta/muse-spark-1.3 (escalation).
- **Gotchas:** JSON Schema `pattern` + `format: date` cannot reject Feb 30 / month 13 — split the test (model rejects all five bad dates, schema rejects the three misshapen ones) rather than weakening either layer.
