"""Simulator beacon serving tests (REQ-025 item 5, simulators side).

Covers the admin plane (`POST /admin/beacon`), the public descriptor
(answer-key-free), record-shape application through the shared dataset
funnel, behaviour-predicate hooks, pagination-contract serving, the
Meridian body-cursor choke points, and the FIAS dynamic layout/link
arming. State is global: every beacon-setting test resets afterwards.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import simulators.common.faultgen as faultgen
from simulators.common import beacon_serve
from simulators.common.admin import create_admin_app
from simulators.common.state import get_state, reset_state

EVAL_SEED_HEX = "05ec57a0c1e763e985c9ac18df2313a98392a9c24ecbceefd0b21216a9beaefa"


@pytest.fixture()
def _clean_state():
    reset_state()
    try:
        yield
    finally:
        reset_state()


def test_set_beacon_returns_public_descriptor_only(_clean_state: None) -> None:
    descriptor = beacon_serve.set_beacon(EVAL_SEED_HEX, 0)
    assert set(descriptor) == {"index", "vendor", "family", "params"}
    assert "expected" not in descriptor
    assert descriptor["index"] == 0
    # Round-robin: index 0 is atlas.
    assert descriptor["vendor"] == "atlas"
    assert get_state()["beacon"] == {"eval_seed_hex": EVAL_SEED_HEX, "index": 0}


def test_set_beacon_rejects_garbage(_clean_state: None) -> None:
    for bad_hex, bad_index in (
        ("", 0),
        ("zz", 0),
        ("ab" * 16, 0),
        (EVAL_SEED_HEX, -1),
        (EVAL_SEED_HEX, True),
        (EVAL_SEED_HEX, 10_001),
    ):
        with pytest.raises(ValueError):
            beacon_serve.set_beacon(bad_hex, bad_index)  # type: ignore[arg-type]
    assert beacon_serve.active_instance() is None


def test_clear_beacon_restores_baseline(_clean_state: None) -> None:
    beacon_serve.set_beacon(EVAL_SEED_HEX, 1)
    assert beacon_serve.active_instance() is not None
    beacon_serve.clear_beacon()
    assert beacon_serve.active_instance() is None
    assert get_state()["beacon"] is None


def test_active_instance_regenerates_deterministically(_clean_state: None) -> None:
    beacon_serve.set_beacon(EVAL_SEED_HEX, 3)
    first = beacon_serve.active_instance()
    second = beacon_serve.active_instance()
    assert first is not None and first == second
    assert first == faultgen.generate_instance(bytes.fromhex(EVAL_SEED_HEX), 3)


def test_record_shape_applies_to_app_dataset(_clean_state: None) -> None:
    from simulators.common.generator import generate_dataset
    from simulators.common.helpers import get_dataset_for_current_state

    target = None
    for index in range(30):
        inst = faultgen.generate_instance(bytes.fromhex(EVAL_SEED_HEX), index)
        if faultgen.behavior_for_instance(inst) is None:
            target = index
            break
    assert target is not None
    beacon_serve.set_beacon(EVAL_SEED_HEX, target)
    instance = beacon_serve.active_instance()
    assert instance is not None
    baseline = generate_dataset(seed=0)
    served = get_dataset_for_current_state()
    expected = faultgen.apply_instance_records(baseline, instance)
    assert served == expected
    assert served != baseline


def test_behaviour_instance_leaves_records_untouched(_clean_state: None) -> None:
    from simulators.common.generator import generate_dataset
    from simulators.common.helpers import get_dataset_for_current_state

    # Find a behaviour-family index on this seed (deterministic search).
    target = None
    for index in range(5):
        inst = faultgen.generate_instance(bytes.fromhex(EVAL_SEED_HEX), index)
        if faultgen.behavior_for_instance(inst) is not None and inst["vendor"] != "fias-pabx":
            target = index
            break
    assert target is not None
    beacon_serve.set_beacon(EVAL_SEED_HEX, target)
    assert get_dataset_for_current_state() == generate_dataset(seed=0)


def test_transport_predicates_follow_beacon(_clean_state: None) -> None:
    from simulators.common import faults

    # Find a transport_auth instance (deterministic search, first 30).
    target = None
    for index in range(30):
        inst = faultgen.generate_instance(bytes.fromhex(EVAL_SEED_HEX), index)
        if inst["family"] == "transport_auth" and inst["vendor"] != "fias-pabx":
            target = (index, inst)
            break
    assert target is not None
    index, inst = target
    kind = str(inst["params"]["kind"])
    beacon_serve.set_beacon(EVAL_SEED_HEX, index)
    behavior = faultgen.behavior_for_instance(inst)
    assert behavior is not None
    status, _, terminal = faultgen.transport_response(behavior, 3)
    assert faults.is_rate_limited("baseline", 3) == (status == 429)
    assert faults.is_transient_error("baseline", 3) == (status == 503)
    assert faults.is_auth_expired("baseline") == (kind == "token_expired")
    assert faults.is_auth_revoked("baseline") == (kind == "token_revoked")
    assert faults.is_endpoint_removed("baseline") == (kind == "endpoint_removed")
    assert terminal == (kind in ("token_revoked", "endpoint_removed"))


def test_pagination_mode_follows_beacon(_clean_state: None) -> None:
    from simulators.common import faults

    target = None
    for index in range(30):
        inst = faultgen.generate_instance(bytes.fromhex(EVAL_SEED_HEX), index)
        if inst["family"] == "pagination" and inst["vendor"] != "fias-pabx":
            target = (index, inst)
            break
    assert target is not None
    index, inst = target
    beacon_serve.set_beacon(EVAL_SEED_HEX, index)
    assert faults.is_pagination_mutated("baseline") is True
    assert faults.pagination_mode_for_scenario("baseline", default="offset") == inst["params"]["strategy"]


def test_paginate_by_contract_envelope_paths() -> None:
    items = [{"id": i} for i in range(10)]
    contract = {
        "family": "pagination",
        "strategy": "cursor",
        "cursor_param": "pageToken",
        "limit_param": "pageSize",
        "items_path": ["results", "items"],
        "next_path": ["links", "next"],
    }
    first = beacon_serve.paginate_by_contract(items, {"pageSize": "4"}, contract, 50)
    assert first["results"]["items"] == items[0:4]
    token = first["links"]["next"]
    assert isinstance(token, str)
    second = beacon_serve.paginate_by_contract(items, {"pageSize": "4", "pageToken": token}, contract, 50)
    assert second["results"]["items"] == items[4:8]
    last = beacon_serve.paginate_by_contract(
        items, {"pageSize": "4", "pageToken": faultgen.cursor_token_for(8, contract)}, contract, 50
    )
    assert last["results"]["items"] == items[8:10]
    assert last["links"]["next"] is None


def test_paginate_by_contract_offset_and_page() -> None:
    items = [{"id": i} for i in range(6)]
    offset_contract = {
        "family": "pagination",
        "strategy": "offset",
        "offset_param": "skip",
        "limit_param": "take",
        "items_path": ["data"],
    }
    page = beacon_serve.paginate_by_contract(items, {"skip": "2", "take": "2"}, offset_contract, 50)
    assert page == {"data": items[2:4]}
    # Baseline names are ignored under a contract (unfixed client replays defaults).
    defaulted = beacon_serve.paginate_by_contract(items, {"offset": "2", "limit": "2"}, offset_contract, 50)
    assert defaulted == {"data": items[0:50]}
    page_contract = {
        "family": "pagination",
        "strategy": "page",
        "page_param": "page",
        "per_page_param": "perPage",
        "items_path": ["data"],
    }
    page2 = beacon_serve.paginate_by_contract(items, {"page": "2", "perPage": "2"}, page_contract, 50)
    assert page2 == {"data": items[2:4]}
    with pytest.raises(ValueError):
        beacon_serve.paginate_by_contract(items, {}, {"family": "pagination", "strategy": "bogus"}, 50)


def test_cursor_index_for_garbage() -> None:
    assert beacon_serve.cursor_index_for(None) == 0
    assert beacon_serve.cursor_index_for("bogus") == 0
    assert beacon_serve.cursor_index_for("c000007_abcdef") == 7


def test_admin_beacon_endpoints(_clean_state: None) -> None:
    client = TestClient(create_admin_app())
    headers = {"X-Admin-Network": "admin-net"}
    r = client.post("/admin/beacon", json={"eval_seed_hex": EVAL_SEED_HEX, "index": 2}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"index", "vendor", "family", "params", "request_count"}
    assert "expected" not in body
    assert body["index"] == 2
    r = client.get("/admin/beacon", headers=headers)
    assert r.status_code == 200
    assert r.json()["beacon"]["index"] == 2
    # Bad hex / bad vendor routing fail closed.
    r = client.post("/admin/beacon", json={"eval_seed_hex": "zz", "index": 0}, headers=headers)
    assert r.status_code == 400
    r = client.post("/admin/reset", headers=headers)
    assert r.status_code == 200
    r = client.get("/admin/beacon", headers=headers)
    assert r.json() == {"beacon": None}


def test_meridian_limitation_follows_contract(_clean_state: None) -> None:
    from simulators.meridianpms.app import _cursor_leaf, _limitation

    target = None
    for index in range(30):
        inst = faultgen.generate_instance(bytes.fromhex(EVAL_SEED_HEX), index)
        if inst["vendor"] == "meridian" and inst["family"] == "pagination":
            target = (index, inst)
            break
    assert target is not None
    index, inst = target
    beacon_serve.set_beacon(EVAL_SEED_HEX, index)
    params = inst["params"]
    body = {"Limitation": {params["page_size_param"]: 3, params["cursor_leaf"]: None}}
    count, cursor = _limitation(body)
    assert count == 3 and cursor is None
    assert _cursor_leaf("baseline") == params["cursor_leaf"]
    # The contract's own names are read; baseline names only apply when the
    # contract happens to reuse them.
    if params["page_size_param"] != "Count":
        count, _ = _limitation({"Limitation": {"Count": 5}})
        assert count == 100
    if params["cursor_leaf"] != "Cursor":
        _, cursor = _limitation({"Limitation": {"Cursor": "abc"}})
        assert cursor is None


def test_generate_instance_matches_batch() -> None:
    seed = bytes.fromhex(EVAL_SEED_HEX)
    batch = faultgen.generate_instances(seed, 10)
    for index in range(10):
        assert faultgen.generate_instance(seed, index) == batch[index]
    with pytest.raises(ValueError):
        faultgen.generate_instance(b"short", 0)
    with pytest.raises(ValueError):
        faultgen.generate_instance(seed, -1)


def test_apply_instance_records_per_family() -> None:
    from simulators.common.generator import generate_dataset

    seed = bytes.fromhex(EVAL_SEED_HEX)
    baseline = generate_dataset(seed=0)
    seen_shape: set[str] = set()
    seen_behavior: set[str] = set()
    for index in range(30):
        instance = faultgen.generate_instance(seed, index)
        out = faultgen.apply_instance_records(baseline, instance)
        if faultgen.behavior_for_instance(instance) is not None:
            seen_behavior.add(instance["family"])
            assert out == baseline
            assert out is not baseline
        else:
            seen_shape.add(instance["family"])
            assert out != baseline or instance["family"] in seen_shape
    assert {"rename", "nesting", "code_list", "semantic", "dates"} <= seen_shape
    assert seen_behavior, "expected at least one behaviour instance in 30"
    # Base dataset is never mutated in place.
    fresh = generate_dataset(seed=0)
    assert baseline == fresh


def test_fias_wire_value_and_layout_shaping() -> None:
    from simulators.fias_pabx.pabx import PabxLink, _wire_value

    assert _wire_value({"TA": "1250"}, {}, "TA") == "1250"
    assert _wire_value({"TA": "1250"}, {"TM": "TA"}, "TM") == "1250"
    assert _wire_value({}, {}, "GX") == ""
    link = PabxLink()
    link.faults.ps_layout_fields = ["RN", "P#", "TM", "GX"]
    link.faults.ps_field_aliases = {"TM": "TA"}
    layout, aliases = link._current_shape()
    assert layout == ["RN", "P#", "TM", "GX"]
    assert aliases == {"TM": "TA"}
    rec = link._next_posting(next(iter(link._rooms.values())), minibar=False)
    assert rec.field_ids() == ["RN", "P#", "TM", "GX"]
    assert rec.field("TM") != ""
    assert rec.field("GX") == ""


def test_fias_admin_beacon_arms_link(_clean_state: None) -> None:
    from simulators.fias_pabx import admin as fias_admin

    # Index 4 is fias-pabx by round-robin; resolve the layout instance.
    target = None
    for index in range(4, 40, 5):
        inst = faultgen.generate_instance(bytes.fromhex(EVAL_SEED_HEX), index)
        if inst["family"] == "fias_layout":
            target = index
            break
    assert target is not None
    fias_admin._link = None
    from fastapi import FastAPI as _FastAPI

    probe = _FastAPI()
    probe.include_router(fias_admin.router)
    client = TestClient(probe)
    # No link engine in unit tests: the endpoint must 503, not crash.
    r = client.post(
        "/admin/fias/beacon",
        json={"eval_seed_hex": EVAL_SEED_HEX, "index": target},
        headers={"X-Admin-Network": "admin-net"},
    )
    assert r.status_code == 503
