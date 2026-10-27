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
