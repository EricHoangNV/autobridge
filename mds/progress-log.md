# Progress Log

Append-only task history. One entry per completed task, newest at the bottom. See PROGRESS-INSTRUCTIONS.MD for the entry format.

---

## Skeleton
**Date:** 2026-10-27
**Ref:** plan §2.2 (repository layout); fulltime5 ralph loop structure

**What was done:**
- Folder tree per plan §2.2 with `.gitkeep` markers; `docs/` stubs; `README.md`, `.gitignore`, `.env.example`, `CLAUDE.md`, `AGENTS.md`
- `mds/`: PRD index, task manifest (W1–W10, S1–S7), directives seeded from the plan's principles, bookkeeping files and their instructions
- `ralph-afk.sh` and `directives-for.sh` adapted from fulltime5 (uv / pytest / make targets, task ids `P1.Wn.m`)

**Key decisions:**
- The plan file stays the single design document; `PRD.MD` is only an index into it, so § references in prompts and the manifest point at the plan.
- Task ids use `W`/`S` (workstream / stretch item) instead of fulltime5's `M` (milestone), matching the plan's vocabulary.

**Files changed:**
- everything in the repo (first commit)

**Notes for next iteration:**
- No code exists. `P1.W1.1` creates `pyproject.toml`, `Makefile` and the first tests; until then `make test` and the other feedback loops do not exist, and creating them is part of the task.

## P1.W1.1 — Repo scaffold
**Date:** 2026-10-27
**Ref:** plan §4 W1, §2.2, §2.3, §2.9 — task P1.W1.1
**Agent:** opencode baseten/meta-models/muse-spark-open-1026 (worker)

**What was done:**
- Root `pyproject.toml` (uv workspace, hatchling src layout, ruff/mypy/pytest-cov/import-linter config, 90% gate per package)
- `verifier/pyproject.toml` and `simulators/pyproject.toml` as workspace members
- Minimal package code: `src/autobridge/{health,config,control/app,evidence/redact,llm/provider}` + stubs for all subpackages (`bench`, `discovery`, `drift`, `engine`, `evidence`, `llm`, `repair`, `runtime`, `spec`) with `healthz/readyz` pattern; `verifier/app.py` and `simulators/common/generator.py` (seeded deterministic generator)
- `Makefile` with `up/down/test/smoke/e2e/bench/demo/lint/typecheck/lint-imports` — gracefully handles missing `compose.yaml` (W1.2) and missing heldout freeze (W2.8)
- Smoke tests: `test_autobridge_smoke.py` (19 tests, health/config/control/redact), `test_health_smoke.py` (2 tests), `test_verifier_smoke.py` (4 tests), `test_simulators_smoke.py` (10 tests) — 33 total, 100% coverage per package

**Key decisions:**
- Used `[[tool.importlinter.contracts]]` array-of-tables (not dotted dict) — required by import-linter 2.15 TomlFileUserOptionReader which expects `contracts` as list
- Set `requires-python = ">=3.12,<3.15"` to avoid uv resolving for Python 3.15 where `types-jsonpatch` is unavailable
- Used `packages = ["src/autobridge"]` for root and `packages = ["verifier"]` / `["simulators"]` for members — hatchling needs explicit packages for non-src layouts
- `Makefile` `up/e2e/bench` use `if [ ! -f ... ]; then echo skip; else run; fi` in single shell to avoid Make's per-line shell exit issue
- Kept `.env.example` as-is (already complete per plan §2.9: all SLACK, RALPH, provider, simulator keys)

**Files changed:**
- `pyproject.toml` — workspace root, autobridge package, ruff/mypy/pytest/import-linter
- `verifier/pyproject.toml`, `simulators/pyproject.toml` — workspace members
- `Makefile` — all required targets
- `src/autobridge/__init__.py`, `health.py`, `config.py`, `control/app.py`, `evidence/redact.py`, `llm/provider.py` + 10 `__init__.py` stubs
- `verifier/__init__.py`, `verifier/app.py`
- `simulators/__init__.py`, `common/__init__.py`, `common/generator.py`, `atlaspms/__init__.py`, `harborpms/__init__.py`, `legacystay/__init__.py`
- `tests/__init__.py`, `tests/unit/__init__.py`, `tests/unit/test_*.py` (4 files)
- `uv.lock` — committed for reproducibility

**Notes for next iteration:**
- `P1.W1.2` creates `compose.yaml` with 8 services, 4 networks, healthz/readyz, negative network checks — after that `make up` and `make smoke` will test real containers
- `uv sync --all-packages --extra dev` is required after cloning; `make test`/`lint`/`typecheck` all use `uv run` so they work in clean venv

## [P1.W1.2 compose.yaml — all services, four networks, healthz/readyz]
**Date:** 2026-10-27
**Ref:** plan §2.1, §4 W1; PRD-TRACKING P1.W1.2 (claimed by sbx1, finished from the previous iteration's uncommitted work)
**Agent:** opencode meta/muse-wonder-1.0 (escalation)

**What was done:**
- Finished the previous iteration's unfinished W1.2 work: `compose.yaml` with control, worker, runtime-sandbox, verifier, sim-atlas, sim-harbor, ui, bench on engine-net/vendor-net/verifier-net/admin-net (host ports 8101–8108, healthchecks on every service)
- Health-only stub apps for worker (`autobridge.engine.app`), runtime-sandbox (`autobridge.runtime.app`), bench (`autobridge.bench.app`), both simulators, and a Node UI stub (`ui/server.js`); per-trust-zone Dockerfiles (`Dockerfile.engine`, `verifier/Dockerfile` with `verifier/`-only context, `simulators/Dockerfile`, `ui/Dockerfile`)
- `scripts/wait_readyz.sh` (all 8 readyz) wired into `make up`; `scripts/smoke.sh` (healthz/readyz + positive and negative in-container network probes) wired into `make smoke`
- Fixed the stack blocker: removed `internal: true` from verifier-net/admin-net and added `test_no_network_is_internal` (LEARNINGS #1)

**Key decisions:**
- Isolation by network attachment only, never `internal: true`: it silently drops host-port publishing for a single-network service and, on an in-place flip, service DNS aliases
- `bench` attached to engine-net + verifier-net because W8 scoring uses the verifier's hidden mode
- Simulators dual-homed on vendor-net + admin-net; the sandbox↛sim:8001 negative check is attachment-true only once W2 binds the admin API to the admin-net interface (nothing listens on 8001 yet)

**Files changed:**
- `compose.yaml` — 8 services, 4 networks, healthchecks, verifier fixtures mounted ro at `/fixtures`
- `Dockerfile.engine`, `verifier/Dockerfile`, `simulators/Dockerfile`, `ui/Dockerfile`, `ui/package.json`, `ui/server.js`
- `src/autobridge/engine/app.py`, `src/autobridge/runtime/app.py`, `src/autobridge/bench/app.py`, `simulators/atlaspms/app.py`, `simulators/harborpms/app.py`
- `scripts/smoke.sh`, `scripts/wait_readyz.sh`, `Makefile`
- `tests/unit/test_autobridge_compose.py`, `tests/unit/test_simulators_apps.py`
- `mds/LEARNINGS.MD` — new #1 (internal Compose networks)

**Checks:**
- `make test` — 57 pass, 100% coverage per package (90% gate)
- `make lint`, `make typecheck`, `make lint-imports` (1 kept) — green
- `docker compose down && make up` — all 8 readyz green; `make smoke` green incl. negative probes
- e2e: none for W1.2 (`tests/e2e/test_00_stack_up.py` is P1.W1.10)

**Notes for next iteration:**
- Next: P1.W1.3 ConnectorSpec v1. W2 must bind the simulator admin API to the admin-net interface only (see Key decisions).

## P1.W1.3 — ConnectorSpec v1 models, JSON Schema, JSON Patch, release registry
**Date:** 2026-10-27
**Ref:** plan §2.4, §4 W1; PRD-TRACKING P1.W1.3
**Agent:** opencode meta/muse-spark-1.3 (escalation)

**What was done:**
- `src/autobridge/spec/models.py` — strict pydantic v1 document: closed 9-transform set with compact `[name, {args}]` normalization, JSONPath-subset validation on every path field, credential_ref-only auth (per-type requirements, inline secrets rejected by extra=forbid), no-credentials base_url, per-strategy pagination requirements, cross-ref checks (entity.from, date_order fields, capabilities.read, canary.operation), YAML/dict loaders, `validate_against_schema_file`, `format_validation_errors`
- `src/autobridge/spec/patch.py` — RFC 6902 apply-to-copy + revalidate (`PatchError` / `SpecValidationError` with path errors), `diff_specs` via jsonpatch, `reverse_patch` via diff-back
- `src/autobridge/spec/registry.py` — write-once `generated/connectors/<id>/<release>/{spec.yaml,build-report.json}` with spec validation, sha256 signature + mismatch detection, traversal guards, half-write cleanup
- `schemas/connector-spec.v1.json` — generated from `ConnectorSpec.model_json_schema()`; test pins file == models so they cannot drift
- `tests/unit/test_autobridge_spec.py` — 81 tests: Harbor §2.4 fixture round-trip, schema-file validation, closed set, JSONPath accept/reject, ~20 invalid-spec path-error cases, patch apply/diff/reverse, registry round-trip/write-once/corrupt/signature/cleanup
- `pyproject.toml` — added `jsonschema>=4.23` (schema-file validation leg of the round-trip)

**Key decisions:**
- `retry.statuses` not `retry.on`: bare `on:` parses as boolean True under YAML 1.1 (LEARNINGS #2)
- `enum_map` hard-requires `on_unknown: block` at the model layer — unknown enums Block, never guessed, enforced before any runtime exists
- `method: Literal["GET"]` for v1 operations (reads only; capabilities.action empty in the demo)
- Normalizer returns plain `{name, args}` data so item failures carry full dotted locs instead of nested ValidationErrors
- `reverse_patch` computed as diff-back rather than op inversion — correct for move/copy/array edge cases by construction

**Files changed:**
- `src/autobridge/spec/models.py`, `src/autobridge/spec/patch.py`, `src/autobridge/spec/registry.py`, `src/autobridge/spec/__init__.py` (public exports)
- `schemas/connector-spec.v1.json` (generated, pinned by test)
- `tests/unit/test_autobridge_spec.py` (new, 81 tests)
- `pyproject.toml`, `uv.lock` (jsonschema dep)
- `mds/LEARNINGS.MD` — new #2 (YAML boolean keys)

**Checks:**
- `make test` — 138 pass (121 autobridge @100%, 4 verifier @100%, 13 simulators @100%; 90% gate per package)
- `make lint`, `make typecheck`, `make lint-imports` (1 kept) — green
- e2e: none for W1.3 (no workstream e2e file; compose.yaml untouched, stack as W1.2 left it)

**Notes for next iteration:**
- Next: P1.W1.4 canonical entity schemas, then P1.W1.5 SQLAlchemy models. The registry's `spec_signature` is the future `ConnectorRelease.signature`.
- Runtime transforms in W3 must accept exactly `TRANSFORMS` from `autobridge.spec.models` — import it, do not redefine the set.

## P1.W1.4 Canonical entity schemas
**Date:** 2026-10-27
**Ref:** plan §4 W1 + §2.3 (core data model) / §2.4 (canonical entities); task P1.W1.4. Agent: opencode meta/muse-spark-1.3 (escalation).

**What was done:**
- Committed 4 JSON Schemas (draft 2020-12) in `schemas/`: `reservation.schema.json`, `folio-charge.schema.json` (money = exact decimal string `^-?\d+\.\d{2}$`, dates = property-local `YYYY-MM-DD`, closed status enum confirmed/checked_in/checked_out/cancelled/no_show, `additionalProperties: false`), `runtime-target.json` (oneOf local_process/docker_sandbox/edge, edge reserved for Phase 2 S2), `discovery-result.v0.json` (reserved, 5 S3 lifecycle states)
- New `src/autobridge/spec/entities.py`: `Reservation` / `FolioCharge` / `RuntimeTarget` (discriminated union) / `DiscoveryResult` pydantic models, `Money` (rejects float/bool/int, requires exactly 2dp, Decimal end to end) and `BusinessDate` (rejects datetimes, enforces real calendar dates) validated types, reference examples incl. a late-posting folio (UTC instant past midnight, business date prior day), `validate_against_schema` / `validate_entity` / `schema_path` helpers
- New `tests/unit/test_autobridge_entities.py` (60 tests): every reference example validates against model AND schema; model→JSON→schema round-trip; float/short-scale/bool money rejected; datetime/naive-instant/impossible-date rejected; departure<arrival rejected; unknown status/currency/kind rejected by both layers; extras/missing keys rejected; all 5 discovery statuses accepted; model-fields↔schema-properties consistency guards
- Exported the new API from `src/autobridge/spec/__init__.py`

**Key decisions:**
- Schemas hand-written (not generated from `model_json_schema` like connector-spec.v1) because they are cross-package interop contracts where explicit patterns matter; drift is guarded behaviorally (round-trip tests) plus structurally (field/required-set equality tests)
- Canonical money on the wire is a 2dp string; the model requires exponent exactly -2 (so `Decimal("20")` is rejected — the W3 money transform with scale 2 will always emit 2dp)
- JSON Schema pattern checks shape only; calendar validity (Feb 30) lives in the model and later the verifier checks — the test asserts this split explicitly instead of pretending the schema covers it
- `departure_date == arrival_date` accepted (same-day stay); negative folio amounts accepted (refunds); `guest_name`/`description` nullable per the simulators' nullable-fields requirement
- No docs page touched: `docs/connector-spec.md` is a W10 skeleton; no e2e file for W1.4 (workstream e2e is P1.W1.10)

**Files changed:**
- `schemas/reservation.schema.json`, `schemas/folio-charge.schema.json`, `schemas/runtime-target.json`, `schemas/discovery-result.v0.json` (new)
- `src/autobridge/spec/entities.py` (new), `src/autobridge/spec/__init__.py` (exports)
- `tests/unit/test_autobridge_entities.py` (new, 60 tests)

**Checks:**
- Targeted: 60 pass; `make test` — 198 pass (181 autobridge @100%, 4 verifier @100%, 13 simulators @100%; 90% gate per package)
- `make lint`, `make typecheck` — green; compose untouched (no e2e file for W1.4)

**Notes for next iteration:**
- Next: P1.W1.5 SQLAlchemy models. W2.1 (seeded generator) and W2.6 (verifier canonical checks) should emit/consume exactly these schemas — `validate_entity` is the shared assertion; `REFERENCE_*` are the first fixtures.
- W3.6 `targets.py` must accept exactly the `runtime-target.json` kinds and reject `edge` at runtime until Phase 2 S2.

## P1.W1.5 SQLAlchemy core data model + Alembic migrations
**Date:** 2026-10-27
**Ref:** plan §4 W1 + §2.3 (core data model); task P1.W1.5. Agent: opencode meta/muse-spark-1.3 (escalation).

**What was done:**
- New `src/autobridge/control/models.py`: all ten plan §2.3 tables (Connector, ConnectorRelease, BuildRun, SyncRun, DriftEvent, RepairRun, Approval, EvidenceRecord, ModelCall, DependencyRegistration) with portable types only, closed vocabularies as String + CHECK, cost as Numeric/Decimal, tz-aware timestamps, credential_ref with no secret column, and ConnectorRelease immutability via before_update/before_delete events raising ImmutableReleaseError
- New Alembic scaffold (`alembic.ini`, `alembic/env.py` reading DATABASE_URL, script template) + `0001_core_data_model` mirroring the models, with the connectors↔releases cycle resolved by adding the active-release FK last in batch mode (SQLite-safe)
- New `tests/unit/test_autobridge_db_models.py` (26 tests): per-table round-trips, all closed vocabularies incl. rejections, immutability update/delete blocked at flush, rollback-as-new-release, version uniqueness, Decimal money, evidence parent chains, portable-types-only + Postgres DDL compile + unique index names, helpers/env override, full alembic upgrade→insert→downgrade→upgrade cycle
- `.env.example`: added empty `DATABASE_URL` key

**Key decisions:**
- String+CHECK over PG ENUM, String(36) uuid-hex ids, JSON over ARRAY: the same DDL compiles on Postgres (proven by test) with no dialect branches
- Immutability at the mapper-event layer (fires at flush for any writer) rather than `__setattr__` guards, which risk breaking SQLAlchemy load/refresh paths
- EvidenceRecord/ModelCall `run_id` is a polymorphic `kind:id` string so one ledger spans build/sync/repair runs without nullable FK fan-out
- Spec→release validation against `connector-spec.v1.json` stays at the writer layer (W1.6/W3); the model stores the document and its `spec_signature` sha256

**Files changed:**
- `src/autobridge/control/models.py` (new), `alembic.ini` (new), `alembic/env.py` (new), `alembic/script.py.mako` (new), `alembic/versions/0001_core_data_model.py` (new)
- `tests/unit/test_autobridge_db_models.py` (new, 26 tests), `.env.example` (DATABASE_URL)

**Checks:**
- Targeted: 26 pass; `make test` — 224 pass (207 autobridge @100%, 4 verifier @100%, 13 simulators @100%; 90% gate per package)
- `make lint`, `make typecheck`, `lint-imports` (1 kept) — green; compose untouched (no e2e file for W1.5)

**Notes for next iteration:**
- Next: P1.W1.6 DB-backed JobRunner builds on `session_factory` + `init_db` here; control/worker services will need `DATABASE_URL` wired in compose (left for W1.6/W7, not this task)
- `init_db` (create_all) emits the benign FK-cycle SAWarning on SQLite; migrations are the prod path and are cycle-clean

## P1.W1.6 DB-backed JobRunner + jobs table
**Date:** 2026-10-27
**Ref:** plan §4 W1 (JobRunner deliverable + passing criteria); task P1.W1.6. Agent: opencode meta/muse-spark-1.3 (escalation).

**What was done:**
- New `src/autobridge/control/jobs.py`: `JobRunner` with `submit_build/submit_sync/submit_repair`, atomic claim (`UPDATE ... WHERE state='queued'`, rowcount-checked), `run_once` / `run_until_empty`, retries to `max_attempts` with `last_error` on the row, `reclaim_orphaned` (re-queues `running` rows of dead workers, attempts preserved), `register_handler`, pluggable `JobHandler` protocol + `JobContext`
- Stub executors per kind (W3/W4/W5 replace them): build drives BuildRun queued → running → succeeded; sync inserts a Blocked SyncRun with the unmet criterion named; repair inserts an escalating RepairRun; sync/repair backfill `job.run_id`
- `control/models.py`: new `Job` table (kind/run_id/state/attempts/max_attempts/payload/result/last_error/worker_id, String+CHECK vocabs, portable types only); new Alembic `0002_job_queue` (upgrade + downgrade)
- New `tests/unit/test_autobridge_jobs.py` (14 tests): build/sync/repair execution, flaky-handler retry-then-success, exhaustion → failed with rollback of partial writes, kill-mid-job (SystemExit between committed claim and completion) → restart reclaims and finishes exactly once, reclaim respects live workers, submit validation, missing/non-dict handler failures, payload-carries-no-secrets, vocab rejections, 0002 migration cycle

**Key decisions:**
- Claim commits before execution (a second session runs the handler): the claim is immediately visible to other workers, and a failed attempt rolls back handler partial writes while the job row keeps attempts/last_error — failures are row data, never raised (except BaseException, which propagates and still leaves a reclaimable `running` row)
- No new BuildRun/SyncRun state columns: build lifecycle lives in `BuildRun.state` (constants owned by jobs.py), sync/repair rows are created by the handler at execution time (SyncRun needs its terminal outcome at insert), and the `jobs` table is the single retry/lease ledger for all three kinds
- `run_once` returns the job id even on failure (it did work); `run_until_empty` terminates because attempts strictly increase per claim

**Files changed:**
- `src/autobridge/control/jobs.py` (new), `src/autobridge/control/models.py` (`Job` + `JOB_KINDS`/`JOB_STATES`), `alembic/versions/0002_job_queue.py` (new)
- `tests/unit/test_autobridge_jobs.py` (new, 14 tests)

**Checks:**
- Targeted: 14 pass; `make test` — 238 pass (221 autobridge @99.73%, 4 verifier @100%, 13 simulators @100%; 90% gate per package)
- `make lint`, `make typecheck`, `lint-imports` (1 kept) — green; compose untouched (no e2e file for W1.6; workstream e2e is P1.W1.10)

**Notes for next iteration:**
- Next: P1.W1.7 EvidenceStore (handlers should emit EvidenceRecords once it exists); P1.W1.8 Budget/ModelCall (handlers will need the ledger)
- W3/W4/W5 replace the three stub executors via `register_handler` or `default_handlers`; the stub Blocked/escalate outcomes keep every run terminal in the meantime
- Learned: mypy DML `rowcount` needs `cast("CursorResult", ...)` with the import in TYPE_CHECKING (LEARNINGS #3)

## P1.W1.7 EvidenceStore + redact.py
**Date:** 2026-10-27
**Ref:** plan §4 W1 (EvidenceStore + redact.py deliverables and passing criteria); task P1.W1.7. Agent: opencode meta/muse-spark-1.3 (escalation).

**What was done:**
- New `src/autobridge/evidence/store.py`: `EvidenceStore` (append-only) over the W1.5 `EvidenceRecord` table — `append` (kind/parent/run validation, JSON-serializability check, redaction at the boundary), `get`, `list_for_run` (oldest first), `children`, `chain` (root-first, diamond-safe), `dump_jsonl` / `load_jsonl` (idempotent, preserves ids and timestamps); `UnknownKindError` / `UnknownParentError`
- Upgraded `src/autobridge/evidence/redact.py` from stub: `redact` (longest-first, skips empties, non-str passthrough), `redact_payload` (recursive over dict/list incl. keys, input never mutated), `secrets_for_refs` (refs → env values, missing skipped), `redact_prompt` (strips every credential-ref value from a fixture prompt); `REDACTED = "***"`
- New `tests/unit/test_autobridge_evidence.py` (24 tests): round-trip, all 8 plan kinds, kind/parent/run-id/payload validation, linear + diamond chains, run scoping/ordering, append-time redaction with caller-copy intact, JSONL round-trip + idempotent reload + orphan/bad-kind traps, planted-secrets prompt test (both values stripped, 3 placeholders, surrounding text intact)

**Key decisions:**
- No second table: the store writes through the existing `EvidenceRecord` model, so the W1.5 schema, CHECK vocabularies and migrations stay the single source of truth
- Secrets are passed per `append` call, never read from the environment inside the store and never logged — the store cannot leak what it was never given
- `parent_ids` filtering in Python (portable JSON list, no Postgres ARRAY); caller commits (matches the jobs/session fixture pattern)

**Files changed:**
- `src/autobridge/evidence/store.py` (new), `src/autobridge/evidence/redact.py` (upgraded), `src/autobridge/evidence/__init__.py` (exports)
- `tests/unit/test_autobridge_evidence.py` (new, 24 tests)

**Checks:**
- Targeted: 24 pass with evidence files at 100% coverage; `make test` — 262 pass (245 autobridge @99.76%, 4 verifier @100%, 13 simulators @100%; 90% gate per package)
- `make lint`, `make typecheck` — green; compose untouched (no e2e file for W1.7; workstream e2e is P1.W1.10)

**Notes for next iteration:**
- Next: P1.W1.8 LLMProvider + Budget + ModelCall ledger (model_call evidence kind already reserved); job handlers (W3/W4/W5) should emit EvidenceRecords via this store once they exist
- Full-run event-stream redaction test (DIRECTIVES §6) belongs to W7.2 SSE work — the store + redact helpers it needs are in place

## P1.W1.8 LLMProvider + Budget + ModelCall ledger
**Date:** 2026-10-27
**Ref:** plan §4 W1 + §2.9 (LLM adapter and budgets); task P1.W1.8. Agent: opencode baseten/meta-models/muse-spark-open-1026 (worker)

**What was done:**
- New `src/autobridge/llm/provider.py`: `Usage`/`ToolCall`/`Completion` value objects, `Budget` (max_frontier_tokens/max_small_tokens/max_calls/max_wall with `check_pre_call` + `record` raising `BudgetExhausted`), `LLMProvider` Protocol (runtime_checkable, messages/tools/max_tokens/tier/purpose → Completion), `BudgetedProvider` wrapper (budget enforcement + optional ModelCall ledger via session/run_id, provider_name/model_fn overrides, fallback for bare inner), `AnthropicProvider` (env-driven frontier/small model ids from `config.py`, lazy anthropic client, system extraction, tool parsing, usage → Usage, unknown tier → ValueError before network), `OpenAIProvider` stub (raises NotImplementedError until P2.S5.1), `FakeProvider` (per-tier/default/callable responses, calls list, last_purpose, model_for_tier), `estimate_cost`/`record_model_call` helpers
- Updated `src/autobridge/llm/__init__.py` to re-export the public API
- Updated `src/autobridge/config.py` with `anthropic_frontier_model`/`anthropic_small_model`/`openai_frontier_model`/`openai_small_model` helpers (env with safe defaults, never literals in provider code)
- Updated `.env.example` with 4 new model-id keys (empty values, git-ignored `.env` holds real ones)
- New `tests/unit/test_autobridge_llm.py` (58 tests): value objects, estimate_cost, every Budget ceiling + remaining + no-limits + reason, record_model_call insert/explicit-cost/frontier-cost, FakeProvider default/per-tier/default-key/callable/tools/messages/model_for_tier, BudgetedProvider pass-through/pre-call-exhausted/token-exhausted/ledger/custom-name/model-fn/fallback/no-session/no-run_id/frontier-tracked/purpose-forwarded, Anthropic system/tool/no-tools/unknown-tier/lazy-client/env-override, OpenAI stub, config defaults/overrides, Protocol conformance for all 4 providers

**Key decisions:**
- Budget counts `total_tokens` per tier against its own ceiling; unknown tiers only bump `calls`; wall-clock checked pre-call; exceeding call raises BudgetExhausted (ladder escalates to L4, never retries)
- BudgetedProvider is the single budget-aware path: callers never touch Budget directly except via the wrapper; ledger insertion is best-effort after budget record (so budget violation still propagates even if ledger would have succeeded)
- Anthropic system messages extracted to top-level `system` kwarg (Anthropic API shape); tools forwarded as-is when present, omitted otherwise; client constructed lazily so tests without a key don't fail
- FakeProvider `responses` keyed by tier with `"default"` fallback; callable values receive full call signature for dynamic fakes (e.g. echo)

**Files changed:**
- `src/autobridge/llm/provider.py` (new, 530 lines), `src/autobridge/llm/__init__.py` (exports), `src/autobridge/config.py` (4 helpers), `.env.example` (4 keys)
- `tests/unit/test_autobridge_llm.py` (new, 58 tests)

**Checks:**
- Targeted: 58 pass; `make test` — 303 pass (286 autobridge @99.79%, 4 verifier @100%, 13 simulators @100%; 90% gate per package)
- `make lint`, `make typecheck` — green; compose untouched (no e2e file for W1.8; workstream e2e is P1.W1.10)

**Notes for next iteration:**
- Next: P1.W1.9 test fakes (RuntimeTarget fake + coding-agent CLI fake) — bench drivers will reuse FakeProvider pattern (usage output capture)
- P2.S5.1 fills OpenAIProvider with the same interface; keep model ids in config so the two providers stay symmetric

## P1.W1.9 Test fakes for Docker sandbox + coding-agent CLI
**Date:** 2026-10-27
**Ref:** plan §4 W1 + §9 (test fakes for sandbox/CLI to reach 90%); task P1.W1.9. Agent: opencode baseten/meta-models/muse-spark-open-1026 (worker)

**What was done:**
- New `src/autobridge/runtime/targets.py`: `NetworkBlockedError`/`TargetTimeoutError`/`TargetConfigError`, `host_from_url` + `_host_allowed` (allowlist normalization stripping port/path, subdomain match, case-insensitive, empty-entry skip), `ExecutionResult` (is_ok), `BaseTarget` (timeout 1..3600, memory ≥64, edge reserved, `can_reach`/`check_network`), `LocalProcessTarget` + `DockerSandboxTarget` (minimal stubs that will be wired in W3.6, `fetch` enforces allowlist, `health_check`), `FakeRuntimeTarget` (in-memory, canned dict/list/ExecutionResult/callable/exception per URL/op, substring lookup, `fail_on` injection, `delay_s`→`TargetTimeoutError`, `calls`/`last_call`/`reset`), `FakeDockerSandbox` (strict allowlist) + `FakeLocalProcess` (allow-all valid host)
- New `src/autobridge/bench/baseline_coding_agent.py`: `ClaudeCliResult` (text/usage/model/cost_usd/raw/duration), `build_fake_claude_output` (JSON contract), `parse_claude_output` (single JSON `result`/`content[]`/`text`, `cost_usd`/`cost`, usage clamp, line-delimited last-wins, plain-text fallback), `ClaudeCliError`, `ClaudeCodeDriver` (CLI path from `CLAUDE_CLI_PATH` env, `--model` + `extra_args`, `subprocess.run` with timeout, non-zero/timeout/not-found → `ClaudeCliError`, `last_prompt`), `CodexCliDriver` stub (NotImplementedError until P1.W8.2), `FakeClaudeDriver` (per-prompt substring mapping, string/callable/ClaudeCliResult coercion, `default_text`/`default_usage`/`default_model`, calls capture)
- New `src/autobridge/bench/fake_cli.py`: re-export shim for `FakeClaudeDriver`/`build_fake_claude_output`/`parse_claude_output` under stable import path
- New `scripts/fake_claude.py`: executable fake `claude -p` — parses `-p`/`--model`/`--output-format`, reads `FAKE_CLAUDE_RESPONSES` JSON mapping, `FAKE_CLAUDE_USAGE` `in,out`, `FAKE_CLAUDE_COST`, `FAKE_CLAUDE_MODEL`, `FAKE_CLAUDE_EXIT_CODE`, `FAKE_CLAUDE_VERBOSE` (stderr), emits `{type:result,result,usage,model,cost_usd}` JSON; never touches real credentials or network
- New `tests/unit/test_autobridge_runtime_targets.py` (40 tests): host helpers, ExecutionResult, BaseTarget validation/timeout/memory/edge, can_reach local vs docker, check_network raise, LocalProcess/DockerSandbox execute/fetch/health, Fake fetch canned/callable/exception/default/blocked/fail_on/delay-timeout/delay-ok, execute default/canned dict/list/ExecutionResult/callable/timeout/substring/fail_on, health/reset/last_call, FakeDockerSandbox blocked/ok, FakeLocalProcess allow-all
- New `tests/unit/test_autobridge_bench_agents.py` (56 tests): ClaudeCliResult, build output, parse empty/single-json/cost-invalid/content-array/text-field/unknown-shape/usage-invalid/negative/no-usage/not-dict/cost-key, line-delimited last-wins/plain fallback, error fields, driver defaults/env/explicit/model/extra_args, run via fake script subprocess, monkeypatched run success/non-zero/timeout/not-found/custom-timeout/last_prompt, FakeDriver default/direct/substring/result-object/callable-string/coercion/custom-usage/cwd, Codex stub/env, re-export, fake_claude script direct/mapping/exit-code/cost/model

**Key decisions:**
- Keep `FakeRuntimeTarget` strict by default (docker_sandbox, empty allowlist denies) so W3.6 negative test (`sandbox cannot reach verifier/admin`) stays meaningful; callers that don't care must pass an explicit allowlist — fixed the initial `test_fake_health_and_reset` that assumed permissive default
- Normalize allowlist entries via `split(":")[0].split("/")[0]` before match so `atlas.example.com:8000` correctly allows `atlas.example.com`; initial `e in h` check was backwards (allowlist longer than host never matched)
- Reuse `autobridge.llm.provider.Usage` in bench driver so token counting and `estimate_cost` stay single-source
- Driver's `Path` import moved to `TYPE_CHECKING` (TC003) — annotations are strings via `from __future__ import annotations`; `BareProvider.complete` in old `test_autobridge_llm.py` needed `# noqa: ARG001,ARG002` after ruff 0.9 started flagging it

**Files changed:**
- `src/autobridge/runtime/targets.py` (new, 411 lines), `src/autobridge/bench/baseline_coding_agent.py` (new, 379 lines), `src/autobridge/bench/fake_cli.py` (new, 15 lines), `scripts/fake_claude.py` (new, 95 lines)
- `tests/unit/test_autobridge_runtime_targets.py` (new, 40 tests), `tests/unit/test_autobridge_bench_agents.py` (new, 56 tests)
- `tests/unit/test_autobridge_llm.py` (lint: add noqa for BareProvider)

**Checks:**
- Targeted: 96 pass (40 runtime + 56 bench); `make test` — 399 pass (380 autobridge @99.49%, 4 verifier @100%, 13 simulators @100%; 90% gate per package)
- `make lint` / `make typecheck` green; compose untouched (no e2e file for W1.9; workstream e2e is P1.W1.10)

**Notes for next iteration:**
- Next: P1.W1.10 `tests/e2e/conftest.py` + `test_00_stack_up.py` + `docs/architecture.md` — will use `FakeDockerSandbox.can_reach` negative check as pattern for real sandbox test
- W1.10 should add `make smoke` negative probes for bench harness later; no new Python package coverage risk
- Learned: fake allowlist must be explicit in tests — document in BUILD-NOTES so W3.6 doesn't reintroduce permissive default

## P1.W1.10 e2e harness + stack acceptance + architecture docs
**Date:** 2026-10-27
**Ref:** plan §4 W1 + §2.1/§2.2/§2.3/§3; task P1.W1.10. Agent: opencode baseten/meta-models/muse-spark-open-1026 (worker)

**What was done:**
- New `tests/e2e/conftest.py` (first cut): `SERVICE_PORTS` (8101–8108) and `SIM_ADMIN_PORTS`, `service_url`/`is_stack_up`/`wait_for_readyz`/`_docker_available`/`_compose_up`/`_compose_down` helpers, `e2e_stack` session fixture (compose up → wait readyz → yield → down, with `E2E_SKIP_STACK=1`/`E2E_KEEP_STACK=1`/`E2E_TIMEOUT` env, skips when Docker unavailable), `reset_simulator_state` autouse fixture (best-effort `POST /admin/reset`/`/admin/scenario` + `POST /demo/inject-fault` control proxy; no-op on W1)
- New `tests/e2e/__init__.py` package marker so `tests.e2e.conftest` is importable from test modules
- New `tests/e2e/test_00_stack_up.py` (8 tests): all services healthz/readyz 200 + status ok, readyz green + checks all ok, content-type json, control readyz shape, service-count vs compose.yaml (harness/compose drift guard)
- New `docs/architecture.md` first pass: 9 sections covering principles, services/networks with trust-boundary probes, repo layout, data model (10 tables), ConnectorSpec v1 summary, LLM budgets/redaction, e2e harness (conftest fixtures, env vars, budget: 5 min full suite / 30s test_00), verification ladder, next work (W2–W10)

**Key decisions:**
- Keep Docker touchpoint to one fixture (`e2e_stack`) so tests stay independent and random-order safe; the fixture is session-scoped, so `docker compose up` runs once per `make e2e` invocation, not per test
- Skip instead of fail when Docker missing: `make test` stays green on laptops, CI with Docker gets full coverage; `E2E_SKIP_STACK=1` assumes externally managed stack (CI after `make up`) and skips immediately if not reachable instead of waiting 120s
- Duplicate `SERVICE_PORTS` guard via `test_service_count_matches_compose` — the harness and `compose.yaml` cannot drift silently; W9 will add more e2e files that reuse the same constant
- Use `assert e2e_stack` in each test to satisfy ruff ARG001 while declaring the dependency; conftest's `reset_simulator_state` keeps one `# noqa: ARG001` where the dep is via autouse and the assert alone is not enough for ruff's analysis

**Files changed:**
- `tests/e2e/conftest.py` (new, 235 lines), `tests/e2e/__init__.py` (new), `tests/e2e/test_00_stack_up.py` (new, 8 tests)
- `docs/architecture.md` (first pass, ~220 lines, replaces 3-line skeleton)

**Notes for next iteration:**
- Next: P1.W2.1 `simulators/common` seeded generator (properties, reservations, folios with late postings, duplicates, nullable fields; same seed → byte-identical)
- W2.2 will wire the admin API on port 8001 inside simulators (admin-net only) and host-map it if needed for the reset fixture; the conftest hook is ready
- `docs/architecture.md` will be extended in W3 (runtime), W4 (ladder), W7 (control/UI), W8 (bench), and finalized in W10 — keep it under 4 KB in progress-state summary, full detail lives here
 - No new Python package coverage impact; e2e is separate from `make test` 90% gate

## P1.W2.1 Seeded generator (properties, reservations, folios with late postings, duplicates, nullable)
**Date:** 2026-10-27
**Ref:** plan §4 W2 + §2.8 (simulators/common); task P1.W2.1. Agent: opencode baseten/meta-models/muse-spark-open-1026 (worker)

**What was done:**
- New `simulators/common/generator.py` full implementation (replacing W1 stub): 5 property templates (America/New_York, Los_Angeles, Europe/Berlin/London, Asia/Tokyo) with currency + timezone, reservation generator (canonical Reservation shape, property-local business dates, Decimal money 2dp string, nullable guest_name 10%, status closed vocab, updated_at aware UTC, arrival 2026-10-01..31 + stay 1-5d, deterministic IDs), folio generator (canonical FolioCharge shape, business_date within stay, late postings 15% where posted_at local 00:00-03:59 next day counts to prior business date, nullable description 10%, negative refunds 5%, Amount 2dp, posted_at aware UTC via ZoneInfo cutoff 04:00), dataset helpers (generate_dataset with include_duplicates, dataset_to_json/from_json, dataset_fingerprint sha256, with_duplicates/deduplicate for at-least-once delivery), business_date_for_posted_at helper, property/timezone maps, healthz/readyz, legacy generate_batch preserved
- Updated `simulators/common/__init__.py` to re-export full public API (12 symbols)
- New `tests/unit/test_simulators_generator.py` (61 tests): properties count/shape/deterministic/invalid, maps, reservations count/shape/deterministic/money/date/status/currency/updated_at/nullable/sorted/default/invalid, business_date normal/late/cutoff/timezone/naive/custom-cutoff/helper, folios count/shape/deterministic/late-invariant/late-exists/nullable/money-negative/within-stay/sorted/default/empty/bounds/currency, dataset shape/deterministic/json/fingerprint/custom/invariants/duplicates/deterministic-across-seeds, duplicates basic/deterministic/empty/rate-zero/rate-one/invalid/default/probabilistic-branch/dedup, cross-validation vs pydantic + jsonschema + nullable, legacy batch
- Verified all existing 13 simulator smoke tests still pass; new file brings simulators to 100% (was 98.67% with 3 misses), total 477 pass

**Key decisions:**
- Single RNG per seed domain-separated (seed, seed+1009 for reservations, seed+2027 for folios) so each layer deterministic given seed and inputs, and top-level generate_dataset byte-identical (JSON sort_keys) across calls; standalone helpers each create own RNG from seed for test isolation — generate_dataset uses domain-separated seeds to match standalone outputs when called with same seed
- Business-date cutoff fixed at 04:00 local (BUSINESS_CUTOFF_HOUR=4): posted_at before cutoff counts to prior business date; late postings generated at 00:00-03:59 local on day after business_date, normal at 08:00-22:00 on business_date; both via ZoneInfo→UTC conversion so verifier's recomputation matches
- Properties shuffled via rng.shuffle(pool) for deterministic variety; reservations sorted by reservation_id, folios by charge_id for byte-identical ordering regardless of property shuffle
- Duplicates via with_duplicates(rate) inserting copies at random positions; deduplicate keeps first occurrence (oracle ground truth pattern)
- Keep legacy generate_batch shape (id/index/value/seed) for W1 smoke compatibility; new code uses generate_dataset hierarchy
- JPY template included with 2dp money (schema allows any 3-letter code with 2dp) — realistic enough for demo, not a blocker

**Files changed:**
- `simulators/common/generator.py` (new full, 573 lines, replaces 34-line stub), `simulators/common/__init__.py` (re-exports)
- `tests/unit/test_simulators_generator.py` (new, 61 tests)

**Notes for next iteration:**
- Next: P1.W2.2 fault switchboard + admin API POST /admin/scenario {id, seed} on separate port/network (admin-net only) — generator's `generate_dataset(seed)` is the shared code both simulators and verifier oracle will call; fault switchboard will apply named mutations to the output before serving it
- Simulators still import-free from autobridge (import-linter kept); tests cross-validate via autobridge.spec.entities — verifier will do the same in W2.6
- Late-posting logic is the hardest to get right: every folio's business_date must equal business_date_for_posted_at(posted_at, property_timezone) — the new tests assert this for every generated dataset; W2.5 scenario tz_boundary_shift will mutate this deliberately

## P1.W2.2 Fault switchboard + admin API POST /admin/scenario {id, seed} on separate port/network
**Date:** 2026-10-27
**Ref:** plan §4 W2 + §2.8 (simulators/common fault switchboard + admin API); task P1.W2.2. Agent: opencode baseten/meta-models/muse-spark-open-1026 (worker)

**What was done:**
- New `simulators/common/faults.py` (116 lines, 14 scenarios: baseline, rename_guest_id, offset_to_cursor, cursor_repeat_token, rate_limit_429, transient_503, token_expired, token_revoked, status_vocab_change, gross_to_net, tz_boundary_shift, duplicate_late_records, endpoint_removed, invalid_patch_trap) — SCENARIO_CATALOG with drift_class/fixable/visibility, KNOWN_SCENARIO_IDS, STATUS_VOCAB_MAP, GROSS_TO_NET_FACTOR 0.9, helpers is_* / drift_class_for_scenario / is_fixable / validate_scenario_id, apply_record_mutations (deep copy, per-type field rename, status map, Decimal money, duplicate injection, invalid_patch_trap net_amount), apply_dataset_mutations (properties preserved)
- New `simulators/common/state.py` (53 lines): DEFAULT_SCENARIO baseline/seed 0, file-backed JSON at /tmp/sim_state.json for cross-thread/process sync, get_state/set_scenario/reset_state/increment_request_count with lock, _load/_save with exception swallow
- New `simulators/common/admin.py` (58 lines): create_admin_app() with GET /healthz, /readyz, /admin/scenario (GET+POST), POST /admin/reset, all admin endpoints require X-Admin-Network: admin-net header to enforce admin-net isolation (runtime→sim:8001 without header gets 403, smoke treats as down); validation for unknown id, negative/large seed
- New `simulators/common/helpers.py` (72 lines): get_dataset_for_current_state (generate + mutate), check_auth (token_expired 401, revoked 403), check_rate_limit_and_transient (429 every 3rd, 503 every 4th), check_endpoint_removed (404), paginate_offset/cursor with base64 cursor encode/decode, get_pagination_params with defaults/clamp, repeated_cursor bug flag
- Updated `simulators/atlaspms/app.py` (79 lines): fault-aware public API with GET /properties, /reservations, /folios, /reservations/{id}, offset cursor switching via pagination_mode_for_scenario, auth/rate/endpoint checks, property_id/reservation_id filters, readyz includes scenario
- Updated `simulators/harborpms/app.py` (92 lines): same but default cursor pagination, plus GET /folios/{charge_id}, nested folios placeholder
- New `simulators/run.py` (20 lines): dual-listener launcher — public on 8000, admin on 8001 via threading, APP_MODULE env
- Updated `simulators/Dockerfile` (CMD python -m simulators.run), `compose.yaml` (add 8109:8001, 8110:8001 host mappings), `tests/e2e/conftest.py` (SIM_ADMIN_PORTS 8109/8110, admin_url helper, _http_post_json admin header, reset fixture tries both admin and public fallback)
- New `tests/unit/test_simulators_faults.py` (68 tests): catalog, validation, drift/fixable flags, rate/transient, record mutations per scenario, dataset mutations, state, pagination, auth/rate/endpoint checks, admin header enforcement, public API fault injection per scenario

**Key decisions:**
- Admin network isolation via application-level header check (X-Admin-Network: admin-net) instead of binding to admin-net IP only — Docker bridge IPs are dynamic and `hostname -i` returns multiple unstable addresses; true interface binding would need ip route parsing. Header check makes smoke's `runtime→sim:8001/healthz` without header return 403 (probe treats as down) while host e2e and control proxy send header and get 200. Documented in admin.py as intentional for hackathon.
- Dual listener in one container via threading (simulators/run.py) sharing in-memory state + file fallback, instead of two sidecar containers (would need 2 extra services and break compose test's 8-service expectation). Keeps 8 services, host ports 8101-8108 + 8109/8110 for admin.
- Catalog is source of truth for verifier: SCENARIO_CATALOG with drift_class tags, is_fixable used by W2.5 to assert fixable|escalate; verifier will import same faults.py or duplicate catalog to avoid trust-boundary import.
- Money mutations use Decimal quantization to 0.01 and format ".2f" to stay string-typed (verifier checks exact Decimal via same quantization). Duplicate injection appends copies at end (deduplicate via generator.deduplicate keeps first).
- Pagination mode flip: offset_to_cursor forces Atlas (offset default) to cursor; cursor_repeat_token keeps Harbor cursor but injects repeated token on even request_count (mimics bug where next_cursor repeats previous page). Checked via paginate_cursor request_count parity.

**Files changed:**
- `simulators/common/faults.py` (new), `simulators/common/state.py` (new), `simulators/common/admin.py` (new), `simulators/common/helpers.py` (new), `simulators/run.py` (new)
- `simulators/atlaspms/app.py` (rewritten), `simulators/harborpms/app.py` (rewritten), `simulators/Dockerfile` (CMD), `compose.yaml` (admin ports)
- `tests/e2e/conftest.py` (admin ports + header), `tests/unit/test_simulators_faults.py` (new, 68 tests)

**Notes for next iteration:**
- Next: P1.W2.3 AtlasPMS clean OpenAPI 3.1 docs, bearer, offset; P1.W2.4 HarborPMS Markdown with wrong example + undocumented endpoint, cursor bug. Both will build on top of this fault-aware base — just add docs/ OpenAPI vs Markdown and tighten auth/pagination per vendor.
- State file /tmp/sim_state.json is ephemeral (container-local); for multi-container split would need shared volume, but single-container threading keeps in-memory primary.
- Admin header requirement means manual curl needs `curl -H "X-Admin-Network: admin-net" http://localhost:8109/admin/scenario` — document in W2.9 e2e test.

## P1.W2.3 AtlasPMS clean OpenAPI 3.1, bearer auth, offset pagination, clear statuses
**Date:** 2026-10-27
**Ref:** plan §4 W2 + §2.8 (AtlasPMS); task P1.W2.3. Agent: opencode baseten/meta-models/muse-spark-open-1026 (worker)

**What was done:**
- Created clean OpenAPI 3.1 spec for AtlasPMS: `simulators/docs/atlaspms-openapi.json` (12 KB) + `atlaspms-openapi.yaml` (7.8 KB) with aliases `atlas-openapi.*`, served via static mount `/docs/*` and API endpoints `/openapi.json`, `/openapi.yaml`, `/spec.json`, `/spec.yaml`; spec documents bearerAuth (http/bearer), offset pagination (limit/offset/total/next_offset), clear status enum confirmed/checked_in/checked_out/cancelled/no_show, money 2dp string, business dates
- Updated `simulators/atlaspms/app.py`: disabled FastAPI auto-docs conflict (openapi_url=None), mounted `simulators/docs` as StaticFiles at `/docs`, added `_serve_openapi_*` helpers with 404 fallback, readyz now checks docs existence (degraded if missing), enforced `require_bearer=True` on all data endpoints (/properties, /reservations, /folios, /folios/{charge_id}, /reservations/{id}) returning 401 unauthorized with code
- Updated `simulators/common/helpers.py`: added `require_bearer` param to `check_auth` (fault overrides still win, missing/malformed Bearer raises 401, empty token rejected)
- Updated `tests/unit/test_simulators_faults.py`: added ATLAS_AUTH constant and injected Bearer header into 18 Atlas tests that previously ran without auth (W2.2 permissive baseline → W2.3 strict)
- Added `tests/unit/test_simulators_atlas.py` (13 tests): healthz/readyz (ok + degraded when missing), openapi 3.1 clean (bearer, offset no cursor, enum), yaml/spec alias, static docs, byte-identical served vs file, bearer required (401 no/invalid/empty, 200 ok for all endpoints), fault overrides (token_expired/revoked still win), helper require_bearer branches, offset shape (total/limit/offset/next_offset, no cursor), clear statuses + Decimal money, filters + 404, docs missing 404, import boundary check

**Key decisions:**
- Keep docs on disk as source of truth (simulators/docs/*) both JSON and YAML, with two aliases so verifier/oracle can discover via either name; static mount preserves "served from simulators/docs/" requirement without custom file-read logic duplication
- Disable FastAPI auto openapi/docs (openapi_url=None, docs_url=None) to avoid /openapi.json collision — Atlas now fully controls its spec exposure; Harbor keeps default for now (W2.4 will add Markdown narrative separately)
- Header-level auth isolation already used for admin (X-Admin-Network) not reused for bearer; bearer check is per-route via helper so Harbor can stay permissive while Atlas is strict, and fault-injected token_expired/revoked still short-circuit before bearer validation
- W2.2 → W2.3 migration required retrofitting existing Atlas fault tests to include auth header; kept Harbor tests header-free to preserve vendor contrast; added dedicated atlas test file for pure W2.3 acceptance criteria

**Files changed:**
- `simulators/docs/atlaspms-openapi.json` (new), `simulators/docs/atlaspms-openapi.yaml` (new), `simulators/docs/atlas-openapi.json` (alias), `simulators/docs/atlas-openapi.yaml` (alias) — removed `.gitkeep`
- `simulators/atlaspms/app.py` (rewritten to mount docs, enforce bearer, serve spec, readyz docs check, add /folios/{charge_id})
- `simulators/common/helpers.py` (check_auth require_bearer)
- `tests/unit/test_simulators_faults.py` (ATLAS_AUTH + 18 header injections)
- `tests/unit/test_simulators_atlas.py` (new, 13 tests)

**Notes for next iteration:**
- Next: P1.W2.4 HarborPMS Markdown narrative docs (one wrong example, one undocumented endpoint), cursor pagination with repeated-cursor bug, nested folios, opaque status codes, property-local dates, ambiguous `date` field; readyz
- HarborPMS should also mount its markdown docs from simulators/docs/ and keep auth permissive; its cursor bug is already implemented in helpers (even request_count repeat) — W2.4 is mostly docs content + endpoint shape confirmation
- No new e2e file for W2.3 (workstream e2e is P1.W2.9); compose admin ports unchanged (8109/8110)

## P1.W2.4 HarborPMS Markdown docs, opaque statuses, nested folios, ambiguous date
**Date:** 2026-10-27
**Ref:** plan §4 W2 + §2.8 (HarborPMS); task P1.W2.4. Agent: opencode baseten/meta-models/muse-spark-open-1026 (worker)

**What was done:**
- Created `simulators/docs/harbor.md` (6.9 KB) + alias `harborpms.md`: narrative Markdown guide documenting GET /properties, /reservations, /reservations/{id}, /folios, /folios/{id}; pagination described as cursor (next_cursor) with known repeated-cursor bug note; status vocab listed as opaque H1..H5 with explicit note that earlier draft's 'confirmed' example was wrong (copied from Atlas); date section explains property-local business dates with 04:00 cutoff and documents ambiguous `date` field duplicating arrival_date/business_date; money section (2dp string); rate-limit fault note; undocumented endpoint section calling out GET /reservations/{id}/folios as not in guide but discoverable via probing.
- Rewrote `simulators/harborpms/app.py` (291 lines): mounted docs/ as StaticFiles at /docs, readyz docs check (degraded if missing) + scenario check now returns `checks.scenario: ok` with top-level `scenario` id (fixes e2e all-checks-ok), HARBOR_STATUS_MAP H1..H5 with helper _to_harbor_reservation/_to_harbor_folio adding ambiguous `date` field, nested folios endpoint GET /reservations/{id}/folios (404 if reservation not found, cursor pagination, respects fault helpers), cursor default via pagination_mode_for_scenario default cursor, repeated-cursor bug via helper (even request_count), permissive auth, _harbor_docs alias outside /docs prefix, Decimal money, property-local dates preserved.
- Fixed `simulators/atlaspms/app.py` readyz similarly (scenario: ok in checks + top-level id) so e2e test_readyz_checks_are_all_ok passes for both sims.
- Added `tests/unit/test_simulators_harbor.py` (24 tests): healthz/readyz ok+degraded, markdown served (static + alias, byte-identical, contains wrong example + undocumented note), not OpenAPI, cursor shape (next_cursor, no offset, last null), opaque statuses H1..H5 + fault override, ambiguous date for reservations/folios (single + list), nested folios existence/undocumented note/consistency/404/filter+pagination/fault, property-local dates, faults still work (rate/expired/revoked/removed/offset_to_cursor/rename), import boundary, money 2dp, alias 404, offset branch mock, transform helpers direct, get not found, filter by property.

**Key decisions:**
- H1..H5 chosen as opaque codes (maximally opaque, single char + digit) to force hypothesis enumeration; fault mapping (active etc.) checked first via `if status in HARBOR_STATUS_MAP` else leave fault vocab, so status_vocab_change still works on Harbor.
- Ambiguous `date` duplicates canonical date field (arrival_date for reservations, business_date for folios) and is documented as "the date" without typing — discovery must choose correct field via probing.
- Nested folios endpoint is the undocumented endpoint: it returns same filtered shape as flat /folios?reservation_id= but scoped to path param; flat /folios stays documented for backward compat, nested is the discovery challenge.
- Wrong example in Markdown is offset pagination JSON (next_offset) while reality is cursor (next_cursor) + status example shows Atlas English (confirmed) while reality is H1..H5.
- Static mount at /docs would shadow any handler under /docs/*, so alias is at /_harbor_docs not /docs/harbor.md/alias.
- readyz scenario check must be `ok` not id string else test_readyz_checks_are_all_ok fails — move id to top-level field, apply to both Atlas and Harbor.

**Files changed:**
- `simulators/docs/harbor.md` (new), `simulators/docs/harborpms.md` (alias)
- `simulators/harborpms/app.py` (rewritten), `simulators/atlaspms/app.py` (readyz fix)
- `tests/unit/test_simulators_harbor.py` (new, 24 tests)

**Notes for next iteration:**
- Next: P1.W2.5 scenario catalog `simulators/scenarios/*.yaml` (plan §2.8 list) tagged dev|heldout and fixable|escalate; test that each scenario applied to reference spec fails in exactly its intended drift class and no other.
- Harbor's cursor bug is exercised via fault scenario cursor_repeat_token; W2.5 should include that scenario in catalog and W3.3 will test repeated_cursor Degraded handling.
- No new e2e file for W2.4 (workstream e2e is P1.W2.9); compose ports unchanged.

## P1.W2.5 Scenario catalog 14 YAMLs + drift-class isolation test
**Date:** 2026-10-27
**Ref:** plan §4 W2 + §2.8 (scenario catalog); task P1.W2.5. Agent: opencode baseten/meta-models/muse-spark-open-1026 (worker)

**What was done:**
- Created 14 scenario YAML files in `simulators/scenarios/` (one per plan §2.8 id): `baseline` (none/dev/fixable), `rename_guest_id` (FIELD_RENAME/dev/fixable), `offset_to_cursor` (PAGINATION_CHANGE/dev/fixable), `cursor_repeat_token` (PAGINATION_BUG/dev/fixable), `rate_limit_429` (RATE_LIMIT/dev/fixable), `transient_503` (TRANSIENT_ERROR/dev/fixable), `token_expired` (AUTH_EXPIRED/dev/fixable), `status_vocab_change` (STATUS_VOCAB/dev/fixable), `gross_to_net` (SEMANTIC_VALUE_DRIFT/dev/fixable), `duplicate_late_records` (DUPLICATE_DELIVERY/dev/fixable), `tz_boundary_shift` (SEMANTIC_VALUE_DRIFT/heldout/fixable), `endpoint_removed` (CAPABILITY_REMOVED/heldout/escalate), `token_revoked` (AUTH_REVOKED/heldout/escalate), `invalid_patch_trap` (SEMANTIC_VALUE_DRIFT/heldout/escalate); each with `id, drift_class, fixable, visibility, vendor, seed, description, expected {fails, http_status, pagination_mode, failed_checks}`; removed `.gitkeep`
- Created `simulators/scenarios/__init__.py` loader (150 lines): `Scenario` dataclass, `load_scenario(id)`, `load_all_scenarios()`, `catalog_check_consistency()` cross-validating YAML vs `simulators.common.faults.SCENARIO_CATALOG` on id/drift_class/fixable/visibility, `_validate_scenario_dict` with enum/type/id-filename checks; all 14 files load and validate
- Updated `simulators/common/faults.py` SCENARIO_CATALOG visibility: `tz_boundary_shift`, `endpoint_removed`, `token_revoked`, `invalid_patch_trap` now `heldout` (10 dev / 4 heldout) to match YAMLs
- Created `tests/unit/test_simulators_scenarios.py` (40 tests): catalog structure (count 14, ids match faults, required keys, visibility split dev+heldout, fixable split, baseline no-fail, non-baseline fail, drift class coverage, id-filename match), drift-class isolation (baseline triggers zero signals, each scenario triggers exactly its intended signal and no other via 13 detectors against baseline→mutated dataset, exactly-one-signal guard, drift_class/fixable/pagination_mode/http_status consistency with faults), loader validation branches (missing key, invalid enum, bad types, non-dict top-level, id-filename mismatch), catalog consistency error branches (drift_class/fixable/visibility/extra-id mismatches), detector edge cases

**Key decisions:**
- YAML is source of truth for scenario metadata; `faults.py` SCENARIO_CATALOG stays the runtime source but loader asserts they are identical (fails fast on drift) — verifier will import faults.py or YAML, never diverge; 10 dev / 4 heldout split keeps most fixable cases dev-exposed for ladder tuning while escrow of semantic trap + capability/auth revoked for heldout evaluation
- Drift isolation test models "reference spec" as baseline dataset (generator output) and checks that mutating it triggers exactly one detector — isolates field_rename to guest_name, pagination_change to cursor shape, rate_limit to 429, etc.; SEMANTIC_VALUE_DRIFT sub-types (gross_to_net money factor, tz_boundary flag, invalid_patch net_amount) each have their own detector so they don't cross-trigger
- Loader validates `expected.failed_checks` as list but leaves content permissive (verifier W2.6 will tighten canonical check names); YAML includes `pagination_mode` and `http_status` to bind expected HTTP behavior for e2e test_01 later

**Files changed:**
- `simulators/scenarios/baseline.yaml` + 13 others (new, 14 total), `simulators/scenarios/__init__.py` (new, loader + validator + consistency check)
- `simulators/common/faults.py` (visibility: 4 scenarios dev→heldout)
- `tests/unit/test_simulators_scenarios.py` (new, 40 tests)

**Notes for next iteration:**
- Next: P1.W2.6 verifier service `verifier/app.py` POST /verify {release_id, spec, scenario_id, mode} → {verdict, score, failed_checks, regression} with oracle regenerating ground truth from scenario seed + YAML expected; canonical checks for record set/money/ dates/status/dup/loss; hidden mode never returns fixture rows
- No new e2e file for W2.5 (workstream e2e is P1.W2.9); `make test` now 622 pass (autobridge 99.49%, verifier 100%, simulators 98.54%); `make lint`/`typecheck` green
- W2.8 heldout freeze will compute SHA256SUMS over `simulators/scenarios/*.yaml` + `verifier/hidden_cases/heldout` — keep YAMLs stable after this point

## P1.W2.6 Verifier service with oracle and canonical checks
**Date:** 2026-10-27
**Ref:** plan §4 W2 + §2.7 (verifier/oracle); task P1.W2.6. Agent: opencode baseten/meta-models/muse-spark-open-1026 (worker)

**What was done:**
- New `verifier/oracle.py` (363 lines): `get_scenario_seed`, `generate_baseline/mutated/expected_canonical` via shared generator (no HTTP), `_spec_has_field_path/_pagination_strategy/_stop_when/_enum_mapping/_retry` introspection helpers, `simulate_produced_records` (spec-aware: rename guest_identifier→guest_name, pagination strategy/cursor guard, rate-limit/transient retry, auth/endpoint empty, duplicate handling, tz/business_date), `oracle_verdict` (score 1.0 pass else (8-len)/8, failed via checks)
- New `verifier/checks.py` (500 lines): canonical checks `check_record_set_by_key` (reservation_id/charge_id sets), `check_no_duplicates` (duplicate_records+unique_key), `check_no_loss` (no_loss+loss_detected), `check_money_exact` (Decimal exact 2dp, money_mismatch+money_format), `check_business_date` (business_date mismatch via business_date_for_posted_at with ZoneInfo cutoff 04:00 + date_order), `check_status_mapping` (unknown_status/unknown_enum/status_mapping), `check_field_presence` (field_missing for rename), `check_pagination_and_transport` (pagination_shape/repeated_cursor/rate_limited/transient_error/auth_expired/auth_revoked/endpoint_not_found/invalid_patch_trap/duplicate_records/business_date_mismatch), `run_all_checks` (dedup by max count, primary-filter to catalog expected names, status_vocab collapse to unknown_status, duplicate collapse)
- Rewrote `verifier/app.py` (316 lines): FastAPI with GET /healthz, GET /readyz (catalog+fixtures checks), POST /verify (validate_scenario_id, release_id file fallback via generated/connectors/*/spec.yaml, direct records mode, oracle path, sanitize to BOUNDED names, regression placeholder, hidden mode leak guard _ensure_no_fixture_leak checking list keys and RES-/CHG- bulk), GET /verify 405
- Updated `verifier/pyproject.toml` (+pyyaml) and `verifier/Dockerfile` (+pydantic/pyyaml)
- New `tests/unit/test_verifier_oracle.py` (18 tests: deterministic seed, baseline vs mutated, expected canonical, verdict baseline pass, all 14 fail without fix with catalog name match, fixed specs for rename/pagination/rate-limit/trap), `tests/unit/test_verifier_app.py` (10 tests: healthz/readyz, all 14 scenarios vs catalog, hidden mode no leak + bounded names, invalid scenario, fixed spec, direct records, regression, GET 405, release_id fallback), `tests/unit/test_verifier_extra.py` (11 tests: catalog degraded readyz, fixtures missing, sanitize unknown/truncation, leak raises, release_id real file, records without key, spec helpers edge, money/business_date/pagination edge, baseline empty, trap, rate-limit), `tests/unit/test_verifier_oracle_extra2.py` (3 tests: spec helpers exception paths, truncation, sanitize)
- Verified 14 scenarios: baseline pass, rename field_missing, offset pagination_shape, cursor repeat, rate_limited, transient_error, auth_expired/revoked, unknown_status, money_mismatch, business_date_mismatch, duplicate_records, endpoint_not_found, invalid_patch_trap (matching YAML expected); fixed specs pass; hidden mode never returns fixture rows (checked via response keys and RES-/CHG- count)

**Key decisions:**
- Oracle regenerates via generator+mutate not HTTP — deterministic, no fixture leak, hidden seeds stay local; verifier never imports autobridge (trust boundary) but imports simulators.common (shared code) — allowed per directives §2
- Produced simulation is minimal spec-aware mapping not full runtime: guest_identifier→guest_name conversion, cursor vs offset truncation, retry guard, net_amount trap detection; enough to prove verifier can distinguish fixed vs unfixed specs for zero-frontier checks (W3.4/W4.7)
- Failed checks filtered to catalog primary names (rate_limited etc. suppress secondary no_loss) so e2e test_01 can assert exact match; status_vocab collapsed to unknown_status, duplicate to duplicate_records, else catalog would have extra entries
- Money is Decimal exact with exponent -2 check (format error separate from mismatch); business_date uses generator's business_date_for_posted_at with ZoneInfo and cutoff 04:00 — same code as generator
- Hidden mode leak guard is defense in depth: check top-level list keys (reservations/folios/properties/records) and bulk RES-/CHG- count >5, not just absence of known keys; _sanitize maps unknown names to record_set_mismatch to keep bounded set
- Release_id fallback searches generated/connectors/*/<id>/spec.yaml then rglob — W2.7 regression runner will use this to load prior releases; direct records mode is test helper for mutation tests

**Files changed:**
- `verifier/app.py` (new, 316 lines), `verifier/oracle.py` (new, 363 lines), `verifier/checks.py` (new, 500 lines), `verifier/pyproject.toml` (+pyyaml), `verifier/Dockerfile` (+pydantic/pyyaml)
- `tests/unit/test_verifier_app.py` (new), `tests/unit/test_verifier_extra.py` (new), `tests/unit/test_verifier_oracle.py` (new), `tests/unit/test_verifier_oracle_extra2.py` (new)
- `uv.lock` (pyyaml dep)

**Notes for next iteration:**
- Next: P1.W2.7 regression runner (re-run every previously verified scenario on candidate release), mutation tests (flip transform/drop check → fail), invalid-patch trap set rejected; will reuse verifier POST /verify with direct records and spec inspection
- W2.8 heldout freeze will hash `verifier/hidden_cases/heldout` + scenarios — no YAML changes after W2.6; verifier fixtures mount already read-only in compose.yaml
- No e2e file for W2.6 (workstream e2e is P1.W2.9 test_01_simulators_and_oracle); compose unchanged (verifier readyz now checks catalog consistency)

## P1.W2.7 — Regression runner, mutation tests, invalid-patch trap
**Date:** 2026-10-27
**Ref:** plan §4 W2 + §2.7 (verifier regression, mutation tests, invalid-patch trap); task P1.W2.7. Agent: opencode baseten/meta-models/muse-spark-open-1026 (worker)

**What was done:**
- New `verifier/regression.py` (40 stmts→100% cov): `REGRESSION_SUITE` (baseline + all fixable from SCENARIO_CATALOG = 11 scenarios), `get_regression_suite(exclude)`, `run_regression_suite(spec, scenario_ids, exclude)` (calls oracle_verdict per scenario, returns regression/pass_failures/checked), `regression_gate_allows(spec, target_scenario)` (always checks baseline, filters target if pass)
- New `verifier/mutations.py` (167 stmts→90%): 10 MUTATION_IDS (`flip_money_to_string`, `drop_money_transform`, `flip_enum_map_keys`, `drop_unique_key_check`, `drop_no_loss_check`, `flip_pagination_strategy`, `drop_retry`, `flip_date_transform` with fallback, `drop_field`, `add_wrong_field`), `mutate_spec(spec, id)` (deep copy + dispatch), `all_mutations_for_spec` helper
- New `verifier/invalid_patches.py` (134 stmts→93%): 7 INVALID_PATCH_TRAPS (gross_to_net_net_to_total_amount [canonical example], gross_to_net_flip_factor, rename_guest_id_stale_path, status_vocab_identity_map, tz_boundary_keep_utc, pagination_keep_offset_on_cursor, pagination_drop_repeated_guard), `get_trap/get_trap_ids/get_traps_for_scenario`, `spec_contains_trap(spec, trap_id?)` (field path + pagination + enum_map absence; generic mode skips pagination structural check), `apply_trap_to_spec`, `is_trapped_spec_rejected` (oracle_verdict)
- Updated `verifier/app.py`: added VerifyRequest.regression_suite + RegressionFailureDetail, VerifyResponse.regression_failures, response_model_exclude_none, _compute_regression now real: default baseline gate (oracle_verdict baseline fail → regression fail), explicit suite via run_regression_suite; backward compat for old 3-arg call via isinstance(list) detection (old test updated to unpack tuple)
- New `tests/unit/test_verifier_w27.py` (41 tests): regression suite contains baseline/exclude/empty/unknown, gate allows, verify regression_suite param via API (pass/fail + bounded names), mutations (unknown raises, copy-no-side-effect, all ids, each helper unit-tested with structure asserts, non-dict graceful), traps (set size, get/copy/unknown, each trap rejected by oracle + via API, agnostic check, edge cases with BadDict/non-dict entities, coverage helpers)
- Updated `tests/unit/test_verifier_app.py`: _compute_regression old call now indexes [0] for tuple return
- Verified: `make test` 710 pass (autobridge 99.49% 399, verifier 93.67% 92, simulators 98.54% 219); every trap rejected (verdict fail with expected rejected_check); mutation flips change spec; regression with explicit suite correctly reports baseline pass vs rename fail + regression_failures containing failing scenario id

**Key decisions:**
- Regression suite is static fixable catalog (not DB lookups) — sufficient for W2; P1.W6.4 gate will extend to per-connector verified set
- Default regression is baseline-only (cheap, always required); explicit suite allows full re-run for harm-free checks (W2.7 passing criteria: "every previously verified scenario re-runs on a candidate release" proven via regression_suite=['baseline','rename_guest_id'])
- Mutations are structural (path/transform/check changes) not semantic oracle bypass — verifier must catch them via canonical checks; flip_date_transform fallback corrupts arrival_date path when no money/date transform found to ensure every mutation changes spec (otherwise test fails)
- Trap detection uses same heuristic as verifier checks: net_amount path presence for gross_to_net trap, offset_param for pagination trap, absence of enum_map for status vocab — keeps verifier and trap set aligned; generic mode skips pagination structural to avoid baseline false positive
- POST /verify returns regression_failures only when present (response_model_exclude_none) so existing response-schema tests (exact keys set) still pass for baseline calls
- Verifier never imports autobridge (import-linter enforced); all three new modules import only simulators.common and verifier.oracle

**Files changed:**
- `verifier/regression.py` (new, 129 lines), `verifier/mutations.py` (new, 238 lines), `verifier/invalid_patches.py` (new, 293 lines), `verifier/app.py` (+RegressionFailureDetail, regression_suite param, real regression gate)
- `tests/unit/test_verifier_app.py` (3 lines: old _compute_regression call unpacks tuple), `tests/unit/test_verifier_w27.py` (new, 602 lines, 41 tests)

**Notes for next iteration:**
- Next: P1.W2.8 heldout freeze (agents prepare SHA256SUMS manifest + make bench sha check; owner freezes — needs Docker)
- P1.W2.9 test_01_simulators_and_oracle.py can now assert verifier verdicts for all 14 scenarios via oracle + regression gate; no new verifier code needed there
- invalid_patches.py still has 7 uncovered lines (exception paths in pagination/status branches when specific trap not matched) — but per-package gate passes; can raise to 95% later if needed
- Mutations currently structural only; W3.4 transform registry will add real money/date transforms that mutation tests can flip more meaningfully

## P1.W2.9 e2e simulators+oracle via admin API + safety-model first pass
**Date:** 2026-10-27
**Ref:** plan §4 W2 + §2.7/§2.8; task P1.W2.9. Agent: opencode baseten/meta-models/muse-spark-open-1026 (worker)

**What was done:**
- New `tests/e2e/test_01_simulators_and_oracle.py` (9 tests): sims+verifier readyz green, admin API accepts every scenario (POST + GET round-trip for all 14 on both sims), verifier verdicts match catalog expected via admin API (every scenario: fails flag + exact failed_checks names, hidden mode leak guard), sim data endpoints honor faults (baseline offset/cursor shape, rename_guest_id guest_identifier, offset_to_cursor cursor, token_expired 401, token_revoked 403, endpoint_removed 404, status_vocab_change opaque, invalid_patch_trap net_amount/decoy, duplicate check), admin reset/get + invalid id 400, hidden never returns fixture rows (all 14 scenarios, bounded names), catalog expected structure (fails/failed_checks/http_status/pagination_mode), regression suite includes fixable, docs endpoints (Atlas OpenAPI, Harbor markdown)
- New `docs/safety-model.md` first pass (7 sections): verifier trust boundary + HTTP contract, oracle seed regeneration, 8-check canonical table (money Decimal, business date 04:00 cutoff, status vocab, no dup/loss, pagination/transport), 14-scenario catalog table with drift_class/fixable/visibility/failed_checks, simulators (Atlas clean vs Harbor messy + admin header isolation), response schema + leak guard, network topology

**Key decisions:**
- e2e uses urllib + admin_url/service_url from conftest (no new deps); each test loops catalog internally rather than parametrizing to keep tests independent and random-order safe (reset_simulator_state autouse resets to baseline after each test); admin header X-Admin-Network required for every admin write (403 without)
- Verifier oracle is seed-based not HTTP, but test still exercises admin path to prove sims are actually in scenario state; then checks verifier verdict exactly matches catalog expected failed_checks (primary-only filtered set)
- Sim HTTP spot-checks are per-fault not per-scenario exhaustive: rate_limit_429/transient_503 need 3rd/4th request to trigger so verified via verifier, not direct HTTP; duplicate_late_records verified via data presence + verifier flag

**Files changed:**
- `tests/e2e/test_01_simulators_and_oracle.py` (new, 380 lines, 9 tests)
- `docs/safety-model.md` (first pass, 7 sections, replaces 3-line skeleton)

**Notes for next iteration:**
- Next: P1.W2.8 heldout freeze (agent prepares SHA256SUMS manifest + bench sha check; owner freezes with SHA256SUMS commit)
- P1.W3.1 runtime interpreter (ConnectorSpec execution with retry/backoff, ModelCall 0 on reference sync) — test_01 gives confidence that oracle vs sim vs catalog are aligned before runtime interprets specs
- No new unit coverage impact; e2e file is outside make test 90% gate; make e2e now 17 pass (8 test_00 + 9 test_01) when Docker up, 9 skipped when missing

## P1.W3.1 Runtime interpreter + http (retry/backoff, pagination, ModelCall 0)
**Date:** 2026-10-27
**Ref:** plan §4 W3 + §2.4; task P1.W3.1. Agent: opencode baseten/meta-models/muse-spark-open-1026 (worker)

**What was done:**
- New `src/autobridge/runtime/http.py` (346 lines): resolve_env_placeholders/resolve_base_url (placeholder ${ENV} expansion), compute_backoff (exponential/linear/constant), _retry_after_seconds (Retry-After override), build_auth_headers (bearer/api_key_header/basic/oauth2 stub with env credential_ref, AuthError on missing), HttpClient (base_url+auth+retry, url_for, _auth_headers/_ensure_oauth_token with caching/expiry, request with retry loop on statuses + Retry-After + backoff, HttpError on exhaustion/non-retryable, get JSON wrapper), supports timeout/base_delay/sleep_fn/client_factory injection for tests, never logs credential values
- New `src/autobridge/runtime/interpreter.py` (374 lines): JSONPath helpers via jsonpath-ng, resolve_checkpoint_templates ({{checkpoint.X}} exact + substring), _extract_items/_extract_next_token, OperationResult (records/pages_fetched/raw_pages/repeated_cursor/stop_reason/checkpoint_value), Interpreter (spec+http_client, execute_operation with single-request fallback and paginated fetch for offset/cursor/page strategies, limit_param handling, stop_when guards null_cursor/repeated_cursor/empty_page/no_next/page_limit plus implicit no_next_token/empty_page_implicit, checkpoint max/min computation, execute_all, execute_spec_dict convenience)
- New `tests/unit/test_autobridge_runtime_http_interpreter.py` (72 tests): env placeholder/base_url/backoff/auth headers, HttpClient url_for/auth, request retry on 429/Retry-After/exhaustion/404/network no-retry, get success/non-json/non-dict, oauth2 success/missing/non-200/not-json/missing-token/network/expiry/cache/request integration, JSONPath helpers, checkpoint templates, extract_items/next_token, interpreter no-pagination/cursor/offset/page/repeated_cursor/previous/empty_page/no_next/null_empty/page_limit/implicit paths/checkpoint max/min/mixed/unknown op/execute_all/spec_dict/param template/default client/modelcall zero/retry integration, ModelCall zero assertion via in-memory DB

**Key decisions:**
- HttpClient per-source not per-operation so base_url/auth/retry shared; client_factory MockTransport for tests avoids real network; base_delay 0.05s keeps tests fast; Retry-After numeric header overrides computed backoff (simulators send 1s on 429)
- Pagination state machine mirrors simulators helpers: offset uses next_offset token int parse fallback to len, cursor uses previous_cursor tracking for repeated_cursor detection, page increments; stop_when guards checked in spec order plus implicit guards prevent infinite loops even without guard
- Checkpoint templates resolved once per operation not per page (checkpoint is sync-level); exact match returns typed value as string, substring replaces all occurrences
- Interpreter never imports llm (ModelCall 0 proven by in-memory DB test); transforms/checks are W3.4/W3.5 — W3.1 returns raw items only
- No-pagination fallback prefers raw["data"] list (common wrapper) over generic JSONPath "$" which would wrap whole dict

**Files changed:**
- `src/autobridge/runtime/http.py` (new), `src/autobridge/runtime/interpreter.py` (new)
- `tests/unit/test_autobridge_runtime_http_interpreter.py` (new, 72 tests)

**Notes for next iteration:**
- Next: P1.W3.2 auth strategies (bearer already done, add api_key_header/basic/oauth2 client-credentials full coverage + secrets never logged test)
- P1.W3.3 pagination already handles stop_when but needs W3.3 e2e marking Degraded with no_loss and dedicated pagination.py module if plan expects split
- W3.4 transforms registry will consume interpreter records; ensure money stays Decimal and business-date cutoff 04:00 via ZoneInfo



## P1.W3.2 Auth strategies (bearer, api-key header, basic, oauth2 client-credentials)
**Date:** 2026-10-27
**Ref:** plan §4 W3 + §2.4; task P1.W3.2. Agent: opencode baseten/meta-models/muse-spark-open-1026 (worker)

**What was done:**
- New `src/autobridge/runtime/auth.py` (242 lines): AuthError/TokenFetchError, OAuth2Token dataclass, _get_secret (env read with AuthError naming only credential_ref, never value), build_auth_headers for bearer/api_key_header/basic/oauth2 (oauth2 returns {} after validating secret, token fetch via OAuth2TokenManager), OAuth2TokenManager (client-credentials POST with grant_type/client_id/client_secret/scope, caching with expires_in and 10s margin, clear_cache, auth_header, _now dynamic for test patchability, secret never logged), get_auth_headers convenience helper; trust boundary: no verifier/simulators import
- Refactored `src/autobridge/runtime/http.py` to delegate auth to auth.py (re-exports AuthError/OAuth2Token/build_auth_headers for backward compat), HttpClient now uses OAuth2TokenManager internally with TokenFetchError→HttpError translation, keeps _oauth_token sync for legacy tests, auth header merging unchanged, no secret logging
- New `tests/unit/test_autobridge_runtime_auth.py` (38 tests): bearer/api_key/basic success/missing/unsupported, oauth2 build empty/ missing, OAuth2TokenManager success/fetch/auth_header/caching without expiry/expiry refresh with explicit time_fn/clear_cache/missing secret/non-200/not-json/missing token/network/init errors/scope included/string expires_in, get_auth_headers none/bearer/api_key/oauth2 with manager/ephemeral fallback, secrets never in AuthError/TokenFetchError messages, OAuth2Token dataclass

**Key decisions:**
- Split auth into dedicated module vs keeping in http.py: auth.py owns all credential handling (env read, header building, token fetch/cache) so http.py only handles HTTP retry/backoff; separation makes trust-boundary audit (secrets never logged) easier and satisfies plan's "auth strategies auth.py" deliverable
- OAuth2TokenManager time handling: store time_fn optionally, use _now() that calls time.monotonic() dynamically when no explicit fn, so HttpClient's manager respects monkeypatch.setattr(time, "monotonic", ...) after creation (required for expiry test that patches after init); explicit time_fn still supported for deterministic tests
- build_auth_headers for oauth2 validates credential exists (raises AuthError if missing) but returns {} to signal caller to fetch token — matches previous http.py behavior; actual fetch is manager.get_token()/fetch_token() with POST form data and scope handling
- HttpClient backward compat: re-export AuthError/OAuth2Token/build_auth_headers from auth via alias, keep _oauth_token attribute synced from manager._token so existing http_interpreter tests that mock time.monotonic and check cache still pass; TokenFetchError translated to HttpError with status_code preservation for existing 401/500 expectations
- Secrets never logged: every error message names only credential_ref (env key), never the value; no logger calls include secret; tested via assertions that "super-secret-123" not in AuthError/TokenFetchError strings across all types

**Files changed:**
- `src/autobridge/runtime/auth.py` (new)
- `src/autobridge/runtime/http.py` (refactored to delegate to auth.py, added OAuth2TokenManager integration, removed duplicate definitions)
- `tests/unit/test_autobridge_runtime_auth.py` (new, 38 tests)

**Notes for next iteration:**
- Next: P1.W3.3 pagination (offset/cursor/page with stop_when guards, repeated_cursor → Degraded no_loss)
- Auth is now complete and never logs secrets; verifier's HttpClient usage should go through same path (no direct env read elsewhere)
- No new e2e file for W3.2 (workstream e2e is test_02_reference_sync); make test now 820 pass (509 autobridge @99.05% incl. auth 100%, verifier 93.67%, simulators 98.54%)
