"""P1.W4.3: 8 deterministic repair rules with precondition, patch, confidence, verification level."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from autobridge.drift.fingerprint import FingerprintDelta
from autobridge.repair.context import RepairContext
from autobridge.repair.registry import RULE_REGISTRY, all_rules, get_rule, matching_rules
from autobridge.spec.patch import apply_patch


def _load_spec(vendor: str) -> dict:
    path = Path(f"generated/connectors/{vendor}/reference/spec.yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _offset_fixture_spec() -> dict:
    """Legacy offset-pagination fixture (mirrors the pre-REQ-019 Atlas shape).

    Derived from the Atlas reference spec with both operations flipped to the
    legacy ``offset`` strategy, so JSON-Patch ``replace`` applies and the
    fixture validates. The ``offset_to_cursor`` rule stays covered by these
    tests even though no live vendor is offset-based anymore (REQ-019).
    """
    import copy

    spec = copy.deepcopy(_load_spec("atlaspms"))
    for op in spec["operations"].values():
        pag = op.get("pagination", {})
        pag["strategy"] = "offset"
        pag["offset_param"] = "offset"
        pag["page_param"] = None
        pag["cursor_param"] = None
        pag["limit_param"] = "limit"
        pag["next_path"] = "$.next_offset"
        pag.pop("total_path", None)
        pag["items_path"] = "$.data"
        pag["stop_when"] = ["null_cursor", "empty_page"]
    return spec


def _delta_with_types(entity: str, old_field: str, new_field: str, old_type: str = "str", new_type: str = "str") -> FingerprintDelta:
    """Helper to build a FingerprintDelta with types_details for rename/type change."""
    prev_types = {old_field: old_type, "other": "str"}
    cur_types = {new_field: new_type, "other": "str"}
    # Use generic previous/current with same value for rename (same type)
    return FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=False,
        keys_changed=False,
        types_changed=True,
        changed_entities=[entity],
        pagination_details={},
        keys_details={},
        types_details={entity: {"previous": prev_types, "current": cur_types}},
    )


def _delta_pagination(op: str, prev_strategy: str, cur_strategy: str) -> FingerprintDelta:
    return FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=True,
        keys_changed=False,
        types_changed=False,
        changed_entities=[],
        pagination_details={
            op: {
                "previous": {"strategy": prev_strategy, "items_path": "$.data"},
                "current": {"strategy": cur_strategy, "items_path": "$.data"},
            }
        },
        keys_details={},
        types_details={},
    )


def _delta_items_path(op: str, old_path: str, new_path: str) -> FingerprintDelta:
    return FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=True,
        keys_changed=False,
        types_changed=False,
        changed_entities=[],
        pagination_details={
            op: {
                "previous": {"strategy": "cursor", "items_path": old_path},
                "current": {"strategy": "cursor", "items_path": new_path},
            }
        },
        keys_details={},
        types_details={},
    )


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


def test_registry_has_nine_rules() -> None:
    assert len(RULE_REGISTRY) == 9
    expected = {
        "rename_field",
        "offset_to_cursor",
        "page_to_cursor",
        "cursor_to_offset",
        "retry_backoff_only",
        "enum_extend_requires_evidence",
        "nested_path_shift",
        "type_coerce_string_number",
        "cursor_next_path_shift",
    }
    assert set(RULE_REGISTRY.keys()) == expected


def test_registry_all_rules_ordered() -> None:
    rules = all_rules()
    assert len(rules) == 9
    assert rules[0].id == "rename_field"
    assert rules[-1].id == "type_coerce_string_number"


def test_get_rule_ok_and_unknown() -> None:
    r = get_rule("rename_field")
    assert r.id == "rename_field"
    assert r.confidence == 0.92
    assert r.verification_level == "visible"
    try:
        get_rule("no_such_rule")
        raise AssertionError("should raise")
    except ValueError as e:
        assert "unknown rule" in str(e)


def test_registry_rule_attributes() -> None:
    for rid, entry in RULE_REGISTRY.items():
        assert 0 < entry.confidence <= 1.0
        assert entry.verification_level in ("visible", "none", "evidence_required")
        assert isinstance(entry.produces_patch, bool)
        assert entry.id == rid


# ---------------------------------------------------------------------------
# rename_field
# ---------------------------------------------------------------------------


def test_rename_field_precondition_true_via_delta() -> None:
    from autobridge.repair.rules.rename_field import precondition

    spec = _load_spec("atlaspms")
    delta = _delta_with_types("Reservation", "guest_name", "guest_identifier", "str", "str")
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME", fingerprint_delta=delta)
    assert precondition(ctx) is True


def test_rename_field_precondition_true_via_extra() -> None:
    from autobridge.repair.rules.rename_field import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec,
        drift_class="SCHEMA_RENAME",
        extra={"entity": "Reservation", "field": "guest_name", "new_field": "guest_identifier"},
    )
    assert precondition(ctx) is True


def test_rename_field_precondition_false_wrong_class() -> None:
    from autobridge.repair.rules.rename_field import precondition

    spec = _load_spec("atlaspms")
    delta = _delta_with_types("Reservation", "guest_name", "guest_identifier")
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE", fingerprint_delta=delta)
    assert precondition(ctx) is False


def test_rename_field_precondition_false_no_info() -> None:
    from autobridge.repair.rules.rename_field import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME")
    assert precondition(ctx) is False


def test_rename_field_precondition_false_old_not_in_spec() -> None:
    from autobridge.repair.rules.rename_field import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec,
        drift_class="SCHEMA_RENAME",
        extra={"entity": "Reservation", "field": "nonexistent_xyz", "new_field": "new_one"},
    )
    assert precondition(ctx) is False


def test_rename_field_precondition_false_same_field() -> None:
    from autobridge.repair.rules.rename_field import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec,
        drift_class="SCHEMA_RENAME",
        extra={"entity": "Reservation", "field": "guest_name", "new_field": "guest_name"},
    )
    assert precondition(ctx) is False


def test_rename_field_patch_shape() -> None:
    from autobridge.repair.rules.rename_field import generate_patch

    spec = _load_spec("atlaspms")
    delta = _delta_with_types("Reservation", "guest_name", "guest_identifier")
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME", fingerprint_delta=delta)
    patch = generate_patch(ctx)
    assert len(patch) == 1
    op = patch[0]
    assert op["op"] == "replace"
    assert op["path"] == "/entities/Reservation/fields/guest_name/path"
    assert op["value"] == "$.guest_identifier"
    # Validate patch applies
    result = apply_patch(spec, patch)
    assert result["entities"]["Reservation"]["fields"]["guest_name"]["path"] == "$.guest_identifier"


def test_rename_field_patch_via_extra_new_path() -> None:
    from autobridge.repair.rules.rename_field import generate_patch

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec,
        drift_class="SCHEMA_RENAME",
        extra={"entity": "Reservation", "field": "guest_name", "new_field": "guest_identifier", "new_path": "$.guest.identifier"},
    )
    patch = generate_patch(ctx)
    assert patch[0]["value"] == "$.guest.identifier"


def test_rename_field_patch_empty_without_info() -> None:
    from autobridge.repair.rules.rename_field import generate_patch

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME")
    assert generate_patch(ctx) == []


# rename_field with live wire-key evidence (REQ-027: detector-derived
# inputs carry wire names like ``guestName``, not canonical ``guest_name``).


def test_rename_field_precondition_true_via_wire_names() -> None:
    """Wire-key delta (guestName -> guestIdentifier) resolves canonical field."""
    from autobridge.repair.rules.rename_field import precondition

    spec = _load_spec("atlaspms")
    delta = _delta_with_types("Reservation", "guestName", "guestIdentifier")
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME", fingerprint_delta=delta)
    assert precondition(ctx) is True


def test_rename_field_patch_shape_via_wire_names() -> None:
    """Wire names still patch the canonical field path to the new wire key."""
    from autobridge.repair.rules.rename_field import generate_patch

    spec = _load_spec("atlaspms")
    delta = _delta_with_types("Reservation", "guestName", "guestIdentifier")
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME", fingerprint_delta=delta)
    patch = generate_patch(ctx)
    assert patch == [
        {
            "op": "replace",
            "path": "/entities/Reservation/fields/guest_name/path",
            "value": "$.guestIdentifier",
        }
    ]
    result = apply_patch(spec, patch)
    assert result["entities"]["Reservation"]["fields"]["guest_name"]["path"] == "$.guestIdentifier"


def test_rename_field_precondition_true_via_wire_signal() -> None:
    """keys_changed wire hint (missing/added wire keys) fires the rule."""
    from autobridge.repair.rules.rename_field import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec,
        drift_class="SCHEMA_RENAME",
        signals=[
            {
                "kind": "keys_changed",
                "operation": "list_reservations",
                "details": {
                    "entity": "Reservation",
                    "missing": ["guestName"],
                    "added": [{"key": "guestIdentifier", "sample": "Guest 1", "type": "str"}],
                },
            }
        ],
    )
    assert precondition(ctx) is True


def test_rename_field_wire_key_ambiguous_envelope_no_fire() -> None:
    """A shared-envelope top key never resolves to a canonical field."""
    import copy

    from autobridge.repair.rules.rename_field import _canonical_field_for, precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME")
    assert _canonical_field_for(ctx, "Reservation", "guestName") == "guest_name"
    assert _canonical_field_for(ctx, "Reservation", "guest_name") == "guest_name"
    assert _canonical_field_for(ctx, "Reservation", "no_such_key") is None
    assert _canonical_field_for(ctx, "NoEntity", "guestName") is None
    # Two fields under one envelope top key: unresolvable, rule stays quiet.
    ambiguous = copy.deepcopy(spec)
    fields = ambiguous["entities"]["Reservation"]["fields"]
    fields["nested_a"] = {"path": "$.envelope.a"}
    fields["nested_b"] = {"path": "$.envelope.b"}
    actx = RepairContext(spec=ambiguous, drift_class="SCHEMA_RENAME")
    assert _canonical_field_for(actx, "Reservation", "envelope") is None
    delta = _delta_with_types("Reservation", "envelope", "envelope2")
    actx2 = RepairContext(spec=ambiguous, drift_class="SCHEMA_RENAME", fingerprint_delta=delta)
    assert precondition(actx2) is False


# ---------------------------------------------------------------------------
# offset_to_cursor
# ---------------------------------------------------------------------------


def test_offset_to_cursor_precondition_true() -> None:
    from autobridge.repair.rules.offset_to_cursor import precondition

    spec = _offset_fixture_spec()  # legacy offset fixture (REQ-019)
    delta = _delta_pagination("list_reservations", "offset", "cursor")
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE", fingerprint_delta=delta)
    assert precondition(ctx) is True


def test_offset_to_cursor_precondition_true_no_delta() -> None:
    from autobridge.repair.rules.offset_to_cursor import precondition

    spec = _offset_fixture_spec()
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE")
    assert precondition(ctx) is True


def test_offset_to_cursor_precondition_false_wrong_class() -> None:
    from autobridge.repair.rules.offset_to_cursor import precondition

    spec = _offset_fixture_spec()
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME")
    assert precondition(ctx) is False


def test_offset_to_cursor_precondition_false_already_cursor() -> None:
    from autobridge.repair.rules.offset_to_cursor import precondition

    spec = _load_spec("harborpms")  # harbor is cursor
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE")
    assert precondition(ctx) is False


def test_offset_to_cursor_precondition_false_delta_mismatch() -> None:
    from autobridge.repair.rules.offset_to_cursor import precondition

    spec = _offset_fixture_spec()
    delta = _delta_pagination("list_reservations", "cursor", "offset")
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE", fingerprint_delta=delta)
    assert precondition(ctx) is False


def test_offset_to_cursor_patch_shape() -> None:
    from autobridge.repair.rules.offset_to_cursor import generate_patch

    spec = _offset_fixture_spec()
    delta = _delta_pagination("list_reservations", "offset", "cursor")
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE", fingerprint_delta=delta)
    patch = generate_patch(ctx)
    # Fixture has 2 ops both offset, so 5 ops per op = 10 total
    assert len(patch) == 10
    # Check structure of first 5
    assert patch[0]["path"] == "/operations/list_reservations/pagination/strategy"
    assert patch[0]["value"] == "cursor"
    assert patch[1]["value"] == "cursor"
    assert patch[2]["value"] == "$.next_cursor"
    assert patch[3]["value"] is None
    # Validate applies
    result = apply_patch(spec, patch)
    assert result["operations"]["list_reservations"]["pagination"]["strategy"] == "cursor"
    assert result["operations"]["list_reservations"]["pagination"]["cursor_param"] == "cursor"
    assert result["operations"]["list_folios"]["pagination"]["strategy"] == "cursor"


def test_offset_to_cursor_patch_filtered_operations() -> None:
    from autobridge.repair.rules.offset_to_cursor import generate_patch

    spec = _offset_fixture_spec()
    ctx = RepairContext(
        spec=spec,
        drift_class="PAGINATION_CHANGE",
        extra={"operations": ["list_reservations"]},
    )
    patch = generate_patch(ctx)
    assert len(patch) == 5
    assert all("list_reservations" in p["path"] for p in patch)


# ---------------------------------------------------------------------------
# page_to_cursor (REQ-019: AtlasPMS is page-paginated)
# ---------------------------------------------------------------------------


def test_page_to_cursor_precondition_true() -> None:
    from autobridge.repair.rules.page_to_cursor import precondition

    spec = _load_spec("atlaspms")  # atlas is page
    delta = _delta_pagination("list_reservations", "page", "cursor")
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE", fingerprint_delta=delta)
    assert precondition(ctx) is True


def test_page_to_cursor_precondition_true_no_delta() -> None:
    from autobridge.repair.rules.page_to_cursor import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE")
    assert precondition(ctx) is True


def test_page_to_cursor_precondition_false_wrong_class() -> None:
    from autobridge.repair.rules.page_to_cursor import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME")
    assert precondition(ctx) is False


def test_page_to_cursor_precondition_false_already_cursor() -> None:
    from autobridge.repair.rules.page_to_cursor import precondition

    spec = _load_spec("harborpms")  # harbor is cursor
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE")
    assert precondition(ctx) is False


def test_page_to_cursor_precondition_false_offset_spec() -> None:
    from autobridge.repair.rules.page_to_cursor import precondition

    spec = _offset_fixture_spec()  # offset, not page
    delta = _delta_pagination("list_reservations", "page", "cursor")
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE", fingerprint_delta=delta)
    assert precondition(ctx) is False


def test_page_to_cursor_precondition_false_delta_mismatch() -> None:
    from autobridge.repair.rules.page_to_cursor import precondition

    spec = _load_spec("atlaspms")
    delta = _delta_pagination("list_reservations", "offset", "cursor")
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE", fingerprint_delta=delta)
    assert precondition(ctx) is False


def test_page_to_cursor_patch_shape() -> None:
    from autobridge.repair.rules.page_to_cursor import generate_patch

    spec = _load_spec("atlaspms")
    delta = _delta_pagination("list_reservations", "page", "cursor")
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE", fingerprint_delta=delta)
    patch = generate_patch(ctx)
    # Atlas has 2 ops both page, so 5 ops per op = 10 total
    assert len(patch) == 10
    # Check structure of first 5
    assert patch[0]["path"] == "/operations/list_reservations/pagination/strategy"
    assert patch[0]["value"] == "cursor"
    assert patch[1]["value"] == "cursor"
    assert patch[2]["value"] == "$.next_cursor"
    assert patch[3]["value"] is None
    # Validate applies
    result = apply_patch(spec, patch)
    assert result["operations"]["list_reservations"]["pagination"]["strategy"] == "cursor"
    assert result["operations"]["list_reservations"]["pagination"]["cursor_param"] == "cursor"
    assert result["operations"]["list_folios"]["pagination"]["strategy"] == "cursor"


def test_page_to_cursor_patch_filtered_operations() -> None:
    from autobridge.repair.rules.page_to_cursor import generate_patch

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec,
        drift_class="PAGINATION_CHANGE",
        extra={"operations": ["list_reservations"]},
    )
    patch = generate_patch(ctx)
    assert len(patch) == 5
    assert all("list_reservations" in p["path"] for p in patch)


# ---------------------------------------------------------------------------
# cursor_to_offset
# ---------------------------------------------------------------------------


def test_cursor_to_offset_precondition_true() -> None:
    from autobridge.repair.rules.cursor_to_offset import precondition

    spec = _load_spec("harborpms")
    delta = _delta_pagination("list_reservations", "cursor", "offset")
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE", fingerprint_delta=delta)
    assert precondition(ctx) is True


def test_cursor_to_offset_precondition_true_no_delta() -> None:
    from autobridge.repair.rules.cursor_to_offset import precondition

    spec = _load_spec("harborpms")
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE")
    assert precondition(ctx) is True


def test_cursor_to_offset_precondition_false_wrong_class() -> None:
    from autobridge.repair.rules.cursor_to_offset import precondition

    spec = _load_spec("harborpms")
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME")
    assert precondition(ctx) is False


def test_cursor_to_offset_precondition_false_already_offset() -> None:
    from autobridge.repair.rules.cursor_to_offset import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE")
    assert precondition(ctx) is False


def test_cursor_to_offset_patch_shape() -> None:
    from autobridge.repair.rules.cursor_to_offset import generate_patch

    spec = _load_spec("harborpms")
    delta = _delta_pagination("list_reservations", "cursor", "offset")
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE", fingerprint_delta=delta)
    patch = generate_patch(ctx)
    assert len(patch) == 12  # 2 ops * 6
    assert patch[0]["value"] == "offset"
    assert patch[1]["value"] == "offset"
    assert patch[2]["value"] == "limit"
    assert patch[3]["value"] == "$.next_offset"
    result = apply_patch(spec, patch)
    assert result["operations"]["list_reservations"]["pagination"]["strategy"] == "offset"
    assert result["operations"]["list_reservations"]["pagination"]["offset_param"] == "offset"


def test_cursor_to_offset_patch_filtered() -> None:
    from autobridge.repair.rules.cursor_to_offset import generate_patch

    spec = _load_spec("harborpms")
    ctx = RepairContext(
        spec=spec, drift_class="PAGINATION_CHANGE", extra={"operations": ["list_folios"]}
    )
    patch = generate_patch(ctx)
    assert len(patch) == 6
    assert all("list_folios" in p["path"] for p in patch)


# ---------------------------------------------------------------------------
# retry_backoff_only
# ---------------------------------------------------------------------------


def test_retry_backoff_precondition_true_rate_limit() -> None:
    from autobridge.repair.rules.retry_backoff_only import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="RATE_LIMIT", extra={"http_status": 429})
    assert precondition(ctx) is True


def test_retry_backoff_precondition_true_transient() -> None:
    from autobridge.repair.rules.retry_backoff_only import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="TRANSIENT", extra={"http_status": 503})
    assert precondition(ctx) is True


def test_retry_backoff_precondition_true_via_signal() -> None:
    from autobridge.repair.rules.retry_backoff_only import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec,
        drift_class="RATE_LIMIT",
        signals=[{"kind": "rate_limited", "details": {"http_status": 429}}],
    )
    assert precondition(ctx) is True


def test_retry_backoff_precondition_false_wrong_class() -> None:
    from autobridge.repair.rules.retry_backoff_only import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME")
    assert precondition(ctx) is False


def test_retry_backoff_precondition_false_unknown() -> None:
    from autobridge.repair.rules.retry_backoff_only import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="UNKNOWN")
    assert precondition(ctx) is False


def test_retry_backoff_patch_empty_when_already_covered() -> None:
    from autobridge.repair.rules.retry_backoff_only import generate_patch

    spec = _load_spec("atlaspms")
    # Atlas already has 429 and 503 in retry
    ctx = RepairContext(spec=spec, drift_class="RATE_LIMIT", extra={"http_status": 429})
    assert generate_patch(ctx) == []
    ctx2 = RepairContext(spec=spec, drift_class="TRANSIENT", extra={"http_status": 503})
    assert generate_patch(ctx2) == []


def test_retry_backoff_patch_adds_missing_status() -> None:
    from autobridge.repair.rules.retry_backoff_only import generate_patch

    spec = _load_spec("atlaspms")
    # Remove 429 from spec to test adding
    mod = copy.deepcopy(spec)
    mod["source"]["rate_limit"]["retry"]["statuses"] = [503]
    ctx = RepairContext(spec=mod, drift_class="RATE_LIMIT", extra={"http_status": 429})
    patch = generate_patch(ctx)
    assert len(patch) == 1
    assert patch[0]["path"] == "/source/rate_limit/retry/statuses"
    assert 429 in patch[0]["value"]
    assert 503 in patch[0]["value"]
    result = apply_patch(mod, patch)
    assert 429 in result["source"]["rate_limit"]["retry"]["statuses"]


def test_retry_backoff_patch_via_signal_status() -> None:
    from autobridge.repair.rules.retry_backoff_only import generate_patch

    spec = _load_spec("atlaspms")
    mod = copy.deepcopy(spec)
    mod["source"]["rate_limit"]["retry"]["statuses"] = [429]
    ctx = RepairContext(
        spec=mod, drift_class="TRANSIENT", signals=[{"kind": "transient_error", "details": {"http_status": 503}}]
    )
    patch = generate_patch(ctx)
    assert len(patch) == 1
    assert 503 in patch[0]["value"]


# ---------------------------------------------------------------------------
# enum_extend_requires_evidence
# ---------------------------------------------------------------------------


def test_enum_extend_precondition_true() -> None:
    from autobridge.repair.rules.enum_extend_requires_evidence import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec,
        drift_class="ENUM_NEW_VALUE",
        signals=[{"kind": "unknown_enum", "details": {"unmet_criteria": "unknown_enum:status"}}],
    )
    assert precondition(ctx) is True


def test_enum_extend_precondition_true_via_extra() -> None:
    from autobridge.repair.rules.enum_extend_requires_evidence import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="ENUM_NEW_VALUE", extra={"failed_checks": ["unknown_status"]})
    assert precondition(ctx) is True


def test_enum_extend_precondition_false_wrong_class() -> None:
    from autobridge.repair.rules.enum_extend_requires_evidence import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec, drift_class="SCHEMA_RENAME", signals=[{"kind": "unknown_enum", "details": {}}]
    )
    assert precondition(ctx) is False


def test_enum_extend_precondition_false_no_signal() -> None:
    from autobridge.repair.rules.enum_extend_requires_evidence import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="ENUM_NEW_VALUE", signals=[])
    assert precondition(ctx) is False


def test_enum_extend_patch_empty_without_evidence() -> None:
    from autobridge.repair.rules.enum_extend_requires_evidence import generate_patch

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec,
        drift_class="ENUM_NEW_VALUE",
        signals=[{"kind": "unknown_enum", "details": {}}],
        extra={"new_enum_value": "active"},
    )
    assert generate_patch(ctx) == []


def test_enum_extend_patch_with_evidence() -> None:
    from autobridge.repair.rules.enum_extend_requires_evidence import generate_patch

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec,
        drift_class="ENUM_NEW_VALUE",
        signals=[{"kind": "unknown_enum", "details": {"value": "active"}}],
        extra={"evidence_confirmed": True, "new_enum_value": "active", "canonical_value": "confirmed"},
    )
    patch = generate_patch(ctx)
    assert len(patch) == 1
    assert patch[0]["op"] == "replace"
    assert "enum_map" in patch[0]["path"] or "map" in patch[0]["path"]
    assert "active" in patch[0]["value"]
    assert patch[0]["value"]["active"] == "confirmed"
    result = apply_patch(spec, patch)
    # Verify enum_map extended
    found = False
    for ent in result["entities"].values():
        for fm in ent["fields"].values():
            for step in fm.get("transform", []):
                if step.get("name") == "enum_map" and "active" in step.get("args", {}).get("map", {}):
                    found = True
    assert found


def test_enum_extend_patch_no_new_value() -> None:
    from autobridge.repair.rules.enum_extend_requires_evidence import generate_patch

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec, drift_class="ENUM_NEW_VALUE", signals=[{"kind": "unknown_enum", "details": {}}], extra={"evidence_confirmed": True}
    )
    # Should handle gracefully (no new value extractable -> empty)
    assert generate_patch(ctx) == []


def test_enum_extend_hypothesis() -> None:
    from autobridge.repair.rules.enum_extend_requires_evidence import hypothesis_for

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec, drift_class="ENUM_NEW_VALUE", signals=[{"kind": "unknown_enum", "details": {}}], extra={"unknown_value": "active"}
    )
    hyp = hypothesis_for(ctx)
    assert hyp is not None
    assert hyp["requires_evidence"] is True
    assert hyp["rule_id"] == "enum_extend_requires_evidence"


def test_enum_extend_hypothesis_none_when_not_applicable() -> None:
    from autobridge.repair.rules.enum_extend_requires_evidence import hypothesis_for

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME")
    assert hypothesis_for(ctx) is None


# ---------------------------------------------------------------------------
# nested_path_shift
# ---------------------------------------------------------------------------


def test_nested_path_shift_precondition_true_via_delta() -> None:
    from autobridge.repair.rules.nested_path_shift import precondition

    spec = _load_spec("atlaspms")
    delta = _delta_items_path("list_reservations", "$.data", "$.data.records")
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE", fingerprint_delta=delta)
    assert precondition(ctx) is True


def test_nested_path_shift_precondition_true_via_extra() -> None:
    from autobridge.repair.rules.nested_path_shift import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec, drift_class="SCHEMA_RENAME", extra={"operation": "list_reservations", "old_items_path": "$.data", "new_items_path": "$.data.items"}
    )
    assert precondition(ctx) is True


def test_nested_path_shift_precondition_true_extra_items_path_dict() -> None:
    from autobridge.repair.rules.nested_path_shift import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec,
        drift_class="UNKNOWN",
        extra={"items_path": {"old": "$.data", "new": "$.data.payload"}},
    )
    assert precondition(ctx) is True


def test_nested_path_shift_precondition_false_no_delta() -> None:
    from autobridge.repair.rules.nested_path_shift import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME")
    assert precondition(ctx) is False


def test_nested_path_shift_precondition_false_wrong_class_no_delta() -> None:
    from autobridge.repair.rules.nested_path_shift import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="RATE_LIMIT")
    assert precondition(ctx) is False


def test_nested_path_shift_patch_via_delta() -> None:
    from autobridge.repair.rules.nested_path_shift import generate_patch

    spec = _load_spec("atlaspms")
    delta = _delta_items_path("list_reservations", "$.data", "$.data.records")
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE", fingerprint_delta=delta)
    patch = generate_patch(ctx)
    assert len(patch) == 1
    assert patch[0]["path"] == "/operations/list_reservations/pagination/items_path"
    assert patch[0]["value"] == "$.data.records"
    result = apply_patch(spec, patch)
    assert result["operations"]["list_reservations"]["pagination"]["items_path"] == "$.data.records"


def test_nested_path_shift_patch_via_extra() -> None:
    from autobridge.repair.rules.nested_path_shift import generate_patch

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec,
        drift_class="SCHEMA_RENAME",
        extra={"operation": "list_reservations", "new_items_path": "$.payload.data"},
    )
    patch = generate_patch(ctx)
    assert patch[0]["value"] == "$.payload.data"
    result = apply_patch(spec, patch)
    assert result["operations"]["list_reservations"]["pagination"]["items_path"] == "$.payload.data"


def test_nested_path_shift_patch_empty_without_info() -> None:
    from autobridge.repair.rules.nested_path_shift import generate_patch

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME")
    assert generate_patch(ctx) == []


def test_nested_path_shift_patch_via_nested_extra() -> None:
    from autobridge.repair.rules.nested_path_shift import generate_patch

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec,
        drift_class="PAGINATION_CHANGE",
        extra={"nested_path_shift": {"operation": "list_folios", "new_items_path": "$.data.items"}},
    )
    patch = generate_patch(ctx)
    assert len(patch) == 1
    assert "list_folios" in patch[0]["path"]


# ---------------------------------------------------------------------------
# type_coerce_string_number
# ---------------------------------------------------------------------------


def test_type_coerce_precondition_true_via_delta() -> None:
    from autobridge.repair.rules.type_coerce_string_number import precondition

    spec = _load_spec("atlaspms")
    # Need delta with same-field type change (helper creates separate fields)
    delta2 = FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=False,
        keys_changed=False,
        types_changed=True,
        changed_entities=["Reservation"],
        pagination_details={},
        keys_details={},
        types_details={"Reservation": {"previous": {"total_amount": "str"}, "current": {"total_amount": "Decimal"}}},
    )
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_TYPE_CHANGE", fingerprint_delta=delta2)
    assert precondition(ctx) is True


def test_type_coerce_precondition_true_via_extra() -> None:
    from autobridge.repair.rules.type_coerce_string_number import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec,
        drift_class="SCHEMA_TYPE_CHANGE",
        extra={"entity": "Reservation", "field": "total_amount", "old_type": "str", "new_type": "Decimal"},
    )
    assert precondition(ctx) is True


def test_type_coerce_precondition_true_type_change_dict() -> None:
    from autobridge.repair.rules.type_coerce_string_number import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec, drift_class="SCHEMA_TYPE_CHANGE", extra={"type_change": {"entity": "Reservation", "field": "total_amount"}}
    )
    assert precondition(ctx) is True


def test_type_coerce_precondition_false_wrong_class() -> None:
    from autobridge.repair.rules.type_coerce_string_number import precondition

    spec = _load_spec("atlaspms")
    delta2 = FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=False,
        keys_changed=False,
        types_changed=True,
        changed_entities=["Reservation"],
        pagination_details={},
        keys_details={},
        types_details={"Reservation": {"previous": {"total_amount": "str"}, "current": {"total_amount": "Decimal"}}},
    )
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME", fingerprint_delta=delta2)
    assert precondition(ctx) is False


def test_type_coerce_precondition_false_no_info() -> None:
    from autobridge.repair.rules.type_coerce_string_number import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_TYPE_CHANGE")
    assert precondition(ctx) is False


def test_type_coerce_precondition_false_field_not_in_spec() -> None:
    from autobridge.repair.rules.type_coerce_string_number import precondition

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec, drift_class="SCHEMA_TYPE_CHANGE", extra={"entity": "Reservation", "field": "nonexistent_xyz", "old_type": "str", "new_type": "int"}
    )
    assert precondition(ctx) is False


def test_type_coerce_patch_shape() -> None:
    from autobridge.repair.rules.type_coerce_string_number import generate_patch

    spec = _load_spec("atlaspms")
    delta2 = FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=False,
        keys_changed=False,
        types_changed=True,
        changed_entities=["Reservation"],
        pagination_details={},
        keys_details={},
        types_details={"Reservation": {"previous": {"total_amount": "str"}, "current": {"total_amount": "int"}}},
    )
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_TYPE_CHANGE", fingerprint_delta=delta2)
    patch = generate_patch(ctx)
    assert len(patch) == 1
    assert patch[0]["path"] == "/entities/Reservation/fields/total_amount/transform"
    assert patch[0]["value"] == [{"name": "money", "args": {"scale": 2}}]
    result = apply_patch(spec, patch)
    assert result["entities"]["Reservation"]["fields"]["total_amount"]["transform"][0]["name"] == "money"


def test_type_coerce_patch_via_extra() -> None:
    from autobridge.repair.rules.type_coerce_string_number import generate_patch

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec, drift_class="SCHEMA_TYPE_CHANGE", extra={"entity": "Reservation", "field": "total_amount", "old_type": "int", "new_type": "str"}
    )
    patch = generate_patch(ctx)
    assert len(patch) == 1
    result = apply_patch(spec, patch)
    assert result["entities"]["Reservation"]["fields"]["total_amount"]["transform"][0]["name"] == "money"


def test_type_coerce_patch_empty_no_match() -> None:
    from autobridge.repair.rules.type_coerce_string_number import generate_patch

    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_TYPE_CHANGE")
    assert generate_patch(ctx) == []


def test_type_coerce_patch_via_type_change_extra() -> None:
    from autobridge.repair.rules.type_coerce_string_number import generate_patch

    spec = _load_spec("atlaspms")
    ctx = RepairContext(
        spec=spec, drift_class="SCHEMA_TYPE_CHANGE", extra={"type_change": {"entity": "FolioCharge", "field": "amount"}}
    )
    patch = generate_patch(ctx)
    assert len(patch) == 1
    assert "FolioCharge" in patch[0]["path"]


# ---------------------------------------------------------------------------
# matching_rules integration
# ---------------------------------------------------------------------------


def test_matching_rules_finds_correct() -> None:
    spec = _load_spec("atlaspms")
    delta = _delta_with_types("Reservation", "guest_name", "guest_identifier")
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME", fingerprint_delta=delta)
    matched = matching_rules(ctx)
    ids = [r.id for r in matched]
    assert "rename_field" in ids


def test_matching_rules_none_for_unknown() -> None:
    spec = _load_spec("atlaspms")
    ctx = RepairContext(spec=spec, drift_class="UNKNOWN")
    assert matching_rules(ctx) == []


def test_registry_entry_precondition_and_patch_delegation() -> None:
    spec = _load_spec("atlaspms")
    delta = _delta_with_types("Reservation", "guest_name", "guest_identifier")
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME", fingerprint_delta=delta)
    entry = get_rule("rename_field")
    assert entry.precondition(ctx) is True
    patch = entry.generate_patch(ctx)
    assert len(patch) == 1


# ---------------------------------------------------------------------------
# cursor_next_path_shift (REQ-020 item 6: response cursor leaf renamed)
# ---------------------------------------------------------------------------


def _body_cursor_fixture_spec() -> dict:
    """Minimal v2 spec with body_cursor pagination (Meridian shape)."""
    spec = _load_spec("atlaspms")
    for op in spec["operations"].values():
        pag = op.get("pagination", {})
        pag["strategy"] = "body_cursor"
        pag.pop("page_param", None)
        pag.pop("offset_param", None)
        pag.pop("limit_param", None)
        pag.pop("total_path", None)
        pag["cursor_path"] = "Limitation.Cursor"
        pag["count_path"] = "Limitation.Count"
        pag["count_value"] = 100
        pag["next_path"] = "$.Cursor"
        pag["items_path"] = "$.Reservations"
        pag["stop_when"] = ["null_cursor", "empty_page"]
    spec["spec_version"] = 2
    return spec


def _delta_next_path(op: str, old_path: str, new_path: str) -> FingerprintDelta:
    return FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=True,
        keys_changed=False,
        types_changed=False,
        changed_entities=[],
        pagination_details={
            op: {
                "previous": {"strategy": "body_cursor", "next_path": old_path, "pages_fetched": 8},
                "current": {"strategy": "body_cursor", "next_path": new_path, "pages_fetched": 1},
            }
        },
        keys_details={},
        types_details={},
    )


def test_cursor_next_path_shift_precondition_true() -> None:
    from autobridge.repair.rules.cursor_next_path_shift import precondition

    spec = _body_cursor_fixture_spec()
    delta = _delta_next_path("list_reservations", "$.Cursor", "$.NextCursor")
    ctx = RepairContext(
        spec=spec,
        drift_class="PAGINATION_CHANGE",
        fingerprint_delta=delta,
        extra={"new_next_path": "$.NextCursor"},
    )
    assert precondition(ctx) is True


def test_cursor_next_path_shift_precondition_true_no_delta() -> None:
    from autobridge.repair.rules.cursor_next_path_shift import precondition

    spec = _body_cursor_fixture_spec()
    ctx = RepairContext(
        spec=spec,
        drift_class="PAGINATION_CHANGE",
        extra={"new_next_path": "$.NextCursor"},
    )
    assert precondition(ctx) is True


def test_cursor_next_path_shift_precondition_false_wrong_class() -> None:
    from autobridge.repair.rules.cursor_next_path_shift import precondition

    spec = _body_cursor_fixture_spec()
    ctx = RepairContext(
        spec=spec,
        drift_class="SCHEMA_RENAME",
        extra={"new_next_path": "$.NextCursor"},
    )
    assert precondition(ctx) is False


def test_cursor_next_path_shift_precondition_false_no_new_path() -> None:
    from autobridge.repair.rules.cursor_next_path_shift import precondition

    spec = _body_cursor_fixture_spec()
    delta = _delta_next_path("list_reservations", "$.Cursor", "$.NextCursor")
    ctx = RepairContext(spec=spec, drift_class="PAGINATION_CHANGE", fingerprint_delta=delta)
    assert precondition(ctx) is False


def test_cursor_next_path_shift_precondition_false_page_spec() -> None:
    from autobridge.repair.rules.cursor_next_path_shift import precondition

    spec = _load_spec("atlaspms")  # page strategy, not cursor family
    ctx = RepairContext(
        spec=spec,
        drift_class="PAGINATION_CHANGE",
        extra={"new_next_path": "$.NextCursor"},
    )
    assert precondition(ctx) is False


def test_cursor_next_path_shift_patch_shape_and_valid() -> None:
    from autobridge.repair.rules.cursor_next_path_shift import generate_patch, precondition

    spec = _body_cursor_fixture_spec()
    ctx = RepairContext(
        spec=spec,
        drift_class="PAGINATION_CHANGE",
        extra={"new_next_path": "$.NextCursor"},
    )
    assert precondition(ctx) is True
    patch = generate_patch(ctx)
    assert patch, "expected non-empty patch"
    assert all(p["op"] == "replace" and p["path"].endswith("/pagination/next_path") for p in patch)
    assert all(p["value"] == "$.NextCursor" for p in patch)
    assert len(patch) == len(spec["operations"])
    # Patched spec still validates as v2.
    new_spec = apply_patch(spec, patch)
    from autobridge.spec.models import load_spec_from_dict

    parsed = load_spec_from_dict(new_spec)
    assert parsed is not None
