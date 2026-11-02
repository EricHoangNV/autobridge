"""REQ-084 audit: nesting-aware field helpers in every simulator.

Direct branch coverage for the ``_inner_get`` / ``_wire_get`` /
``_wire_value`` helpers added to the four HTTP simulators: flat hits,
nested wrappers, non-dict nodes, missing keys and the depth bound.
"""

from __future__ import annotations


def _check_mod(mod: object, fn: str, cases: list[tuple]) -> None:
    func = getattr(mod, fn)
    for node, key, expected in cases:
        assert func(node, key, "DEFAULT") == expected, (fn, node, key)


def test_meridian_wire_value_branches() -> None:
    from simulators.meridianpms import app as m

    flat = {"ServiceId": "prop-001", "Id": "R1"}
    assert m._wire_value(flat, "ServiceId") == "prop-001"
    assert m._wire_value(flat, "Nope", "dflt") == "dflt"
    nested = {"stay": {"Id": "R1", "ServiceId": "prop-002"}}
    assert m._wire_value(nested, "ServiceId") == "prop-002"
    deep = {"a": {"b": {"Id": "R7"}}}
    assert m._wire_value(deep, "Id") == "R7"
    assert m._wire_value({"a": {"b": "leaf"}}, "Id", "d") == "d"
    assert m._wire_value({"x": 1, "y": 2}, "Id", "d") == "d"
    assert m._wire_value(None, "Id", "d") == "d"
    assert m._wire_value("notadict", "Id", "d") == "d"
    # Depth bound: deeper than the loop still returns the default.
    very_deep: object = {"Id": "R9"}
    for _ in range(8):
        very_deep = {"w": very_deep}
    assert m._wire_value(very_deep, "Id", "d") == "d"
    assert m._wire_value({"Id": "R1"}, "Missing") is None


def test_atlas_inner_and_wire_get_branches() -> None:
    from simulators.atlaspms import app as a

    rec = {"reservation_id": "RES-1", "guest_name": "Ada Lovelace"}
    assert a._inner_get(rec, "reservation_id") == "RES-1"
    assert a._inner_get({"stay": dict(rec)}, "guest_name") == "Ada Lovelace"
    assert a._inner_get({"stay": dict(rec)}, "missing", "d") == "d"
    assert a._inner_get(None, "reservation_id", "d") == "d"
    assert a._inner_get("nope", "reservation_id", "d") == "d"
    wire = {"status": "confirmed"}
    assert a._wire_get(wire, "status") == "confirmed"
    assert a._wire_get({"stay": dict(wire)}, "status") == "confirmed"
    assert a._wire_get({"stay": dict(wire)}, "missing", "d") == "d"
    assert a._wire_get(None, "status", "d") == "d"
    assert a._wire_get({"a": 1, "b": 2}, "status", "d") == "d"
    very_deep: object = {"status": "confirmed"}
    for _ in range(8):
        very_deep = {"w": very_deep}
    assert a._wire_get(very_deep, "status", "d") == "d"


def test_harbor_inner_and_wire_get_branches() -> None:
    from simulators.harborpms import app as h

    rec = {"reservation_id": "RES-2", "property_id": "prop-001"}
    assert h._inner_get(rec, "reservation_id") == "RES-2"
    assert h._inner_get({"stay": dict(rec)}, "property_id") == "prop-001"
    assert h._inner_get({"stay": dict(rec)}, "missing", "d") == "d"
    assert h._inner_get(None, "reservation_id", "d") == "d"
    wire = {"property_id": "prop-001"}
    assert h._wire_get(wire, "property_id") == "prop-001"
    assert h._wire_get({"stay": dict(wire)}, "property_id") == "prop-001"
    assert h._wire_get({"stay": dict(wire)}, "missing", "d") == "d"
    assert h._wire_get(["not", "a", "dict"], "property_id", "d") == "d"


def test_legacy_inner_and_wire_get_branches() -> None:
    from simulators.legacystay import app as leg

    rec = {"charge_id": "CHG-1", "reservation_id": "RES-3"}
    assert leg._inner_get(rec, "charge_id") == "CHG-1"
    assert leg._inner_get({"stay": dict(rec)}, "reservation_id") == "RES-3"
    assert leg._inner_get({"stay": dict(rec)}, "missing", "d") == "d"
    assert leg._inner_get(None, "charge_id", "d") == "d"
    wire = {"reservation_id": "RES-3"}
    assert leg._wire_get(wire, "reservation_id") == "RES-3"
    assert leg._wire_get({"stay": dict(wire)}, "reservation_id") == "RES-3"
    assert leg._wire_get({"stay": dict(wire)}, "missing", "d") == "d"
    assert leg._wire_get(42, "reservation_id", "d") == "d"
