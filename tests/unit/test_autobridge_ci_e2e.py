"""CI + `make e2e` contract tests (P1.W9.2, plan §4 W9).

Parses `.github/workflows/ci.yml` statically (no Docker, no network) and
asserts the pipeline order from a clean clone: unit (coverage gate) →
smoke → e2e, with the Compose stack brought up once and always torn down.
Also asserts the `make e2e` target runs the whole pytest suite in random
order (`-p randomly`, failing fast instead of silently running ordered).
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_FILE = ROOT / ".github" / "workflows" / "ci.yml"
MAKEFILE = ROOT / "Makefile"


def _workflow() -> dict:
    assert WORKFLOW_FILE.is_file(), "missing .github/workflows/ci.yml (P1.W9.2)"
    data = yaml.safe_load(WORKFLOW_FILE.read_text())
    assert isinstance(data, dict)
    return data


def _steps(data: dict) -> list[dict]:
    jobs = data.get("jobs", {})
    assert jobs, "ci.yml defines no jobs"
    assert len(jobs) == 1, f"ci.yml must be one ordered job, found: {sorted(jobs)}"
    steps = next(iter(jobs.values())).get("steps", [])
    assert steps, "ci job defines no steps"
    return steps


def test_workflow_triggers_on_push_and_pr() -> None:
    data = _workflow()
    on = data.get("on", True)  # `on` parses as bool True in YAML 1.1
    assert on is True or on, "ci.yml must define an `on:` trigger block"
    triggers = data.get(True, data.get("on", {}))
    assert "push" in triggers, "CI must run on push"
    assert "pull_request" in triggers, "CI must run on pull_request"


def test_pipeline_starts_from_a_clean_clone() -> None:
    steps = _steps(_workflow())
    first = steps[0]
    assert first.get("uses", "").startswith("actions/checkout@"), (
        f"first step must be a clean checkout, found: {first}"
    )


def test_pipeline_order_is_unit_then_smoke_then_e2e() -> None:
    steps = _steps(_workflow())
    runs = [s.get("run", "") for s in steps]

    def index_of(fragment: str) -> int:
        for i, cmd in enumerate(runs):
            if fragment in cmd:
                return i
        raise AssertionError(f"no CI step runs `{fragment}`")

    i_test = index_of("make test")
    i_up = index_of("make up")
    i_smoke = index_of("make smoke")
    i_e2e = index_of("make e2e")
    assert i_test < i_up < i_smoke < i_e2e, (
        f"CI order must be unit → up → smoke → e2e, got step order: {runs}"
    )


def test_stack_teardown_is_unconditional() -> None:
    steps = _steps(_workflow())
    downs = [s for s in steps if "make down" in s.get("run", "")]
    assert downs, "CI must tear the stack down"
    assert any(s.get("if") == "always()" for s in downs), (
        "teardown must run even when an earlier step fails (`if: always()`)"
    )


def test_e2e_phase_covers_pytest_suite_and_ui_journey() -> None:
    steps = _steps(_workflow())
    runs = [s.get("run", "") for s in steps]
    assert any("make e2e-ui" in cmd or "playwright" in cmd for cmd in runs), (
        "CI e2e phase must also run the Playwright UI journey (test_06)"
    )


def test_make_e2e_runs_whole_suite_in_random_order() -> None:
    text = MAKEFILE.read_text()
    assert "pytest tests/e2e" in text, "`make e2e` must run the whole tests/e2e suite"
    assert "-p randomly" in text, (
        "`make e2e` must enforce random order explicitly "
        "(-p randomly fails fast instead of silently running ordered)"
    )


def test_make_e2e_ui_target_exists() -> None:
    text = MAKEFILE.read_text()
    assert "e2e-ui:" in text, "Makefile must define an `e2e-ui` target (Playwright journey)"


def test_make_ui_deps_target_installs_npm_ci() -> None:
    text = MAKEFILE.read_text()
    assert "ui-deps:" in text, "Makefile must define a `ui-deps` target (REQ-009)"
    assert "npm ci" in text, "`ui-deps` must install with `npm ci`"
    assert "ui/node_modules" in text, "`ui-deps` must key off ui/node_modules"


def test_make_test_depends_on_ui_deps() -> None:
    text = MAKEFILE.read_text()
    assert "test: ui-deps" in text, (
        "`make test` must depend on `ui-deps` so a fresh clone installs "
        "ui/node_modules before pytest runs test_ui_client_compiles"
    )


def test_make_e2e_ui_installs_playwright_browsers() -> None:
    text = MAKEFILE.read_text()
    assert "e2e-ui: ui-e2e-deps" in text or "e2e-ui: ui-deps" in text, (
        "`make e2e-ui` must depend on the UI/playwright install targets "
        "so it works in a fresh clone without a manual browser install"
    )
    assert "playwright install" in text, (
        "Makefile must install the Playwright browsers (REQ-009)"
    )


def test_ui_typecheck_test_skips_loudly_without_node_modules() -> None:
    text = (ROOT / "tests" / "unit" / "test_autobridge_ui_client.py").read_text()
    assert "pytest.skip" in text and "ui-deps" in text, (
        "test_ui_client_compiles must skip loudly (naming `make ui-deps`) "
        "when ui/node_modules is missing, so a bare pytest run on a fresh "
        "clone skips instead of failing"
    )


def test_readme_quickstart_lists_node_and_ui_deps() -> None:
    text = (ROOT / "README.md").read_text()
    assert "Node" in text and "npm" in text, (
        "README quickstart must list Node/npm as a requirement (REQ-009)"
    )
    assert "ui-deps" in text or "npm ci" in text, (
        "README quickstart must include the UI install step (REQ-009)"
    )


def test_make_e2e_ui_sets_node_path_for_root_config() -> None:
    text = MAKEFILE.read_text()
    assert "NODE_PATH" in text and "ui/node_modules" in text, (
        "`make e2e-ui` must set NODE_PATH to ui/node_modules: the root "
        "playwright.config.ts and the tests/e2e specs import "
        "@playwright/test, which is installed under ui/ (no root "
        "node_modules), so config load fails without it"
    )
