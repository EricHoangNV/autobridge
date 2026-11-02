"""REQ-086 (bench side): no reference patch for ``gross_net``, and the
preflight passes over a round that draws one.

``gross_net`` is keyed ``degraded_question``, so ``reference_patch_for``
returns ``None`` for it and ``preflight_prove_repairs`` (which only
proves ``repair`` instances) never asks for one.
"""

from __future__ import annotations

from bench import beacon_run
from simulators.common import faultgen


def test_reference_patch_returns_none_for_gross_net() -> None:
    from bench import reference_patch as refpatch

    vendors = beacon_run.load_vendors()
    seen = 0
    for n in range(10):
        seed = bytes.fromhex(f"{n:064x}")
        for inst in faultgen.generate_instances(seed, 5):
            if str(inst["family"]) != "semantic" or str(dict(inst["params"]).get("kind")) != "gross_net":
                continue
            seen += 1
            spec = beacon_run.engine_spec_for(
                {"index": int(inst["index"]), "vendor": str(inst["vendor"])}, vendors
            )
            assert spec is not None
            assert refpatch.reference_patch_for(inst, spec) is None
    assert seen > 0


def test_preflight_passes_over_round_with_gross_net() -> None:
    target_hex = None
    gross_index = None
    for n in range(60):
        seed_hex = f"{n:064x}"
        for index in range(10):
            inst = faultgen.generate_instance(bytes.fromhex(seed_hex), index)
            if str(inst["family"]) == "semantic" and str(dict(inst["params"]).get("kind")) == "gross_net":
                target_hex, gross_index = seed_hex, index
                break
        if target_hex is not None:
            break
    assert target_hex is not None, "no 10-draw round contains gross_net"
    assert gross_index is not None
    assert str(faultgen.generate_instance(bytes.fromhex(target_hex), gross_index)["expected"]) == "degraded_question"

    protocol = beacon_run.BeaconProtocol(
        round_after_utc="2026-11-03T00:00:00+00:00",
        target_round=6603643,
        faults=10,
        trials=1,
        arms=("autobridge_cold",),
        engine_commit="c" * 40,
        generator_commit="d" * 40,
        frozen_at_utc="2026-11-03T00:00:00+00:00",
        rehearsal=True,
        frontier_model="muse-spark-1.3",
        agent_model=None,
        token_ceiling=200000,
        wall_clock_s=600.0,
    )
    with beacon_run._placeholder_credential_env():
        report = beacon_run.preflight_prove_repairs(
            protocol, eval_seed_hex=target_hex, verifier_url=None,
        )
    assert report["proved"], "round draws repair instances and none were proven"
    assert gross_index not in report["proved"]
    assert all(
        str(faultgen.generate_instance(bytes.fromhex(target_hex), i)["expected"]) != "repair"
        or i in report["proved"]
        for i in range(10)
    )
