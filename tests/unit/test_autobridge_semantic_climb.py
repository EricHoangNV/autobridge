"""REQ-039: engine-side semantic enumeration (wire signals + spec, no docs)."""

from __future__ import annotations

from typing import Any

from autobridge.repair import semantic as sem


def _harbor_like_spec() -> dict[str, Any]:
    return {
        "spec_version": 3,
        "connector": {"id": "harbor", "vendor": "harbor"},
        "source": {"base_url": "http://example.com"},
        "operations": {},
        "entities": {
            "Reservation": {
                "key": "$.reservation_id",
                "fields": {
                    "reservation_id": {"path": "$.reservation_id"},
                    "total_amount": {"path": "$.total_amount"},
                },
                "checks": [
                    {"kind": "money_invariant", "field": "total_amount", "compare_to": "$.gross_sum"}
                ],
            },
            "FolioCharge": {
                "key": "$.charge_id",
                "fields": {
                    "charge_id": {"path": "$.charge_id"},
                    "amount": {"path": "$.amount"},
                },
                "checks": [
                    {"kind": "money_invariant", "field": "amount", "compare_to": "$.gross_sum"}
                ],
            },
        },
    }


def _failed_summary() -> dict[str, Any]:
    return {
        "outcome": "Degraded",
        "unmet_criteria": ["money_invariant"],
        "canonical_records": {
            "Reservation": [
                {"reservation_id": "r1", "total_amount": "90.00"},
                {"reservation_id": "r2", "total_amount": "180.00"},
            ],
        },
        "operation_results": {
            "list_reservations": {"raw_pages": [{"gross_sum": "300.00"}]},
        },
    }


def test_strategy_tables_match_docs_enumerator() -> None:
    """The engine's amount table must stay in sync with the docs enumerator's."""
    from autobridge.discovery import hypotheses as hyp

    assert list(sem.AMOUNT_STRATEGIES) == list(hyp.AMOUNT_STRATEGIES)
    assert sem._AMOUNT_CLAIMS == hyp._AMOUNT_CLAIMS
    assert sem._AMOUNT_TRANSFORMS == hyp._AMOUNT_TRANSFORMS


def test_enumerate_money_hypotheses_ids_match_enumerator_scheme() -> None:
    hyps, fields = sem.enumerate_money_hypotheses(_harbor_like_spec(), source="sync_runs:s1")
    assert fields == {"Reservation": "total_amount", "FolioCharge": "amount"}
    assert [h.id for h in hyps] == [
        "amount:amount:gross",
        "amount:amount:net",
        "amount:amount:tax_inclusive",
        "amount:total_amount:gross",
        "amount:total_amount:net",
        "amount:total_amount:tax_inclusive",
    ]
    assert {h.family for h in hyps} == {"amount"}
    assert all(h.evidence_for for h in hyps)


def test_wire_filter_drops_hold_predicting_on_failure() -> None:
    hyps, _ = sem.enumerate_money_hypotheses(_harbor_like_spec(), source="s")
    survivors = sem.filter_wire_consistent(hyps, invariant_failed=True)
    assert [h.id for h in survivors] == ["amount:amount:net", "amount:total_amount:net"]
    kept = sem.filter_wire_consistent(hyps, invariant_failed=False)
    assert len(kept) == 6


def test_select_climbed_most_specific_with_siblings() -> None:
    hyps, _ = sem.enumerate_money_hypotheses(_harbor_like_spec(), source="s")
    survivors = sem.filter_wire_consistent(hyps, invariant_failed=True)
    climbed, siblings = sem.select_climbed(survivors)
    assert climbed is not None and climbed.id == "amount:total_amount:net"
    assert siblings == ["amount:amount:net"]
    assert sem.select_climbed([]) == (None, [])


def test_money_invariant_failed_from_summary_and_signals() -> None:
    assert sem.money_invariant_failed(_failed_summary()) is True
    assert sem.money_invariant_failed({"outcome": "Complete", "unmet_criteria": []}) is False
    assert (
        sem.money_invariant_failed(
            {}, [{"kind": "invariant_failure", "details": {"check": "money_invariant"}}]
        )
        is True
    )


def test_build_l3_evidence_redacted_sample() -> None:
    hyps, _ = sem.enumerate_money_hypotheses(_harbor_like_spec(), source="s")
    survivors = sem.filter_wire_consistent(hyps, invariant_failed=True)
    climbed, _ = sem.select_climbed(survivors)
    ev = sem.build_l3_evidence(_harbor_like_spec(), _failed_summary(), climbed)
    assert ev["spec_excerpt"] == {
        "entity": "Reservation",
        "field": "total_amount",
        "path": "$.total_amount",
        "money_check": {"kind": "money_invariant", "compare_to": "$.gross_sum"},
    }
    assert ev["records_sample"]["rows"] == [
        {"reservation_id": "r1", "total_amount": "90.00"},
        {"reservation_id": "r2", "total_amount": "180.00"},
    ]
    assert ev["records_sample"]["observed_sum"] == "270.00"
    assert ev["records_sample"]["baseline_gross_sum"] == "300.00"
    assert sem.build_l3_evidence(_harbor_like_spec(), _failed_summary(), None) == {}
