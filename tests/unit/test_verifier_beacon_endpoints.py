"""Verifier beacon endpoint tests (REQ-025 items 5/7, verifier side).

The verifier regenerates instances from seed + index (sanctioned
``simulators/common`` import) and judges arm outputs: produced records
(``/beacon/records``: canonical set, no loss/duplicates, exact money,
property-local business dates, canonical status vocabulary, plus
contract coverage by the candidate spec) and reported outcome classes
(``/beacon/grade``). Responses carry aggregates only — never fixture
rows, never the answer key beyond the computable accepted set.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import simulators.common.faultgen as faultgen
from verifier import app as verifier_app
from verifier import beacon as beacon_mod

EVAL_SEED = faultgen.derive_eval_seed("ab" * 32)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(verifier_app.app)


def _repair_instance() -> dict:
    for index in range(30):
        inst = faultgen.generate_instance(EVAL_SEED, index)
        if inst["expected"] == "repair" and inst["family"] in ("rename", "nesting", "code_list"):
            return inst
    raise AssertionError("no shape-repair instance found")


def _escalate_instance() -> dict:
    for index in range(30):
        inst = faultgen.generate_instance(EVAL_SEED, index)
        if inst["expected"] == "escalate":
            return inst
    raise AssertionError("no escalate instance found")


def _records_payload(instance: dict, mutated: bool = False) -> dict:
    dataset = faultgen.materialize(instance)["dataset"] if mutated else beacon_mod.baseline_for(instance)
    return {
        "reservations": dataset["reservations"],
        "folios": dataset["folios"],
    }


def test_judge_baseline_records_pass() -> None:
    instance = _repair_instance()
    judged = beacon_mod.judge_produced_records(instance, _records_payload(instance))
    assert judged["verdict"] == "pass"
    assert judged["failed_checks"] == []


def test_judge_mutated_records_fail() -> None:
    instance = _repair_instance()
    judged = beacon_mod.judge_produced_records(instance, _records_payload(instance, mutated=True))
    assert judged["verdict"] == "fail"
    assert judged["failed_checks"] != []


def test_judge_records_for_escalate_fail_closed() -> None:
    instance = _escalate_instance()
    judged = beacon_mod.judge_produced_records(instance, _records_payload(instance))
    assert judged["verdict"] == "fail"
    assert judged["failed_checks"][0]["name"] == "records_for_escalate"


def test_judge_rejects_malformed_records() -> None:
    instance = _repair_instance()
    with pytest.raises(ValueError):
        beacon_mod.judge_produced_records(instance, {"reservations": "nope", "folios": []})


def test_contract_coverage_requires_new_names() -> None:
    instance = _repair_instance()
    baseline_records = _records_payload(instance)
    judged = beacon_mod.judge_produced_records(instance, baseline_records, spec={"operations": {}})
    assert judged["verdict"] == "fail"
    names = {f["name"] for f in judged["failed_checks"]}
    assert "beacon_contract_uncovered" in names


def test_records_endpoint_round_trip(client: TestClient) -> None:
    instance = _repair_instance()
    body = {
        "eval_seed_hex": EVAL_SEED.hex(),
        "index": instance["index"],
        "records": _records_payload(instance),
    }
    r = client.post("/beacon/records", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "pass"
    # Mutated records fail through HTTP too.
    body["records"] = _records_payload(instance, mutated=True)
    r = client.post("/beacon/records", json=body)
    assert r.status_code == 200
    assert r.json()["verdict"] == "fail"


def test_records_endpoint_never_leaks_fixtures(client: TestClient) -> None:
    instance = _repair_instance()
    r = client.post(
        "/beacon/records",
        json={"eval_seed_hex": EVAL_SEED.hex(), "index": instance["index"],
              "records": _records_payload(instance, mutated=True)},
    )
    assert r.status_code == 200
    text = r.text
    assert "reservations" not in text or '"reservations":[]' in text or "record_set_mismatch" in text
    assert text.count("RES-") <= 5 and text.count("CHG-") <= 5
    payload = r.json()
    assert set(payload) == {"verdict", "score", "failed_checks", "index"}
    for failed in payload["failed_checks"]:
        assert set(failed) <= {"name", "count", "details"}


def test_records_endpoint_rejects_bad_seed(client: TestClient) -> None:
    r = client.post("/beacon/records", json={"eval_seed_hex": "zz", "index": 0, "records": {}})
    assert r.status_code == 400
    r = client.post("/beacon/records", json={"eval_seed_hex": EVAL_SEED.hex(), "index": -1, "records": {}})
    assert r.status_code == 400


def test_grade_endpoint_outcome_classes(client: TestClient) -> None:
    repair = _repair_instance()
    ok = client.post(
        "/beacon/grade",
        json={"eval_seed_hex": EVAL_SEED.hex(), "index": repair["index"], "outcome": "Complete",
              "patch_reached_release": True, "operator_question": False},
    )
    assert ok.status_code == 200
    assert ok.json()["passed"] is True
    no_patch = dict(ok.json())
    assert no_patch is not None
    # Complete without a patch is not a repair.
    bad = client.post(
        "/beacon/grade",
        json={"eval_seed_hex": EVAL_SEED.hex(), "index": repair["index"], "outcome": "Complete",
              "patch_reached_release": False, "operator_question": False},
    )
    assert bad.json()["passed"] is False
    # Unknown outcome names fail closed.
    unknown = client.post(
        "/beacon/grade",
        json={"eval_seed_hex": EVAL_SEED.hex(), "index": repair["index"], "outcome": "Splendid",
              "patch_reached_release": False, "operator_question": False},
    )
    assert unknown.json()["passed"] is False


def test_grade_accepted_set_echoes_computable_key(client: TestClient) -> None:
    instance = _repair_instance()
    r = client.post(
        "/beacon/grade",
        json={"eval_seed_hex": EVAL_SEED.hex(), "index": instance["index"], "outcome": "Blocked",
              "patch_reached_release": False, "operator_question": False},
    )
    assert r.status_code == 200
    assert r.json()["accepted"] == sorted(beacon_mod.accepted_outcomes_for(instance))
