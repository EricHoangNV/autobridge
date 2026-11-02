"""REQ-086 (verifier side): ``gross_net`` accepts ``degraded_question`` only.

``minor_major`` stays ``repair`` (``minor_units: true`` is expressible);
the decoy branch is unchanged. The recoverability proof shows the
scaling manifested instead of asserting a Python inverse.
"""

from __future__ import annotations

import copy
import hashlib

import pytest

from simulators.common import faultgen
from verifier import beacon as vbeacon
from verifier.beacon import (
    GeneratorBug,
    accepted_outcomes_for,
    grade_arm_outcome,
    reference_solve,
)


def _seeds(k: int) -> list[bytes]:
    return [hashlib.sha256(f"beacon-req086-{i}".encode()).digest() for i in range(k)]


def _of_kind(kind: str) -> list[dict]:
    out = []
    for raw in _seeds(40):
        for inst in faultgen.generate_instances(raw, 5):
            if str(inst["family"]) == "semantic" and str(dict(inst["params"]).get("kind")) == kind:
                out.append(inst)
    assert out, f"no semantic/{kind} instance in sweep"
    return out


def test_accepted_outcomes_for_all_semantic_kinds() -> None:
    for inst in _of_kind("gross_net"):
        assert accepted_outcomes_for(inst) == {"degraded_question"}
    for inst in _of_kind("minor_major"):
        assert accepted_outcomes_for(inst) == {"repair", "degraded_question"}
    for inst in _of_kind("decoy"):
        assert accepted_outcomes_for(inst) == {"degraded_question", "no_patch"}


def test_reference_solve_gross_net_is_a_question() -> None:
    for inst in _of_kind("gross_net")[:5]:
        assert reference_solve(inst) == "degraded_question"


def test_grading_question_on_gross_net() -> None:
    inst = _of_kind("gross_net")[0]
    for outcome in ("Degraded", "Blocked"):
        passed, reason = grade_arm_outcome(
            inst, {"outcome": outcome, "patch_reached_release": False, "operator_question": True}
        )
        assert passed, reason
    # The output is wrong and a guessed factor is a wrong patch: neither
    # a no-patch Complete nor a released patch passes.
    assert not grade_arm_outcome(
        inst, {"outcome": "Complete", "patch_reached_release": False, "operator_question": False}
    )[0]
    assert not grade_arm_outcome(
        inst, {"outcome": "Complete", "patch_reached_release": True, "operator_question": False}
    )[0]


def test_semantic_scaled_proof_fails_closed() -> None:
    inst = _of_kind("gross_net")[0]
    good = faultgen.materialize(inst)["dataset"]
    with pytest.raises(GeneratorBug):
        vbeacon._assert_semantic_scaled(
            {**inst, "params": {"kind": "gross_net", "factor": "xx"}}, good
        )
    with pytest.raises(GeneratorBug):
        vbeacon._assert_semantic_scaled(inst, {**good, "reservations": []})
    bad_shape = {
        "reservations": ["not-a-dict", *list(good["reservations"][1:])],
        "folios": list(good["folios"]),
    }
    with pytest.raises(GeneratorBug):
        vbeacon._assert_semantic_scaled(inst, bad_shape)
    other_key = copy.deepcopy(good)
    other_key["reservations"][0] = dict(other_key["reservations"][0], status="XX-NOPE")
    with pytest.raises(GeneratorBug):
        vbeacon._assert_semantic_scaled(inst, other_key)
    non_str = copy.deepcopy(good)
    non_str["reservations"][0] = dict(non_str["reservations"][0], total_amount=12.5)
    with pytest.raises(GeneratorBug):
        vbeacon._assert_semantic_scaled(inst, non_str)
    base = faultgen.beacon_dataset(seed=int(inst["dataset_seed"]))
    unscaled = copy.deepcopy(good)
    unscaled["reservations"][0] = dict(
        unscaled["reservations"][0], total_amount=base["reservations"][0]["total_amount"]
    )
    with pytest.raises(GeneratorBug):
        vbeacon._assert_semantic_scaled(inst, unscaled)
    unparseable = copy.deepcopy(good)
    unparseable["reservations"][0] = dict(unparseable["reservations"][0], total_amount="xx")
    with pytest.raises(GeneratorBug):
        vbeacon._assert_semantic_scaled(inst, unparseable)
    with pytest.raises(GeneratorBug):
        faultgen._invert_semantic(
            {"reservations": [{"total_amount": "1.00"}], "folios": [], "properties": []},
            {"kind": "gross_net", "factor": "0.5"},
        )
    saved = faultgen.materialize
    try:
        faultgen.materialize = lambda _i: {"dataset": unscaled, "behavior": None}  # type: ignore[return-value]
        with pytest.raises(GeneratorBug):
            reference_solve(dict(inst))
    finally:
        faultgen.materialize = saved
