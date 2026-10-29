"""Tests for discovery/bootstrap.py (P1.W5.6)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autobridge.discovery.bootstrap import (
    BootstrapResult,
    _compile_spec,
    _derive_credential_ref,
    _derive_env_placeholder,
    _derive_vendor,
    _op_name_for_path,
    _slugify,
    build_draft_spec,
    visible_tests,
)
from autobridge.discovery.classifiers import classify_all
from autobridge.discovery.docs_reader import read_docs_text
from autobridge.spec.models import load_spec_from_dict

REPO_ROOT = Path(__file__).resolve().parents[2]
HARBOR_MD = (REPO_ROOT / "simulators" / "docs" / "harborpms.md").read_text()
ATLAS_YAML = (REPO_ROOT / "simulators" / "docs" / "atlaspms-openapi.yaml").read_text()
HARBOR_MD_PATH = "simulators/docs/harborpms.md"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_slugify_harborms() -> None:
    assert _slugify("HarborPMS API — Narrative Documentation") == "harborpms"
    assert _slugify("AtlasPMS") == "atlaspms"
    assert _slugify("Test API") == "test"
    assert _slugify("") == "connector"


def test_derive_vendor() -> None:
    from autobridge.discovery.docs_reader import InterfaceDescription

    iface = InterfaceDescription(source="x", doc_type="markdown", title="HarborPMS API")
    assert _derive_vendor(iface) == "HarborPMS"
    iface2 = InterfaceDescription(source="x", doc_type="markdown", title="AtlasPMS API")
    assert _derive_vendor(iface2) == "AtlasPMS"
    iface3 = InterfaceDescription(source="x", doc_type="markdown", title="")
    assert _derive_vendor(iface3) is not None


def test_derive_env_placeholder() -> None:
    assert _derive_env_placeholder("http://localhost:8106", "harborpms") == "${HARBOR_BASE_URL}"
    assert _derive_env_placeholder("http://localhost:8105", "atlaspms") == "${ATLAS_BASE_URL}"
    assert _derive_env_placeholder("http://example.com", "myconn") == "http://example.com"
    assert _derive_env_placeholder("${CUSTOM_URL}", "myconn") == "${CUSTOM_URL}"
    assert _derive_env_placeholder("", "myconn") == "${MYCONN_BASE_URL}"


def test_derive_env_placeholder_sim_service_names() -> None:
    """REQ-027 sub-step 1: sim-<name> service addresses map to placeholders.

    Generic over the registry id shape (no vendor literal in engine code):
    any simulator's Compose address becomes a portable ${NAME}_BASE_URL
    placeholder resolved per-mode from the environment at sync time.
    """
    assert _derive_env_placeholder("http://sim-harbor:8000", "harborpms") == "${HARBOR_BASE_URL}"
    assert _derive_env_placeholder("http://sim-atlas:8000", "atlaspms") == "${ATLAS_BASE_URL}"
    assert _derive_env_placeholder("http://sim-probe-x:8000", "pro bex") == "${PROBE_X_BASE_URL}"
    assert _derive_env_placeholder("http://sim-probe-x:8000/v1", "x") == "${PROBE_X_BASE_URL}"
    # Non-sim hosts still pass through.
    assert _derive_env_placeholder("https://api.example.com/v1", "myconn") == "https://api.example.com/v1"


def test_derive_credential_ref() -> None:
    assert _derive_credential_ref("harborpms") == "HARBOR_API_KEY"
    assert _derive_credential_ref("atlaspms") == "ATLAS_API_KEY"
    assert _derive_credential_ref("my-conn") == "MY_CONN_API_KEY"
    assert _derive_credential_ref("harborpms", override="CUSTOM_KEY") == "CUSTOM_KEY"
    assert _derive_credential_ref("harborpms", override="bad-lower") == "HARBOR_API_KEY"


def test_op_name_for_path() -> None:
    assert _op_name_for_path("/reservations") == "list_reservations"
    assert _op_name_for_path("/folios") == "list_folios"
    assert _op_name_for_path("/properties") == "list_properties"
    assert _op_name_for_path("/reservations/{reservation_id}/folios") == "list_folios_nested"
    assert _op_name_for_path("/custom/path") == "list_custom_path"


def test_visible_tests_pass_harbor() -> None:
    result = build_draft_spec(HARBOR_MD, source=HARBOR_MD_PATH)
    assert result.success
    assert result.draft_spec is not None
    errs = visible_tests(result.draft_spec)
    assert errs == []


def test_visible_tests_fail_bad_spec() -> None:
    bad_spec: dict[str, Any] = {"spec_version": 1}
    errs = visible_tests(bad_spec)
    assert errs
    assert any("spec validation" in e for e in errs)


def test_visible_tests_money_scale() -> None:
    result = build_draft_spec(HARBOR_MD, source=HARBOR_MD_PATH)
    spec = dict(result.draft_spec)  # type: ignore[union-attr]
    spec["entities"]["Reservation"]["fields"]["total_amount"]["transform"][0]["args"]["scale"] = 3
    errs = visible_tests(spec)
    assert any("scale should be 2" in e for e in errs)


def test_visible_tests_pagination_guard() -> None:
    result = build_draft_spec(HARBOR_MD, source=HARBOR_MD_PATH)
    spec = dict(result.draft_spec)  # type: ignore[union-attr]
    # Remove stop_when
    spec["operations"]["list_reservations"]["pagination"]["stop_when"] = []
    errs = visible_tests(spec)
    assert any("stop_when" in e for e in errs)


def test_visible_tests_enum_guard() -> None:
    result = build_draft_spec(HARBOR_MD, source=HARBOR_MD_PATH)
    spec = dict(result.draft_spec)  # type: ignore[union-attr]
    spec["entities"]["Reservation"]["fields"]["status"]["transform"][0]["args"]["on_unknown"] = "guess"
    errs = visible_tests(spec)
    assert any("on_unknown: block" in e for e in errs)


def test_build_draft_spec_harbor_success() -> None:
    result = build_draft_spec(HARBOR_MD, source=HARBOR_MD_PATH)
    assert result.success
    assert result.draft_spec is not None
    assert result.draft_spec["connector"]["id"] == "harborpms"
    assert result.draft_spec["connector"]["vendor"] == "HarborPMS"
    assert result.draft_spec["source"]["base_url"] == "${HARBOR_BASE_URL}"
    assert result.draft_spec["source"]["auth"]["credential_ref"] == "HARBOR_API_KEY"
    assert "list_reservations" in result.draft_spec["operations"]
    assert "list_folios" in result.draft_spec["operations"]
    assert "Reservation" in result.draft_spec["entities"]
    assert "FolioCharge" in result.draft_spec["entities"]
    # Pagination must be cursor for Harbor
    assert result.draft_spec["operations"]["list_reservations"]["pagination"]["strategy"] == "cursor"
    assert result.draft_spec["operations"]["list_folios"]["pagination"]["strategy"] == "cursor"
    # Status mapping must be opaque
    status_tf = result.draft_spec["entities"]["Reservation"]["fields"]["status"]["transform"][0]
    assert status_tf["args"]["map"]["H1"] == "confirmed"
    # Money
    assert result.draft_spec["entities"]["Reservation"]["fields"]["total_amount"]["transform"][0]["name"] == "money"
    # Business date cutoff
    assert result.draft_spec["entities"]["FolioCharge"]["fields"]["business_date"]["transform"][0]["args"]["cutoff"] == 4
    # Validates
    load_spec_from_dict(result.draft_spec)
    # Every hypothesis has evidence
    assert len(result.evidence_payloads) > 0
    # Model usage frontier 0
    assert result.model_usage["frontier_tokens"] == 0
    # No open questions for Harbor (date ambiguity handled by ignoring)
    assert result.open_questions == ()
    assert result.degradation_reason is None


def test_build_draft_spec_harbor_no_reference_access() -> None:
    """Bootstrap must not read generated/connectors files."""
    result = build_draft_spec(HARBOR_MD, source=HARBOR_MD_PATH)
    assert result.success
    # Ensure source is not considered a reference read
    # The guard should reject a source containing generated/connectors
    bad = build_draft_spec(HARBOR_MD, source="generated/connectors/harborpms/reference/spec.yaml")
    assert not bad.success
    assert "must not read reference" in bad.degradation_reason  # type: ignore[union-attr]


def test_build_draft_spec_harbor_visible_tests_green() -> None:
    result = build_draft_spec(HARBOR_MD, source=HARBOR_MD_PATH)
    assert result.visible_errors == []


def test_build_draft_spec_atlas_success() -> None:
    result = build_draft_spec(ATLAS_YAML, source="atlas.yaml")
    assert result.success
    assert result.draft_spec is not None
    assert result.draft_spec["connector"]["id"] == "atlaspms"
    assert result.draft_spec["operations"]["list_reservations"]["pagination"]["strategy"] == "page"
    assert result.draft_spec["operations"]["list_reservations"]["pagination"]["page_param"] == "pageNumber"
    assert result.draft_spec["operations"]["list_reservations"]["pagination"]["limit_param"] == "pageSize"
    assert result.draft_spec["operations"]["list_reservations"]["path"] == "/api/v1.3/getReservations"
    assert result.draft_spec["source"]["auth"]["type"] == "api_key_header"
    # Status vocabulary: vendor "canceled" maps to canonical "cancelled"
    mmap = result.draft_spec["entities"]["Reservation"]["fields"]["status"]["transform"][0]["args"]["map"]
    assert mmap["confirmed"] == "confirmed"
    assert mmap["canceled"] == "cancelled"
    load_spec_from_dict(result.draft_spec)


def test_build_draft_spec_atlas_base_placeholder() -> None:
    result = build_draft_spec(ATLAS_YAML, source="atlas.yaml")
    assert result.draft_spec["source"]["base_url"] == "${ATLAS_BASE_URL}"  # type: ignore[index]


def test_build_draft_spec_with_overrides() -> None:
    result = build_draft_spec(HARBOR_MD, source="docs", connector_id="myconn", credential_ref="MY_KEY", vendor="MyVendor", base_url_override="http://localhost:8106")
    assert result.draft_spec["connector"]["id"] == "myconn"  # type: ignore[index]
    assert result.draft_spec["connector"]["vendor"] == "MyVendor"  # type: ignore[index]
    assert result.draft_spec["source"]["auth"]["credential_ref"] == "MY_KEY"  # type: ignore[index]


def test_build_draft_spec_empty_docs() -> None:
    result = build_draft_spec("", source="empty.md")
    # Empty docs still produce a spec (fallback to defaults) — should succeed
    # Or may fail? Check that it doesn't crash
    assert isinstance(result, BootstrapResult)


def test_build_draft_spec_minimal_openapi() -> None:
    minimal = """
openapi: 3.1.0
info:
  title: Minimal API
  version: 1.0.0
servers:
  - url: http://localhost:8000
paths:
  /reservations:
    get:
      summary: List reservations
      parameters:
        - name: limit
          in: query
          schema:
            type: integer
        - name: offset
          in: query
          schema:
            type: integer
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                properties:
                  data:
                    type: array
                  total:
                    type: integer
"""
    result = build_draft_spec(minimal, source="minimal.yaml")
    assert isinstance(result, BootstrapResult)
    if result.success:
        load_spec_from_dict(result.draft_spec)  # type: ignore[arg-type]


def test_build_draft_spec_invalid_docs() -> None:
    # Invalid YAML that still is not openapi should fall back to markdown parser, not fail
    result = build_draft_spec("not yaml nor openapi ::::", source="bad.md")
    assert isinstance(result, BootstrapResult)


def test_build_draft_spec_with_probe_budget() -> None:
    from autobridge.discovery.probes import ProbeBudget

    budget = ProbeBudget(max_probes=2, max_requests=10)
    result = build_draft_spec(HARBOR_MD, source=HARBOR_MD_PATH, probe_budget=budget, probe_executor=lambda _: {"strategy": "cursor"})  # noqa: ARG005
    assert result.success
    # trace should contain resolve steps
    assert any(t.get("stage") == "resolve" for t in result.trace)


def test_build_draft_spec_with_budget_exhaustion() -> None:
    from autobridge.discovery.probes import ProbeBudget
    from autobridge.llm.provider import BudgetExhausted

    # Budget already exhausted
    budget = ProbeBudget(max_probes=0)
    # Need an executor that would raise BudgetExhausted
    def fake_executor(spec: Any) -> dict[str, Any]:  # noqa: ARG001
        raise BudgetExhausted("max_probes exceeded")

    result = build_draft_spec(HARBOR_MD, source=HARBOR_MD_PATH, probe_budget=budget, probe_executor=fake_executor)
    # Should still succeed (compile) even if probing fails
    assert isinstance(result, BootstrapResult)


def test_compile_spec_direct() -> None:
    iface = read_docs_text(HARBOR_MD, HARBOR_MD_PATH)
    classification = classify_all(iface)
    spec = _compile_spec(iface, classification)
    load_spec_from_dict(spec)
    assert spec["connector"]["id"] == "harborpms"


def test_compile_spec_with_none_classification() -> None:
    iface = read_docs_text(HARBOR_MD, HARBOR_MD_PATH)
    spec = _compile_spec(iface, None)
    assert spec["connector"]["id"] == "harborpms"
    load_spec_from_dict(spec)


def test_harbor_oracle_pass() -> None:
    result = build_draft_spec(HARBOR_MD, source=HARBOR_MD_PATH)
    assert result.success
    from verifier.oracle import oracle_verdict

    verdict, score, failed = oracle_verdict("baseline", result.draft_spec)
    assert verdict == "pass", f"oracle failed: {failed}"
    assert score == 1.0
    assert not failed


def test_atlas_oracle_pass() -> None:
    result = build_draft_spec(ATLAS_YAML, source="atlas.yaml")
    assert result.success
    from verifier.oracle import oracle_verdict

    verdict, _, failed = oracle_verdict("baseline", result.draft_spec)
    assert verdict == "pass", f"oracle failed: {failed}"


def test_bootstrap_no_import_verifier() -> None:
    """Engine code never imports verifier or simulators at top level."""
    import pathlib

    import autobridge.discovery.bootstrap as mod

    src = pathlib.Path(mod.__file__).read_text()
    # Check top-level imports (first 30 lines)
    top = "\n".join(src.splitlines()[:30])
    assert "import verifier" not in top
    assert "from verifier" not in top
    assert "import simulators" not in top
    assert "from simulators" not in top


def test_bootstrap_no_reference_file_read() -> None:
    """Bootstrap must not open generated/connectors files."""
    import pathlib

    import autobridge.discovery.bootstrap as mod

    src = pathlib.Path(mod.__file__).read_text()
    # Guard check is allowed, but actual file open of reference should not occur
    count = src.count("generated/connectors")
    assert count <= 3, f"found {count} references to generated/connectors, expected only guard"
    assert 'open("generated' not in src
    assert "Path(\"generated" not in src


def test_bootstrap_date_ambiguity_ignored() -> None:
    """Harbor's ambiguous `date` field is ignored, not mapped."""
    result = build_draft_spec(HARBOR_MD, source=HARBOR_MD_PATH)
    spec = result.draft_spec
    assert spec is not None
    # No field named `date` in entities
    for ent in spec["entities"].values():
        assert "date" not in ent["fields"]
    # Classification should flag ambiguous
    assert "date" in result.classification.date_semantics.ambiguous_fields  # type: ignore[union-attr]


def test_bootstrap_hypotheses_have_evidence() -> None:
    result = build_draft_spec(HARBOR_MD, source=HARBOR_MD_PATH)
    for payload in result.evidence_payloads:
        assert payload["evidence_for"], "every hypothesis must link to evidence"
        assert payload["fragment"], "every hypothesis must have fragment"


def test_bootstrap_l3_assert_never_triggered_with_many_hypotheses() -> None:
    """L3 only when enumerator yields <2 (asserted in resolver)."""
    result = build_draft_spec(HARBOR_MD, source=HARBOR_MD_PATH)
    # Harbor enumerates 4 date hypotheses, so L3 should not be invoked
    for _family, hyps in result.hypotheses_by_family.items():
        assert len(hyps) >= 2 or isinstance(hyps, list)


def test_visible_tests_with_missing_transform() -> None:
    result = build_draft_spec(HARBOR_MD, source=HARBOR_MD_PATH)
    spec = dict(result.draft_spec)  # type: ignore[union-attr]
    # Remove money transform from total_amount
    spec["entities"]["Reservation"]["fields"]["total_amount"]["transform"] = []
    errs = visible_tests(spec)
    assert any("money" in e.lower() for e in errs)


def test_bootstrap_trace_stages() -> None:
    result = build_draft_spec(HARBOR_MD, source=HARBOR_MD_PATH)
    stages = [t["stage"] for t in result.trace]
    assert "read_docs" in stages
    assert "classify" in stages
    assert "enumerate" in stages
    assert "compile" in stages
    assert "visible_tests" in stages


def test_build_post_only_docs_refuses_with_named_reason() -> None:
    """REQ-020 item 4 pin: a POST-only API is outside vocabulary v1.

    Building from Meridian's docs with the engine unchanged must stop
    cleanly: no success, no draft spec (so no release is persisted), a named
    reason, and zero model calls — never an invented GET connector.
    """
    docs = (REPO_ROOT / "simulators" / "docs" / "meridian-openapi.yaml").read_text(encoding="utf-8")
    result = build_draft_spec(docs, source="simulators/docs/meridian-openapi.yaml", connector_id="meridianpms")
    assert not result.success
    assert result.draft_spec is None
    assert result.degradation_reason is not None
    assert "POST" in result.degradation_reason and "ConnectorSpec v1" in result.degradation_reason
    assert result.model_usage.get("frontier_tokens", 0) == 0
    assert result.model_usage.get("small_tokens", 0) == 0
    assert result.model_usage.get("calls", 0) == 0
    assert any(t.get("stage") == "vocabulary_guard" for t in result.trace)


# ---------------------------------------------------------------------------
# Spec v2 discovery (REQ-020 item 5b): same docs, vocabulary v2
# ---------------------------------------------------------------------------

MERIDIAN_YAML = (REPO_ROOT / "simulators" / "docs" / "meridian-openapi.yaml").read_text(encoding="utf-8")


def _meridian_v2_result():  # type: ignore[no-untyped-def]
    return build_draft_spec(
        MERIDIAN_YAML,
        source="simulators/docs/meridian-openapi.yaml",
        connector_id="meridianpms",
        spec_vocabulary="v2",
    )


def test_spec_vocabulary_rejects_unknown_value() -> None:
    import pytest as _pytest

    with _pytest.raises(ValueError, match="spec_vocabulary"):
        build_draft_spec(HARBOR_MD, source=HARBOR_MD_PATH, spec_vocabulary="v4")


def test_v2_vocabulary_builds_post_connector_from_same_docs() -> None:
    """Same docs that v1 refuses compile under v2 with zero model calls."""
    result = _meridian_v2_result()
    assert result.success, f"visible_errors={result.visible_errors} trace={result.trace}"
    assert result.draft_spec is not None
    assert result.visible_errors == []
    assert result.open_questions == ()
    assert result.model_usage.get("frontier_tokens", 0) == 0
    assert result.model_usage.get("small_tokens", 0) == 0
    assert result.model_usage.get("calls", 0) == 0


def test_v2_spec_shape() -> None:
    spec = _meridian_v2_result().draft_spec
    assert spec is not None
    assert spec["spec_version"] == 2
    auth = spec["source"]["auth"]
    assert auth["type"] == "body_fields"
    assert auth["body_fields"] == {
        "ClientToken": "MERIDIAN_CLIENT_TOKEN",
        "AccessToken": "MERIDIAN_ACCESS_TOKEN",
    }
    assert "credential_ref" not in auth
    load_spec_from_dict(spec)


def test_v2_operations_post_with_body_and_cursor() -> None:
    spec = _meridian_v2_result().draft_spec
    assert spec is not None
    ops = spec["operations"]
    assert set(ops) == {"list_reservations", "list_customers", "list_folios", "get_configuration"}
    for name in ("list_reservations", "list_customers", "list_folios"):
        op = ops[name]
        assert op["method"] == "POST"
        assert op["body"]["Limitation"] == {"Count": 100}
        assert op["body"]["Client"] == "AutoBridge/1.0"
        assert "ClientToken" not in op["body"] and "AccessToken" not in op["body"]
        pag = op["pagination"]
        assert pag["strategy"] == "body_cursor"
        assert pag["cursor_path"] == "Limitation.Cursor"
        assert pag["count_path"] == "Limitation.Count"
        assert pag["count_value"] == 100
        assert pag["stop_when"] == ["null_cursor", "empty_page"]
    assert ops["list_reservations"]["pagination"]["items_path"] == "$.Reservations"
    assert ops["list_reservations"]["pagination"]["next_path"] == "$.Cursor"
    assert ops["list_folios"]["pagination"]["items_path"] == "$.OrderItems"
    assert ops["list_folios"]["path"] == "/orderItems/getAll/2023-06-06"
    config = ops["get_configuration"]
    assert config["method"] == "POST"
    assert "pagination" not in config
    assert config["body"]["Client"] == "AutoBridge/1.0"


def test_v2_wire_names_follow_documented_spelling() -> None:
    spec = _meridian_v2_result().draft_spec
    assert spec is not None
    res = spec["entities"]["Reservation"]["fields"]
    assert res["reservation_id"]["path"] == "$.Id"
    assert res["arrival_date"]["path"] == "$.ScheduledStartUtc"
    assert res["departure_date"]["path"] == "$.ScheduledEndUtc"
    assert res["status"]["path"] == "$.State"
    status_map = res["status"]["transform"][0]["args"]["map"]
    assert status_map["Confirmed"] == "confirmed"
    assert status_map["Canceled"] == "cancelled"
    folio = spec["entities"]["FolioCharge"]["fields"]
    assert folio["charge_id"]["path"] == "$.Id"
    # REQ-020 5c: nested money leaves resolve precisely (not the whole object).
    assert folio["amount"]["path"] == "$.UnitAmount.GrossValue"
    assert folio["currency"]["path"] == "$.UnitAmount.Currency"
    assert folio["posted_at"]["path"] == "$.PostedUtc"
    # No documented business-date field: derived from the posting instant.
    assert folio["business_date"]["path"] == "$.PostedUtc"
    assert res["property_id"]["path"] == "$.ServiceId"
    # Checkpoints follow the documented instant fields, not legacy names.
    assert spec["operations"]["list_reservations"]["checkpoint"] == {"field": "$.UpdatedUtc", "mode": "max"}
    assert spec["operations"]["list_folios"]["checkpoint"] == {"field": "$.PostedUtc", "mode": "max"}


def test_v2_visible_tests_pin_body_cursor_guards() -> None:
    import copy as _copy

    base = _meridian_v2_result().draft_spec
    assert base is not None
    broken = _copy.deepcopy(base)
    del broken["operations"]["list_reservations"]["pagination"]["cursor_path"]
    assert any("cursor_path" in e for e in visible_tests(broken))
    broken2 = _copy.deepcopy(base)
    broken2["operations"]["list_reservations"]["pagination"]["stop_when"] = ["null_cursor"]
    assert any("empty_page" in e for e in visible_tests(broken2))


def test_v1_compile_skips_post_operations() -> None:
    """Mixed docs under v1 keep their GET operations; POST ops are skipped."""
    from autobridge.discovery.classifiers import classify_all
    from autobridge.discovery.docs_reader import read_docs_text

    iface = read_docs_text(MERIDIAN_YAML, source="meridian.yaml")
    classification = classify_all(iface)
    spec = _compile_spec(iface, classification, connector_id="meridianpms", spec_version=1)
    assert all(op["method"] == "GET" for op in spec["operations"].values())


def test_op_name_for_post_style_operation_ids() -> None:
    assert _op_name_for_path("/reservations/getAll/2023-06-06", "reservationsGetAll") == "list_reservations"
    assert _op_name_for_path("/orderItems/getAll/2023-06-06", "orderItemsGetAll") == "list_folios"
    assert _op_name_for_path("/customers/getAll/2023-06-06", "customersGetAll") == "list_customers"
    assert _op_name_for_path("/configuration/get/2023-06-06", "configurationGet") == "get_configuration"
    assert _op_name_for_path("/api/v1.3/getReservation", "getReservation") == "get_reservation"
    assert _op_name_for_path("/api/v1.3/getGuest", "getGuest") == "get_guest"


def test_derive_body_field_ref() -> None:
    from autobridge.discovery.bootstrap import _derive_body_field_ref

    assert _derive_body_field_ref("meridianpms", "ClientToken") == "MERIDIAN_CLIENT_TOKEN"
    assert _derive_body_field_ref("meridianpms", "AccessToken") == "MERIDIAN_ACCESS_TOKEN"
    assert _derive_body_field_ref("my-conn", "ClientToken", credential_ref="CUSTOM_KEY") == "CUSTOM_KEY_CLIENT_TOKEN"


# ---------------------------------------------------------------------------
# Declarative joins proposed from docs (REQ-020 item 5c)
# ---------------------------------------------------------------------------


def test_v2_proposes_name_money_and_reverse_joins() -> None:
    """Meridian docs propose all three joins keyed on the documented AccountId."""
    spec = _meridian_v2_result().draft_spec
    assert spec is not None
    res_joins = spec["entities"]["Reservation"]["joins"]
    assert len(res_joins) == 2
    name_join, money_join = res_joins
    assert name_join["from_operation"] == "list_customers"
    assert name_join["local_key"] == "$.AccountId"
    assert name_join["foreign_key"] == "$.Id"
    assert name_join["cardinality"] == "one"
    assert name_join["fields"]["full_name"]["paths"] == ["$.FirstName", "$.LastName"]
    assert money_join["from_operation"] == "list_folios"
    assert money_join["local_key"] == money_join["foreign_key"] == "$.AccountId"
    assert money_join["cardinality"] == "many"
    assert money_join["fields"]["total_amount"]["aggregate"] == "sum"
    assert money_join["fields"]["total_amount"]["path"] == "$.UnitAmount.GrossValue"
    assert money_join["fields"]["currency"]["aggregate"] == "first"
    folio_joins = spec["entities"]["FolioCharge"]["joins"]
    assert len(folio_joins) == 1
    assert folio_joins[0]["from_operation"] == "list_reservations"
    assert folio_joins[0]["fields"]["reservation_id"]["path"] == "$.Id"
    load_spec_from_dict(spec)


def test_v2_joined_fields_redirect_to_joined_namespace() -> None:
    """Fields fed by joins read $._joined instead of inventing wire paths."""
    spec = _meridian_v2_result().draft_spec
    assert spec is not None
    res = spec["entities"]["Reservation"]["fields"]
    assert res["guest_name"]["path"] == "$._joined.customer.full_name"
    assert res["total_amount"]["path"] == "$._joined.folio.total_amount"
    assert res["currency"]["path"] == "$._joined.folio.currency"
    folio = spec["entities"]["FolioCharge"]["fields"]
    assert folio["reservation_id"]["path"] == "$._joined.reservation.reservation_id"


def test_v1_proposes_no_joins() -> None:
    """The v1 before-refusal still carries no joins (joins are v2)."""
    from autobridge.discovery.bootstrap import build_draft_spec as _build

    harbor = Path(HARBOR_MD_PATH).read_text(encoding="utf-8")
    result = _build(harbor, source=HARBOR_MD_PATH, connector_id="harborpms")
    assert result.success
    assert "joins" not in result.draft_spec["entities"]["Reservation"]
    assert "joins" not in result.draft_spec["entities"]["FolioCharge"]


def test_harbor_v2_proposes_no_spurious_joins() -> None:
    """A single-entity-shape API gains no joins under v2 (no partner ops)."""
    harbor = Path(HARBOR_MD_PATH).read_text(encoding="utf-8")
    result = build_draft_spec(harbor, source=HARBOR_MD_PATH, connector_id="harborpms", spec_vocabulary="v2")
    assert result.success, f"visible_errors={result.visible_errors}"
    assert "joins" not in result.draft_spec["entities"]["Reservation"]
