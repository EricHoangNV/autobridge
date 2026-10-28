"""Declarative joins — spec v2 data, runtime enrichment, sync wiring (REQ-020 5c).

A join indexes one operation's records by key and attaches computed
scalars under ``_joined.<as>``: ``one`` for lookups (reservation guest
name from customers), ``many`` + ``sum`` for line-item aggregation
(reservation total from order-item gross amounts). No vendor branch
anywhere: every test below uses generic operation and field names.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from autobridge.engine.sync_run import execute_sync
from autobridge.runtime.joins import (
    JOIN_NAMESPACE,
    JoinError,
    resolve_joins,
    resolve_joins_for_entity,
)
from autobridge.runtime.mapping import map_records_to_entities
from autobridge.spec.models import ConnectorSpec, load_spec_from_dict

BASE_SPEC: dict = {
    "spec_version": 2,
    "connector": {"id": "jointest", "vendor": "JoinTest", "runtime_target": "cloud"},
    "source": {
        "base_url": "https://example.com",
        "auth": {"type": "bearer", "credential_ref": "JOIN_TOKEN"},
        "rate_limit": {
            "max_rps": 5,
            "retry": {"statuses": [429, 503], "backoff": "exponential", "max_attempts": 3},
        },
        "timezone_source": {"kind": "static", "value": "UTC"},
    },
    "operations": {
        "list_reservations": {"method": "GET", "path": "/reservations"},
        "list_customers": {"method": "GET", "path": "/customers"},
        "list_lines": {"method": "GET", "path": "/lines"},
    },
    "entities": {
        "Reservation": {
            "from": "list_reservations",
            "key": "$.Id",
            "fields": {
                "reservation_id": {"path": "$.Id"},
                "guest_name": {"path": f"$.{JOIN_NAMESPACE}.customer.full_name"},
                "total_amount": {
                    "path": f"$.{JOIN_NAMESPACE}.folio.total",
                    "transform": [{"name": "money", "args": {"scale": 2}}],
                },
            },
            "joins": [
                {
                    "from_operation": "list_customers",
                    "local_key": "$.AccountId",
                    "foreign_key": "$.Id",
                    "cardinality": "one",
                    "as": "customer",
                    "fields": {
                        "full_name": {
                            "paths": ["$.FirstName", "$.LastName"],
                            "transform": [{"name": "concat", "args": {"separator": " "}}],
                        }
                    },
                },
                {
                    "from_operation": "list_lines",
                    "local_key": "$.AccountId",
                    "foreign_key": "$.AccountId",
                    "cardinality": "many",
                    "as": "folio",
                    "fields": {
                        "total": {"path": "$.Gross", "aggregate": "sum"},
                        "currency": {"path": "$.Currency", "aggregate": "first"},
                        "n": {"path": "$.Id", "aggregate": "count"},
                    },
                },
            ],
        },
    },
    "capabilities": {"read": ["Reservation"]},
    "health": {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {}},
}


def _spec() -> ConnectorSpec:
    return load_spec_from_dict(BASE_SPEC)


def _records() -> dict[str, list[dict]]:
    return {
        "list_reservations": [
            {"Id": "R1", "AccountId": "A1"},
            {"Id": "R2", "AccountId": "A9"},
        ],
        "list_customers": [{"Id": "A1", "FirstName": "Ada", "LastName": "Lovelace"}],
        "list_lines": [
            {"AccountId": "A1", "Id": "L1", "Gross": Decimal("19.99"), "Currency": "USD"},
            {"AccountId": "A1", "Id": "L2", "Gross": Decimal("0.01"), "Currency": "USD"},
            {"AccountId": "A1", "Id": "L3", "Gross": None, "Currency": None},
        ],
    }


# ---------------------------------------------------------------------------
# spec models: v1 gate + validation
# ---------------------------------------------------------------------------


def test_v1_rejects_joins() -> None:
    import copy

    v1 = copy.deepcopy(BASE_SPEC)
    v1["spec_version"] = 1
    with pytest.raises(Exception, match="joins require spec_version 2"):
        load_spec_from_dict(v1)


def test_join_unknown_operation_rejected() -> None:
    import copy

    bad = copy.deepcopy(BASE_SPEC)
    bad["entities"]["Reservation"]["joins"][0]["from_operation"] = "nope"
    with pytest.raises(Exception, match="unknown operation"):
        load_spec_from_dict(bad)


def test_join_many_without_aggregate_rejected() -> None:
    import copy

    bad = copy.deepcopy(BASE_SPEC)
    bad["entities"]["Reservation"]["joins"][1]["fields"] = {"total": {"path": "$.Gross"}}
    with pytest.raises(Exception, match="needs 'aggregate'"):
        load_spec_from_dict(bad)


def test_join_one_with_aggregate_rejected() -> None:
    import copy

    bad = copy.deepcopy(BASE_SPEC)
    bad["entities"]["Reservation"]["joins"][0]["fields"] = {
        "full_name": {"path": "$.FirstName", "aggregate": "first"}
    }
    with pytest.raises(Exception, match="needs cardinality many"):
        load_spec_from_dict(bad)


def test_join_paths_with_many_rejected() -> None:
    import copy

    bad = copy.deepcopy(BASE_SPEC)
    bad["entities"]["Reservation"]["joins"][1]["fields"] = {
        "total": {"paths": ["$.Gross"], "aggregate": "sum"}
    }
    with pytest.raises(Exception, match="needs cardinality one"):
        load_spec_from_dict(bad)


def test_join_field_needs_path_or_paths() -> None:
    import copy

    bad = copy.deepcopy(BASE_SPEC)
    bad["entities"]["Reservation"]["joins"][0]["fields"] = {"full_name": {}}
    with pytest.raises(Exception, match="exactly one of"):
        load_spec_from_dict(bad)


def test_join_as_name_must_be_slug() -> None:
    import copy

    bad = copy.deepcopy(BASE_SPEC)
    bad["entities"]["Reservation"]["joins"][0]["as"] = "Bad Name"
    with pytest.raises(Exception, match="must match"):
        load_spec_from_dict(bad)


def test_join_key_expression_rejected() -> None:
    import copy

    bad = copy.deepcopy(BASE_SPEC)
    bad["entities"]["Reservation"]["joins"][0]["local_key"] = "$..AccountId"
    with pytest.raises(Exception, match="JSONPath subset"):
        load_spec_from_dict(bad)


# ---------------------------------------------------------------------------
# runtime: enrichment semantics
# ---------------------------------------------------------------------------


def test_one_join_concat_and_many_sum() -> None:
    spec = _spec()
    out = resolve_joins(spec, _records())
    recs = out["Reservation"]
    r1 = recs[0]
    assert r1["_joined"]["customer"] == {"full_name": "Ada Lovelace"}
    # Decimal-exact: 19.99 + 0.01, None skipped, never float.
    assert r1["_joined"]["folio"]["total"] == Decimal("20.00")
    assert r1["_joined"]["folio"]["currency"] == "USD"
    assert r1["_joined"]["folio"]["n"] == 3
    # No match: honest None, namespace still present.
    r2 = recs[1]
    assert r2["_joined"]["customer"] == {"full_name": None}
    assert r2["_joined"]["folio"]["total"] is None


def test_join_inputs_not_mutated() -> None:
    spec = _spec()
    recs = _records()
    resolve_joins(spec, recs)
    assert all(JOIN_NAMESPACE not in r for r in recs["list_reservations"])


def test_join_missing_local_key_matches_nothing() -> None:
    spec = _spec()
    recs = _records()
    recs["list_reservations"] = [{"Id": "RX"}]
    out = resolve_joins(spec, recs)
    assert out["Reservation"][0]["_joined"]["customer"] == {"full_name": None}


def test_join_unknown_operation_raises() -> None:
    spec = _spec()
    with pytest.raises(JoinError, match="unknown operation"):
        resolve_joins_for_entity(
            spec.entities["Reservation"], [{"Id": "R1"}], {"list_reservations": []}
        )


def test_join_sum_rejects_float() -> None:
    import copy

    spec_dict = copy.deepcopy(BASE_SPEC)
    load_spec_from_dict(spec_dict)  # validates
    spec = load_spec_from_dict(spec_dict)
    recs = _records()
    recs["list_lines"] = [{"AccountId": "A1", "Gross": 19.99}]
    with pytest.raises(JoinError, match="float rejected"):
        resolve_joins(spec, {**recs, "list_reservations": [{"Id": "R1", "AccountId": "A1"}]})


def test_join_first_skips_null_and_min_max() -> None:
    import copy

    spec_dict = copy.deepcopy(BASE_SPEC)
    spec_dict["entities"]["Reservation"]["joins"][1]["fields"] = {
        "lo": {"path": "$.Gross", "aggregate": "min"},
        "hi": {"path": "$.Gross", "aggregate": "max"},
    }
    spec = load_spec_from_dict(spec_dict)
    recs = _records()
    out = resolve_joins(spec, recs)
    assert out["Reservation"][0]["_joined"]["folio"] == {"lo": Decimal("0.01"), "hi": Decimal("19.99")}


def test_join_count_counts_matches() -> None:
    spec = _spec()
    out = resolve_joins(spec, _records())
    assert out["Reservation"][0]["_joined"]["folio"]["n"] == 3
    assert out["Reservation"][1]["_joined"]["folio"]["n"] == 0


# ---------------------------------------------------------------------------
# mapping + sync wiring
# ---------------------------------------------------------------------------


def test_mapping_reads_joined_paths() -> None:
    spec = _spec()
    enriched = resolve_joins(spec, _records())["Reservation"]
    canonical, blocked = map_records_to_entities(
        enriched, spec.entities["Reservation"], source_spec=spec.source
    )
    assert not blocked
    assert canonical[0]["guest_name"] == "Ada Lovelace"
    assert canonical[0]["total_amount"] == Decimal("20.00")
    # Unmatched record: joined values stay None, never invented.
    assert canonical[1].get("guest_name") is None
    assert canonical[1].get("total_amount") is None


class _FakeHttp:
    """Minimal HttpClient double: canned pages per operation path."""

    def __init__(self, pages: dict[str, list[dict]]):
        self.pages = pages

    def get(self, path: str, params: dict | None = None) -> dict:  # noqa: ARG002
        for op_path, rows in self.pages.items():
            if path == op_path:
                return {"data": rows}
        raise AssertionError(f"unexpected path {path!r}")


def test_execute_sync_applies_joins_before_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    import copy

    monkeypatch.setenv("JOIN_TOKEN", "tok")
    spec_dict = copy.deepcopy(BASE_SPEC)
    for op in spec_dict["operations"].values():
        op["pagination"] = {
            "strategy": "cursor",
            "cursor_param": "cursor",
            "next_path": "$.next",
            "items_path": "$.data",
            "stop_when": ["null_cursor"],
        }
    http = _FakeHttp(
        {
            "/reservations": [{"Id": "R1", "AccountId": "A1"}],
            "/customers": [{"Id": "A1", "FirstName": "Ada", "LastName": "Lovelace"}],
            "/lines": [{"AccountId": "A1", "Id": "L1", "Gross": Decimal("5.00"), "Currency": "USD"}],
        }
    )
    summary = execute_sync(spec_dict, http)
    assert summary["outcome"] == "Complete"
    rec = summary["canonical_records"]["Reservation"][0]
    assert rec["guest_name"] == "Ada Lovelace"
    assert rec["total_amount"] == Decimal("5.00")


def test_partial_results_attach_none_without_error() -> None:
    """Single-operation probes (canary) resolve known-but-unfetched joins to None."""
    spec = _spec()
    out = resolve_joins_for_entity(
        spec.entities["Reservation"],
        [{"Id": "R1", "AccountId": "A1"}],
        {"list_reservations": [{"Id": "R1", "AccountId": "A1"}]},
        known_ops={"list_reservations", "list_customers", "list_lines"},
    )
    assert out[0]["_joined"]["customer"] == {"full_name": None}


def test_patch_can_add_join() -> None:
    """REQ-020.5c: repairs are JSON Patches, and patches reach join fields."""
    import copy

    from autobridge.spec.patch import apply_patch

    data = copy.deepcopy(BASE_SPEC)
    del data["entities"]["Reservation"]["joins"]
    patched = apply_patch(
        data,
        [
            {
                "op": "add",
                "path": "/entities/Reservation/joins",
                "value": BASE_SPEC["entities"]["Reservation"]["joins"],
            }
        ],
    )
    spec = load_spec_from_dict(patched)
    assert len(spec.entities["Reservation"].joins) == 2
