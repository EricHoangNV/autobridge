"""REQ-083 B6: pagination renames are announced in the served vendor docs.

The drawn request param names are otherwise unobservable (the simulator
ignores unknown params, yet the verifier demands the exact tokens), so
the served vendor docs — the same docs every arm reads — must describe
the new parameters for the faulted round, and no arm may get them any
other way.
"""

from __future__ import annotations

import pytest

from bench import beacon_run
from bench import faults as faults

# (seed, index, vendor): beacon pagination instances with distinctive
# drawn names (deterministic draws).
_PAGINATION_CASES = [
    ("22" * 32, 5, "atlas"),  # offset/from/pageSize/data
]


def _pagination_instance(seed_hex: str, index: int):
    import simulators.common.faultgen as faultgen

    inst = faultgen.generate_instance(bytes.fromhex(seed_hex), index)
    assert str(inst["family"]) == "pagination", inst
    return inst


def _request_param_values(inst) -> list[str]:
    params = dict(inst["params"])
    values = []
    for key in ("offset_param", "limit_param", "page_param", "per_page_param",
                "cursor_param", "cursor_leaf", "page_size_param"):
        value = params.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    return values


@pytest.mark.parametrize("seed_hex,index,vendor", _PAGINATION_CASES)
def test_served_docs_announce_new_pagination_params(seed_hex: str, index: int, vendor: str) -> None:
    inst = _pagination_instance(seed_hex, index)
    text = beacon_run.beacon_served_docs(vendor, seed_hex, index)
    assert text, f"no served docs for {vendor}"
    for value in _request_param_values(inst):
        assert value in text, f"served docs do not announce {value!r}"
    # No announcement without a faulted round: the static docs stay clean.
    vendors = beacon_run.load_vendors()
    spec = beacon_run.engine_spec_for({"index": index, "vendor": vendor}, vendors)
    assert spec is not None
    clean = beacon_run.unfaulted_baseline_records(vendor, seed_hex, index, spec)
    assert clean is not None


def test_announcement_absent_without_pagination_fault() -> None:
    from simulators.common import beacon_serve as _beacon

    with faults.isolated_sim_state():
        assert _beacon.pagination_docs_note("atlas") is None
        assert _beacon.pagination_docs_note("harbor") is None


@pytest.mark.parametrize("seed_hex,index,vendor", _PAGINATION_CASES)
def test_new_request_params_reach_arms_only_via_docs(seed_hex: str, index: int, vendor: str) -> None:
    """Leak audit: drawn request params ride the served docs (and the
    frontier context built from them), never the fault evidence or the
    engine diagnostics."""
    import json as _json

    inst = _pagination_instance(seed_hex, index)
    values = _request_param_values(inst)
    assert values
    vendors = beacon_run.load_vendors()
    desc = {"index": index, "vendor": vendor}
    spec = beacon_run.engine_spec_for(desc, vendors)
    assert spec is not None
    case = beacon_run.beacon_case_for(desc, vendors)
    assert case is not None
    from bench.harness import make_trial_inputs

    inputs = make_trial_inputs(beacon_run.default_beacon_config(), f"beacon:{index}", 1)
    ctx = beacon_run.beacon_bench_context(desc, case, inputs, seed_hex)
    for value in values:
        assert value in ctx.docs_text, f"{value!r} missing from arm docs"
    # Evidence (served response samples) and engine diagnostics carry no
    # request param names.
    assert isinstance(ctx.evidence_text, str)
    for value in values:
        assert value not in ctx.evidence_text, f"{value!r} leaked into arm evidence"
    diag = beacon_run.derive_beacon_diagnostics(
        dict(spec), descriptor=desc, eval_seed_hex=seed_hex,
        vendor=vendor, connector_id=str(case.connector_id),
    )
    blob = _json.dumps(diag, default=str) if not hasattr(diag, "__dict__") else _json.dumps(
        {k: getattr(diag, k) for k in dir(diag) if not k.startswith("_")}, default=str
    )
    for value in values:
        assert value not in blob, f"{value!r} leaked into engine diagnostics"
