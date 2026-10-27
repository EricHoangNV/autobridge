"""P1.W6.2 — 5 scenarios wired end to end (gross_to_net etc.).

Verifies:
- gross_to_net → Degraded with money_invariant via gross_sum
- tz_boundary_shift → Degraded with business_date_invariant
- status_vocab_change → Blocked with unknown_enum
- endpoint_removed → Blocked with endpoint_not_found, ladder escalate no patch
- token_revoked → Blocked with auth_revoked, ladder escalate no patch
"""

# ruff: noqa: ARG002, F401, I001

import yaml

from autobridge.drift.classifier import classify_sync
from autobridge.engine.sync_run import execute_sync
from autobridge.repair.controller import RepairController
from autobridge.runtime.checks import check_business_date_invariant, check_money_invariant


def _load_spec(vendor: str = "atlaspms") -> dict:
    import pathlib

    p = pathlib.Path(f"generated/connectors/{vendor}/reference/spec.yaml")
    return yaml.safe_load(p.read_text())


def _fake_client_with_gross(mutated, baseline):
    from decimal import Decimal as D

    res_gross = sum(D(r["total_amount"]) for r in baseline["reservations"])
    fol_gross = sum(D(f["amount"]) for f in baseline["folios"])

    class C:
        def get(self, path, params=None, headers=None, json_body=None):
            key = path.strip("/").split("?")[0]
            if "reservations" in key:
                data = mutated["reservations"]
                offset = int(params.get("offset", 0)) if params else 0
                limit = int(params.get("limit", 50)) if params else 50
                sliced = data[offset : offset + limit]
                nxt = offset + limit if offset + limit < len(data) else None
                return {"data": sliced, "total": len(data), "next_offset": nxt, "limit": limit, "offset": offset, "gross_sum": format(res_gross, ".2f")}
            if "folios" in key:
                data = mutated["folios"]
                offset = int(params.get("offset", 0)) if params else 0
                limit = int(params.get("limit", 50)) if params else 50
                sliced = data[offset : offset + limit]
                nxt = offset + limit if offset + limit < len(data) else None
                return {"data": sliced, "total": len(data), "next_offset": nxt, "limit": limit, "offset": offset, "gross_sum": format(fol_gross, ".2f")}
            return {"data": []}

    return C()


def test_gross_to_net_degraded_money_invariant():
    from simulators.common.faults import apply_dataset_mutations
    from simulators.common.generator import generate_dataset

    spec = _load_spec("atlaspms")
    ds = generate_dataset(seed=42)
    mut = apply_dataset_mutations(ds, "gross_to_net")
    client = _fake_client_with_gross(mut, ds)
    summary = execute_sync(spec, client)
    assert summary["outcome"] == "Degraded"
    assert "money_invariant" in summary["unmet_criteria"]
    cls = classify_sync(summary)
    assert cls.drift_class == "SEMANTIC_VALUE_DRIFT"
    # Ladder should escalate (no independent evidence)
    ctrl = RepairController()
    res = ctrl.handle(spec=spec, drift_class=cls.drift_class, signals=[])
    assert res.decision == "escalate"
    assert res.candidate_patch is None


def test_tz_boundary_shift_degraded_business_date():
    from simulators.common.faults import apply_dataset_mutations
    from simulators.common.generator import generate_dataset

    spec = _load_spec("atlaspms")
    ds = generate_dataset(seed=42)
    mut = apply_dataset_mutations(ds, "tz_boundary_shift")
    client = _fake_client_with_gross(mut, ds)
    summary = execute_sync(spec, client)
    assert summary["outcome"] == "Degraded"
    assert "business_date_invariant" in summary["unmet_criteria"]
    cls = classify_sync(summary)
    assert cls.drift_class == "SEMANTIC_VALUE_DRIFT"
    ctrl = RepairController()
    res = ctrl.handle(spec=spec, drift_class=cls.drift_class, signals=[])
    assert res.decision == "escalate"


def test_status_vocab_change_blocked_unknown_enum():
    from simulators.common.faults import apply_dataset_mutations
    from simulators.common.generator import generate_dataset

    spec = _load_spec("atlaspms")
    ds = generate_dataset(seed=42)
    mut = apply_dataset_mutations(ds, "status_vocab_change")
    client = _fake_client_with_gross(mut, ds)
    summary = execute_sync(spec, client)
    assert summary["outcome"] == "Blocked"
    assert any("unknown_enum" in c for c in summary["unmet_criteria"])
    cls = classify_sync(summary)
    assert cls.drift_class == "ENUM_NEW_VALUE"


def test_endpoint_removed_blocked_escalate_no_patch():
    from autobridge.runtime.http import HttpError

    spec = _load_spec("atlaspms")

    class ErrClient:
        def get(self, path, params=None, headers=None, json_body=None):
            raise HttpError("HTTP 404 for GET /reservations", status_code=404)

    summary = execute_sync(spec, ErrClient())
    assert summary["outcome"] == "Blocked"
    assert "endpoint_not_found" in summary["unmet_criteria"]
    cls = classify_sync(summary)
    assert cls.drift_class == "CAPABILITY_REMOVED"
    ctrl = RepairController()
    res = ctrl.handle(spec=spec, drift_class=cls.drift_class, signals=[])
    assert res.decision == "escalate"
    assert res.candidate_patch is None
    assert res.ladder_level_reached == 4


def test_token_revoked_blocked_escalate_no_patch():
    from autobridge.runtime.http import HttpError

    spec = _load_spec("atlaspms")

    class ErrClient:
        def get(self, path, params=None, headers=None, json_body=None):
            raise HttpError("HTTP 403 token_revoked", status_code=403)

    summary = execute_sync(spec, ErrClient())
    assert summary["outcome"] == "Blocked"
    assert "auth_revoked" in summary["unmet_criteria"]
    cls = classify_sync(summary)
    assert cls.drift_class == "AUTH_REVOKED"
    ctrl = RepairController()
    res = ctrl.handle(spec=spec, drift_class=cls.drift_class, signals=[])
    assert res.decision == "escalate"
    assert res.candidate_patch is None


def test_money_invariant_with_expected_sum():
    recs = [{"total_amount": "100.00", "status": "confirmed"}, {"total_amount": "200.00", "status": "confirmed"}]
    # Within 5% of 300
    assert check_money_invariant(recs, "total_amount", expected_sum="300.00", tolerance_pct=5.0, entity_name="Reservation") == []
    # 10% low should fail
    recs2 = [{"total_amount": "90.00"}, {"total_amount": "180.00"}]
    assert check_money_invariant(recs2, "total_amount", expected_sum="300.00", tolerance_pct=5.0, entity_name="Reservation") == ["money_invariant"]
    # Negative for folio should be allowed
    recs3 = [{"amount": "-10.00"}, {"amount": "20.00"}]
    assert check_money_invariant(recs3, "amount", entity_name="FolioCharge") == []
    # Negative for reservation should fail
    recs4 = [{"total_amount": "-10.00"}]
    assert check_money_invariant(recs4, "total_amount", entity_name="Reservation") == ["money_invariant"]


def test_business_date_invariant_property_lookup():
    # Baseline with correct timezone via property_id
    recs = [{"business_date": "2026-10-18", "posted_at": "2026-10-18T23:54:18+00:00", "property_id": "prop-002"}]
    assert check_business_date_invariant(recs, "business_date", "posted_at", cutoff=4) == []
    # Shifted should fail
    recs2 = [{"business_date": "2026-10-19", "posted_at": "2026-10-18T23:54:18+00:00", "property_id": "prop-002"}]
    assert check_business_date_invariant(recs2, "business_date", "posted_at", cutoff=4) == ["business_date_invariant"]


def test_sync_http_error_variants():
    spec = _load_spec("atlaspms")
    from autobridge.runtime.http import HttpError

    for status, expected in [(401, "auth_expired"), (429, "rate_limited"), (503, "transient_error")]:
        class EC:
            def __init__(self, s):
                self.s = s

            def get(self, path, params=None, headers=None, json_body=None):
                raise HttpError(f"HTTP {self.s}", status_code=self.s)

        summary = execute_sync(spec, EC(status))
        assert summary["outcome"] == "Blocked"
        assert expected in summary["unmet_criteria"] or "http_error" in summary["unmet_criteria"]
