# Coverage report style; the ralph loop sets term-missing:skip-covered for OpenCode only.
COV_REPORT ?= term-missing

.PHONY: up down local-up local-down test smoke e2e e2e-ui bench bench-heldout verify-heldout-reveal demo lint typecheck lint-fix openapi openapi-check ui-typecheck ui-deps ui-e2e-deps lockfiles edge-sign edge-check eval-freeze bench-beacon verify-beacon beacon-eval

# ---- Docker ----
up: edge-check
	@if [ ! -f compose.yaml ]; then echo "compose.yaml not yet created (W1.2) — skipping"; else docker compose up -d --build --wait && ./scripts/wait_readyz.sh; fi

# REQ-022 owner check B1: mint + pin the edge bundle signature into .env.
# Required once EDGE_RELEASE_SECRET is set; harmless otherwise.
edge-sign:
	./scripts/edge-sign.sh

# REQ-022 owner check B1: fail fast when a keyed deployment lacks the pin.
edge-check:
	./scripts/check-edge-key.sh

# ---- Docker-free local mode (REQ-017) ----
# Same host ports as compose, SQLite in ./.local/, logs in ./.local/logs/.
# Works on macOS and Linux with Python 3.12 + uv and Node 22/npm (UI);
# on Windows use WSL2 (see README "No Docker").
local-up:
	./scripts/local-up.sh

local-down:
	./scripts/local-down.sh

# AR-2-5: regenerate the hash-pinned requirements locks the images install
# with `pip install --require-hashes`. Run after changing any pyproject.toml;
# CI fails if the committed locks drift from `uv export` output.
lockfiles:
	uv export --frozen --format requirements.txt --package autobridge --no-emit-project -o requirements.engine.lock
	uv export --frozen --format requirements.txt --package verifier --no-emit-project -o verifier/requirements.lock
	uv export --frozen --format requirements.txt --package simulators --no-emit-project -o simulators/requirements.lock

down:
	@if [ -f compose.yaml ]; then docker compose down -v; else echo "no compose.yaml — nothing to do"; fi

# ---- Tests ----
# Fresh-clone install for the UI (needs Node 22 + npm, see README quickstart).
# No-op when ui/node_modules already exists. `test` and `e2e-ui` depend on
# this, so a clean clone works without a manual `cd ui && npm ci`.
ui-deps:
	@if [ -d ui/node_modules ]; then echo "ui deps present (ui/node_modules)"; else \
	  command -v npm >/dev/null || (echo "ui-deps: npm not found — install Node 22 + npm first (see README quickstart)" >&2; exit 1); \
	  cd ui && npm ci; fi

test: ui-deps
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
	. ./scripts/load_dotenv.sh && uv run --extra dev pytest tests/unit/test_*smoke*.py tests/unit/test_*health*.py -q
	@if [ -f compose.yaml ]; then . ./scripts/load_dotenv.sh && ./scripts/smoke.sh; else echo "no compose.yaml — skipping stack smoke"; fi
	@echo "smoke OK"

# Env: E2E_SKIP_STACK=1 (reuse the `make up` stack) · E2E_KEEP_STACK=1 (no
# teardown) · E2E_TIMEOUT=180 (readyz wait seconds).
# `-p randomly` enforces the W9 random order (fails fast if pytest-randomly
# is missing instead of silently running ordered). Suite budget 300s warm,
# excluding the one-off image build (SUITE_RUNTIME_BUDGET_SECONDS in
# tests/e2e/conftest.py). The Playwright UI journey (test_06, .spec.ts) is
# not collected by pytest by design — run `make e2e-ui` for it; CI runs both.
# AUTOBRIDGE_E2E_REQUIRE_STACK=1 (REQ-010): live-marked tests fail instead
# of skipping when the stack is unreachable, so CI never passes vacuously.
e2e:
	@if [ ! -f compose.yaml ]; then echo "no compose.yaml — skipping e2e (W1.2 creates it)"; else . ./scripts/load_dotenv.sh && AUTOBRIDGE_E2E_REQUIRE_STACK=1 uv run --extra dev pytest tests/e2e -q --tb=short -p randomly; fi

# Playwright browsers for the UI journey (needs ui-deps first). `npx
# playwright install` is a no-op when the browsers are already installed.
ui-e2e-deps: ui-deps
	cd ui && npx playwright install chromium

# UI journey (plan §4 W7 service confirmation): Playwright spec against the
# Compose stack (`make up` first, UI on :8107). Green with mocks when the
# stack is down, so it never blocks `make e2e`. NODE_PATH is required: the
# root playwright.config.ts and the specs in tests/e2e import
# `@playwright/test`, which lives in ui/node_modules (no root node_modules).
e2e-ui: ui-e2e-deps
	cd ui && NODE_PATH="$$PWD/node_modules" npm run test:e2e

# Env: BENCH_SCENARIOS (csv) · BENCH_SEED · BENCH_TRIALS (bench defaults 5
# for publishable 95% intervals, REQ-024 B3) · BENCH_VISIBILITY (dev|heldout|all) ·
# BENCH_NO_SCORE=1 (harness JSONL only) · BENCH_ARMS (matched|ablation).
# Dev runs skip the heldout gate; heldout/all runs verify HELDOUT_DIR against
# the committed manifest first (exit 2 on missing env, missing file, mismatch).
# Host targets load repo .env first (REQ-014: scripts/load_dotenv.sh exports
# only non-empty KEY=value lines; explicit environment always wins).
bench:
	@echo ">> bench: matched-budget run + scorecard (W8.5), then all ablation arms (REQ-024 B)"
	. ./scripts/load_dotenv.sh && uv run python -m autobridge.bench.harness --control-api-url http://localhost:8101 --verifier-url http://localhost:8104 && \
	. ./scripts/load_dotenv.sh && BENCH_TRIALS=$${BENCH_TRIALS:-5} uv run python -m autobridge.bench.harness --arms ablation --control-api-url http://localhost:8101 --verifier-url http://localhost:8104 && \
	. ./scripts/load_dotenv.sh && uv run python -m autobridge.bench.ablation_curve

# REQ-024 B6 rehearsal without a stack: engine arms run in-process and grading
# degrades to unverified when the verifier is down (labels say so).
bench-ablation:
	@echo ">> bench-ablation: all arms on the dev split + learning curve (REQ-024 B)"
	. ./scripts/load_dotenv.sh && BENCH_TRIALS=$${BENCH_TRIALS:-5} AUTOBRIDGE_BENCH_SKIP_PROBE=1 uv run python -m autobridge.bench.harness --arms ablation --control-api-url http://localhost:8101 --verifier-url http://localhost:8104 && \
	. ./scripts/load_dotenv.sh && uv run python -m autobridge.bench.ablation_curve

bench-heldout:
	@echo ">> bench (heldout split — private seeds from HELDOUT_DIR, Nov 3 run)"
	. ./scripts/load_dotenv.sh; if [ -z "$${HELDOUT_DIR:-}" ]; then echo "heldout SHA check failed: HELDOUT_DIR not set" >&2; exit 2; fi; \
	BENCH_VISIBILITY=heldout uv run python -m autobridge.bench.harness --control-api-url http://localhost:8101 --verifier-url http://localhost:8104

# REQ-024 A4: after the single Nov 3 run the owner publishes the seed files
# under verifier/hidden_cases/heldout/revealed/ (an owner commit). Anyone can
# then check them against the frozen SHA256SUMS and reproduce the scorecard
# from them. Refuses when the reveal has not happened yet (exit 2).
verify-heldout-reveal:
	@echo ">> verify heldout reveal against frozen SHA256SUMS, then rerun bench-heldout from it"
	. ./scripts/load_dotenv.sh; if [ ! -d verifier/hidden_cases/heldout/revealed ]; then echo "no reveal yet: verifier/hidden_cases/heldout/revealed/ missing (owner publishes after the Nov 3 run)" >&2; exit 2; fi; \
	HELDOUT_DIR=verifier/hidden_cases/heldout/revealed uv run python -c "from autobridge.bench.harness import check_heldout_sha; check_heldout_sha()" && \
	HELDOUT_DIR=verifier/hidden_cases/heldout/revealed BENCH_VISIBILITY=heldout uv run python -m autobridge.bench.harness --control-api-url http://localhost:8101 --verifier-url http://localhost:8104

demo:
	@echo ">> demo: eight steps via control API (W10)"
	. ./scripts/load_dotenv.sh && uv run python -m autobridge.bench.demo

demo-reset:
	@echo ">> demo-reset: full reset to a known first-run state (REQ-021 item 1)"
	. ./scripts/load_dotenv.sh && uv run python -m autobridge.bench.demo_reset

record-frontier:
	@echo ">> record-frontier: capture a live frontier climb (REQ-021 item 5; needs FRONTIER_API_KEY)"
	@if [ -z "$${SCENARIO:-}" ]; then echo "record-frontier: SCENARIO is required (make record-frontier SCENARIO=gross_to_net)" >&2; exit 2; fi
	. ./scripts/load_dotenv.sh && uv run python -m autobridge.bench.record_frontier "$${SCENARIO}"

# REQ-025 beacon evaluation (items 4-7). Freeze commits experiments/beacon/
# protocol.md before the target round exists (commit-once; dirty tree
# refuses); the run fetches + verifies the round (fail closed), pushes each
# instance to its vendor sim, runs all REQ-024 arms under matched budget and
# grades with the verifier. REHEARSE=<published round> rehearses the whole
# pipeline (labelled, under experiments/beacon/rehearsals/, never committed
# as a result). The one-shot A-Z command is `make beacon-eval`.
eval-freeze:
	@echo ">> eval-freeze: freeze the beacon protocol (REQ-025 item 4)"
	@if [ -z "$${ROUND_AFTER:-}" ]; then echo "eval-freeze: ROUND_AFTER is required (ISO UTC, e.g. make eval-freeze ROUND_AFTER=2026-11-05T12:00:00+00:00 FAULTS=25)" >&2; exit 2; fi
	@if [ -z "$${FAULTS:-}" ]; then echo "eval-freeze: FAULTS is required" >&2; exit 2; fi
	. ./scripts/load_dotenv.sh && uv run python -m autobridge.bench.beacon_run freeze --round-after "$${ROUND_AFTER}" --faults "$${FAULTS}" --trials "$${TRIALS:-5}"

bench-beacon:
	@echo ">> bench-beacon: run the frozen protocol (REQ-025 item 5)"
	. ./scripts/load_dotenv.sh && \
	if [ -n "$${REHEARSE:-}" ]; then \
		uv run python -m autobridge.bench.beacon_run rehearse --round "$${REHEARSE}" --faults "$${FAULTS:-10}" --trials "$${TRIALS:-1}" --verifier-url http://localhost:8104; \
	else \
		uv run python -m autobridge.bench.beacon_run run --verifier-url http://localhost:8104; \
	fi

verify-beacon:
	@echo ">> verify-beacon: reproduce + check published instances (REQ-025 item 7)"
	@if [ -z "$${ROUND:-}" ]; then echo "verify-beacon: ROUND is required (make verify-beacon ROUND=<round>)" >&2; exit 2; fi
	. ./scripts/load_dotenv.sh && uv run python -m autobridge.bench.beacon_run verify-repro --round "$${ROUND}" $${OUTDIR:+--output-dir "$${OUTDIR}"}

beacon-eval:
	@echo ">> beacon-eval: one unattended freeze + run (REQ-025 item 6b)"
	./scripts/beacon-eval.sh

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
