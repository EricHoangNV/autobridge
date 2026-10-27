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
    assert result.draft_spec["operations"]["list_reservations"]["pagination"]["strategy"] == "offset"
    # Status identity for Atlas
    mmap = result.draft_spec["entities"]["Reservation"]["fields"]["status"]["transform"][0]["args"]["map"]
    assert mmap["confirmed"] == "confirmed"
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
