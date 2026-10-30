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


def test_allowed_l3_pointers_name_exact_members() -> None:
    ptrs = sem.allowed_l3_pointers(_harbor_like_spec(), "Reservation")
    assert "/entities/Reservation/fields/total_amount/path" in ptrs
    assert "/entities/Reservation/fields/total_amount/transform" in ptrs
    assert "/entities/Reservation/fields/reservation_id/path" in ptrs
    # Nothing outside the entity's fields is patchable at L3.
    assert all(p.startswith("/entities/Reservation/fields/") for p in ptrs)
    assert sem.allowed_l3_pointers(_harbor_like_spec(), "Nope") == []
    assert sem.allowed_l3_pointers({}, "Reservation") == []


def test_spec_subtree_carries_no_source_or_auth() -> None:
    spec = dict(_harbor_like_spec())
    spec["source"] = {"base_url": "http://secret-host:8000", "auth": {"credential_ref": "X"}}
    subtree = sem.spec_subtree_for_entity(spec, "Reservation")
    assert subtree["entity"] == "Reservation"
    assert subtree["fields"]["total_amount"]["pointer"] == (
        "/entities/Reservation/fields/total_amount"
    )
    assert subtree["fields"]["total_amount"]["path"] == "$.total_amount"
    assert subtree["checks"] == [
        {"kind": "money_invariant", "field": "total_amount", "compare_to": "$.gross_sum"}
    ]
    dumped = str(subtree)
    assert "secret-host" not in dumped and "credential_ref" not in dumped


def test_raw_records_redact_credentials_and_pii_by_shape() -> None:
    rec = {
        "reservation_id": "r1",
        "total_amount": 90.0,
        "gross_amount": 100.0,
        "api_token": "SECRET-XYZ",
        "guest_email": "guest@example.com",
        "note": "x" * 100,
    }
    out = sem.redact_raw_record(rec, money_fields={"total_amount"})
    assert out["reservation_id"] == "r1"
    assert out["total_amount"] == "90.0"
    assert out["gross_amount"] == 100.0
    assert out["api_token"] == "<redacted>"
    assert out["guest_email"] == "<redacted>"
    assert out["note"] == "x" * 64 + "…"


def test_raw_records_of_summary_deterministic_and_capped() -> None:
    summary = {
        "operation_results": {
            "b_op": {"raw_pages": [{"items": [{"a": 1}, {"a": 2}, {"a": 3}]}]},
            "a_op": {"raw_pages": [{"rows": [{"b": 1}]}]},
        }
    }
    recs = sem.raw_records_of_summary(summary, limit=2)
    # Operations visit in name order: a_op first, capped at two records.
    assert recs == [{"b": 1}, {"a": 1}]
    assert sem.raw_records_of_summary({}, limit=2) == []
    assert sem.raw_records_of_summary({"outcome": "Blocked"}, limit=2) == []


def test_build_l3_evidence_has_patch_target_and_raw_records() -> None:
    from autobridge.discovery.docs_reader import Provenance as _P
    from autobridge.discovery.hypotheses import Hypothesis as _H

    prov = (_P(source="s", line=1, snippet="x"),)
    climbed = _H(
        id="amount:total_amount:net", family="amount", claim="c",
        candidate_mapping_fragment={"family": "amount"}, prior=0.34, evidence_for=prov, field="total_amount",
    )
    summary = dict(_failed_summary())
    summary["operation_results"] = {
        "list_reservations": {
            "raw_pages": [
                {
                    "gross_sum": "300.00",
                    "reservations": [
                        {"reservation_id": "r1", "total_amount": 90.0, "gross_amount": 100.0,
                         "api_token": "SECRET-XYZ"}
                    ],
                }
            ]
        }
    }
    ev = sem.build_l3_evidence(_harbor_like_spec(), summary, climbed)
    assert ev["patch_target"]["document"] == "connector spec"
    assert ev["patch_target"]["allowed_ops"] == ["replace", "add"]
    assert "/entities/Reservation/fields/total_amount/path" in ev["patch_target"]["allowed_pointers"]
    assert "money" in ev["patch_target"]["transforms"]
    assert ev["spec_subtree"]["entity"] == "Reservation"
    assert "total_amount" in ev["spec_subtree"]["fields"]
    assert ev["raw_records"][0]["gross_amount"] == 100.0
    assert ev["raw_records"][0]["api_token"] == "<redacted>"
    # Old keys unchanged for existing callers.
    assert ev["spec_excerpt"]["field"] == "total_amount"
    assert ev["records_sample"]["observed_sum"] == "270.00"
