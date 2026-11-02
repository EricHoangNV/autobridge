"""REQ-083 C15: the L2 confident winner passes the dry-run gate before propose.

A confident L2 winner is promoted with a pointer check only while the
comment promises the dry-run. After the fix the dry-run gate runs
before propose: a winner whose patch fails on the break's records is
dropped (traced, like a rejected L3 proposal) and the climb escalates;
a winner whose patch passes is proposed.
"""

from __future__ import annotations


def _l2_spec() -> dict:
    return {
        "spec_version": 2,
        "connector": {"id": "t", "vendor": "test"},
        "source": {
            "base_url": "http://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_r": {
                "method": "GET",
                "path": "/reservations",
                "pagination": {
                    "strategy": "cursor",
                    "cursor_param": "cursor",
                    "limit_param": "limit",
                    "next_path": "$.next",
                    "items_path": "$.data",
                    "stop_when": ["null_cursor"],
                },
            }
        },
        "entities": {
            "R": {
                "from": "list_r",
                "key": "$.id",
                "fields": {
                    "id": {"path": "$.id"},
                    "total": {"path": "$.total", "transform": [{"name": "money", "args": {"scale": 2}}]},
                },
                "checks": [{"kind": "money_invariant", "field": "total"}],
            }
        },
        "capabilities": {"read": ["R"]},
        "health": {"canary": {"every": "5m", "operation": "list_r"}},
    }


def _hyps(include_blank: bool = False) -> list:
    from autobridge.discovery.docs_reader import Provenance
    from autobridge.discovery.hypotheses import Hypothesis

    prov = (Provenance(source="test", line=1, snippet="cursor"),)
    hyps = [
        Hypothesis(
            id="h1",
            family="pagination",
            claim="page with new params",
            candidate_mapping_fragment={"family": "pagination", "field": "/reservations", "strategy": "cursor"},
            prior=0.90,
            evidence_for=prov,
        ),
        Hypothesis(
            id="h2",
            family="pagination",
            claim="offset with new params",
            candidate_mapping_fragment={"family": "pagination", "field": "/reservations", "strategy": "offset"},
            prior=0.30,
            evidence_for=prov,
        ),
    ]
    if include_blank:
        hyps.append(Hypothesis(
            id="h3", family="unknown", claim="nothing patchable",
            candidate_mapping_fragment={"family": "unknown", "field": "x"}, prior=0.10, evidence_for=prov,
        ))
    return hyps


def _fault(canonical_total: str) -> dict:
    return {
        "unmet_criteria": ["money_invariant"],
        "canonical_records": {"R": [{"id": "R1", "total": canonical_total}]},
        "wire_records": {},
        "raw_first_pages": {},
    }


def _climb(dry_run_fault: dict | None, include_blank: bool = False):
    from autobridge.llm.provider import Budget, Completion, FakeProvider, Usage
    from autobridge.repair.controller import RepairController, build_generic_l3_evidence

    fake = FakeProvider(
        responses={"default": Completion(text='["h1", "h2"]', usage=Usage(4, 4))}
    )
    evidence = build_generic_l3_evidence(
        _l2_spec(), [], None, None, "PAGINATION_CHANGE", failed_attempts=None
    )
    return RepairController()._handle_model_climb(
        spec=_l2_spec(),
        drift_class="PAGINATION_CHANGE",
        signals_list=[],
        fingerprint_delta=None,
        extra_dict={},
        vendor_val="test",
        drift_event_ids=[],
        session=None,
        hypotheses=_hyps(include_blank=include_blank),
        enumerator_yield_count=2,
        provider=fake,
        budget=Budget(),
        scenario="c15",
        validate_against=None,
        run_id="c15-test",
        evidence=evidence,
        hypothesis_stages=None,
        dry_run_fault=dry_run_fault,
    )


def test_l2_winner_failing_dry_run_is_dropped_not_proposed() -> None:
    """Invalid money on the break's records: the L2 patch is dropped."""
    result = _climb(_fault("abc"))
    assert result.decision != "propose", result.decision
    trace = result.ladder_trace or []
    assert any(
        isinstance(t, dict) and t.get("action") == "l2_dry_run_rejected" for t in trace
    ), trace


def test_l2_winner_passing_dry_run_is_proposed() -> None:
    """Valid money: the L2 patch passes the gate and is proposed."""
    result = _climb(_fault("10.00"))
    assert result.decision == "propose", result.decision
    assert result.ladder_level_reached == 2
    assert result.candidate_patch, "no candidate patch carried"

