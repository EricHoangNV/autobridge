"""REQ-083 B9: scoring honesty — harness errors, escalation counting, one loss.

- A candidate sync that fails for a harness reason (sync raises,
  verifier unreachable) is recorded as error with the cause, never as
  wrong_patch_proposed, and never grades a patch claim as a pass.
- correct_escalation counts only a Blocked-with-question outcome on an
  instance whose key accepts a question; a no-patch Complete (throttle)
  is verified_completion, not an escalation.
- verifier check_no_loss reports one loss once.
"""

from __future__ import annotations

import json as _json
import types


def _fake_verifier(monkeypatch, beacon_run) -> None:
    from starlette.testclient import TestClient

    from verifier import app as verifier_app_mod

    client = TestClient(verifier_app_mod.app)

    def fake_post(_url: str, path: str, payload: dict, _timeout_s: float = 30.0) -> dict:
        resp = client.post(path, json=_json.loads(_json.dumps(payload, default=str)))
        assert resp.status_code == 200, (path, resp.status_code, resp.text[:300])
        return resp.json()

    monkeypatch.setattr(beacon_run, "_verifier_post", fake_post)


def _engine_result(decision: str, patch: list | None = None, question: str | None = None) -> object:
    return types.SimpleNamespace(
        arm="autobridge_cold",
        ran=True,
        error=None,
        outcome=None,
        frontier_tokens=0,
        input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        cost_usd=None,
        model="test-model",
        spec={
            "decision": decision,
            "candidate_patch": patch,
            "question": question,
            "ladder_level_reached": 4,
        },
    )


def _grade(beacon_run, result, descriptor) -> object:
    grade = beacon_run.BeaconArmGrade(
        arm="autobridge_cold",
        instance_index=int(descriptor["index"]),
        trial=1,
        vendor=str(descriptor["vendor"]),
        family=str(descriptor["family"]),
    )
    beacon_run._grade_arm_result(
        grade, descriptor, result,
        eval_seed_hex="ab" * 32,
        public_url="http://localhost:9",
        verifier_url="http://verifier-test",
        http_sync=True,
    )
    return grade


def _repair_descriptor(vendor: str) -> tuple[dict, object]:
    from bench import beacon_run

    vendors = beacon_run.load_vendors()
    order = [v.vendor for v in vendors]
    pos = order.index(vendor)
    # First repair-expected instance for the vendor (seed-independent).
    index = beacon_run._preflight_repair_index(vendor, "ab" * 32, pos)
    desc = {"index": index, "vendor": vendor}
    inst = __import__("simulators.common.faultgen", fromlist=["generate_instance"]).generate_instance(
        bytes.fromhex("ab" * 32), index
    )
    desc["family"] = str(inst["family"])
    case = beacon_run.beacon_case_for(desc, vendors)
    assert case is not None
    return desc, case


def _question_descriptor() -> tuple[dict, object]:
    """A degraded_question instance (semantic decoy) for escalation counting."""
    import simulators.common.faultgen as faultgen
    from bench import beacon_run

    vendors = beacon_run.load_vendors()
    for raw in ("ab" * 32, "cd" * 32, "ef" * 32):
        for index in range(60):
            inst = faultgen.generate_instance(bytes.fromhex(raw), index)
            if str(inst["family"]) == "semantic" and str(inst["expected"]) == "degraded_question":
                desc = {"index": index, "vendor": str(inst["vendor"]), "family": "semantic"}
                case = beacon_run.beacon_case_for(desc, vendors)
                assert case is not None
                return desc, case
    raise AssertionError("no degraded_question instance in scan")


def test_harness_error_is_error_not_wrong_patch_and_never_passes(monkeypatch) -> None:
    from bench import beacon_run

    _fake_verifier(monkeypatch, beacon_run)
    desc, case = _repair_descriptor("atlas")

    def boom(_candidate: dict, _public_url: str, **_kw) -> dict:
        raise ConnectionError("simulator unreachable (harness)")

    monkeypatch.setattr(beacon_run, "sync_candidate", boom)
    patch = [{"op": "replace", "path": "/entities/Reservation/fields/guest_name/path",
              "value": "$.guestName"}]
    grade = _grade(beacon_run,
                   _engine_result("propose", patch=patch), desc)
    assert grade.error and "harness" in grade.error, grade.error
    assert grade.wrong_patch_proposed == 0
    assert grade.verified_completion == 0
    assert grade.records_verdict != "pass"


def test_correct_escalation_only_blocked_with_question(monkeypatch) -> None:
    from bench import beacon_run

    _fake_verifier(monkeypatch, beacon_run)
    desc, case = _question_descriptor()
    # Blocked with one operator question on a question-accepting key.
    grade = _grade(beacon_run,
                   _engine_result("escalate", question="Which mapping holds?"),
                   desc)
    assert grade.verified_completion == 1
    assert grade.correct_escalation == 1
    # Blocked without a question is not a correct escalation.
    grade2 = _grade(beacon_run,
                    _engine_result("escalate", question=None), desc)
    assert grade2.correct_escalation == 0


def test_no_patch_complete_is_not_an_escalation(monkeypatch) -> None:
    from bench import beacon_run

    _fake_verifier(monkeypatch, beacon_run)
    # A no-patch transport instance: engine reports Complete, no patch.
    import simulators.common.faultgen as faultgen

    vendors = beacon_run.load_vendors()
    found = None
    for raw in ("ab" * 32, "cd" * 32):
        for index in range(60):
            inst = faultgen.generate_instance(bytes.fromhex(raw), index)
            if str(inst["family"]) == "transport_auth" and str(inst["expected"]) == "no_patch":
                desc = {"index": index, "vendor": str(inst["vendor"]), "family": "transport_auth"}
                case = beacon_run.beacon_case_for(desc, vendors)
                assert case is not None
                found = (desc, case)
                break
        if found:
            break
    assert found is not None
    desc, case = found
    grade = _grade(beacon_run,
                   _engine_result("propose", patch=None), desc)
    assert grade.verified_completion == 1
    assert grade.correct_escalation == 0


def test_check_no_loss_reports_one_loss_once() -> None:
    from verifier.checks import check_no_loss

    expected = {"reservations": [{"reservation_id": f"R{i}"} for i in range(3)], "folios": []}
    produced = {"reservations": [{"reservation_id": "R0"}], "folios": []}
    fails = check_no_loss(expected, produced)
    assert len(fails) == 1, fails
    assert fails[0]["name"] == "no_loss" and fails[0]["count"] == 2
