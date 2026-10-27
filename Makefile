# Coverage report style; the ralph loop sets term-missing:skip-covered for OpenCode only.
COV_REPORT ?= term-missing

.PHONY: up down test smoke e2e e2e-ui bench bench-heldout demo lint typecheck lint-fix openapi openapi-check ui-typecheck

# ---- Docker ----
up:
	@if [ ! -f compose.yaml ]; then echo "compose.yaml not yet created (W1.2) — skipping"; else docker compose up -d --build --wait && ./scripts/wait_readyz.sh; fi

down:
	@if [ -f compose.yaml ]; then docker compose down -v; else echo "no compose.yaml — nothing to do"; fi

# ---- Tests ----
test:
	@echo ">> test: autobridge package (90% gate)"
	uv run --extra dev pytest tests/unit/test_autobridge_*.py tests/unit/test_health_*.py --cov=autobridge --cov-report=$(COV_REPORT) --cov-fail-under=90 -q
	@echo ">> test: verifier package (90% gate)"
	uv run --extra dev pytest tests/unit/test_verifier_*.py --cov=verifier --cov-report=$(COV_REPORT) --cov-fail-under=90 -q
	@echo ">> test: simulators package (90% gate)"
	uv run --extra dev pytest tests/unit/test_simulators_*.py --cov=simulators --cov-report=$(COV_REPORT) --cov-fail-under=90 -q
	@echo ">> test: UI component tests (Vitest)"
	@if [ -d ui/node_modules ]; then cd ui && npm run test; else echo "UI tests skipped: ui/node_modules missing (run: cd ui && npm ci) — CI runs them unconditionally"; fi
	@echo ">> test: all green"

smoke:
	@echo ">> smoke: unit health tests"
	uv run --extra dev pytest tests/unit/test_*smoke*.py tests/unit/test_*health*.py -q
	@if [ -f compose.yaml ]; then ./scripts/smoke.sh; else echo "no compose.yaml — skipping stack smoke"; fi
	@echo "smoke OK"

# Env: E2E_SKIP_STACK=1 (reuse the `make up` stack) · E2E_KEEP_STACK=1 (no
# teardown) · E2E_TIMEOUT=180 (readyz wait seconds).
# `-p randomly` enforces the W9 random order (fails fast if pytest-randomly
# is missing instead of silently running ordered). Suite budget 300s warm,
# excluding the one-off image build (SUITE_RUNTIME_BUDGET_SECONDS in
# tests/e2e/conftest.py). The Playwright UI journey (test_06, .spec.ts) is
# not collected by pytest by design — run `make e2e-ui` for it; CI runs both.
e2e:
	@if [ ! -f compose.yaml ]; then echo "no compose.yaml — skipping e2e (W1.2 creates it)"; else uv run --extra dev pytest tests/e2e -q --tb=short -p randomly; fi

# UI journey (plan §4 W7 service confirmation): Playwright spec against the
# Compose stack (`make up` first, UI on :8107). Green with mocks when the
# stack is down, so it never blocks `make e2e`.
e2e-ui:
	cd ui && npm run test:e2e

# Env: BENCH_SCENARIOS (csv) · BENCH_SEED · BENCH_TRIALS · BENCH_VISIBILITY
# (dev|heldout|all) · BENCH_NO_SCORE=1 (harness JSONL only). The harness
# itself runs the heldout SHA gate first: no-op until the owner freezes
# heldout (P1.W2.8), hard fail (exit 2) on mismatch.
bench:
	@echo ">> bench: heldout sha check + matched-budget run + scorecard (W8.5)"
	uv run python -m autobridge.bench.harness

bench-heldout:
	@echo ">> bench (heldout split — frozen SHA256SUMS required, Nov 3 run)"
	BENCH_VISIBILITY=heldout uv run python -m autobridge.bench.harness

demo:
	@echo ">> demo: six steps via control API (W10)"
	uv run python -m autobridge.bench.demo

# ---- Quality ----
lint:
	uv run --extra dev ruff check .
	uv run --extra dev lint-imports

lint-fix:
	uv run --extra dev ruff check --fix .

typecheck:
	uv run --extra dev mypy src/autobridge

# ---- OpenAPI + UI ----
openapi:
	uv run python scripts/export_openapi.py
	cd ui && npm run gen

openapi-check:
	uv run python scripts/export_openapi.py --check
	cd ui && npm run gen && git diff --exit-code -- lib/api/schema.d.ts openapi.json

ui-typecheck:
	cd ui && npm run typecheck

# ---- Import linter ----
lint-imports:
	uv run --extra dev lint-imports
