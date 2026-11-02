"""REQ-086 (generator side): ``semantic/gross_net`` keys ``degraded_question``.

The mutation scales the served amounts in place by the factor and leaves
no gross source on the wire; the closed transform set has no factor
argument, so the key is a question. ``minor_major`` stays ``repair``;
the decoy branch is unchanged.
"""

from __future__ import annotations

import hashlib

from simulators.common import faultgen


def _of_kind(kind: str) -> list[dict]:
    out = []
    for raw in [hashlib.sha256(f"beacon-req086-{i}".encode()).digest() for i in range(40)]:
        for inst in faultgen.generate_instances(raw, 5):
            if str(inst["family"]) == "semantic" and str(dict(inst["params"]).get("kind")) == kind:
                out.append(inst)
    assert out, f"no semantic/{kind} instance in sweep"
    return out


def test_expected_for_all_semantic_kinds() -> None:
    for inst in _of_kind("gross_net"):
        assert faultgen.expected_for("semantic", dict(inst["params"])) == "degraded_question"
        assert str(inst["expected"]) == "degraded_question"
    for inst in _of_kind("minor_major"):
        assert faultgen.expected_for("semantic", dict(inst["params"])) == "repair"
        assert str(inst["expected"]) == "repair"
    for inst in _of_kind("decoy"):
        assert faultgen.expected_for("semantic", dict(inst["params"])) == "degraded_question"
        assert str(inst["expected"]) == "degraded_question"
