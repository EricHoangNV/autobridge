"""ConnectorSpec v1 tests (P1.W1.3): models, JSON Schema round-trip, patch, registry."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from autobridge.spec import models as m
from autobridge.spec.models import (
    ConnectorSpec,
    format_validation_errors,
    load_spec_from_dict,
    load_spec_from_yaml,
    spec_to_json_data,
    validate_against_schema_file,
)
from autobridge.spec.patch import (
    PatchError,
    SpecValidationError,
    apply_patch,
    diff_specs,
    reverse_patch,
    validate_patch_shape,
)
from autobridge.spec.registry import (
    ReleaseCorruptError,
    ReleaseExistsError,
    ReleaseNotFoundError,
    list_releases,
    load_release,
    release_exists,
    spec_signature,
    store_release,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILE = ROOT / "schemas" / "connector-spec.v3.json"
SCHEMA_FILE_V2 = ROOT / "schemas" / "connector-spec.v2.json"
SCHEMA_FILE_V1 = ROOT / "schemas" / "connector-spec.v1.json"

HARBOR_YAML = """
spec_version: 1
connector: { id: harborpms, vendor: HarborPMS, runtime_target: cloud }
source:
  base_url: ${HARBOR_BASE_URL}
  auth: { type: bearer, credential_ref: HARBOR_API_KEY }
  rate_limit: { max_rps: 5, retry: { statuses: [429, 503], backoff: exponential, max_attempts: 5 } }
  timezone_source: { kind: static, value: America/New_York }
operations:
  list_reservations:
    method: GET
    path: /v2/bookings
    params: { updated_since: "{{checkpoint.reservations}}" }
    pagination:
      strategy: cursor
      cursor_param: cursor
      next_path: $.meta.next_cursor
      items_path: $.data
      stop_when: [null_cursor, repeated_cursor]
    checkpoint: { field: $.updated_at, mode: max }
entities:
  Reservation:
    from: list_reservations
    key: $.id
    fields:
      reservation_id: { path: $.id }
      arrival_date:   { path: $.stay.from, transform: [date_local, { tz: source }] }
      departure_date: { path: $.stay.to, transform: [date_local, { tz: source }] }
      status:
        path: $.state
        transform:
          - name: enum_map
            args: { map: { CI: checked_in, CO: checked_out, RES: confirmed }, on_unknown: block }
      total_amount:   { path: $.totals.gross, transform: [money, { scale: 2 }] }
      currency:       { path: $.totals.currency }
    checks:
      - { kind: unique_key }
      - { kind: no_loss, compare_to: $.meta.total_count }
      - { kind: date_order, before: arrival_date, after: departure_date }
  FolioCharge:
    from: list_reservations
    key: $.folio.id
    fields:
      charge_id: { path: $.folio.id }
      amount: { path: $.folio.amount, transform: money }
    checks:
      - { kind: unique_key }
capabilities:
  read: [Reservation, FolioCharge]
  action: []
health:
  canary: { every: 60s, operation: list_reservations, max_pages: 1 }
  fingerprint: { keys: true, types: true, pagination_shape: true }
"""


def harbor_data(**overrides: Any) -> dict[str, Any]:
    data = yaml.safe_load(HARBOR_YAML)
    assert isinstance(data, dict)
    for key, value in overrides.items():
        data[key] = value
    return data


def harbor_spec() -> ConnectorSpec:
    return load_spec_from_dict(harbor_data())


# --- round-trip: YAML -> model -> JSON Schema ---


def test_yaml_to_model_round_trip() -> None:
    spec = harbor_spec()
    assert spec.connector.id == "harborpms"
    assert spec.spec_version == 1
    assert spec.entities["Reservation"].from_operation == "list_reservations"
    arrival = spec.entities["Reservation"].fields["arrival_date"]
    assert arrival.transform[0].name == "date_local"
    assert arrival.transform[0].args == {"tz": "source"}
    status = spec.entities["Reservation"].fields["status"]
    assert status.transform[0].name == "enum_map"
    dumped = spec_to_json_data(spec)
    assert dumped["entities"]["Reservation"]["from"] == "list_reservations"
    assert load_spec_from_dict(dumped) == spec


def test_dump_validates_against_schema_file() -> None:
    validate_against_schema_file(spec_to_json_data(harbor_spec()))


def test_schema_file_matches_models() -> None:
    on_disk = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert on_disk == ConnectorSpec.model_json_schema()


def test_frozen_schema_files_unchanged() -> None:
    """v1/v2 schema files stay frozen: v1 pins const 1, v2 pins enum [1, 2]."""
    assert SCHEMA_FILE_V1.is_file()
    v1_disk = json.loads(SCHEMA_FILE_V1.read_text(encoding="utf-8"))
    assert v1_disk["properties"]["spec_version"].get("const") == 1
    assert SCHEMA_FILE_V2.is_file()
    v2_disk = json.loads(SCHEMA_FILE_V2.read_text(encoding="utf-8"))
    assert v2_disk["properties"]["spec_version"]["enum"] == [1, 2]


def test_load_spec_from_yaml_file(tmp_path: Path) -> None:
    path = tmp_path / "spec.yaml"
    path.write_text(HARBOR_YAML, encoding="utf-8")
    assert load_spec_from_yaml(path) == harbor_spec()


def test_load_spec_from_yaml_invalid_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: [unclosed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid YAML"):
        load_spec_from_yaml(bad)
    lst = tmp_path / "list.yaml"
    lst.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_spec_from_yaml(lst)


def test_load_spec_from_non_mapping() -> None:
    with pytest.raises(TypeError, match="must be a mapping"):
        load_spec_from_dict([1, 2, 3])  # type: ignore[arg-type]


def test_format_validation_errors_paths() -> None:
    data = harbor_data()
    del data["source"]["auth"]["credential_ref"]
    with pytest.raises(ValidationError) as exc:
        load_spec_from_dict(data)
    lines = format_validation_errors(exc.value)
    assert lines
    assert any("credential_ref" in line for line in lines)


# --- closed transform set ---


@pytest.mark.parametrize("name", list(m.TRANSFORMS))
def test_closed_transform_set_all_names_accepted(name: str) -> None:
    transform: Any
    if name == "enum_map":
        transform = {
            "name": name,
            "args": {"map": {"A": "b"}, "on_unknown": "block"},
        }
    elif name == "const":
        transform = {"name": name, "args": {"value": 1}}
    else:
        transform = name
    data = harbor_data()
    data["entities"]["Reservation"]["fields"]["probe"] = {"path": "$.x", "transform": transform}
    spec = load_spec_from_dict(data)
    assert spec.entities["Reservation"].fields["probe"].transform[0].name == name


def test_unknown_transform_rejected_with_path() -> None:
    data = harbor_data()
    data["entities"]["Reservation"]["fields"]["status"]["transform"] = ["tellemap"]
    with pytest.raises(ValidationError) as exc:
        load_spec_from_dict(data)
    locs = [e["loc"] for e in exc.value.errors()]
    assert any("transform" in loc for loc in locs)


def test_transform_bare_args_first_rejected() -> None:
    data = harbor_data()
    data["entities"]["Reservation"]["fields"]["total_amount"]["transform"] = [{"scale": 2}]
    with pytest.raises(ValidationError, match="must follow a transform name"):
        load_spec_from_dict(data)


def test_transform_empty_list_means_no_transform() -> None:
    data = harbor_data()
    data["entities"]["Reservation"]["fields"]["total_amount"]["transform"] = []
    spec = load_spec_from_dict(data)
    assert spec.entities["Reservation"].fields["total_amount"].transform == []
    data["entities"]["Reservation"]["fields"]["total_amount"]["transform"] = None
    assert load_spec_from_dict(data).entities["Reservation"].fields["total_amount"].transform == []


def test_field_mapping_accepts_built_steps() -> None:
    field = m.FieldMapping(
        path="$.x", transform=[m.TransformStep(name="money", args={"scale": 2})]
    )
    assert field.transform[0].args == {"scale": 2}


def test_transform_bad_item_type_rejected() -> None:
    data = harbor_data()
    data["entities"]["Reservation"]["fields"]["total_amount"]["transform"] = [42]
    with pytest.raises(ValidationError, match="must be a name or"):
        load_spec_from_dict(data)


def test_transform_chain_and_explicit_form() -> None:
    data = harbor_data()
    data["entities"]["Reservation"]["fields"]["total_amount"]["transform"] = [
        "flatten",
        {"name": "money", "args": {"scale": 2}},
    ]
    spec = load_spec_from_dict(data)
    steps = spec.entities["Reservation"].fields["total_amount"].transform
    assert [s.name for s in steps] == ["flatten", "money"]
    assert steps[1].args == {"scale": 2}


def test_transform_explicit_form_single_mapping() -> None:
    data = harbor_data()
    data["entities"]["Reservation"]["fields"]["total_amount"]["transform"] = {
        "name": "money",
        "args": {"scale": 2},
    }
    spec = load_spec_from_dict(data)
    assert spec.entities["Reservation"].fields["total_amount"].transform[0].args == {"scale": 2}


def test_transform_explicit_form_bad_args() -> None:
    data = harbor_data()
    data["entities"]["Reservation"]["fields"]["total_amount"]["transform"] = [
        {"name": "money", "args": 42}
    ]
    with pytest.raises(ValidationError, match="'args' must be a mapping"):
        load_spec_from_dict(data)


def test_transform_non_list_scalar_rejected() -> None:
    data = harbor_data()
    data["entities"]["Reservation"]["fields"]["total_amount"]["transform"] = 42
    with pytest.raises(ValidationError, match="must be a name or a list"):
        load_spec_from_dict(data)


def test_enum_map_requires_map_and_block() -> None:
    data = harbor_data()
    field = data["entities"]["Reservation"]["fields"]["status"]
    field["transform"] = [{"name": "enum_map", "args": {"map": {"A": "b"}, "on_unknown": "guess"}}]
    with pytest.raises(ValidationError, match="on_unknown: block"):
        load_spec_from_dict(data)
    field["transform"] = [{"name": "enum_map", "args": {"on_unknown": "block"}}]
    with pytest.raises(ValidationError, match="non-empty 'map'"):
        load_spec_from_dict(data)
    field["transform"] = [{"name": "enum_map", "args": {"map": {}, "on_unknown": "block"}}]
    with pytest.raises(ValidationError, match="non-empty 'map'"):
        load_spec_from_dict(data)


def test_const_requires_value_and_money_scale_bounds() -> None:
    step_ok = m.TransformStep(name="const", args={"value": 1})
    assert step_ok.args == {"value": 1}
    with pytest.raises(ValidationError, match="requires a 'value'"):
        m.TransformStep(name="const", args={})
    with pytest.raises(ValidationError, match="'scale' must be an int"):
        m.TransformStep(name="money", args={"scale": 7})
    with pytest.raises(ValidationError, match="'scale' must be an int"):
        m.TransformStep(name="money", args={"scale": True})
    assert m.TransformStep(name="money", args={"scale": 0}).args == {"scale": 0}


# --- JSONPath subset ---


@pytest.mark.parametrize(
    "path", ["$", "$.data", "$.meta.next_cursor", "$.a[0].b", "$['x']", '$["y"]', "$.a[*]"]
)
def test_jsonpath_subset_accepts(path: str) -> None:
    assert m.check_jsonpath(path) == path


@pytest.mark.parametrize(
    "path",
    [
        "data",
        "$.data[?(@.x > 1)]",
        "$..book",
        "$.a + $.b",
        "$.a(@.b)",
        "$.a b",
        "",
        "$.a|b",
    ],
)
def test_jsonpath_subset_rejects(path: str) -> None:
    with pytest.raises(ValueError, match="JSONPath subset"):
        m.check_jsonpath(path)


def test_jsonpath_violation_has_field_path() -> None:
    data = harbor_data()
    data["operations"]["list_reservations"]["pagination"]["items_path"] = "$.data[?(@.x)]"
    with pytest.raises(ValidationError) as exc:
        load_spec_from_dict(data)
    assert any(list(e["loc"][-1:] ) == ["items_path"] for e in exc.value.errors())


# --- invalid specs: path-level errors ---


def test_missing_required_field_reports_path() -> None:
    data = harbor_data()
    del data["entities"]["Reservation"]["key"]
    with pytest.raises(ValidationError) as exc:
        load_spec_from_dict(data)
    assert ("entities", "Reservation", "key") in [e["loc"] for e in exc.value.errors()]


def test_spec_version_accepts_1_2_and_3() -> None:
    assert load_spec_from_dict(harbor_data()).spec_version == 1
    assert load_spec_from_dict(harbor_data(spec_version=2)).spec_version == 2
    assert load_spec_from_dict(harbor_data(spec_version=3)).spec_version == 3
    with pytest.raises(ValidationError):
        load_spec_from_dict(harbor_data(spec_version=4))


def test_v1_rejects_v2_features() -> None:
    # POST operations, body templates, body_fields auth and body_cursor
    # pagination are not expressible in ConnectorSpec v1 (REQ-020.5a).
    data = harbor_data(spec_version=2)
    auth = data["source"]["auth"]
    op = data["operations"]["list_reservations"]
    pag = op["pagination"]
    # Sanity: the v2 variants below are accepted with spec_version 2.
    op["method"] = "POST"
    op["body"] = {"EnterpriseIds": ["e1"], "Limitation": {"Count": 100}}
    auth.update({"type": "body_fields", "body_fields": {"ClientToken": "VENDOR_CLIENT_TOKEN"}})
    auth.pop("credential_ref", None)
    pag.update({"strategy": "body_cursor", "cursor_path": "Limitation.Cursor",
                "count_path": "Limitation.Count", "count_value": 100})
    pag.pop("cursor_param", None)
    spec = load_spec_from_dict(data)
    assert spec.spec_version == 2
    validate_against_schema_file(spec_to_json_data(spec))
    # Each v2 feature alone is rejected with spec_version 1.
    for mutate in (
        lambda d: d["operations"]["list_reservations"].update({"method": "POST"}),
        lambda d: d["operations"]["list_reservations"].update({"method": "POST", "body": {"A": 1}}),
        lambda d: d["source"]["auth"].update(
            {"type": "body_fields", "body_fields": {"ClientToken": "VENDOR_CLIENT_TOKEN"}}),
        lambda d: d["operations"]["list_reservations"]["pagination"].update(
            {"strategy": "body_cursor", "cursor_path": "Limitation.Cursor"}),
    ):
        bad = harbor_data()
        mutate(bad)
        if bad["source"]["auth"].get("type") == "body_fields":
            bad["source"]["auth"].pop("credential_ref", None)
        with pytest.raises(ValidationError, match="spec_version 2"):
            load_spec_from_dict(bad)


def test_v1_spec_file_stays_valid_under_current_schema() -> None:
    assert SCHEMA_FILE_V1.is_file()
    validate_against_schema_file(spec_to_json_data(harbor_spec()))


def test_body_fields_auth_shape() -> None:
    data = harbor_data(spec_version=2)
    auth = data["source"]["auth"]
    auth.update({"type": "body_fields"})
    auth.pop("credential_ref", None)
    with pytest.raises(ValidationError, match="body_fields mapping"):
        load_spec_from_dict(data)
    auth["body_fields"] = {"ClientToken": "bad-ref"}
    with pytest.raises(ValidationError, match="env key name"):
        load_spec_from_dict(data)
    auth["body_fields"] = {"ClientToken": "VENDOR_CLIENT_TOKEN", "AccessToken": "VENDOR_ACCESS_TOKEN"}
    assert load_spec_from_dict(data).source.auth.type == "body_fields"
    # body_fields refs must not leak into the dumped spec as values.
    dumped = spec_to_json_data(load_spec_from_dict(data))
    assert dumped["source"]["auth"]["body_fields"] == {
        "ClientToken": "VENDOR_CLIENT_TOKEN", "AccessToken": "VENDOR_ACCESS_TOKEN"}


def test_body_cursor_pagination_shape() -> None:
    data = harbor_data(spec_version=2)
    pag = data["operations"]["list_reservations"]["pagination"]
    pag.update({"strategy": "body_cursor", "cursor_path": "Limitation.Cursor"})
    pag.pop("cursor_param", None)
    spec = load_spec_from_dict(data)
    assert spec.operations["list_reservations"].pagination is not None
    pag.pop("cursor_path")
    with pytest.raises(ValidationError, match="cursor_path and next_path"):
        load_spec_from_dict(data)


def test_get_with_body_rejected() -> None:
    data = harbor_data(spec_version=2)
    data["operations"]["list_reservations"]["body"] = {"A": 1}
    with pytest.raises(ValidationError, match="GET operations must not set body"):
        load_spec_from_dict(data)


def test_inline_secret_rejected_by_extra_forbid() -> None:
    data = harbor_data()
    data["source"]["auth"]["token"] = "s3cr3t-value"
    with pytest.raises(ValidationError) as exc:
        load_spec_from_dict(data)
    assert any("token" in str(e["loc"]) for e in exc.value.errors())


def test_credential_ref_shape() -> None:
    data = harbor_data()
    data["source"]["auth"]["credential_ref"] = "lowercase-key"
    with pytest.raises(ValidationError, match="env key name"):
        load_spec_from_dict(data)


def test_base_url_embedded_credentials_rejected() -> None:
    data = harbor_data()
    data["source"]["base_url"] = "https://user:pass@harbor.example.com"
    with pytest.raises(ValidationError, match="must not embed credentials"):
        load_spec_from_dict(data)


def test_base_url_bad_placeholder_and_bare_dollar() -> None:
    data = harbor_data()
    data["source"]["base_url"] = "https://${lower-case}/api"
    with pytest.raises(ValidationError, match="UPPER_SNAKE"):
        load_spec_from_dict(data)
    data["source"]["base_url"] = "https://h.example.com/$foo"
    with pytest.raises(ValidationError, match="only use"):
        load_spec_from_dict(data)
    data["source"]["base_url"] = "ftp://h.example.com"
    with pytest.raises(ValidationError, match="http"):
        load_spec_from_dict(data)


def test_auth_per_type_requirements() -> None:
    data = harbor_data()
    auth = data["source"]["auth"]
    auth.update({"type": "api_key_header"})
    with pytest.raises(ValidationError, match="header_name"):
        load_spec_from_dict(data)
    auth.update({"type": "basic"})
    with pytest.raises(ValidationError, match="username"):
        load_spec_from_dict(data)
    auth.update({"type": "oauth2_client_credentials"})
    with pytest.raises(ValidationError, match="token_url"):
        load_spec_from_dict(data)
    auth.update(
        {
            "type": "api_key_header",
            "header_name": "X-Key",
            "credential_ref": "HARBOR_API_KEY",
        }
    )
    auth.pop("username", None)
    auth.pop("token_url", None)
    assert load_spec_from_dict(data).source.auth.header_name == "X-Key"


def test_timezone_static_and_field() -> None:
    data = harbor_data()
    data["source"]["timezone_source"] = {"kind": "static", "value": "Not A Zone"}
    with pytest.raises(ValidationError, match="IANA"):
        load_spec_from_dict(data)
    data["source"]["timezone_source"] = {"kind": "field"}
    with pytest.raises(ValidationError, match="requires path"):
        load_spec_from_dict(data)
    data["source"]["timezone_source"] = {"kind": "field", "path": "$.property.tz"}
    assert load_spec_from_dict(data).source.timezone_source.kind == "field"


def test_pagination_per_strategy() -> None:
    data = harbor_data()
    pag = data["operations"]["list_reservations"]["pagination"]
    pag.pop("next_path")
    with pytest.raises(ValidationError, match="cursor_param and next_path"):
        load_spec_from_dict(data)
    pag.update({"strategy": "offset", "offset_param": "offset"})
    pag.pop("cursor_param", None)
    assert load_spec_from_dict(data).operations["list_reservations"].pagination is not None
    pag.pop("offset_param")
    with pytest.raises(ValidationError, match="offset_param"):
        load_spec_from_dict(data)
    pag.update({"strategy": "page"})
    with pytest.raises(ValidationError, match="page_param"):
        load_spec_from_dict(data)


def test_retry_statuses_and_empty() -> None:
    data = harbor_data()
    retry = data["source"]["rate_limit"]["retry"]
    retry["statuses"] = []
    with pytest.raises(ValidationError, match="at least one status"):
        load_spec_from_dict(data)
    retry["statuses"] = [99]
    with pytest.raises(ValidationError, match="not a valid HTTP status"):
        load_spec_from_dict(data)


def test_operation_path_and_method() -> None:
    data = harbor_data()
    op = data["operations"]["list_reservations"]
    op["path"] = "https://h.example.com/v2"
    with pytest.raises(ValidationError, match="relative path"):
        load_spec_from_dict(data)
    op["path"] = "/v2/bookings"
    op["method"] = "POST"
    with pytest.raises(ValidationError):
        load_spec_from_dict(data)


def test_no_loss_and_date_order_operands() -> None:
    data = harbor_data()
    checks = data["entities"]["Reservation"]["checks"]
    checks[1] = {"kind": "no_loss"}
    with pytest.raises(ValidationError, match="compare_to"):
        load_spec_from_dict(data)
    checks[1] = {"kind": "no_loss", "compare_to": "$.meta.total_count"}
    checks[2] = {"kind": "date_order", "before": "arrival_date"}
    with pytest.raises(ValidationError, match="before and after"):
        load_spec_from_dict(data)


def test_cross_references() -> None:
    data = harbor_data()
    data["entities"]["Reservation"]["from"] = "nope"
    with pytest.raises(ValidationError, match="unknown operation"):
        load_spec_from_dict(data)
    data = harbor_data()
    data["entities"]["Reservation"]["checks"][2] = {
        "kind": "date_order",
        "before": "arrival_date",
        "after": "missing_field",
    }
    with pytest.raises(ValidationError, match="unknown field"):
        load_spec_from_dict(data)
    data = harbor_data()
    data["capabilities"]["read"] = ["Ghost"]
    with pytest.raises(ValidationError, match="unknown entity"):
        load_spec_from_dict(data)
    data = harbor_data()
    data["health"]["canary"]["operation"] = "nope"
    with pytest.raises(ValidationError, match="unknown operation"):
        load_spec_from_dict(data)


def test_name_shapes() -> None:
    data = harbor_data()
    data["connector"]["id"] = "Bad Id!"
    with pytest.raises(ValidationError, match="slug"):
        load_spec_from_dict(data)
    data = harbor_data()
    data["operations"] = {"Bad-Name": data["operations"]["list_reservations"]}
    with pytest.raises(ValidationError, match="operation name"):
        load_spec_from_dict(data)
    data = harbor_data()
    data["entities"] = {"reservation": data["entities"]["Reservation"]}
    with pytest.raises(ValidationError, match="PascalCase"):
        load_spec_from_dict(data)
    data = harbor_data()
    data["entities"]["Reservation"]["fields"] = {"Bad-Name": {"path": "$.id"}}
    with pytest.raises(ValidationError, match="field name"):
        load_spec_from_dict(data)


def test_canary_interval_shape() -> None:
    data = harbor_data()
    data["health"]["canary"]["every"] = "hourly"
    with pytest.raises(ValidationError, match="60s"):
        load_spec_from_dict(data)


def test_default_schema_path_points_at_repo_file() -> None:
    assert m.default_schema_path() == SCHEMA_FILE
    assert SCHEMA_FILE.is_file()


def test_validate_against_schema_file_fails_for_bad_data() -> None:
    import jsonschema

    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema_file({"spec_version": 1})


# --- patch ---


def _dump() -> dict[str, Any]:
    return spec_to_json_data(harbor_spec())


def test_validate_patch_shape_ok_and_bad() -> None:
    good = [{"op": "replace", "path": "/source/rate_limit/max_rps", "value": 10}]
    assert validate_patch_shape(good) == good
    with pytest.raises(PatchError, match="non-empty"):
        validate_patch_shape([])
    with pytest.raises(PatchError, match="non-empty"):
        validate_patch_shape("nope")
    with pytest.raises(PatchError, match="unknown op"):
        validate_patch_shape([{"op": "merge", "path": "/a", "value": 1}])
    with pytest.raises(PatchError, match="must be a JSON Pointer"):
        validate_patch_shape([{"op": "remove", "path": "a"}])
    with pytest.raises(PatchError, match="requires a value"):
        validate_patch_shape([{"op": "add", "path": "/a"}])
    with pytest.raises(PatchError, match="requires a 'from'"):
        validate_patch_shape([{"op": "move", "path": "/a"}])
    with pytest.raises(PatchError, match="must be a mapping"):
        validate_patch_shape(["x"])
    err = None
    try:
        validate_patch_shape([{"op": "remove"}])
    except PatchError as e:
        err = e
    assert err is not None and err.op_index == 0


def test_apply_patch_replace_and_add() -> None:
    base = _dump()
    patch = [
        {"op": "replace", "path": "/source/rate_limit/max_rps", "value": 10},
        {"op": "add", "path": "/operations/list_reservations/params/page_size", "value": "50"},
    ]
    out = apply_patch(base, patch)
    assert out["source"]["rate_limit"]["max_rps"] == 10
    assert out["operations"]["list_reservations"]["params"]["page_size"] == "50"
    assert base["source"]["rate_limit"]["max_rps"] == 5  # input untouched


def test_apply_patch_accepts_model_instance() -> None:
    out = apply_patch(
        harbor_spec(), [{"op": "replace", "path": "/source/rate_limit/max_rps", "value": 7}]
    )
    assert out["source"]["rate_limit"]["max_rps"] == 7


def test_apply_patch_missing_path() -> None:
    with pytest.raises(PatchError, match="does not apply"):
        apply_patch(_dump(), [{"op": "remove", "path": "/operations/nope"}])


def test_apply_patch_invalid_result_rejected() -> None:
    base = _dump()
    before = copy.deepcopy(base)
    with pytest.raises(SpecValidationError) as exc:
        apply_patch(
            base,
            [{"op": "replace", "path": "/entities/Reservation/fields/status/transform", "value": ["nope"]}],
        )
    assert exc.value.errors  # path-level errors attached
    assert base == before  # input still untouched


def test_apply_patch_non_mapping_result() -> None:
    with pytest.raises((PatchError, SpecValidationError)):
        apply_patch(_dump(), [{"op": "replace", "path": "", "value": []}])


def test_diff_and_reverse_round_trip() -> None:
    base = _dump()
    patch = [
        {"op": "replace", "path": "/source/rate_limit/max_rps", "value": 10},
        {"op": "replace", "path": "/operations/list_reservations/path", "value": "/v3/bookings"},
    ]
    after = apply_patch(base, patch)
    assert diff_specs(base, after) == patch or apply_patch(base, diff_specs(base, after)) == after
    inverse = reverse_patch(base, patch)
    assert apply_patch(after, inverse) == base


def test_diff_specs_accepts_models() -> None:
    base = harbor_spec()
    after = _dump()
    after["source"]["rate_limit"]["max_rps"] = 9
    patch = diff_specs(base, after)
    assert apply_patch(base, patch) == after


def test_move_copy_test_ops() -> None:
    base = _dump()
    out = apply_patch(
        base,
        [
            {"op": "test", "path": "/connector/id", "value": "harborpms"},
            {"op": "copy", "from": "/connector/id", "path": "/operations/list_reservations/params/cid"},
            {"op": "move", "from": "/operations/list_reservations/params/cid", "path": "/operations/list_reservations/params/did"},
        ],
    )
    assert out["operations"]["list_reservations"]["params"]["did"] == "harborpms"


# --- registry ---


def test_registry_store_load_round_trip(tmp_path: Path) -> None:
    spec = harbor_spec()
    target = store_release(tmp_path, "harborpms", "r1", spec, {"built_by": "test"})
    assert (target / "spec.yaml").is_file()
    assert (target / "build-report.json").is_file()
    loaded, report = load_release(tmp_path, "harborpms", "r1")
    assert loaded == spec
    assert report["connector_id"] == "harborpms"
    assert report["release_id"] == "r1"
    assert report["built_by"] == "test"
    assert report["signature"] == spec_signature(spec)
    assert release_exists(tmp_path, "harborpms", "r1")
    assert list_releases(tmp_path, "harborpms") == ["r1"]


def test_registry_accepts_plain_dict_and_lists_sorted(tmp_path: Path) -> None:
    store_release(tmp_path, "harborpms", "r2", _dump(), {})
    store_release(tmp_path, "harborpms", "r1", _dump(), {})
    assert list_releases(tmp_path, "harborpms") == ["r1", "r2"]
    assert list_releases(tmp_path, "unknown") == []
    assert not release_exists(tmp_path, "harborpms", "r9")


def test_registry_write_once(tmp_path: Path) -> None:
    store_release(tmp_path, "harborpms", "r1", _dump(), {})
    with pytest.raises(ReleaseExistsError):
        store_release(tmp_path, "harborpms", "r1", _dump(), {})


def test_registry_rejects_invalid_spec_without_writing(tmp_path: Path) -> None:
    bad = _dump()
    bad["connector"]["id"] = "Bad!"
    with pytest.raises(ValidationError):
        store_release(tmp_path, "harborpms", "r1", bad, {})
    assert not (tmp_path / "harborpms" / "r1").exists()


def test_registry_rejects_non_serializable_report(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not JSON-serializable"):
        store_release(tmp_path, "harborpms", "r1", _dump(), {"x": object()})


def test_registry_rejects_bad_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="path separators"):
        store_release(tmp_path, "../evil", "r1", _dump(), {})
    with pytest.raises(ValueError, match="path separators"):
        store_release(tmp_path, "harborpms", "../r1", _dump(), {})
    with pytest.raises(ValueError, match="not a valid id"):
        store_release(tmp_path, "Bad Id!", "r1", _dump(), {})
    with pytest.raises(ValueError, match="path separators"):
        list_releases(tmp_path, "..")
    with pytest.raises(ValueError, match="not a valid id"):
        list_releases(tmp_path, "Bad Id!")


def test_registry_missing_release_and_corrupt(tmp_path: Path) -> None:
    with pytest.raises(ReleaseNotFoundError):
        load_release(tmp_path, "harborpms", "r1")
    target = store_release(tmp_path, "harborpms", "r1", _dump(), {})
    (target / "spec.yaml").unlink()
    with pytest.raises(ReleaseCorruptError, match="missing files"):
        load_release(tmp_path, "harborpms", "r1")


def test_registry_corrupt_unreadable_and_malformed(tmp_path: Path) -> None:
    target = store_release(tmp_path, "harborpms", "r1", _dump(), {})
    (target / "spec.yaml").write_text("a: [unclosed\n", encoding="utf-8")
    with pytest.raises(ReleaseCorruptError, match="unreadable"):
        load_release(tmp_path, "harborpms", "r1")
    (target / "spec.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ReleaseCorruptError, match="malformed"):
        load_release(tmp_path, "harborpms", "r1")
    spec = _dump()
    spec["source"]["auth"]["credential_ref"] = "nope-lowercase"
    (target / "spec.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    with pytest.raises(ReleaseCorruptError, match="spec invalid"):
        load_release(tmp_path, "harborpms", "r1")


def test_registry_signature_mismatch(tmp_path: Path) -> None:
    target = store_release(tmp_path, "harborpms", "r1", _dump(), {})
    report = json.loads((target / "build-report.json").read_text(encoding="utf-8"))
    report["signature"] = "0" * 64
    (target / "build-report.json").write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ReleaseCorruptError, match="signature mismatch"):
        load_release(tmp_path, "harborpms", "r1")


def test_registry_cleans_up_half_written_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json as jsonlib

    real_dumps = jsonlib.dumps
    calls = 0

    def flaky_dumps(*args: Any, **kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        if calls == 3:  # fail while writing build-report.json, after spec.yaml
            raise OSError("disk full")
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(jsonlib, "dumps", flaky_dumps)
    with pytest.raises(OSError, match="disk full"):
        store_release(tmp_path, "harborpms", "r1", _dump(), {})
    assert not (tmp_path / "harborpms" / "r1").exists()
    assert not release_exists(tmp_path, "harborpms", "r1")


def test_signature_stable_and_form() -> None:
    sig = spec_signature(harbor_spec())
    assert sig == spec_signature(_dump())
    assert len(sig) == 64 and all(c in "0123456789abcdef" for c in sig)


def test_default_base_dir_points_at_repo_generated() -> None:
    from autobridge.spec.registry import default_base_dir

    assert default_base_dir() == ROOT / "generated" / "connectors"


def test_spec_v1_cannot_express_post_with_body_auth() -> None:
    """REQ-020 item 4 pin: ConnectorSpec v1 is GET-only with header auth.

    The "before" refusal is structural, not a guess: any Meridian-shaped
    operation (POST + JSON body + body-field auth) fails v1 validation, so
    a spec-v1-only engine must stop with Blocked instead of persisting a
    half-working connector.
    """
    data = harbor_data()
    op = data["operations"]["list_reservations"]
    op["method"] = "POST"
    op["body"] = {"EnterpriseIds": ["e1"], "Limitation": {"Count": 100}}
    with pytest.raises(ValidationError, match="spec_version 2"):
        load_spec_from_dict(data)


def test_patch_can_touch_v2_fields() -> None:
    """REQ-020.5a: JSON Patches reach the new v2 fields (repair plumbing)."""
    from autobridge.spec.patch import apply_patch

    data = harbor_data(spec_version=2)
    op = data["operations"]["list_reservations"]
    op["method"] = "POST"
    op["body"] = {"Limitation": {"Count": 100}}
    pag = op["pagination"]
    pag.update({"strategy": "body_cursor", "cursor_path": "Limitation.Cursor",
                "count_path": "Limitation.Count", "count_value": 100})
    pag.pop("cursor_param", None)
    patched = apply_patch(
        data,
        [{"op": "replace",
          "path": "/operations/list_reservations/pagination/count_value",
          "value": 50}],
    )
    assert patched["operations"]["list_reservations"]["pagination"]["count_value"] == 50
    assert load_spec_from_dict(patched).spec_version == 2


# --- REQ-020 owner-check regression (2026-10-28): v1 signatures stable across v2 ---


def _reference_report(connector_id: str) -> dict[str, Any]:
    report_path = ROOT / "generated" / "connectors" / connector_id / "reference" / "build-report.json"
    assert report_path.is_file(), f"committed reference report missing: {report_path}"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert isinstance(report, dict)
    return report


def test_committed_reference_releases_verify() -> None:
    """Committed reference releases load without signature mismatch.

    Regression for the REQ-020.5a break: ``spec_signature()`` changed for
    v1 specs, so ``load_release`` raised ``ReleaseCorruptError`` on the
    committed ``generated/connectors/*/reference`` releases. This lives in
    unit tests (not only the e2e file) so ``make test`` catches it even
    when only one e2e file is selected.
    """
    for cid in ("atlaspms", "harborpms"):
        report = _reference_report(cid)
        loaded, reloaded_report = load_release(ROOT / "generated" / "connectors", cid, "reference")
        assert loaded.connector.id == cid
        assert reloaded_report["signature"] == report["signature"]
        assert reloaded_report["signature"] == spec_signature(loaded)


def test_v1_reference_signatures_golden() -> None:
    """Golden pin: ``spec_signature()`` of each committed v1 reference spec.

    The hex values below are the signatures stored before spec v2 shipped;
    they must never change without a deliberate, reviewed re-signing. Do
    not re-sign or rewrite the committed reference releases to make this
    pass — fix the canonicalisation instead.
    """
    golden = {
        "atlaspms": "7a3c60cecda5d8df3d3c0dbbf98a62d742fd7dcd80a72f818ad962e35977115a",
        "harborpms": "8a463c2451e6eead4a3ecf06d557f70e7c35ffd533870cb767cf3cf02664f21d",
    }
    from autobridge.control.models import spec_signature as control_signature

    for cid, expected in golden.items():
        report = _reference_report(cid)
        assert report["signature"] == expected, f"{cid} reference was re-signed; see test docstring"
        spec_path = ROOT / "generated" / "connectors" / cid / "reference" / "spec.yaml"
        data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert load_spec_from_dict(data).spec_version == 1
        assert spec_signature(load_spec_from_dict(data)) == expected
        # The DB signing path agrees with the registry path when unkeyed.
        assert control_signature(spec_to_json_data(load_spec_from_dict(data))) == expected


def _v2_spec_with_all_features() -> dict[str, Any]:
    """Harbor-shaped v2 spec exercising every v2-only field."""
    data = harbor_data(spec_version=2)
    auth = data["source"]["auth"]
    op = data["operations"]["list_reservations"]
    pag = op["pagination"]
    op["method"] = "POST"
    op["body"] = {"EnterpriseIds": ["e1"], "Limitation": {"Count": 100}}
    auth.update({"type": "body_fields", "body_fields": {"ClientToken": "VENDOR_CLIENT_TOKEN"}})
    auth.pop("credential_ref", None)
    pag.update({"strategy": "body_cursor", "cursor_path": "Limitation.Cursor",
                "count_path": "Limitation.Count", "count_value": 100})
    pag.pop("cursor_param", None)
    data["entities"]["Reservation"]["joins"] = [{
        "from_operation": "list_reservations",
        "local_key": "$.id",
        "foreign_key": "$.id",
        "cardinality": "many",
        "as": "charges",
        "fields": {"total": {"path": "$.folio.amount", "aggregate": "sum",
                             "transform": [{"name": "money"}]}},
    }]
    return data


def test_v2_field_change_moves_signature() -> None:
    """A v2 spec's signature changes when any v2 field changes."""
    base = _v2_spec_with_all_features()
    base_sig = spec_signature(load_spec_from_dict(base))
    # Sanity: a v2 spec with real v2 content differs from its v1 cousin.
    assert base_sig != spec_signature(harbor_spec())

    def mutated(path: str, value: Any) -> str:
        from autobridge.spec.patch import apply_patch

        patched = apply_patch(copy.deepcopy(base), [{"op": "replace", "path": path, "value": value}])
        return spec_signature(load_spec_from_dict(patched))

    assert mutated("/operations/list_reservations/body/Limitation/Count", 50) != base_sig
    assert mutated("/operations/list_reservations/pagination/count_value", 50) != base_sig
    assert mutated("/operations/list_reservations/pagination/cursor_path",
                   "Limitation.Resume") != base_sig
    assert mutated("/source/auth/body_fields/ClientToken", "OTHER_TOKEN") != base_sig
    assert mutated("/entities/Reservation/joins/0/fields/total/aggregate", "count") != base_sig


def test_v1_signature_ignores_defaulted_v2_keys() -> None:
    """Defaulted v2-only keys are not part of a v1 signature.

    Pins the canonicalisation directly: a v1 dump with explicit
    ``body: None`` / ``body_fields: None`` / ``cursor_path: None`` /
    ``joins: []`` hashes exactly like the same dump without those keys.
    """
    from autobridge.spec.models import signature_canonical_data

    dumped = spec_to_json_data(harbor_spec())
    padded = copy.deepcopy(dumped)
    padded["operations"]["list_reservations"]["body"] = None
    padded["operations"]["list_reservations"]["pagination"].update(
        {"cursor_path": None, "count_path": None, "count_value": None})
    padded["source"]["auth"]["body_fields"] = None
    padded["entities"]["Reservation"]["joins"] = []
    assert spec_signature(padded) == spec_signature(dumped)
    assert signature_canonical_data(padded) == signature_canonical_data(dumped)
    assert "body" not in signature_canonical_data(padded)["operations"]["list_reservations"]
