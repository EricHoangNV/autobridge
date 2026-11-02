"""REQ-087: the dry-run gate judges checks under the spec's declared zone.

A spec with a static ``timezone_source`` whose records derive business
dates under that zone but not under UTC or the legacy map:
- the dry-run passes a correct ``input_format`` date repair;
- it still refuses a patch that leaves a genuinely wrong business date;
- the old-check pass agrees with ``evaluate_entity_checks`` called with
  the same zone.
"""

from __future__ import annotations

from typing import Any

from autobridge.repair.dryrun import (
    _entities_failing_old_checks,
    _evaluate_checks_on_records,
    dry_run_proposal,
)
from autobridge.runtime.checks import evaluate_entity_checks, spec_static_tz


def _spec() -> dict[str, Any]:
    """Valid Harbor-based spec retargeted at the REQ-087 zone case.

    Starts from the real ``harbor.md`` bootstrap (so ``apply_patch``
    validation passes) and narrows ``FolioCharge`` to the business-date
    mapping under test, with ``Reservation`` checks cleared so the gate
    covers only ``FolioCharge``. The source keeps the static
    ``America/New_York`` zone the bootstrap declares.
    """
    import copy
    import json
    import pathlib

    from autobridge.discovery.bootstrap import build_draft_spec

    docs = (pathlib.Path(__file__).resolve().parents[2] / "simulators" / "docs" / "harbor.md").read_text(
        encoding="utf-8"
    )
    res = build_draft_spec(
        docs,
        source="simulators/docs/harbor.md",
        connector_id="x",
        vendor="harborpms",
        spec_vocabulary="v1",
        credential_ref="HARBOR_API_KEY",
        base_url_override="http://localhost:8106",
    )
    assert res.success, res.degradation_reason
    spec = json.loads(json.dumps(res.draft_spec, default=str))
    spec = copy.deepcopy(spec)
    assert spec["source"]["timezone_source"] == {"kind": "static", "value": "America/New_York"}
    folio = spec["entities"]["FolioCharge"]
    folio["fields"] = {
        "charge_id": {"path": "$.charge_id", "transform": []},
        "business_date": {
            "path": "$.bdate",
            "transform": [{"name": "date_local", "args": {}}],
        },
        "posted_at": {"path": "$.posted"},
        "property_id": {"path": "$.prop"},
    }
    folio["checks"] = [
        {
            "kind": "business_date_invariant",
            "field": "business_date",
            "reference_field": "posted_at",
        }
    ]
    spec["entities"]["Reservation"]["checks"] = []
    return spec


def _wire(bdate: str) -> dict[str, Any]:
    return {
        "charge_id": "c1",
        "bdate": bdate,
        "posted": "2026-10-16T04:30:00Z",
        "prop": "prop-003",
    }


def _fault_for(wire_bdate: str) -> dict[str, Any]:
    # Canonical records as the faulted sync observed them with the
    # unpatched spec: the "%m-%d-%Y" wire value passes through unchanged
    # (unparseable), so the invariant skips the record.
    from autobridge.runtime.mapping import map_records_to_entities

    spec = _spec()
    ent = spec["entities"]["FolioCharge"]
    wire = [_wire(wire_bdate)]
    canonical, _ = map_records_to_entities(
        wire, ent, source_spec=spec["source"], entity_name="FolioCharge"
    )
    assert canonical and canonical[0].get("business_date") == wire_bdate
    return {
        "unmet_criteria": [],
        "canonical_records": {"FolioCharge": canonical},
        "wire_records": {"list_folios": wire},
        "raw_first_pages": {"list_folios": dict(wire[0])},
    }


def _input_format_patch() -> list[dict[str, Any]]:
    return [
        {
            "op": "add",
            "path": "/entities/FolioCharge/fields/business_date/transform/0/args/input_format",
            "value": "%m-%d-%Y",
        }
    ]


def test_spec_static_tz_shared_helper() -> None:
    assert spec_static_tz(_spec()) == "America/New_York"
    assert spec_static_tz({"source": {"timezone_source": {"kind": "field", "path": "$.tz"}}}) is None
    assert spec_static_tz({}) is None
    # Object-style spec (engine path) agrees with the dict path.
    class _Tz:
        kind = "static"
        value = "America/New_York"

    class _Src:
        timezone_source = _Tz()

    class _Spec:
        source = _Src()

    assert spec_static_tz(_Spec()) == "America/New_York"


def test_old_check_pass_agrees_with_sync_zone() -> None:
    spec = _spec()
    checks = spec["entities"]["FolioCharge"]["checks"]
    # Canonical record deriving under NY only (REQ-087 §: 04:30Z on
    # prop-003 reads 2026-10-15 in NY, 2026-10-16 in UTC/Berlin).
    records = [
        {
            "business_date": "2026-10-15",
            "posted_at": "2026-10-16T04:30:00Z",
            "property_id": "prop-003",
        }
    ]
    assert evaluate_entity_checks("FolioCharge", records, checks) == ["business_date_invariant"]
    assert (
        evaluate_entity_checks("FolioCharge", records, checks, context_tz="America/New_York") == []
    )
    fault = {
        "canonical_records": {"FolioCharge": records},
        "wire_records": {},
        "raw_first_pages": {},
    }
    # The dry-run's old-check pass judges under the spec's declared zone,
    # exactly as the sync does — so this entity does not fail.
    assert _entities_failing_old_checks(spec, fault["canonical_records"], {}) == set()
    assert _evaluate_checks_on_records("FolioCharge", records, checks, None, "America/New_York") == []


def test_dry_run_passes_correct_input_format_repair() -> None:
    spec = _spec()
    fault = _fault_for("10-15-2026")
    ok, detail = dry_run_proposal(spec, _input_format_patch(), fault)
    assert ok is True, detail
    assert "checks pass" in detail


def test_dry_run_refuses_genuinely_wrong_business_date() -> None:
    spec = _spec()
    fault = _fault_for("10-14-2026")
    ok, detail = dry_run_proposal(spec, _input_format_patch(), fault)
    assert ok is False, detail
    assert "FolioCharge.business_date_invariant" in detail
