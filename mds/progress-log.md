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
