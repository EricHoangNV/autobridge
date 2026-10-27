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
