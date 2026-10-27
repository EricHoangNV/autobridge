"""Semantic invariant checks for P1.W6.1 (money/date/count via invariants).

Each invariant is schema-valid but value-wrong drift — Degraded with the
failing invariant named in unmet_criteria, classified as SEMANTIC_VALUE_DRIFT.
"""
from decimal import Decimal

from autobridge.drift.classifier import classify_sync
from autobridge.drift.signals import signals_from_sync
from autobridge.engine.sync_run import determine_outcome
from autobridge.runtime.checks import (
    check_business_date_invariant,
    check_count_invariant,
    check_money_invariant,
    evaluate_entity_checks,
    run_checks,
)


def test_money_invariant_pass():
    recs = [{"reservation_id": "R1", "total_amount": "100.00", "status": "confirmed"}]
    assert check_money_invariant(recs, "total_amount") == []


def test_money_invariant_negative():
    recs = [{"reservation_id": "R1", "total_amount": "-10.00"}]
    assert check_money_invariant(recs, "total_amount") == ["money_invariant"]


def test_money_invariant_missing_active():
    recs = [{"reservation_id": "R1", "status": "confirmed"}]
    assert check_money_invariant(recs, "total_amount") == ["money_invariant"]


def test_money_invariant_cancelled_ok():
    recs = [{"reservation_id": "R1", "status": "cancelled"}]
    assert check_money_invariant(recs, "total_amount") == []


def test_money_invariant_decimal():
    recs = [{"reservation_id": "R1", "total_amount": Decimal("100.00")}]
    assert check_money_invariant(recs, "total_amount") == []


def test_money_invariant_invalid():
    recs = [{"reservation_id": "R1", "total_amount": "not-a-number"}]
    assert check_money_invariant(recs, "total_amount") == ["money_invariant"]


def test_money_invariant_empty():
    assert check_money_invariant([], "total_amount") == []


def test_business_date_valid():
    recs = [{"business_date": "2026-10-27", "posted_at": "2026-10-27T12:00:00+00:00"}]
    assert check_business_date_invariant(recs, "business_date", "posted_at", cutoff=4) == []


def test_business_date_late_posting_mismatch():
    # 03:00 UTC with cutoff 4 => business date should be prior day
    recs = [{"business_date": "2026-10-27", "posted_at": "2026-10-27T03:00:00+00:00"}]
    assert check_business_date_invariant(recs, "business_date", "posted_at", cutoff=4) == ["business_date_invariant"]


def test_business_date_late_posting_correct():
    recs = [{"business_date": "2026-10-26", "posted_at": "2026-10-27T03:00:00+00:00"}]
    assert check_business_date_invariant(recs, "business_date", "posted_at", cutoff=4) == []


def test_business_date_null_skip():
    recs = [{"business_date": None, "posted_at": "2026-10-27T03:00:00+00:00"}]
    assert check_business_date_invariant(recs, "business_date", "posted_at", cutoff=4) == []


def test_business_date_empty():
    assert check_business_date_invariant([], "business_date", "posted_at") == []


def test_count_invariant_pass():
    recs = [{"id": "1"}, {"id": "2"}]
    assert check_count_invariant(recs, expected_count=2) == []


def test_count_invariant_low():
    recs = [{"id": "1"}]
    assert check_count_invariant(recs, expected_count=5) == ["count_invariant"]


def test_count_invariant_high():
    recs = [{"id": "1"}, {"id": "2"}, {"id": "3"}]
    assert check_count_invariant(recs, expected_count=1) == ["count_invariant"]


def test_count_invariant_field_missing():
    recs = [{"id": "1", "amount": None}, {"id": "2", "amount": "10.00"}]
    assert check_count_invariant(recs, field="amount") == ["count_invariant"]


def test_count_invariant_field_ok():
    recs = [{"id": "1", "amount": "10.00"}]
    assert check_count_invariant(recs, field="amount") == []


def test_count_compare_to_value():
    recs = [{"id": "1"}, {"id": "2"}]
    assert check_count_invariant(recs, compare_to_value="5") == ["count_invariant"]
    assert check_count_invariant(recs, compare_to_value=2) == []
    assert check_count_invariant(recs, compare_to_value="bad") == []


def test_run_checks_money():
    recs = [{"reservation_id": "R1", "total_amount": "-5.00"}]
    checks = [{"kind": "money_invariant", "field": "total_amount"}]
    assert run_checks(recs, checks, entity_name="Reservation") == ["money_invariant"]


def test_run_checks_business_date():
    recs = [{"charge_id": "C1", "business_date": "2026-10-27", "posted_at": "2026-10-27T03:00:00+00:00"}]
    checks = [{"kind": "business_date_invariant", "field": "business_date", "reference_field": "posted_at", "cutoff": 4}]
    assert run_checks(recs, checks, entity_name="FolioCharge") == ["business_date_invariant"]


def test_run_checks_count():
    recs = [{"reservation_id": "R1"}]
    checks = [{"kind": "count_invariant", "compare_to": "$.total"}]
    assert run_checks(recs, checks, expected_counts={"Reservation": 5}, entity_name="Reservation") == ["count_invariant"]


def test_run_checks_count_literal():
    recs = [{"reservation_id": "R1"}]
    checks = [{"kind": "count_invariant", "compare_to": "2"}]
    # Literal compare_to parsing
    assert run_checks(recs, checks, entity_name="Reservation") == ["count_invariant"]


def test_run_checks_business_date_cutoff_str():
    recs = [{"business_date": "2026-10-27", "posted_at": "2026-10-27T03:00:00+00:00"}]
    checks = [{"kind": "business_date_invariant", "field": "business_date", "reference_field": "posted_at", "cutoff": "bad"}]
    # Bad cutoff should default to 4
    assert run_checks(recs, checks, entity_name="FolioCharge") == ["business_date_invariant"]


def test_run_checks_no_field_skip():
    recs = [{"reservation_id": "R1"}]
    assert run_checks(recs, [{"kind": "money_invariant"}], entity_name="Reservation") == []
    assert run_checks(recs, [{"kind": "business_date_invariant", "field": "bd"}], entity_name="Reservation") == []


def test_evaluate_money():
    recs = [{"reservation_id": "R1", "total_amount": "-1.00"}]
    checks = [{"kind": "money_invariant", "field": "total_amount"}]
    fails = evaluate_entity_checks("Reservation", recs, checks)
    assert "money_invariant" in fails


def test_determine_outcome_money_degraded():
    outcome, unmet = determine_outcome({}, invariant_failures=["money_invariant"])
    assert outcome == "Degraded"
    assert "money_invariant" in unmet


def test_classifier_semantic():
    summary = {"outcome": "Degraded", "unmet_criteria": ["money_invariant"], "operation_results": {}, "records_observed": {}, "blocked_records": {}, "invariant_results": []}
    result = classify_sync(summary)
    assert result.drift_class == "SEMANTIC_VALUE_DRIFT"


def test_classifier_business_date():
    summary = {"outcome": "Degraded", "unmet_criteria": ["business_date_invariant"], "operation_results": {}, "records_observed": {}, "blocked_records": {}, "invariant_results": []}
    result = classify_sync(summary)
    assert result.drift_class == "SEMANTIC_VALUE_DRIFT"


def test_classifier_count():
    summary = {"outcome": "Degraded", "unmet_criteria": ["count_invariant"], "operation_results": {}, "records_observed": {}, "blocked_records": {}, "invariant_results": []}
    result = classify_sync(summary)
    assert result.drift_class == "SEMANTIC_VALUE_DRIFT"


def test_signals_money():
    summary = {"outcome": "Degraded", "unmet_criteria": ["money_invariant"], "operation_results": {}, "records_observed": {}, "blocked_records": {}, "invariant_results": []}
    sigs = signals_from_sync(summary)
    assert any(s.kind == "invariant_failure" for s in sigs)


if __name__ == "__main__":
    import sys
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:
                print(f"FAIL {name}: {e}")
                sys.exit(1)
    print("All passed")
