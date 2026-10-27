.PHONY: up down test smoke e2e bench demo lint typecheck lint-fix

# ---- Docker ----
up:
	@if [ ! -f compose.yaml ]; then echo "compose.yaml not yet created (W1.2) — skipping"; else docker compose up -d --wait; fi

down:
	@if [ -f compose.yaml ]; then docker compose down -v; else echo "no compose.yaml — nothing to do"; fi

# ---- Tests ----
test:
	@echo ">> test: autobridge package (90% gate)"
	uv run pytest tests/unit/test_autobridge_*.py tests/unit/test_health_*.py --cov=autobridge --cov-report=term-missing --cov-fail-under=90 -q
	@echo ">> test: verifier package (90% gate)"
	uv run pytest tests/unit/test_verifier_*.py --cov=verifier --cov-report=term-missing --cov-fail-under=90 -q
	@echo ">> test: simulators package (90% gate)"
	uv run pytest tests/unit/test_simulators_*.py --cov=simulators --cov-report=term-missing --cov-fail-under=90 -q
	@echo ">> test: all green"

smoke:
	@echo ">> smoke: healthz/readyz checks"
	@if [ -f compose.yaml ]; then \
		echo "compose stack smoke (W1.2 will add negative checks)"; \
		for svc in control verifier sim-atlas sim-harbor; do echo "  check $$svc/healthz"; done; \
	fi
	uv run pytest tests/unit/test_*smoke*.py tests/unit/test_*health*.py -q
	@echo "smoke OK"

e2e:
	@if [ ! -f compose.yaml ]; then echo "no compose.yaml — skipping e2e (W1.2 creates it)"; else uv run pytest tests/e2e -q --tb=short; fi

bench:
	@if [ ! -f verifier/hidden_cases/heldout/SHA256SUMS ]; then echo "heldout not frozen — skipping"; else uv run python -m autobridge.bench.harness; fi

demo:
	@echo ">> demo: six steps via control API (W10)"
	uv run python -m autobridge.bench.demo 2>&1 || echo "demo not yet implemented (W10)"

# ---- Quality ----
lint:
	uv run ruff check .

lint-fix:
	uv run ruff check --fix .

typecheck:
	uv run mypy src/autobridge

# ---- Import linter ----
lint-imports:
	uv run lint-imports
