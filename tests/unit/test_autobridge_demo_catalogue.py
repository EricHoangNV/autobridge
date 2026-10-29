"""Catalogue sync test (REQ-023): docs/demo.md, bench/demo.py CATALOGUE,
scenario files, the model-usage matrix and e2e coverage stay in sync.

Every catalogue entry has a scenario file (ladder/agent kinds) or a named
behavior proof (behavior kind), an e2e/unit test file, and — for ladder
entries handled by the RepairController harness — a row in REQ-021's
model-usage matrix. `make demo SCENARIO=<id>` runs every scenario entry;
`make demo STEP=<n|name>` runs every main-script step.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

import pytest

from bench.demo import (
    CATALOGUE,
    CATALOGUE_SCENARIOS,
    STEP_NAMES,
    STEPS,
    catalogue_row,
    main,
    run_catalogue_scenario,
    run_demo,
    run_demo_step,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _matrix_rows() -> list[dict[str, Any]]:
    import ast

    src = (REPO_ROOT / "tests" / "e2e" / "test_model_usage_matrix.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        target: Any = None
        value: Any = None
        if isinstance(node, ast.Assign) and node.targets:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if target is not None and getattr(target, "id", "") == "MATRIX" and value is not None:
            return ast.literal_eval(ast.unparse(value))
    raise AssertionError("MATRIX not found in test_model_usage_matrix.py")


def _demo_doc_rows() -> set[str]:
    """Scenario ids present in the docs/demo.md catalogue table."""
    text = (REPO_ROOT / "docs" / "demo.md").read_text(encoding="utf-8")
    assert "## Fault-scenario catalogue (REQ-023)" in text
    return set(re.findall(r"\| `([a-z0-9_]+)` \|", text))


def test_catalogue_scenarios_unique_and_complete() -> None:
    seen: set[str] = set()
    for row in CATALOGUE:
        scen = row.get("scenario")
        if scen is None:
            assert row.get("name"), row  # behavior rows need a stable name
            continue
        assert scen not in seen, f"duplicate catalogue scenario {scen}"
        seen.add(scen)
    assert set(CATALOGUE_SCENARIOS) == seen
    # Every served scenario file (except the baseline fixture) is catalogued.
    # Synthetic entries (e.g. nested_path_shift: matrix-only drift, no served
    # fault) are catalogued without a file by design.
    files = {p.stem for p in (REPO_ROOT / "simulators" / "scenarios").glob("*.yaml")}
    files.discard("baseline")
    assert files <= seen, f"scenario files missing from catalogue: {files - seen}"
    for row in CATALOGUE:
        if row.get("scenario") is not None and not row.get("synthetic"):
            assert (REPO_ROOT / "simulators" / "scenarios" / f"{row['scenario']}.yaml").exists(), row
        if row.get("synthetic"):
            assert row["kind"] == "ladder-matrix", row


def test_catalogue_docs_table_matches_code() -> None:
    doc_rows = _demo_doc_rows()
    for row in CATALOGUE:
        scen = row.get("scenario")
        if scen is not None:
            assert scen in doc_rows, f"{scen} missing from docs/demo.md catalogue table"
    # Table columns for a sampled row carry the code's rung and REQ.
    text = (REPO_ROOT / "docs" / "demo.md").read_text(encoding="utf-8")
    sample = catalogue_row("page_to_cursor")
    assert sample is not None
    assert str(sample["rung"]) in text and str(sample["req"]) in text


def test_catalogue_entries_have_e2e_and_matrix_rows() -> None:
    matrix = {r["scenario"]: r for r in _matrix_rows()}
    for row in CATALOGUE:
        e2e = row.get("e2e")
        assert e2e, f"{row} has no covering test"
        assert (REPO_ROOT / e2e).exists(), f"{row.get('scenario') or row.get('name')}: {e2e} missing"
        kind = row.get("kind")
        assert kind in {"ladder-matrix", "ladder-demo", "agent", "behavior"}, row
        scen = row.get("scenario")
        if kind == "ladder-matrix":
            if scen is not None and not row.get("synthetic"):
                assert (REPO_ROOT / "simulators" / "scenarios" / f"{scen}.yaml").exists()
            assert scen in matrix, f"{scen}: ladder entry without a model-usage matrix row"
            mrow = matrix[scen]
            assert f"L{mrow['expected_rung']}" in str(row["rung"]), (
                f"{scen}: catalogue rung {row['rung']!r} != matrix L{mrow['expected_rung']}"
            )
            if mrow.get("semantic_climb"):
                assert row["model_calls"] != "0", scen
            else:
                assert row["model_calls"] == "0", scen
        elif kind in {"ladder-demo", "agent"}:
            assert scen is not None and (REPO_ROOT / "simulators" / "scenarios" / f"{scen}.yaml").exists()
            assert str(row["rung"]).startswith(("L1", "n/a")), row
        else:
            assert scen is None, row  # behavior rows have no scenario file
        step = row.get("main_step")
        if step is not None:
            assert 1 <= step <= len(STEPS), row


def test_demo_scenario_runner_offline_ok_for_every_entry(monkeypatch: Any) -> None:
    monkeypatch.setenv("FRONTIER_MODE", "replay")
    for scen in CATALOGUE_SCENARIOS:
        step = run_catalogue_scenario(scen, seed=0, base_url="http://127.0.0.1:9")
        assert step["status"] == "ok", (scen, step.get("error"))
        assert step["control_calls"] == [], scen  # offline: no HTTP attempted


def test_demo_scenario_unknown_fails_loudly() -> None:
    step = run_catalogue_scenario("no_such_scenario", seed=0, base_url="http://127.0.0.1:9")
    assert step["status"] == "failed"
    assert "runnable" in str(step.get("error"))


def test_demo_step_runner_by_number_and_name() -> None:
    for number, _name in STEP_NAMES.items():
        assert len(STEPS) == 8
        by_number = run_demo_step(number, seed=0, base_url="http://127.0.0.1:9")
        assert by_number["status"] == "ok", (number, by_number.get("error"))
        assert by_number["number"] == f"{number}/8"
    named = run_demo(seed=0, control_url="http://127.0.0.1:9", only_step=4)
    assert len(named["steps"]) == 1 and named["failed"] == 0


def test_demo_step_unknown_fails_loudly() -> None:
    step = run_demo_step(99, seed=0, base_url="http://127.0.0.1:9")
    assert step["status"] == "failed"


def test_main_step_and_scenario_flags(capsys: Any) -> None:
    assert main(["--step", "3", "--control-url", "http://127.0.0.1:9"]) == 0
    out = capsys.readouterr().out
    assert "1 steps" in out
    assert main(["--step", "zero_token_recovery", "--control-url", "http://127.0.0.1:9"]) == 0
    assert main(["--scenario", "rate_limit_429", "--control-url", "http://127.0.0.1:9"]) == 0
    out = capsys.readouterr().out
    assert "catalogue:rate_limit_429" in out
    with pytest.raises(SystemExit):
        main(["--scenario", "rate_limit_429", "--step", "3"])
    with pytest.raises(SystemExit):
        main(["--step", "bogus"])
