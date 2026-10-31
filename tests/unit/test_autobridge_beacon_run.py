"""Beacon run tests (REQ-025 items 4-7, bench side): freeze, cases, grading.

Offline throughout: chain info is fabricated, simulator pushes and the
verifier are faked or omitted (verifier_url=None degrades to a recorded
loss), engine arms run through caller-supplied fake runners. Live HTTP
appears only in rehearsals (owner/CI with a stack).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pathlib

import pytest

if TYPE_CHECKING:
    import pathlib as _pl  # noqa: F401

import simulators.common.faultgen as faultgen
from bench import beacon_run
from bench.beacon_run import BeaconError

EVAL_SEED_HEX = "05ec57a0c1e763e985c9ac18df2313a98392a9c24ecbceefd0b21216a9beaefa"
EVAL_SEED = bytes.fromhex(EVAL_SEED_HEX)

FAKE_INFO = {
    "public_key": "868f005eb8e6e4ca0a47c8a77ceaa5309a47978a7c71bc5cce96366b5d7a569937c529eeda66c7293784a9402801af31",
    "period": 30,
    "genesis_time": 1595431050,
    "hash": beacon_run.beacon_client.LOE_MAINNET_CHAIN_HASH,
    "schemeID": beacon_run.beacon_client.LOE_SCHEME_ID,
}


def _descriptor(index: int) -> dict[str, Any]:
    inst = faultgen.generate_instance(EVAL_SEED, index)
    return {"index": index, "vendor": inst["vendor"], "family": inst["family"], "params": inst["params"]}


def test_load_vendors_round_robin() -> None:
    vendors = beacon_run.load_vendors()
    assert [v.vendor for v in vendors] == ["atlas", "harbor", "legacystay", "meridian", "fias-pabx"]
    assert vendors[4].http is False
    assert all(v.admin_url.startswith("http://localhost:") for v in vendors)


def test_load_vendors_rejects_duplicates(tmp_path: pathlib.Path) -> None:
    bad = tmp_path / "vendors.yaml"
    bad.write_text("order:\n  - vendor: atlas\n  - vendor: atlas\n", encoding="utf-8")
    with pytest.raises(BeaconError):
        beacon_run.load_vendors(bad)


def test_parse_protocol_fail_closed(tmp_path: pathlib.Path) -> None:
    with pytest.raises(BeaconError):
        beacon_run.parse_protocol(tmp_path / "missing.md")
    broken = tmp_path / "protocol.md"
    broken.write_text("no fields here\n", encoding="utf-8")
    with pytest.raises(BeaconError):
        beacon_run.parse_protocol(broken)


def test_freeze_refuses_existing_protocol(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    existing = tmp_path / "protocol.md"
    existing.write_text("beacon-target-round: 1\n", encoding="utf-8")
    monkeypatch.setattr(beacon_run, "_git_dirty", lambda: False)
    with pytest.raises(BeaconError):
        beacon_run.freeze("2030-01-01T00:00:00+00:00", 5, protocol_path=existing)


def test_freeze_refuses_dirty_tree_and_past_time(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(beacon_run, "_git_dirty", lambda: True)
    monkeypatch.setattr(
        beacon_run.beacon_client, "fetch_chain_info", lambda _base_url: dict(FAKE_INFO)
    )
    with pytest.raises(BeaconError):
        beacon_run.freeze("2030-01-01T00:00:00+00:00", 5,
                          protocol_path=tmp_path / "protocol.md")
    monkeypatch.setattr(beacon_run, "_git_dirty", lambda: False)
    with pytest.raises(BeaconError):
        beacon_run.freeze("2000-01-01T00:00:00+00:00", 5,
                          protocol_path=tmp_path / "protocol2.md")


def test_freeze_writes_protocol(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(beacon_run, "_git_dirty", lambda: False)
    monkeypatch.setattr(
        beacon_run.beacon_client, "fetch_chain_info", lambda _base_url: dict(FAKE_INFO)
    )
    monkeypatch.setattr(beacon_run, "_git_commit", lambda: "abc123")
    path = beacon_run.freeze("2030-01-01T00:00:00+00:00", 3, trials=1,
                             protocol_path=tmp_path / "protocol.md")
    protocol = beacon_run.parse_protocol(path)
    assert protocol.faults == 3 and protocol.trials == 1
    assert protocol.target_round == beacon_run.beacon_client.round_for_time(
        1595431050, 30, 1893456000)
    assert protocol.engine_commit == "abc123"
    assert protocol.rehearsal is False


def test_push_beacon_validates_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"index": 0, "vendor": "atlas", "family": "rename",
                    "params": {"a": 1}}

    import httpx as _httpx

    monkeypatch.setattr(_httpx, "post", lambda *_a, **_k: _Resp())
    descriptor = beacon_run.push_beacon(EVAL_SEED_HEX, 0, "atlas", admin_url="http://x")
    assert descriptor["vendor"] == "atlas"

    class _Leaky(_Resp):
        def json(self) -> dict[str, Any]:
            return {"index": 0, "vendor": "atlas", "family": "rename",
                    "params": {}, "expected": "repair"}

    monkeypatch.setattr(_httpx, "post", lambda *_a, **_k: _Leaky())
    with pytest.raises(BeaconError):
        beacon_run.push_beacon(EVAL_SEED_HEX, 0, "atlas", admin_url="http://x")

    class _WrongVendor(_Resp):
        def json(self) -> dict[str, Any]:
            return {"index": 0, "vendor": "harbor", "family": "rename", "params": {}}

    monkeypatch.setattr(_httpx, "post", lambda *_a, **_k: _WrongVendor())
    with pytest.raises(BeaconError):
        beacon_run.push_beacon(EVAL_SEED_HEX, 0, "atlas", admin_url="http://x")


def test_beacon_case_for_shape_family() -> None:
    descriptor = None
    for index in range(5):
        cand = _descriptor(index)
        if cand["family"] in ("rename", "nesting", "pagination"):
            descriptor = cand
            break
    assert descriptor is not None
    case = beacon_run.beacon_case_for(descriptor)
    assert case is not None
    assert case.scenario_id == f"beacon:{descriptor['index']}"
    # Equal task setup only: no drift class or hypotheses on the case.
    assert case.spec and case.connector_id and case.docs_ref
    assert not hasattr(case, "drift_class")


def test_beacon_case_for_fias_has_spec() -> None:
    # REQ-062 item 2: every beacon vendor has an in-process engine spec,
    # PABX included (built from its docs as in the demo, vocabulary v3).
    descriptor = _descriptor(4)
    assert descriptor["vendor"] == "fias-pabx"
    case = beacon_run.beacon_case_for(descriptor)
    assert case is not None
    assert case.connector_id == "fiaspabx"
    assert case.docs_ref == "simulators/docs/fias-pabx.md"
    assert case.scenario_id == "beacon:4"
    assert case.spec.get("spec_version") == 3
    assert not hasattr(case, "drift_class")


def test_canonical_to_records_mapping() -> None:
    summary = {"Reservation": [{"reservation_id": "R1"}], "FolioCharge": [{"charge_id": "C1"}]}
    assert beacon_run.canonical_to_records(summary) == {
        "reservations": [{"reservation_id": "R1"}],
        "folios": [{"charge_id": "C1"}],
    }
    assert beacon_run.canonical_to_records({}) == {"reservations": [], "folios": []}


def test_arm_outcome_mapping() -> None:
    from types import SimpleNamespace as _NS

    propose = _NS(ran=True, spec={"decision": "propose", "candidate_patch": [{"op": "add"}]})
    assert beacon_run._arm_outcome_for(propose, True)["outcome"] == "Complete"
    backoff = _NS(ran=True, spec={"decision": "propose", "candidate_patch": []})
    report = beacon_run._arm_outcome_for(backoff, False)
    assert report == {"outcome": "Complete", "patch_reached_release": False, "operator_question": False}
    degraded = _NS(ran=True, spec={"decision": "degraded", "candidate_patch": None})
    assert beacon_run._arm_outcome_for(degraded, False)["operator_question"] is True
    escalated = _NS(ran=True, spec={"decision": "escalate", "candidate_patch": None})
    assert beacon_run._arm_outcome_for(escalated, False)["outcome"] == "Blocked"
    # REQ-063 item 2: the ladder's question rides the escalation report.
    escalated_q = _NS(ran=True, spec={"decision": "escalate", "candidate_patch": None,
                                      "question": "Which mapping is correct?"})
    assert beacon_run._arm_outcome_for(escalated_q, False) == {
        "outcome": "Blocked", "patch_reached_release": False, "operator_question": True}
    failed = _NS(ran=False, spec=None)
    assert beacon_run._arm_outcome_for(failed, False)["outcome"] == "Blocked"


def test_run_instance_trial_offline_no_verifier() -> None:
    from bench.harness import HarnessConfig as _Cfg

    descriptor = _descriptor(0)
    protocol = beacon_run.BeaconProtocol(
        round_after_utc="2030-01-01T00:00:00+00:00", target_round=9999999,
        faults=1, trials=1, arms=("autobridge_cold", "frontier_oneshot"),
        engine_commit="a", generator_commit="b", frozen_at_utc="2030-01-01T00:00:00+00:00",
    )

    def fake_runner(inputs: Any) -> Any:
        from bench.harness import ArmResult as _ArmResult

        return _ArmResult(arm="autobridge_cold", inputs=inputs, ran=True, model="fake")

    grades, reports = beacon_run.run_instance_trial(
        protocol, EVAL_SEED_HEX, descriptor, 0, config=_Cfg(trials_per_scenario=1),
        runners={"autobridge_cold": fake_runner, "frontier_oneshot": fake_runner},
        verifier_url=None,
    )
    assert len(grades) == 2
    assert all(g.error and "no verifier URL" in g.error for g in grades)
    assert all(g.verified_completion == 0 for g in grades)


def test_run_beacon_refuses_unpublished_and_rerolls(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    protocol = beacon_run.BeaconProtocol(
        round_after_utc="2030-01-01T00:00:00+00:00", target_round=9999999,
        faults=1, trials=1, arms=("autobridge_cold",),
        engine_commit="a", generator_commit="b", frozen_at_utc="2030-01-01T00:00:00+00:00",
    )
    monkeypatch.setattr(
        beacon_run.beacon_client, "fetch_chain_info", lambda _base_url: dict(FAKE_INFO)
    )
    monkeypatch.setattr(
        beacon_run.beacon_client, "fetch_latest",
        lambda _b: {"round": 1, "randomness": "ab" * 32,
                          "signature": "cd" * 96, "previous_signature": "ef" * 96},
    )
    with pytest.raises(BeaconError):
        beacon_run.run_beacon(protocol, output_dir=tmp_path / "r1")
    out_dir = tmp_path / "r2"
    out_dir.mkdir()
    (out_dir / "summary.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(BeaconError):
        beacon_run.run_beacon(protocol, output_dir=out_dir)


def test_verify_repro_needs_published_file(tmp_path: pathlib.Path) -> None:
    with pytest.raises(BeaconError):
        beacon_run.verify_repro(123456, output_dir=tmp_path / "nope")


def test_summarize_grades_math() -> None:
    grades = [
        beacon_run.BeaconArmGrade(arm="a", instance_index=0, trial=t,
                                  vendor="atlas", family="rename",
                                  verified_completion=t % 2)
        for t in range(5)
    ]
    summary = beacon_run.summarize_grades(grades)
    assert summary["overall"]["n"] == 5
    block = summary["overall"]["verified_completion"]
    assert block["n"] == 5 and abs(block["mean"] - 0.4) < 1e-9
    assert summary["by_arm"]["a"]["wrong_patch_released"] == {"total": 0}


def test_main_freeze_and_run_guardrails(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(beacon_run, "_git_dirty", lambda: False)
    monkeypatch.setattr(
        beacon_run.beacon_client, "fetch_chain_info", lambda _base_url: dict(FAKE_INFO)
    )
    monkeypatch.setattr(beacon_run, "_git_commit", lambda: "abc123")
    # Redirect the freeze target: the real experiments/beacon/ must never
    # be touched by a unit test.
    beacon_dir = tmp_path / "experiments" / "beacon"
    monkeypatch.setattr(beacon_run, "BEACON_DIR", beacon_dir)
    monkeypatch.setattr(beacon_run, "PROTOCOL_PATH", beacon_dir / "protocol.md")
    monkeypatch.setattr(
        beacon_run.beacon_client, "fetch_latest",
        lambda _b: {"round": 1, "randomness": "ab" * 32,
                          "signature": "cd" * 96, "previous_signature": "ef" * 96},
    )
    assert beacon_run.main(["freeze", "--round-after", "2030-01-01T00:00:00+00:00",
                            "--faults", "2", "--trials", "1"]) == 0
    assert (beacon_dir / "protocol.md").is_file()
    # Second freeze refuses (commit-once).
    assert beacon_run.main(["freeze", "--round-after", "2030-01-01T00:00:00+00:00",
                            "--faults", "2"]) == 1
    # Run refuses: the target round is unpublished (mocked latest is 1).
    assert beacon_run.main(["run", "--protocol", str(beacon_dir / "protocol.md")]) == 1
