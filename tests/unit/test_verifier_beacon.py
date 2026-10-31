"""REQ-025 item 2: beacon answer keys, reference solver and grading (verifier package).

Property tests: the reference solver reaches the expected outcome class on
every generated instance (hundreds of seeds); a failure is a generator bug.
Answer keys carry aggregates only, never fixture rows. Arm grading is a
pure predicate with fail-closed handling of unknown outcomes.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

import pytest

from simulators.common import faultgen
from simulators.common.faultgen import (
    GeneratorBug,
    derive_eval_seed,
    generate_instances,
    materialize,
)
from simulators.common.generator import generate_dataset
from verifier import beacon
from verifier.beacon import (
    accepted_outcomes_for,
    answer_key_for,
    baseline_for,
    grade_arm_outcome,
    reference_solve,
)


def _seeds(k: int) -> list[bytes]:
    return [hashlib.sha256(f"beacon-verifier-{i}".encode()).digest() for i in range(k)]


def _find(pred: object, n: int = 200) -> dict[str, object]:
    """First instance satisfying ``pred`` across seeds (decoy-class outcomes
    are rare per seed, so single-seed ``next()`` is flaky by construction)."""
    assert callable(pred)
    for raw in _seeds(n):
        for inst in generate_instances(raw, 5):
            if pred(inst):  # type: ignore[operator]
                return inst  # type: ignore[return-value]
    raise AssertionError("no matching instance in sweep")


# ---------------------------------------------------------------------------
# Reference solver: no impossible questions
# ---------------------------------------------------------------------------


def test_reference_solve_reaches_expected_everywhere() -> None:
    counts: dict[str, int] = {}
    for raw in _seeds(100):
        for inst in generate_instances(raw, 5):
            assert reference_solve(inst) == inst["expected"]
            counts[str(inst["expected"])] = counts.get(str(inst["expected"]), 0) + 1
    assert set(counts) == {"repair", "no_patch", "escalate", "degraded_question"}


def test_reference_solve_repair_matches_regenerated_baseline() -> None:
    for raw in _seeds(20):
        for inst in generate_instances(raw, 5):
            if str(inst["expected"]) != "repair":
                continue
            mat = materialize(inst)
            faultgen.inverse_canonicalize(inst, mat["dataset"])
            assert baseline_for(inst) == generate_dataset(seed=int(inst["dataset_seed"]))


# ---------------------------------------------------------------------------
# Answer keys: aggregates only
# ---------------------------------------------------------------------------


def test_answer_key_has_no_fixture_rows() -> None:
    for raw in _seeds(10):
        for inst in generate_instances(raw, 5):
            key = answer_key_for(inst)
            assert key["instance_index"] == inst["index"]
            assert key["vendor"] == inst["vendor"]
            assert key["expected"] == inst["expected"]
            truth = key["truth"]
            assert isinstance(truth["n_reservations"], int) and truth["n_reservations"] > 0
            assert isinstance(truth["n_folios"], int) and truth["n_folios"] > 0
            Decimal(str(truth["money_total"]))
            assert len(str(truth["record_keys_hash"])) == 64
            blob = repr(key)
            assert "RES-" not in blob and "CHG-" not in blob


def test_answer_key_deterministic() -> None:
    seed = derive_eval_seed("ab" * 32)
    inst = generate_instances(seed, 5)[2]
    assert answer_key_for(inst) == answer_key_for(inst)


def test_accepted_outcomes_semantic_ambiguity() -> None:
    for raw in _seeds(30):
        for inst in generate_instances(raw, 5):
            accepted = accepted_outcomes_for(inst)
            assert str(inst["expected"]) in accepted
            params = dict(inst["params"])
            if str(inst["family"]) == "semantic" and str(inst["expected"]) == "repair":
                assert accepted == {"repair", "degraded_question"}
            elif (
                str(inst["family"]) == "semantic"
                and str(inst["expected"]) == "degraded_question"
                and params.get("kind") == "decoy"
            ):
                # REQ-063 item 3: the connector ignores the decoy field, so a
                # correct Complete with no patch is accepted alongside the
                # operator question.
                assert accepted == {"degraded_question", "no_patch"}
            else:
                assert accepted == {str(inst["expected"])}


# ---------------------------------------------------------------------------
# Arm grading
# ---------------------------------------------------------------------------


def _arm(outcome: str, patched: bool = False, question: bool = False) -> dict[str, object]:
    return {"outcome": outcome, "patch_reached_release": patched, "operator_question": question}


def test_grade_repair_pass_and_fail() -> None:
    repair = _find(lambda i: i["expected"] == "repair" and i["family"] != "semantic")
    assert grade_arm_outcome(repair, _arm("Complete", patched=True))[0]
    for bad in (
        _arm("Complete", patched=False),
        _arm("Blocked", patched=False),
        _arm("Degraded", patched=False, question=True),
        _arm("Complete", patched=True) | {"outcome": "Complete!"},
    ):
        assert not grade_arm_outcome(repair, bad)[0]


def test_grade_no_patch_pass_and_fail() -> None:
    inst = _find(lambda i: i["expected"] == "no_patch")
    assert grade_arm_outcome(inst, _arm("Complete"))[0]
    assert not grade_arm_outcome(inst, _arm("Complete", patched=True))[0]
    assert not grade_arm_outcome(inst, _arm("Blocked"))[0]


def test_grade_escalate_pass_and_fail() -> None:
    inst = _find(lambda i: i["expected"] == "escalate")
    assert grade_arm_outcome(inst, _arm("Blocked"))[0]
    # REQ-063 item 2: escalate instances keep passing with or without a question.
    assert grade_arm_outcome(inst, _arm("Blocked", question=True))[0]
    assert not grade_arm_outcome(inst, _arm("Complete"))[0]
    assert not grade_arm_outcome(inst, _arm("Blocked", patched=True))[0]


def test_grade_degraded_question_pass_and_fail() -> None:
    inst = _find(lambda i: i["expected"] == "degraded_question")
    assert grade_arm_outcome(inst, _arm("Degraded", question=True))[0]
    assert not grade_arm_outcome(inst, _arm("Degraded", question=False))[0]
    assert not grade_arm_outcome(inst, _arm("Degraded", question=True, patched=True))[0]


def test_grade_decoy_accepts_complete_no_patch() -> None:
    """REQ-063 item 3: the decoy leaves the connector output correct, so
    Complete with no patch passes alongside the operator question."""
    inst = _find(lambda i: i["expected"] == "degraded_question")
    assert grade_arm_outcome(inst, _arm("Complete", patched=False))[0]
    assert not grade_arm_outcome(inst, _arm("Complete", patched=True))[0]


def test_grade_degraded_question_accepts_blocked_with_question() -> None:
    """REQ-063 item 2: a no-patch escalation carrying one operator question
    passes a degraded_question instance whether reported Degraded or
    Blocked (frontier agents report Blocked with a question)."""
    inst = _find(lambda i: i["expected"] == "degraded_question")
    passed, reason = grade_arm_outcome(inst, _arm("Blocked", question=True))
    assert passed, reason
    assert not grade_arm_outcome(inst, _arm("Blocked", question=False))[0]
    assert not grade_arm_outcome(inst, _arm("Blocked", question=True, patched=True))[0]


def test_grade_semantic_repair_accepts_correct_escalation() -> None:
    inst = _find(lambda i: i["family"] == "semantic" and i["expected"] == "repair")
    assert grade_arm_outcome(inst, _arm("Complete", patched=True))[0]
    assert grade_arm_outcome(inst, _arm("Degraded", question=True))[0]
    assert not grade_arm_outcome(inst, _arm("Blocked"))[0]


def test_grade_unknown_outcome_fails_closed() -> None:
    seed = derive_eval_seed("ab" * 32)
    inst = generate_instances(seed, 5)[0]
    passed, reason = grade_arm_outcome(inst, _arm("Splendid", patched=True))
    assert not passed
    assert "unknown arm outcome" in reason


# ---------------------------------------------------------------------------
# Solver asserts triggers (unit-level, no sweep)
# ---------------------------------------------------------------------------


def test_solver_helpers_fail_closed() -> None:
    with pytest.raises(GeneratorBug):
        beacon._assert_trap_present({"reservations": [], "folios": []}, {"decoy_field": "net_amount"})
    with pytest.raises(GeneratorBug):
        beacon._assert_trap_present(
            {"reservations": [{"total_amount": None, "net_amount": "1.00"}], "folios": []},
            {"decoy_field": "net_amount"},
        )
    with pytest.raises(GeneratorBug):
        beacon._assert_trap_present(
            {"reservations": [{"total_amount": "xx", "net_amount": "1.00"}], "folios": []},
            {"decoy_field": "net_amount"},
        )
    with pytest.raises(GeneratorBug):
        beacon._assert_trap_present(
            {"reservations": [{"total_amount": "10.00", "net_amount": "xx"}], "folios": []},
            {"decoy_field": "net_amount"},
        )
    with pytest.raises(GeneratorBug):
        beacon._assert_recoverable(None, "transport_auth")
    with pytest.raises(GeneratorBug):
        beacon._assert_recoverable({"family": "transport_auth", "kind": "token_revoked"}, "transport_auth")
    with pytest.raises(GeneratorBug):
        beacon._assert_recoverable(
            {"family": "transport_auth", "kind": "outage", "fail_first_m": 50}, "transport_auth"
        )
    with pytest.raises(GeneratorBug):
        beacon._assert_recoverable({"family": "fias_link", "kind": "telepathy"}, "fias_link")
    with pytest.raises(GeneratorBug):
        beacon._assert_recoverable({"family": "pagination"}, "pagination")
    with pytest.raises(GeneratorBug):
        beacon._assert_terminal({}, None, "transport_auth", {})
    with pytest.raises(GeneratorBug):
        beacon._assert_terminal(
            {}, {"family": "transport_auth", "kind": "throttle", "every_k": 2, "retry_after_s": 1},
            "transport_auth", {"kind": "throttle"},
        )
    with pytest.raises(GeneratorBug):
        beacon._assert_terminal({}, None, "code_list", {"mapping": {}})
    with pytest.raises(GeneratorBug):
        beacon._assert_terminal(
            {"reservations": [{"status": "ZX"}], "folios": []},
            None, "code_list", {"mapping": {"ZX": "confirmed"}, "unknown_code": "ZX"},
        )
    with pytest.raises(GeneratorBug):
        beacon._assert_terminal(
            {"reservations": [{"status": "confirmed"}], "folios": []},
            None, "code_list", {"mapping": {"ZX": "confirmed"}, "unknown_code": "ZX"},
        )
    with pytest.raises(GeneratorBug):
        beacon._assert_terminal({}, None, "rename", {})
    assert beacon._money_total([{"amount": "xx"}, {"amount": "1.25"}, {}], "amount") == Decimal("1.25")


def test_recoverable_rejects_lying_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    behavior = {"family": "transport_auth", "kind": "token_expired"}
    monkeypatch.setattr(
        faultgen, "transport_response", lambda _b, _c, **_kw: (200, {}, False)
    )
    with pytest.raises(GeneratorBug):
        beacon._assert_recoverable(behavior, "transport_auth")
    monkeypatch.setattr(
        faultgen, "transport_response", lambda _b, _c, **_kw: (401, {}, False)
    )
    with pytest.raises(GeneratorBug):
        beacon._assert_recoverable(behavior, "transport_auth")
    with pytest.raises(ValueError):
        beacon.reference_solve(
            {
                "vendor": "atlas",
                "family": "rename",
                "index": 0,
                "params": {},
                "seed_hex": "ab" * 32,
                "dataset_seed": 1,
                "expected": "mystery",
            }
        )
