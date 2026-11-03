"""REQ-081: seven rehearsal-gap fixes — one test per item."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _legacystay_like_spec_no_money() -> dict:
    return {
        "spec_version": 3,
        "connector": "legacystay",
        "source": {"base_url": "http://localhost:8100", "protocol": "http"},
        "operations": {"listReservations": {"method": "GET", "path": "/reservations"}},
        "entities": {
            "Reservation": {
                "key": "$.reservation_id",
                "fields": {
                    "reservation_id": {"path": "$.reservation_id"},
                    "total_amount": {"path": "$.total_amount", "transform": [{"name": "money"}]},
                },
                # No money_invariant check on purpose (REQ-081 item 1).
                "checks": [],
            }
        },
    }


def _summary_with_amounts() -> dict:
    return {
        "canonical_records": {
            "Reservation": [
                {"reservation_id": "R1", "total_amount": "250.00"},
                {"reservation_id": "R2", "total_amount": "500.00"},
            ]
        },
        "operation_results": {
            "listReservations": {"raw_pages": [{"gross_sum": "1000.00"}]}
        },
        "unmet_criteria": [],
    }


def _money_signal() -> dict:
    return {
        "kind": "money_scale_shift",
        "entity": "Reservation",
        "message": "Money field total_amount shifted by x0.25 for Reservation",
        "details": {
            "entity": "Reservation",
            "money_field": "total_amount",
            "factor": "0.25",
            "matched_pairs": 10,
            "shifted_pairs": 10,
        },
    }


def test_item1_semantic_evidence_seeded_from_signal() -> None:
    from autobridge.repair.controller import prepare_climb_inputs
    from autobridge.repair.ladder import _l3_evidence_blocks

    spec = _legacystay_like_spec_no_money()
    summary = _summary_with_amounts()
    prepared = prepare_climb_inputs(
        spec=spec,
        signals=[_money_signal()],
        summary=summary,
        sync_run_id="run-1",
        drift_class="SEMANTIC_VALUE_DRIFT",
    )
    evidence = prepared.get("evidence") or {}
    assert evidence, "semantic evidence must not be empty for a signal-seeded money field"
    blob = json.dumps(evidence, sort_keys=True, default=str)
    assert "total_amount" in blob
    assert "0.25" in blob
    # Baseline vs faulted values present.
    assert "250" in blob or "750" in blob or "baseline" in blob.lower()
    blocks = _l3_evidence_blocks(evidence)
    assert blocks.get("observed_evidence_block") != "(none recorded)"
    assert blocks.get("spec_subtree_block") != "(none recorded)"


def test_item2_frontier_evidence_has_baseline() -> None:
    from bench.evidence import render_fault_vs_baseline

    faulted = {
        "outcome": "Complete",
        "unmet_criteria": [],
        "invariant_results": [],
        "operation_results": {
            "listReservations": {
                "records": [{"reservation_id": "R1", "total_amount": "250.00"}]
            }
        },
    }
    baseline = {
        "outcome": "Complete",
        "unmet_criteria": [],
        "invariant_results": [],
        "operation_results": {
            "listReservations": {
                "records": [{"reservation_id": "R1", "total_amount": "1000.00"}]
            }
        },
    }
    text = render_fault_vs_baseline(faulted, baseline)
    assert "baseline" in text.lower()
    assert "250.00" in text
    assert "1000.00" in text


def test_item3_apply_error_recorded_not_silent() -> None:
    from bench.beacon_run import _candidate_apply_error, _candidate_spec_for

    case = SimpleNamespace(spec={"entities": {"R": {"fields": {}}}, "operations": {}})
    result = SimpleNamespace(
        spec={"candidate_patch": [{"op": "replace", "path": "/entities/Nope/fields/x/path", "value": "$.y"}]}
    )
    assert _candidate_spec_for(result, case) is None
    err = _candidate_apply_error(result, case)
    assert err is not None and "does not apply" in err


def test_item4_date_input_format() -> None:
    from autobridge.runtime.transforms.base import is_allowed_date_input_format
    from autobridge.runtime.transforms.date_local import transform_date_local

    assert transform_date_local("10-15-2026", {"input_format": "%m-%d-%Y"}) == "2026-10-15"
    assert not is_allowed_date_input_format("%c")
    assert not is_allowed_date_input_format("rm -rf /")
    # REQ-083 C16: the every-generated-format parity assertion lives in
    # the bench-side test (no generator import in engine tests).


def test_item4_spec_rejects_free_format() -> None:
    from autobridge.spec.models import TransformStep

    ok = TransformStep(name="date_local", args={"input_format": "%m-%d-%Y"})
    assert ok.args["input_format"] == "%m-%d-%Y"
    try:
        TransformStep(name="date_local", args={"input_format": "%c"})
    except Exception:
        pass
    else:
        raise AssertionError("free strptime directive %c must be rejected")


def test_item5_meridian_nesting_projects_wire() -> None:
    from simulators.meridianpms.app import to_wire_reservation

    inner = {
        "reservation_id": "R1",
        "property_id": "P1",
        "arrival_date": "2026-11-03",
        "departure_date": "2026-11-06",
        "status": "Processed",
        "guest_name": "A B",
        "total_amount": "100.00",
        "updated_at": "2026-11-01T10:00:00+00:00",
    }
    nested = {"outer": copy.deepcopy(inner)}
    wire = to_wire_reservation(copy.deepcopy(nested))
    # Nesting applies to the projected wire, never null entity/join keys.
    # REQ-083 B5: the served service id is the canonical property id
    # (no SVC- namespace), so it agrees with the oracle exactly.
    assert "outer" in wire
    inner_wire = wire["outer"]
    assert inner_wire.get("Id") == "R1"
    assert str(inner_wire.get("ServiceId")) == "P1"
    assert str(inner_wire.get("AccountId")) == "ACC-R1"
    assert inner_wire.get("ServiceId") is not None


def test_item5_atlas_nesting_projects_wire() -> None:
    from simulators.atlaspms.app import to_wire_reservation

    inner = {
        "reservation_id": "R1",
        "property_id": "P1",
        "arrival_date": "2026-11-03",
        "departure_date": "2026-11-06",
        "status": "confirmed",
        "guest_name": "A B",
        "total_amount": "100.00",
        "updated_at": "2026-11-01T10:00:00+00:00",
    }
    wire = to_wire_reservation(copy.deepcopy({"wrap": inner}))
    assert "wrap" in wire
    assert wire["wrap"].get("reservationID") == "R1"


def test_item6_generic_evidence_renders_paths() -> None:
    from autobridge.repair.controller import build_generic_l3_evidence

    spec = {
        "entities": {
            "FolioCharge": {
                "key": "$.charge_id",
                "fields": {
                    "charge_id": {"paths": ["$.DA", "$.TI"]},
                    "posted_at": {"paths": ["$.DA", "$.TI"]},
                },
                "checks": [],
            }
        },
        "operations": {},
    }
    signals = [
        {"kind": "keys_changed", "message": "layout DA->CD", "details": {"entity": "FolioCharge"}}
    ]
    summary = {"canonical_records": {"FolioCharge": [{"charge_id": "C1"}]}, "unmet_criteria": []}
    ev = build_generic_l3_evidence(spec, signals, None, summary, "SCHEMA_RENAME")
    subtree = (ev.get("spec_subtree") or {}).get("FolioCharge", {}).get("fields", {})
    assert "$.DA" in str(subtree.get("charge_id", "")) and "$.TI" in str(subtree.get("charge_id", ""))
    allowed = ev.get("patch_target", {}).get("allowed_pointers", [])
    assert any(p.endswith("/paths") for p in allowed)
    assert any("/paths/0" in p for p in allowed)


def test_item7_sandbox_writes_permission_config(tmp_path: Path) -> None:
    from bench.baseline_coding_agent import AGENT_PERMISSION_CONFIG
    from bench.context import BenchContext, write_agent_sandbox

    ctx = BenchContext(
        scenario_id="s",
        connector_id="c",
        vocabulary_version="v1",
        schema_json="{}",
        capabilities_json="{}",
        example_vendor="x",
        example_spec_yaml="x: 1",
        docs_text="docs",
        visible_tests=(),
        pre_drift_spec_yaml="spec",
        evidence_text="evidence",
    )
    write_agent_sandbox(ctx, tmp_path)
    cfg_path = tmp_path / "opencode.json"
    assert cfg_path.is_file()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    perm = cfg.get("permission", {})
    # REQ-091/AR-7-9: default-deny — only read/glob/grep allowed, edit denied.
    assert perm == dict(AGENT_PERMISSION_CONFIG)
    assert perm.get("*") == "deny"
    assert perm.get("edit") == "deny"
    for allowed in ("read", "glob", "grep"):
        assert perm.get(allowed) == "allow", allowed
    for denied in ("bash", "webfetch", "websearch", "task", "skill", "lsp", "external_directory"):
        assert perm.get(denied, perm.get("*")) == "deny", denied


def test_req091_agent_child_env_pristine_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-091/AR-7-9: the agent child env is rooted at a pristine per-run home.

    A parent seeded with an operator HOME, XDG dirs, OPENCODE_* names, a
    loosening OPENCODE_PERMISSION, bench-internal endpoints, simulator
    credential refs and an unrelated secret produces a child env whose
    HOME and XDG base directories are the per-run dir (carrying none of
    the operator values), PWD is the sandbox dir, OPENCODE_PERMISSION is
    the default-deny config, every surviving name is explicitly allowed,
    and only the frontier provider's key variables cross.
    """
    from bench.baseline_coding_agent import (
        AGENT_PERMISSION_CONFIG,
        _sandbox_env,
        agent_child_env_allowed_names,
        agent_child_env_allows,
        build_agent_child_env,
        make_pristine_cli_home,
    )

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    cli_home = make_pristine_cli_home(base_dir=tmp_path)
    hostile = {
        "VERIFIER_URL": "http://verifier-hostile:9999",
        "SIM_ADMIN_TOKEN": "hostile-admin",
        "AUTOBRIDGE_CONTROL_TOKEN": "hostile-control",
        "ATLAS_API_KEY": "hostile-atlas",
        "MYSTERY_UNRELATED_SECRET": "hostile-secret",
        "HOME": "/home/operator",
        "XDG_CONFIG_HOME": "/home/operator/.config",
        "XDG_DATA_HOME": "/home/operator/.local/share",
        "XDG_CACHE_HOME": "/home/operator/.cache",
        "OPENCODE_CONFIG": "/home/operator/.config/opencode/evil.json",
        "OPENCODE_MODEL": "evil-model",
        "OPENCODE_PERMISSION": '{"*":"allow","bash":"allow","edit":"allow"}',
        "FRONTIER_API_KEY": "frontier-key",
        "META_MODEL_API_KEY": "meta-key",
        "PATH": "/usr/bin:/bin",
    }
    child = build_agent_child_env(hostile, sandbox, model="meta/muse-spark-1.3", cli_home=cli_home)
    for banned in (
        "VERIFIER_URL",
        "SIM_ADMIN_TOKEN",
        "AUTOBRIDGE_CONTROL_TOKEN",
        "ATLAS_API_KEY",
        "MYSTERY_UNRELATED_SECRET",
        "OPENCODE_CONFIG",
        "OPENCODE_MODEL",
    ):
        assert banned not in child, banned
    # The pristine per-run dir is the CLI home — none of the operator values.
    assert child["HOME"] == str(cli_home)
    assert child["HOME"].startswith(str(tmp_path))
    for xdg in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        assert child[xdg].startswith(str(cli_home)), xdg
    for value in child.values():
        assert "/home/operator" not in value
        assert value != "evil-model"
    assert child["PWD"] == str(sandbox)
    assert json.loads(child["OPENCODE_PERMISSION"]) == dict(AGENT_PERMISSION_CONFIG)
    for name in child:
        assert agent_child_env_allows(name, model="meta/muse-spark-1.3"), name
    # The frontier keys the CLI reads for the pinned model cross.
    assert child["FRONTIER_API_KEY"] == "frontier-key"
    assert child["META_MODEL_API_KEY"] == "meta-key"
    # Unknown providers get the default frontier keys, never bench secrets.
    assert (
        build_agent_child_env(hostile, sandbox, model=None, cli_home=cli_home)["FRONTIER_API_KEY"]
        == "frontier-key"
    )
    assert "MYSTERY_UNRELATED_SECRET" not in build_agent_child_env(
        hostile, sandbox, model=None, cli_home=cli_home
    )
    # No open prefix pass-through: an OPENCODE_*/XDG_* name outside the
    # explicit list never crosses, even when the parent sets it.
    assert "OPENCODE_MODEL" not in agent_child_env_allowed_names("meta/muse-spark-1.3")
    assert not agent_child_env_allows("OPENCODE_MODEL", model="meta/muse-spark-1.3")
    assert not agent_child_env_allows("XDG_RUNTIME_DIR", model="meta/muse-spark-1.3")
    # The os.environ-backed helper honors the same allowlist.
    monkeypatch.setenv("MYSTERY_UNRELATED_SECRET", "hostile-secret")
    monkeypatch.setenv("PWD", str(tmp_path))
    monkeypatch.setenv("HOME", "/home/operator")
    monkeypatch.setenv("OPENCODE_MODEL", "evil-model")
    monkeypatch.setenv(
        "OPENCODE_PERMISSION",
        '{"*":"allow","bash":"allow","edit":"allow"}',
    )
    via_environ = _sandbox_env(sandbox, cli_home=cli_home)
    assert via_environ is not None
    assert "MYSTERY_UNRELATED_SECRET" not in via_environ
    assert "OPENCODE_MODEL" not in via_environ
    assert via_environ["HOME"] == str(cli_home)
    assert via_environ["PWD"] == str(sandbox)
    assert json.loads(via_environ["OPENCODE_PERMISSION"]) == dict(AGENT_PERMISSION_CONFIG)


def test_item4_business_datetime_input_format() -> None:
    from autobridge.runtime.transforms.business_date import transform_business_date
    from autobridge.runtime.transforms.datetime_utc import transform_datetime_utc

    # Midnight UTC with default cutoff 4 counts to the prior business day;
    # pass cutoff 0 to assert the parse itself.
    assert transform_business_date("15-10-2026", {"input_format": "%d-%m-%Y", "cutoff": 0}) == "2026-10-15"
    out = transform_datetime_utc("10-15-2026 10:00", {"input_format": "%m-%d-%Y %H:%M", "tz": "UTC"})
    assert str(out).startswith("2026-10-15T10:00")


def test_item3_grade_records_apply_error() -> None:
    import bench.beacon_run as br

    grade = br.BeaconArmGrade(arm="a", instance_index=0, trial=0, vendor="atlas", family="rename")
    descriptor = {"index": 0, "vendor": "atlas"}
    result = SimpleNamespace(
        spec={"candidate_patch": [{"op": "replace", "path": "/nope", "value": 1}]},
        outcome=None,
        ran=True,
        error=None,
    )
    case = SimpleNamespace(spec={"entities": {}, "operations": {}})
    orig_case = br.beacon_case_for
    orig_verifier = br._verifier_post
    br.beacon_case_for = lambda *_a, **_k: case  # type: ignore[method-assign]
    br._verifier_post = lambda *_a, **_k: {"passed": False, "reason": "x"}  # type: ignore[method-assign]
    try:
        br._grade_arm_result(grade, descriptor, result, "00" * 32, "http://x", "http://v")
    finally:
        br.beacon_case_for = orig_case  # type: ignore[method-assign]
        br._verifier_post = orig_verifier  # type: ignore[method-assign]
    assert grade.wrong_patch_proposed == 1
    assert grade.error and "does not apply" in grade.error


def test_item7_reprompts_once_on_empty_tool_end(tmp_path: Path) -> None:
    import os as _os
    import subprocess as _subprocess

    from bench.baseline_coding_agent import OpenCodeCliDriver

    driver = OpenCodeCliDriver(model="meta/test-model", timeout_s=5.0)
    first_stdout = json.dumps({"type": "text", "part": {"type": "tool", "name": "bash"}}) + "\n" + json.dumps(
        {"type": "session", "session_id": "sess-123"}
    )
    second_stdout = json.dumps({"type": "text", "part": {"type": "text", "text": '{"patch": []}'}}) + "\n" + json.dumps(
        {"type": "session", "session_id": "sess-123"}
    )
    calls: list[list[str]] = []
    envs: list[dict[str, str]] = []
    cwds: list[str] = []

    def _fake_run(cmd: list[str], **_kwargs: object):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))
        env = _kwargs.get("env")
        assert isinstance(env, dict)
        envs.append(dict(env))
        cwd = _kwargs.get("cwd")
        assert isinstance(cwd, str)
        cwds.append(cwd)
        out = first_stdout if len(calls) == 1 else second_stdout
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    import bench.baseline_coding_agent as _mod

    # REQ-091 item 3: the agent dirties the sandbox during the first run —
    # the re-prompt must not reload it.
    (tmp_path / ".opencode").mkdir()
    (tmp_path / ".opencode" / "evil-plugin.json").write_text("{}", encoding="utf-8")
    (tmp_path / "planted.txt").write_text("agent wrote this", encoding="utf-8")

    _orig_run = _subprocess.run
    _orig_resolve = _mod.resolve_opencode_session
    _subprocess.run = _fake_run  # type: ignore[method-assign]
    _mod.resolve_opencode_session = lambda *_a, **_k: None  # type: ignore[method-assign]
    try:
        res = driver.run("do the task", cwd=tmp_path)
    finally:
        _subprocess.run = _orig_run  # type: ignore[method-assign]
        _mod.resolve_opencode_session = _orig_resolve  # type: ignore[method-assign]
    assert res.text.strip() != ""
    assert len(calls) == 2
    assert "--session" in calls[1] and "sess-123" in calls[1]
    # The re-prompt runs in a freshly written copy, not the dirtied dir —
    # same pristine CLI home (so --session continues), none of the
    # agent-planted files.
    assert cwds[0] == str(tmp_path)
    assert cwds[1] != str(tmp_path)
    assert envs[0]["HOME"] == envs[1]["HOME"]
    assert envs[1]["HOME"] not in (str(tmp_path), _os.environ.get("HOME", ""))
    assert envs[1]["PWD"] == cwds[1]
    import pathlib as _pathlib

    fresh = _pathlib.Path(cwds[1])
    assert not (fresh / "planted.txt").exists()
    assert not (fresh / ".opencode" / "evil-plugin.json").exists()


def test_req091_fake_agent_path_parses_inline_json() -> None:
    """REQ-091 item 5: the fake-agent path still parses the inline JSON answer."""
    from bench.baseline_coding_agent import FakeOpenCodeDriver
    from bench.repair_answer import parse_repair_answer

    answer = '{"patch": [{"op": "replace", "path": "/entities/E/fields/f/path", "value": "$.g"}]}'
    driver = FakeOpenCodeDriver(responses={"repair it": answer})
    res = driver.run("repair it")
    assert res.text.strip() == answer
    assert parse_repair_answer(res.text)["kind"] == "patch"
