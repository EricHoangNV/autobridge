"""REQ-083 simulator serving: baseline pin, pagination docs note, contracts."""

from __future__ import annotations


def _beacon_index(vendor: str, family: str, seed_hex: str = "ab" * 32) -> tuple[str, int, dict]:
    import simulators.common.faultgen as faultgen

    for index in range(60):
        if index % 5 != {"atlas": 0, "harbor": 1, "legacystay": 2, "meridian": 3}[vendor]:
            continue
        inst = faultgen.generate_instance(bytes.fromhex(seed_hex), index)
        if str(inst["family"]) == family:
            return seed_hex, index, inst
    raise AssertionError(f"no {family} for {vendor}")


def test_unfaulted_baseline_pin_serves_oracle_unmutated() -> None:
    from simulators.common import beacon_serve as _beacon
    from simulators.common import faultgen as faultgen
    from simulators.common import state as _state
    from simulators.common.helpers import get_dataset_for_current_state

    seed_hex, index, inst = _beacon_index("atlas", "rename")
    ds = int(inst["dataset_seed"])
    _state.set_scenario("baseline", ds)
    try:
        with _beacon.unfaulted_baseline(ds):
            served = get_dataset_for_current_state()
        assert served == faultgen.beacon_dataset(ds)
    finally:
        _state.set_beacon_state(None, None)
        assert _beacon.baseline_pin() is None


def test_pagination_docs_note_announces_drawn_params() -> None:
    from simulators.common import beacon_serve as _beacon
    from simulators.common import state as _state

    seed_hex, index, inst = _beacon_index("atlas", "pagination")
    params = dict(inst["params"])
    _state.set_scenario("baseline", 0)
    try:
        assert _beacon.pagination_docs_note("atlas") is None
        assert _beacon.active_pagination_contract("atlas") is None
        _beacon.set_beacon(seed_hex, index)
        note = _beacon.pagination_docs_note("atlas")
        assert note is not None
        for key in ("offset_param", "limit_param", "page_param", "per_page_param",
                    "cursor_param", "cursor_leaf", "page_size_param"):
            value = params.get(key)
            if isinstance(value, str) and value:
                assert value in note, (key, value)
        # Other vendors see nothing.
        assert _beacon.pagination_docs_note("harbor") is None
        contract = _beacon.active_pagination_contract("atlas")
        assert contract is not None and contract["strategy"] == params["strategy"]
    finally:
        _beacon.clear_beacon()


def test_served_docs_carry_announcement_for_faulted_round() -> None:
    from starlette.testclient import TestClient

    from simulators.common import beacon_serve as _beacon
    from simulators.common import state as _state

    seed_hex, index, inst = _beacon_index("harbor", "pagination")
    params = dict(inst["params"])
    import simulators.harborpms.app as harbor_app

    _state.set_scenario("baseline", 0)
    try:
        with TestClient(harbor_app.app) as client:
            plain = client.get("/_harbor_docs").text
            assert "Pagination update" not in plain
            _beacon.set_beacon(seed_hex, index)
            announced = client.get("/_harbor_docs").text
        assert "Pagination update" in announced
        drawn = [str(params[k]) for k in ("offset_param", "limit_param", "page_param",
                                          "per_page_param", "cursor_param")
                 if isinstance(params.get(k), str)]
        assert drawn and all(v in announced for v in drawn)
    finally:
        _beacon.clear_beacon()
