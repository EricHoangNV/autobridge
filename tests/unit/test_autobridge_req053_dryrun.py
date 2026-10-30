"""REQ-053 item 6: dry-run every proposal, auto-rollback on failed verifying sync.

* A proposal that references a wire field the vendor never sends (deck 5:
  ``$.net_sum``) is rejected with the exact error, retried once, then
  dropped to question-only — never offered for Approve.
* A proposal whose checks pass on the break's records is offered with the
  dry-run result shown ("tested on the current records: the checks pass").
* A verifying sync that still fails after an approval rolls back to the
  previous release automatically; the original repair stays the one open
  item — never a second escalation for the same open drift.
"""

from __future__ import annotations

import json
import pathlib
from typing import TYPE_CHECKING, Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from autobridge.control.models import (
    Approval,
    Base,
    Connector,
    ConnectorRelease,
    DriftEvent,
    RepairRun,
    spec_signature,
)
from autobridge.engine.sync_run import execute_sync
from autobridge.repair.dryrun import build_dry_run_fault, dry_run_proposal

if TYPE_CHECKING:
    import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _mem_factory() -> Any:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _harbor_spec() -> dict[str, Any]:
    from autobridge.discovery.bootstrap import build_draft_spec

    docs = (REPO_ROOT / "simulators" / "docs" / "harbor.md").read_text(encoding="utf-8")
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
    return json.loads(json.dumps(res.draft_spec, default=str))


def _harbor_fault(spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    # Isolated simulator state (REQ-055): the Harbor fault client prefers
    # the ambient simulator state file for its gross sums, which a live
    # stack (or another test) rewrites — pin seed 0 so the parcel is
    # identical with or without a stack on this host.
    from bench.faults import fake_env, fault_client_for, isolated_sim_state

    with isolated_sim_state(), fake_env():
        summary = execute_sync(
            spec, fault_client_for("gross_to_net", seed=1, connector_id="harborpms")
        )
    assert summary["outcome"] == "Degraded", summary
    fault = build_dry_run_fault(summary)
    assert fault is not None
    return summary, fault


def _record_sum(fault: dict[str, Any]) -> str:
    """Canonical Reservation total as a money string (for a stable parcel)."""
    from decimal import Decimal as _Decimal

    recs = fault.get("canonical_records", {}).get("Reservation", [])
    total = sum(
        _Decimal(str(r.get("total_amount", "0")))
        for r in recs
        if isinstance(r, dict) and r.get("total_amount") is not None
    )
    return format(total, ".2f")


def _fault_with_gross(fault: dict[str, Any], gross: str) -> dict[str, Any]:
    """Parcel with the raw page total pinned (the fault client reads ambient
    simulator state for its gross sum, so exact-sum tests pin the value)."""
    import copy as _copy

    fault = _copy.deepcopy(fault)
    fault["raw_first_pages"]["list_reservations"]["gross_sum"] = gross
    return fault


def _net_sum_patch() -> list[dict[str, Any]]:
    # Deck 5's decoy: a field Harbor never sends.
    return [
        {"op": "replace", "path": "/entities/Reservation/checks/3/compare_to", "value": "$.net_sum"}
    ]


def _resolvable_patch() -> list[dict[str, Any]]:
    # A pointer the response actually carries (the faulted page has `total`).
    return [
        {"op": "replace", "path": "/entities/Reservation/checks/3/compare_to", "value": "$.total"}
    ]


def test_dry_run_rejects_wire_field_the_vendor_never_sends() -> None:
    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    ok, detail = dry_run_proposal(spec, _net_sum_patch(), fault)
    assert ok is False
    # REQ-055 item 4a: names alone steer the model wrong (a record count
    # looks like a net total). The retry lists each available field with
    # its documented meaning from the spec the connector was built from.
    assert detail.startswith("`$.net_sum` is not a field in the response; available: ")
    assert "`gross_sum` (compared by the money_invariant check on `total_amount`" in detail
    assert "`total` (" in detail and "number of records on this page" in detail
    assert "`cursor` (page cursor)" in detail
    assert "`data` (the records on this page)" in detail


def test_retry_field_meanings_are_generic() -> None:
    # REQ-055 item 4a: meanings come from the spec + observed response only
    # — no vendor or scenario names anywhere in the retry text.
    from autobridge.repair.dryrun import field_meanings_for_retry

    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    meanings = field_meanings_for_retry(spec, fault)
    assert meanings["total"].endswith("number of records on this page")
    assert "money_invariant" in meanings["gross_sum"]
    blob = " ".join(meanings.values())
    for banned in ("harbor", "Harbor", "gross_to_net", "atlas", "Atlas", "meridian", "Meridian"):
        assert banned not in blob, banned
    _summary2, fault2 = _harbor_fault(spec)
    assert field_meanings_for_retry(spec, fault2) == meanings


def test_dry_run_rejects_a_count_repointed_money_check() -> None:
    # REQ-055 item 2 (deck 5): repointing the money invariant at `$.total`
    # (the record count, 30) passed for the wrong reason — the count was
    # silently ignored, switching the totals comparison off while the
    # label claimed it was repointed. A check that cannot evaluate counts
    # as not passed, with generic wording for the retry.
    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    ok, detail = dry_run_proposal(spec, _resolvable_patch(), fault)
    assert ok is False
    assert detail == "the patched check did not run: `$.total` is not a money sum"


def test_dry_run_passes_a_money_sum_pointer() -> None:
    # A repoint at a value that reads as a money sum still passes judgement.
    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    fault = _fault_with_gross(fault, _record_sum(fault))
    fault["raw_first_pages"]["list_reservations"]["net_sum"] = "900.00"
    patch = [
        {"op": "replace", "path": "/entities/Reservation/checks/3/compare_to", "value": "$.net_sum"}
    ]
    ok, detail = dry_run_proposal(spec, patch, fault)
    assert ok is True, detail
    assert detail == "the checks pass on the current records"


def test_field_notes_name_the_target_wire_field() -> None:
    # REQ-055 item 2: the proposal shows, next to the change, what the
    # target field means — the resolved value and its shape from the
    # response the break's sync saw, contrasting the replaced pointer.
    from autobridge.repair.dryrun import field_notes_for_proposal

    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    fault = _fault_with_gross(fault, _record_sum(fault))
    fault["raw_first_pages"]["list_reservations"]["net_sum"] = "900.00"
    notes = field_notes_for_proposal(
        spec,
        [{"op": "replace", "path": "/entities/Reservation/checks/3/compare_to", "value": "$.net_sum"}],
        fault,
    )
    assert len(notes) == 1
    assert notes[0]["path"] == "/entities/Reservation/checks/3/compare_to"
    assert "`$.net_sum`" in notes[0]["note"]
    assert "a money sum" in notes[0]["note"]
    assert "`$.gross_sum`" in notes[0]["note"]

    count_notes = field_notes_for_proposal(spec, _resolvable_patch(), fault)
    assert len(count_notes) == 1
    assert "`$.total` is `30`" in count_notes[0]["note"]
    assert "an integer" in count_notes[0]["note"]

    remove_notes = field_notes_for_proposal(
        spec, [{"op": "remove", "path": "/entities/Reservation/checks/3"}], fault
    )
    assert len(remove_notes) == 1
    assert "removes the money_invariant check on `total_amount`" in remove_notes[0]["note"]
    assert "`$.gross_sum`" in remove_notes[0]["note"]

    untouched = field_notes_for_proposal(
        spec,
        [{"op": "replace", "path": "/operations/list_reservations/pagination/strategy", "value": "cursor"}],
        fault,
    )
    assert untouched == []


def test_field_notes_cover_malformed_ops_and_missing_fields() -> None:
    """REQ-055 item 2: the notes helper never raises on odd patches."""
    import copy as _copy

    from autobridge.repair.dryrun import field_notes_for_proposal

    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    fault = _copy.deepcopy(fault)
    assert field_notes_for_proposal({}, [], None) == []
    assert field_notes_for_proposal(spec, None, fault) == []
    assert field_notes_for_proposal(spec, [{"op": "replace"}], fault) == []
    assert field_notes_for_proposal(spec, [{"op": "replace", "path": "/operations/x"}], fault) == []
    assert field_notes_for_proposal(spec, [{"op": "remove", "path": "/entities/Reservation/checks/9"}], fault) == []
    assert field_notes_for_proposal(spec, [{"op": "remove", "path": "/entities/Reservation/checks/x"}], fault) == []
    # A removal without a previous pointer still names the check.
    bare = field_notes_for_proposal(spec, [{"op": "remove", "path": "/entities/Reservation/checks/0"}], fault)
    assert len(bare) == 1 and "removes the unique_key check" in bare[0]["note"]
    # Non-string and non-index pointers are skipped, never raised on.
    assert (
        field_notes_for_proposal(
            spec,
            [{"op": "replace", "path": "/entities/Reservation/checks/3/compare_to", "value": 5}],
            fault,
        )
        == []
    )
    assert (
        field_notes_for_proposal(
            spec,
            [{"op": "replace", "path": "/entities/Reservation/checks/x/compare_to", "value": "$.total"}],
            fault,
        )
        == []
    )
    # A resolvable-None pointer (missing field) reads as missing, not money.
    missing = field_notes_for_proposal(
        spec,
        [{"op": "add", "path": "/entities/Reservation/checks/4/compare_to", "value": "$.net_sum"}],
        fault,
    )
    assert len(missing) == 1 and "missing or null" in missing[0]["note"]


def test_field_notes_survive_an_extraction_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extraction raising mid-note degrades to a missing value, never an error."""
    import copy as _copy

    import autobridge.runtime.interpreter as _interp
    from autobridge.repair.dryrun import field_notes_for_proposal

    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    fault = _copy.deepcopy(fault)

    def _boom(_raw: Any, _ptr: str) -> Any:
        raise RuntimeError("no jsonpath here")

    monkeypatch.setattr(_interp, "extract_jsonpath", _boom)
    notes = field_notes_for_proposal(
        spec,
        [{"op": "replace", "path": "/entities/Reservation/checks/3/compare_to", "value": "$.total"}],
        fault,
    )
    assert len(notes) == 1 and "missing or null" in notes[0]["note"]


def test_dry_run_skips_old_missing_pointers_on_extraction_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-055 item 2: an extraction fault on an old pointer is skipped."""

    import autobridge.runtime.interpreter as _interp

    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    real = _interp.extract_jsonpath

    def _flaky(raw: Any, ptr: str) -> Any:
        if ptr == "$.gross_sum":
            raise RuntimeError("wire hiccup")
        return real(raw, ptr)

    monkeypatch.setattr(_interp, "extract_jsonpath", _flaky)
    fault2 = _fault_with_gross(fault, _record_sum(fault))
    # Adding a benign check exercises every old pointer (the gross_sum
    # extraction faults to missing and is skipped) while the run passes.
    ok, detail = dry_run_proposal(
        spec,
        [{"op": "add", "path": "/entities/Reservation/checks/3", "value": {"kind": "unique_key"}}],
        fault2,
    )
    assert ok is True, detail


def test_dry_run_mapping_change_remapped_or_blocked() -> None:
    """A mapping-touching patch is judged on re-mapped wire records."""
    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    fault = _fault_with_gross(fault, _record_sum(fault))
    # Same wire field under the patched mapping: re-maps cleanly.
    ok, detail = dry_run_proposal(
        spec,
        [{"op": "replace", "path": "/entities/Reservation/fields/total_amount/path", "value": "$.total_amount"}],
        fault,
    )
    assert ok is True, detail
    # A wire field the vendor never sends re-maps to empty money: the
    # money check fails on the current records instead of passing.
    ok, detail = dry_run_proposal(
        spec,
        [{"op": "replace", "path": "/entities/Reservation/fields/total_amount/path", "value": "$.net_amount"}],
        fault,
    )
    assert ok is False and "money_invariant" in detail


def test_dry_run_remap_blocked_and_erroring(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-055 item 2: re-map outcomes are row data, never climb errors."""
    import autobridge.runtime.mapping as _mapping

    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    patch = [
        {
            "op": "replace",
            "path": "/entities/Reservation/fields/total_amount/path",
            "value": "$.total_amount",
        }
    ]
    def _blocked(*_args: Any, **_kwargs: Any) -> Any:
        return ([], [{"error": "blocked"}])

    monkeypatch.setattr(_mapping, "map_records_to_entities", _blocked)
    ok, detail = dry_run_proposal(spec, patch, fault)
    assert ok is False and "blocked after re-map" in detail

    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("remap blew up")

    monkeypatch.setattr(_mapping, "map_records_to_entities", _raise)
    ok, detail = dry_run_proposal(spec, patch, fault)
    assert ok is False and "re-map failed" in detail


def test_dry_run_mirror_flags_a_sum_the_shared_slot_missed() -> None:
    """REQ-055 item 2: the money re-run judges the sum even when the shared
    compare slot carried the no_loss count (low records vs gross sum)."""
    import copy as _copy

    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    fault = _copy.deepcopy(fault)
    for rec in fault["canonical_records"]["Reservation"]:
        if isinstance(rec, dict):
            rec["total_amount"] = "1.00"
    fault = _fault_with_gross(fault, "999999.99")
    ok, detail = dry_run_proposal(
        spec,
        [{"op": "replace", "path": "/entities/Reservation/checks/1/compare_to", "value": "$.limit"}],
        fault,
    )
    assert ok is False
    assert "money_invariant" in detail
    assert "no_loss" in detail


def test_dry_run_mirror_survives_a_checker_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mirror re-run is best-effort: a checker error never fails the run."""
    import copy as _copy

    import autobridge.runtime.checks as _checks

    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    fault = _copy.deepcopy(fault)
    for rec in fault["canonical_records"]["Reservation"]:
        if isinstance(rec, dict):
            rec["total_amount"] = "1.00"

    real = _checks.check_money_invariant

    def _boom(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("expected_sum") is not None:
            raise RuntimeError("checker blew up")
        return real(*args, **kwargs)

    monkeypatch.setattr(_checks, "check_money_invariant", _boom)
    ok, detail = dry_run_proposal(
        spec,
        [{"op": "replace", "path": "/entities/Reservation/checks/1/compare_to", "value": "$.limit"}],
        fault,
    )
    # The no_loss failure still lands; the mirror error is swallowed.
    assert ok is False and "no_loss" in detail


def test_dry_run_money_check_without_pointer_is_skipped() -> None:
    """A money check with a field but no pointer has nothing to judge: skipped."""
    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    fault = _fault_with_gross(fault, _record_sum(fault))
    ok, detail = dry_run_proposal(
        spec,
        [
            {
                "op": "add",
                "path": "/entities/Reservation/checks/4",
                "value": {"kind": "money_invariant", "field": "total_amount"},
            }
        ],
        fault,
    )
    assert ok is True, detail


def test_dry_run_passes_patches_that_touch_no_entity() -> None:
    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    pagination_patch = [
        {"op": "replace", "path": "/operations/list_reservations/pagination/strategy", "value": "cursor"}
    ]
    ok, _ = dry_run_proposal(spec, pagination_patch, fault)
    assert ok is True


def test_dry_run_without_fault_records_skips() -> None:
    spec = _harbor_spec()
    ok, detail = dry_run_proposal(spec, _net_sum_patch(), None)
    assert ok is True
    assert "skipped" in detail


def _scripted_frontier(texts: list[str]) -> Any:
    from autobridge.llm.provider import Completion, FakeProvider, Usage

    script = list(texts)

    def _next(*_args: Any, **_kwargs: Any) -> Completion:
        text = script.pop(0) if len(script) > 1 else script[0]
        return Completion(text=text, usage=Usage(10, 10))

    return FakeProvider(
        responses={
            "small": Completion(
                text='["gross_is_canonical", "net_is_canonical"]', usage=Usage(4, 4)
            ),
            "frontier": _next,
        }
    )


def _harbor_semantic_inputs() -> dict[str, Any]:
    from autobridge.discovery.docs_reader import Provenance
    from autobridge.discovery.hypotheses import Hypothesis

    prov = (Provenance(source="demo:gross_to_net", line=1, snippet="total_amount reads net"),)
    return {
        "hypotheses": [
            Hypothesis(
                id="gross_is_canonical",
                family="amount",
                claim="total_amount must stay gross",
                candidate_mapping_fragment={"family": "amount", "field": "total_amount"},
                prior=0.41,
                evidence_for=prov,
                field="total_amount",
            ),
            Hypothesis(
                id="net_is_canonical",
                family="amount",
                claim="total_amount reads net now",
                candidate_mapping_fragment={"family": "amount", "field": "total_amount"},
                prior=0.39,
                evidence_for=prov,
                field="total_amount",
            ),
        ]
    }


def test_climb_retries_with_the_exact_dry_run_error_then_drops() -> None:
    """Bad field first: one retry with the exact error, then question-only."""
    from autobridge.llm.provider import Budget
    from autobridge.repair.ladder import run_frontier_climb
    from autobridge.repair.semantic import allowed_l3_pointers

    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    evidence = {
        "patch_target": {
            "document": "connector spec",
            "entity": "Reservation",
            "allowed_ops": ["replace", "add"],
            "allowed_pointers": allowed_l3_pointers(spec, "Reservation"),
        }
    }
    bad = json.dumps(_net_sum_patch())
    climb = run_frontier_climb(
        spec=spec,
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=_harbor_semantic_inputs()["hypotheses"],
        enumerator_yield_count=1,
        provider=_scripted_frontier([bad, bad]),
        budget=Budget(),
        vendor="harborpms",
        scenario="gross_to_net",
        validate_against=spec,
        evidence=evidence,
        dry_run_fault=fault,
    )
    assert climb["proposal"] is None
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert "dry-run" in (l3.get("retry_error") or l3.get("reason") or "")
    assert "`$.net_sum` is not a field in the response" in (
        l3.get("retry_error") or l3.get("reason") or ""
    )
    assert climb["usage"]["frontier_calls"] == 2


def test_climb_offers_the_retried_proposal_with_the_dry_run_result() -> None:
    """Bad field, then an honest removal: the proposal is offered once, with
    the dry-run result and field notes beside it (REQ-055 item 2)."""
    from autobridge.llm.provider import Budget
    from autobridge.repair.ladder import run_frontier_climb
    from autobridge.repair.semantic import allowed_l3_pointers

    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    evidence = {
        "patch_target": {
            "document": "connector spec",
            "entity": "Reservation",
            "allowed_ops": ["replace", "add"],
            "allowed_pointers": allowed_l3_pointers(spec, "Reservation"),
        }
    }
    remove_patch = [{"op": "remove", "path": "/entities/Reservation/checks/3"}]
    climb = run_frontier_climb(
        spec=spec,
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=_harbor_semantic_inputs()["hypotheses"],
        enumerator_yield_count=1,
        provider=_scripted_frontier([json.dumps(_net_sum_patch()), json.dumps(remove_patch)]),
        budget=Budget(),
        vendor="harborpms",
        scenario="gross_to_net",
        validate_against=spec,
        evidence=evidence,
        dry_run_fault=fault,
    )
    assert climb["proposal"] == remove_patch
    assert climb["dry_run"] == "tested on the current records: the checks pass"
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3.get("dry_run") == "tested on the current records: the checks pass"
    notes = l3.get("field_notes") or []
    assert len(notes) == 1 and "removes the money_invariant check" in notes[0]["note"]


def test_climb_drops_a_count_repoint_with_the_money_sum_error() -> None:
    """REQ-055 item 2 (deck 5): the count repoint fails the dry-run for the
    right reason, the retry gets the exact message, then question-only."""
    from autobridge.llm.provider import Budget
    from autobridge.repair.ladder import run_frontier_climb
    from autobridge.repair.semantic import allowed_l3_pointers

    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    evidence = {
        "patch_target": {
            "document": "connector spec",
            "entity": "Reservation",
            "allowed_ops": ["replace", "add"],
            "allowed_pointers": allowed_l3_pointers(spec, "Reservation"),
        }
    }
    climb = run_frontier_climb(
        spec=spec,
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=_harbor_semantic_inputs()["hypotheses"],
        enumerator_yield_count=1,
        provider=_scripted_frontier([json.dumps(_net_sum_patch()), json.dumps(_resolvable_patch())]),
        budget=Budget(),
        vendor="harborpms",
        scenario="gross_to_net",
        validate_against=spec,
        evidence=evidence,
        dry_run_fault=fault,
    )
    assert climb["proposal"] is None
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert "`$.net_sum` is not a field in the response" in (l3.get("retry_error") or "")
    assert "the patched check did not run: `$.total` is not a money sum" in (
        l3.get("reason") or ""
    )
    assert climb["usage"]["frontier_calls"] == 2


def _seed_harbor(factory: Any, spec: dict[str, Any]) -> tuple[str, str]:
    with factory() as s:
        conn = Connector(
            name="harborpms",
            base_url="http://localhost:8106",
            credential_ref="HARBOR_API_KEY",
            target_entities=["Reservation"],
            runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.flush()
        rel = ConnectorRelease(
            connector_id=conn.id, version=1, spec=spec, signature=spec_signature(spec)
        )
        s.add(rel)
        s.flush()
        conn.active_release_id = rel.id
        s.commit()
        return conn.id, rel.id


def _honest_removal_patch() -> list[dict[str, Any]]:
    # The honest outcome for deck 5 (REQ-055 item 2): stop comparing
    # against the gross total — remove the check, labelled as a removal.
    return [{"op": "remove", "path": "/entities/Reservation/checks/3"}]


def test_value_shape_and_short_json_helpers() -> None:
    from autobridge.repair.dryrun import _short_json, _value_shape

    assert _value_shape(None) == "missing or null"
    assert _value_shape(True) == "a boolean"
    assert _value_shape(30) == "an integer"
    assert _value_shape(1.5) == "a number"
    assert _value_shape("x") == "a string"
    assert _value_shape("1234.56") == "a money sum"
    assert _value_shape(["a"]) == "a list"
    assert _short_json("y" * 100).endswith("…")
    assert _short_json(30) == "30"


def test_gate_records_the_pass_on_the_proposing_rung() -> None:
    """A passing proposal keeps its candidate and stamps the dry-run on
    the proposing rung (shown with the patch on the repair page)."""
    from autobridge.engine.detect_repair import _gate_proposal_on_fault_records
    from autobridge.repair.ladder import LadderResult

    factory = _mem_factory()
    spec = _harbor_spec()
    conn_id, _rel_id = _seed_harbor(factory, spec)
    _summary, _fault = _harbor_fault(spec)
    with factory() as s:
        drift = DriftEvent(
            connector_id=conn_id, drift_class="SEMANTIC_VALUE_DRIFT",
            source_sync_run_id="sync-x", status="open",
        )
        s.add(drift)
        s.flush()
        run = RepairRun(
            drift_event_ids=[drift.id], decision="propose", model_usage={},
            candidate_patch=_honest_removal_patch(), rule_id="test_rule",
            vendor="harborpms", drift_class="SEMANTIC_VALUE_DRIFT",
            ladder_trace=[{"level": 1, "action": "rule", "reason": "matched"}],
            ladder_level_reached=1,
        )
        s.add(run)
        s.flush()
        result = LadderResult(
            decision="propose", ladder_level_reached=1,
            candidate_patch=_honest_removal_patch(), rule_id="test_rule",
            verification_level=None, model_usage={},
            repair_run_id=run.id,
        )
        rewritten, dry_error = _gate_proposal_on_fault_records(
            s, spec_dict=spec, summary=_summary, result=result
        )
        assert dry_error is None
        assert rewritten.decision == "propose"
        assert rewritten.candidate_patch == _honest_removal_patch()
        s.commit()
        row = s.get(RepairRun, run.id)
        assert row is not None
        rung = next(e for e in (row.ladder_trace or []) if e.get("level") == 1)
        assert rung.get("dry_run") == "tested on the current records: the checks pass"


def test_gate_rewrites_a_bad_deterministic_proposal_to_escalate() -> None:
    """The detect-layer gate drops a failing patch: escalation, no candidate."""
    from autobridge.engine.detect_repair import _gate_proposal_on_fault_records
    from autobridge.repair.ladder import LadderResult

    factory = _mem_factory()
    spec = _harbor_spec()
    conn_id, rel_id = _seed_harbor(factory, spec)
    _summary, _fault = _harbor_fault(spec)
    with factory() as s:
        drift = DriftEvent(
            connector_id=conn_id, drift_class="SEMANTIC_VALUE_DRIFT",
            source_sync_run_id="sync-x", status="open",
        )
        s.add(drift)
        s.flush()
        run = RepairRun(
            drift_event_ids=[drift.id], decision="propose", model_usage={},
            candidate_patch=_net_sum_patch(), rule_id="test_rule",
            vendor="harborpms", drift_class="SEMANTIC_VALUE_DRIFT",
        )
        s.add(run)
        s.flush()
        result = LadderResult(
            decision="propose", ladder_level_reached=1,
            candidate_patch=_net_sum_patch(), rule_id="test_rule",
            verification_level=None, model_usage={},
            repair_run_id=run.id,
        )
        rewritten, dry_error = _gate_proposal_on_fault_records(
            s, spec_dict=spec, summary=_summary, result=result
        )
        assert dry_error is not None and "$.net_sum" in dry_error
        assert rewritten.decision == "escalate"
        assert rewritten.candidate_patch is None
        s.commit()
        row = s.get(RepairRun, run.id)
        assert row is not None
        assert row.decision == "escalate"
        assert not row.candidate_patch
        assert "dry-run" in (row.escalation_reason or "")


def test_gate_rewrites_a_count_repoint_to_escalate() -> None:
    """REQ-055 item 2: the detect-layer gate drops the deck-5 count repoint
    (money invariant at `$.total`) — escalation, no candidate."""
    from autobridge.engine.detect_repair import _gate_proposal_on_fault_records
    from autobridge.repair.ladder import LadderResult

    factory = _mem_factory()
    spec = _harbor_spec()
    conn_id, rel_id = _seed_harbor(factory, spec)
    _summary, _fault = _harbor_fault(spec)
    with factory() as s:
        drift = DriftEvent(
            connector_id=conn_id, drift_class="SEMANTIC_VALUE_DRIFT",
            source_sync_run_id="sync-x", status="open",
        )
        s.add(drift)
        s.flush()
        run = RepairRun(
            drift_event_ids=[drift.id], decision="propose", model_usage={},
            candidate_patch=_resolvable_patch(), rule_id="test_rule",
            vendor="harborpms", drift_class="SEMANTIC_VALUE_DRIFT",
        )
        s.add(run)
        s.flush()
        result = LadderResult(
            decision="propose", ladder_level_reached=1,
            candidate_patch=_resolvable_patch(), rule_id="test_rule",
            verification_level=None, model_usage={},
            repair_run_id=run.id,
        )
        rewritten, dry_error = _gate_proposal_on_fault_records(
            s, spec_dict=spec, summary=_summary, result=result
        )
        assert dry_error is not None and "is not a money sum" in dry_error
        assert rewritten.decision == "escalate"
        assert rewritten.candidate_patch is None


def test_failed_verifying_sync_rolls_back_and_reopens_without_a_second_repair() -> None:
    """Approve a bad patch → verifying sync Degraded → automatic rollback;
    the original escalation stays open, no second repair is created."""
    from autobridge.engine.detect_repair import detect_after_sync
    from autobridge.engine.releases import approve_release

    factory = _mem_factory()
    spec = _harbor_spec()
    conn_id, r1_id = _seed_harbor(factory, spec)

    with factory() as s:
        drift = DriftEvent(
            connector_id=conn_id, drift_class="SEMANTIC_VALUE_DRIFT",
            source_sync_run_id="sync-break", status="open",
        )
        s.add(drift)
        s.flush()
        run = RepairRun(
            drift_event_ids=[drift.id], decision="propose", model_usage={},
            candidate_patch=_net_sum_patch(), rule_id="test_rule",
            vendor="harborpms", drift_class="SEMANTIC_VALUE_DRIFT",
        )
        s.add(run)
        s.flush()
        candidate = ConnectorRelease(
            connector_id=conn_id, version=2, spec=spec,
            parent_release_id=r1_id, signature=spec_signature(spec),
            provenance_repair_run_id=run.id,
        )
        s.add(candidate)
        s.flush()
        approve_release(
            s, repair_run_id=run.id, actor="operator",
            from_release_id=r1_id, to_release_id=candidate.id,
            connector_id=conn_id,
        )
        assert run.decision == "approved"
        s.commit()
        candidate_id = candidate.id

    # The verifying sync runs on the approved release and still fails.
    _summary, _fault = _harbor_fault(spec)
    with factory() as s:
        out = detect_after_sync(
            s, connector_id=conn_id, release_id=candidate_id,
            spec_dict=spec, summary=_summary, sync_run_id="sync-verify",
        )
        s.commit()
    assert out.get("rolled_back", {}).get("rolled_back") is True, out
    assert out["drift_found"] is False
    with factory() as s:
        assert s.query(DriftEvent).filter(DriftEvent.status == "open").count() == 1
        # Still exactly one repair: the original, re-opened — never a second.
        assert s.query(RepairRun).count() == 1
        row = s.query(RepairRun).one()
        assert row.decision == "escalate"
        assert not row.candidate_patch
        bundle = row.escalation_bundle or {}
        assert bundle.get("proposal") is None
        assert "automatic rollback" in (bundle.get("reason") or "")
        # Two approvals: the approve and the automatic rollback.
        approvals = (
            s.query(Approval).filter(Approval.repair_run_id == row.id)
            .order_by(Approval.created_at).all()
        )
        assert [a.decision for a in approvals] == ["approve", "rollback"]
        conn = s.get(Connector, conn_id)
        assert conn is not None
        active = s.get(ConnectorRelease, conn.active_release_id)
        assert active is not None and active.version == 3
        # The rollback release restores the pre-approval spec: the bad
        # compare_to is gone, the gross_sum check stands.
        checks = active.spec["entities"]["Reservation"]["checks"]
        money = [c for c in checks if c.get("kind") == "money_invariant"]
        assert money and money[0].get("compare_to") == "$.gross_sum"

    # A later sync on the rolled-back release dedupes onto the original
    # repair instead of opening a second escalation.
    with factory() as s:
        conn = s.get(Connector, conn_id)
        assert conn is not None
        out2 = detect_after_sync(
            s, connector_id=conn_id, release_id=str(conn.active_release_id),
            spec_dict=spec, summary=_summary, sync_run_id="sync-again",
        )
        assert out2.get("deduped") is True, out2
        assert out2.get("repair_run_id") == row.id
        assert s.query(RepairRun).count() == 1


def test_verifying_sync_complete_needs_no_rollback() -> None:
    from autobridge.engine.detect_repair import _maybe_rollback_verifying_sync
    from autobridge.engine.releases import approve_release

    factory = _mem_factory()
    spec = _harbor_spec()
    conn_id, r1_id = _seed_harbor(factory, spec)
    with factory() as s:
        run = RepairRun(drift_event_ids=[], decision="propose", model_usage={},
                        candidate_patch=_resolvable_patch())
        s.add(run)
        s.flush()
        candidate = ConnectorRelease(
            connector_id=conn_id, version=2, spec=spec,
            parent_release_id=r1_id, signature=spec_signature(spec),
            provenance_repair_run_id=run.id,
        )
        s.add(candidate)
        s.flush()
        approve_release(
            s, repair_run_id=run.id, actor="operator",
            from_release_id=r1_id, to_release_id=candidate.id,
            connector_id=conn_id,
        )
        s.commit()
        assert _maybe_rollback_verifying_sync(
            s, connector_id=conn_id, release_id=candidate.id,
            summary={"outcome": "Complete", "operation_results": {"op": {"records": [{}]}}},
        ) is None


def test_l3_retry_after_dry_run_rejection_passes_and_labels_calls() -> None:
    """REQ-055 item 4b: the dry-run gate at work.

    Call 1 proposes ``$.net_sum`` (a field Harbor never sends) — rejected
    by the dry-run with the field-meanings error; call 2 (the one retry,
    carrying that error) proposes ``$.gross_sum`` — passed. The rung
    carries each call's proposal and verdict; the ledger outcomes read
    "rejected by dry-run" / "passed dry-run" instead of codes.
    """
    from autobridge.control.models import ModelCall as _ModelCall
    from autobridge.llm.provider import Budget, Completion, FakeProvider, Usage
    from autobridge.repair.controller import trace_from_attempts
    from autobridge.repair.knowledge import KnowledgeCache
    from autobridge.repair.ladder import run_frontier_climb, rung_ledger
    from tests.unit.test_autobridge_frontier_climb import gross_net_hypotheses

    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    fault = _fault_with_gross(fault, _record_sum(fault))
    bad = (
        '{"patch": [{"op": "replace", "path": "/entities/Reservation/checks/3/compare_to", '
        '"value": "$.net_sum"}], "rationale": "Read the net total."}'
    )
    good = (
        '{"patch": [{"op": "replace", "path": "/entities/Reservation/checks/3/compare_to", '
        '"value": "$.gross_sum"}], "rationale": "Use the documented gross sum."}'
    )
    seen: list[str] = []

    def _frontier(messages: Any, **kwargs: Any) -> Completion:
        _ = kwargs
        seen.append(str(messages[1]["content"]))
        return Completion(text=bad if len(seen) == 1 else good, usage=Usage(10, 10))

    fake = FakeProvider(
        responses={
            "small": Completion(
                text='["gross_is_canonical", "net_is_canonical"]', usage=Usage(4, 4)
            ),
            "frontier": _frontier,
        }
    )
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        climb = run_frontier_climb(
            spec=spec,
            drift_class="SEMANTIC_VALUE_DRIFT",
            hypotheses=gross_net_hypotheses(),
            enumerator_yield_count=1,
            provider=fake,
            budget=Budget(),
            session=s,
            run_id="l3-retry-labels",
            knowledge_cache=KnowledgeCache(),
            fingerprint_delta={"semantic": "gross_to_net"},
            vendor="harbor",
            scenario="gross_to_net",
            validate_against=spec,
            evidence={
                "patch_target": {
                    "allowed_pointers": ["/entities/Reservation/checks/3/compare_to"]
                }
            },
            dry_run_fault=fault,
        )
        s.commit()
        assert climb["proposal"] == [
            {
                "op": "replace",
                "path": "/entities/Reservation/checks/3/compare_to",
                "value": "$.gross_sum",
            }
        ]
        l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
        assert l3["retried"] is True
        # The retry prompt carries the rejection with field meanings —
        # generic, no vendor or scenario names.
        assert "not a field in the response" in seen[1]
        assert "number of records on this page" in seen[1]
        assert "money_invariant" in seen[1]
        for banned in ("harbor", "Harbor", "gross_to_net", "atlas", "Atlas"):
            assert banned not in seen[1], banned
        # The rung carries both calls' proposals and verdicts.
        proposals = l3["proposals"]
        assert len(proposals) == 2
        assert [p["passed"] for p in proposals] == [False, True]
        assert proposals[0]["patch"][0]["value"] == "$.net_sum"
        assert "not a field in the response" in proposals[0]["error"]
        assert proposals[1]["patch"][0]["value"] == "$.gross_sum"
        assert proposals[1]["error"] == ""
        # The ledger outcomes name the gate.
        rows = s.query(_ModelCall).order_by(_ModelCall.created_at).all()
        assert [r.tier for r in rows] == ["small", "frontier", "frontier"]
        frontier_rows = [r for r in rows if r.tier == "frontier"]
        assert [r.outcome for r in frontier_rows] == ["rejected by dry-run", "passed dry-run"]
        attempts = rung_ledger(trace_from_attempts(list(climb["attempts"])), list(rows))
        l3_attempt = next(a for a in attempts if a["rung"] == 3)
        assert [c["outcome"] for c in l3_attempt["calls"]] == [
            "rejected by dry-run",
            "passed dry-run",
        ]
        assert [p["passed"] for p in l3_attempt["proposals"]] == [False, True]


def test_field_meanings_edge_inputs_never_raise() -> None:
    # REQ-055 item 4a: the meanings helper is best-effort — odd specs and
    # parcels yield fewer meanings, never an exception.
    from autobridge.repair.dryrun import _available_fields_note, field_meanings_for_retry

    assert field_meanings_for_retry({}, None) == {}
    assert field_meanings_for_retry(None, None) == {}  # type: ignore[arg-type]
    odd = {"operations": {"op": "not-a-dict"}, "entities": "nope"}
    assert field_meanings_for_retry(odd, None) == {}
    odd2 = {
        "operations": {"op": {"pagination": "nope"}},
        "entities": {"E": {"fields": {"f": {"path": 5}}, "checks": "nope"}},
    }
    assert field_meanings_for_retry(odd2, {"raw_first_pages": {"op": {"a": 1}}}) == {
        "a": "`1` in the current response (an integer)"
    }
    # A record count matching the page size is named as a count.
    counted = field_meanings_for_retry(
        {}, {"raw_first_pages": {"op": {"rows": [{}, {}], "total": 2}}}
    )
    assert counted["total"] == "number of records on this page"
    assert _available_fields_note({}, None) == ""
    assert _available_fields_note({"x": 1}, "not-a-dict") == ""


def test_l3_escalation_proposal_retry_passes_and_labels_calls() -> None:
    """REQ-055 item 4b: the deck-5 keyed shape through the escalate path.

    Call 1 escalates carrying a proposal the dry-run rejects
    (``$.net_sum``); the one retry re-asks and carries the tested proposal
    (``$.gross_sum``), which passes and rides the escalation. The rung
    carries both calls' proposals and verdicts; the ledger outcomes read
    "rejected by dry-run" / "passed dry-run".
    """
    from autobridge.control.models import ModelCall as _ModelCall2
    from autobridge.llm.provider import Budget, Completion, FakeProvider, Usage
    from autobridge.repair.controller import trace_from_attempts
    from autobridge.repair.knowledge import KnowledgeCache
    from autobridge.repair.ladder import run_frontier_climb, rung_ledger
    from tests.unit.test_autobridge_frontier_climb import gross_net_hypotheses

    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    fault = _fault_with_gross(fault, _record_sum(fault))
    bad_proposal = {
        "label": "Accept net",
        "effect": "Retarget the totals check to the net total.",
        "patch": [
            {
                "op": "replace",
                "path": "/entities/Reservation/checks/3/compare_to",
                "value": "$.net_sum",
            }
        ],
    }
    good_proposal = {
        "label": "Accept net",
        "effect": "Retarget the totals check to the page total.",
        "patch": [
            {
                "op": "replace",
                "path": "/entities/Reservation/checks/3/compare_to",
                "value": "$.gross_sum",
            }
        ],
    }
    import json as _json

    first = _json.dumps(
        {
            "escalate": "Should total_amount track the vendor net amount?",
            "rationale": "Observed amounts sum below the gross baseline.",
            "proposal": bad_proposal,
        }
    )
    second = _json.dumps(
        {
            "escalate": "Should total_amount track the vendor net amount?",
            "rationale": "The net field is absent; the page total is documented.",
            "proposal": good_proposal,
        }
    )
    seen: list[str] = []

    def _frontier(messages: Any, **kwargs: Any) -> Completion:
        _ = kwargs
        seen.append(str(messages[1]["content"]))
        return Completion(text=first if len(seen) == 1 else second, usage=Usage(10, 10))

    fake = FakeProvider(
        responses={
            "small": Completion(
                text='["gross_is_canonical", "net_is_canonical"]', usage=Usage(4, 4)
            ),
            "frontier": _frontier,
        }
    )
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        climb = run_frontier_climb(
            spec=spec,
            drift_class="SEMANTIC_VALUE_DRIFT",
            hypotheses=gross_net_hypotheses(),
            enumerator_yield_count=1,
            provider=fake,
            budget=Budget(),
            session=s,
            run_id="l3-escalate-labels",
            knowledge_cache=KnowledgeCache(),
            fingerprint_delta={"semantic": "gross_to_net"},
            vendor="harbor",
            scenario="gross_to_net",
            validate_against=spec,
            evidence={
                "patch_target": {
                    "allowed_pointers": ["/entities/Reservation/checks/3/compare_to"]
                }
            },
            dry_run_fault=fault,
        )
        s.commit()
        assert climb["proposal"] == good_proposal["patch"]
        assert climb["proposal_label"] == "Accept net"
        l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
        assert l3["action"] == "model_escalation"
        assert l3["retried"] is True
        # Call 1's rejection rides the retry message, with field meanings.
        assert "not a field in the response" in seen[1]
        assert "number of records on this page" in seen[1]
        # Both calls' proposals and verdicts ride the rung.
        proposals = l3["proposals"]
        assert len(proposals) == 2
        assert [p["passed"] for p in proposals] == [False, True]
        assert proposals[0]["patch"][0]["value"] == "$.net_sum"
        assert proposals[1]["patch"][0]["value"] == "$.gross_sum"
        rows = s.query(_ModelCall2).order_by(_ModelCall2.created_at).all()
        frontier_rows = [r for r in rows if r.tier == "frontier"]
        assert [r.outcome for r in frontier_rows] == ["rejected by dry-run", "passed dry-run"]
        attempts = rung_ledger(trace_from_attempts(list(climb["attempts"])), list(rows))
        l3_attempt = next(a for a in attempts if a["rung"] == 3)
        assert [c["outcome"] for c in l3_attempt["calls"]] == [
            "rejected by dry-run",
            "passed dry-run",
        ]
        assert [p["passed"] for p in l3_attempt["proposals"]] == [False, True]


def _run_l3_climb(
    spec: Any,
    fault: Any,
    first_text: str,
    second_text: str | None = None,
    run_id: str = "l3-labels",
) -> Any:
    """Run the frontier climb with scripted L3 answers (REQ-055 item 4b).

    One climb per script: call 1 answers *first_text*, the single retry (if
    one runs) answers *second_text*. Returns ``(climb, rows)`` with the
    committed ModelCall rows.
    """
    from autobridge.control.models import ModelCall as _ModelCall3
    from autobridge.llm.provider import Budget, Completion, FakeProvider, Usage
    from autobridge.repair.knowledge import KnowledgeCache
    from autobridge.repair.ladder import run_frontier_climb
    from tests.unit.test_autobridge_frontier_climb import gross_net_hypotheses

    seen: list[str] = []

    def _frontier(messages: Any, **kwargs: Any) -> Completion:
        _ = kwargs
        seen.append(str(messages[1]["content"]))
        text = first_text if len(seen) == 1 else (second_text if second_text is not None else first_text)
        return Completion(text=text, usage=Usage(10, 10))

    fake = FakeProvider(
        responses={
            "small": Completion(
                text='["gross_is_canonical", "net_is_canonical"]', usage=Usage(4, 4)
            ),
            "frontier": _frontier,
        }
    )
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        climb = run_frontier_climb(
            spec=spec,
            drift_class="SEMANTIC_VALUE_DRIFT",
            hypotheses=gross_net_hypotheses(),
            enumerator_yield_count=1,
            provider=fake,
            budget=Budget(),
            session=s,
            run_id=run_id,
            knowledge_cache=KnowledgeCache(),
            fingerprint_delta={"semantic": "gross_to_net"},
            vendor="harbor",
            scenario="gross_to_net",
            validate_against=spec,
            evidence={
                "patch_target": {
                    "allowed_pointers": ["/entities/Reservation/checks/3/compare_to"]
                }
            },
            dry_run_fault=fault,
        )
        s.commit()
        rows = s.query(_ModelCall3).order_by(_ModelCall3.created_at).all()
        return climb, rows, seen


def _good_patch_json() -> str:
    import json as _json

    return _json.dumps(
        {
            "patch": [
                {
                    "op": "replace",
                    "path": "/entities/Reservation/checks/3/compare_to",
                    "value": "$.gross_sum",
                }
            ],
            "rationale": "Use the documented gross sum.",
        }
    )


def test_l3_first_try_adopt_labels_passed_dry_run() -> None:
    """REQ-055 item 4b: a first-try patch that passes is "passed dry-run"."""
    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    fault = _fault_with_gross(fault, _record_sum(fault))
    climb, rows, _seen = _run_l3_climb(spec, fault, _good_patch_json(), run_id="l3-first-adopt")
    assert climb["proposal"] == [
        {
            "op": "replace",
            "path": "/entities/Reservation/checks/3/compare_to",
            "value": "$.gross_sum",
        }
    ]
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3.get("retried") is not True
    assert [p["passed"] for p in l3["proposals"]] == [True]
    frontier_rows = [r for r in rows if r.tier == "frontier"]
    assert [r.outcome for r in frontier_rows] == ["passed dry-run"]


def test_l3_retry_question_only_labels_no_proposal() -> None:
    """REQ-055 item 4b: a retry that only asks reads "no proposal"."""
    import json as _json

    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    fault = _fault_with_gross(fault, _record_sum(fault))
    bad = _json.dumps(
        {
            "patch": [
                {
                    "op": "replace",
                    "path": "/entities/Reservation/checks/3/compare_to",
                    "value": "$.net_sum",
                }
            ],
            "rationale": "Read the net total.",
        }
    )
    question_only = _json.dumps(
        {
            "escalate": "Should total_amount track the vendor net amount?",
            "rationale": "The evidence does not settle it.",
        }
    )
    climb, rows, _seen = _run_l3_climb(spec, fault, bad, question_only, run_id="l3-retry-question")
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3["action"] == "model_escalation"
    assert [p["passed"] for p in l3["proposals"]] == [False, False]
    frontier_rows = [r for r in rows if r.tier == "frontier"]
    assert [r.outcome for r in frontier_rows] == ["rejected by dry-run", "no proposal"]
