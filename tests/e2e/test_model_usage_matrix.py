"""REQ-021 item 4 — scenario x rung matrix (model-usage proof).

For every fault scenario this file declares the expected highest rung and
asserts it: scenarios that must resolve at L0/L1 finish with 0 model calls;
``rate_limit_429`` / ``transient_503`` produce no patch and 0 calls;
``gross_to_net`` and ``invalid_patch_trap`` climb the full L0 -> L1 -> L2 ->
L3 -> verifier-rejection -> L4 path (an L3 frontier call is required; the
other semantic/data-quality drifts escalate with no patch and 0 calls); the
v1 refusal of Meridian docs costs 0 calls. A row fails if its scenario calls
the frontier when it should not, resolves above its rung, or skips a rung
it should have tried (exact trace levels are pinned per row).

Runs in-process always (ladder + fakes, green without Docker) and against
the live stack (``live`` mark, REQ-010 style): the live test cycles
reset -> inject -> reset through the control API and asserts identical
repeatability plus a zero frontier ledger. The live ladder rung proof for
the headline scenario lives in
``test_03_known_repairs_zero_frontier.test_page_to_cursor_live_zero_frontier_via_control_api``.
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import urllib.request
from typing import Any

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from autobridge.control.models import Base, ModelCall, RepairRun
from autobridge.control.routes.demo import expected_reset_targets
from autobridge.discovery.bootstrap import build_draft_spec
from autobridge.drift.fingerprint import FingerprintDelta
from autobridge.repair.controller import RepairController
from autobridge.repair.knowledge import KnowledgeCache
from autobridge.repair.ladder import ledger_totals, rung_ledger

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MERIDIAN_YAML = (REPO_ROOT / "simulators" / "docs" / "meridian-openapi.yaml").read_text(encoding="utf-8")


def _load_reference_spec(connector_id: str) -> dict[str, Any]:
    path = REPO_ROOT / "generated" / "connectors" / connector_id / "reference" / "spec.yaml"
    assert path.is_file(), f"missing {path}"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _mem_session() -> Any:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _page_to_cursor_delta() -> FingerprintDelta:
    return FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=True,
        keys_changed=False,
        types_changed=False,
        changed_entities=[],
        pagination_details={
            "list_reservations": {"previous": {"strategy": "page"}, "current": {"strategy": "cursor"}},
            "list_folios": {"previous": {"strategy": "page"}, "current": {"strategy": "cursor"}},
        },
        keys_details={},
        types_details={},
    )


def _rename_delta() -> FingerprintDelta:
    return FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=False,
        keys_changed=False,
        types_changed=True,
        changed_entities=["Reservation"],
        pagination_details={},
        keys_details={},
        types_details={
            "Reservation": {
                "previous": {"guest_name": "str", "other": "str"},
                "current": {"guest_identifier": "str", "other": "str"},
            }
        },
    )


_RENAME_EXTRA = {
    "entity": "Reservation",
    "field": "guest_name",
    "new_field": "guestIdentifier",
    "new_path": "$.guestIdentifier",
    "rename": {"entity": "Reservation", "old_field": "guest_name", "new_field": "guestIdentifier"},
}


def _offset_spec() -> dict[str, Any]:
    """Atlas reference with offset pagination (offset_to_cursor needs offset ops)."""
    spec = copy.deepcopy(_load_reference_spec("atlaspms"))
    for op in spec.get("operations", {}).values():
        pag = op.get("pagination") if isinstance(op, dict) else None
        if isinstance(pag, dict) and pag.get("strategy") == "page":
            pag["strategy"] = "offset"
    return spec


def _offset_to_cursor_delta() -> FingerprintDelta:
    return FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=True,
        keys_changed=False,
        types_changed=False,
        changed_entities=[],
        pagination_details={
            "list_reservations": {"previous": {"strategy": "offset"}, "current": {"strategy": "cursor"}},
        },
        keys_details={},
        types_details={},
    )


def _nested_delta() -> FingerprintDelta:
    return FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=True,
        keys_changed=False,
        types_changed=False,
        changed_entities=[],
        pagination_details={
            "list_reservations": {
                "previous": {"strategy": "page", "items_path": "$.data"},
                "current": {"strategy": "page", "items_path": "$.data.records"},
            },
        },
        keys_details={},
        types_details={},
    )


def _generic_delta() -> FingerprintDelta:
    return FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=False,
        keys_changed=False,
        types_changed=False,
        changed_entities=[],
        pagination_details={},
        keys_details={},
        types_details={},
    )


def _meridian_spec() -> dict[str, Any]:
    result = build_draft_spec(
        MERIDIAN_YAML,
        source="simulators/docs/meridian-openapi.yaml",
        connector_id="meridianpms",
        spec_vocabulary="v2",
    )
    assert result.success, f"meridian v2 build failed: {result.visible_errors}"
    assert result.draft_spec is not None
    return dict(result.draft_spec)


# ---------------------------------------------------------------------------
# The matrix: scenario -> expected rung, decision, rule, trace levels.
#
# expected_levels pins the exact ladder trace levels: a row fails when its
# scenario resolves above its rung, calls the frontier, or skips a rung it
# should have tried (a missing level) or climbs one it should not (extra).
# Zero-call rows wire no LLM provider, so any model call is a failure.
# Rows with "semantic_climb" wire the replay provider instead: they must
# record an L3 frontier call (frontier_tokens > 0) whose proposal the
# verifier rejects, ending at L4 with no patch. Every other row must stay
# at 0 frontier calls.
# ---------------------------------------------------------------------------

MATRIX: list[dict[str, Any]] = [
    {
        "scenario": "offset_to_cursor",
        "spec": "offset_atlas",
        "drift_class": "PAGINATION_CHANGE",
        "delta": "offset_to_cursor",
        "extra": {"target_strategy": "cursor"},
        "expected_rung": 1,
        "expected_levels": [0, 1],
        "expected_decision": "propose",
        "expected_rule": "offset_to_cursor",
        "patch": True,
        "covers": "rule unit tests + this matrix (no live vendor is offset-based)",
    },
    {
        "scenario": "rename_guest_id",
        "spec": "atlaspms",
        "drift_class": "SCHEMA_RENAME",
        "delta": "rename",
        "extra": "rename",
        "expected_rung": 1,
        "expected_levels": [0, 1],
        "expected_decision": "propose",
        "expected_rule": "rename_field",
        "patch": True,
        "covers": "test_03_known_repairs_zero_frontier (full sync/oracle/resync flow)",
    },
    {
        "scenario": "cursor_repeat_token",
        "spec": "atlaspms",
        "drift_class": "PAGINATION_CHANGE",
        "delta": "page_to_cursor",
        "extra": {"target_strategy": "cursor"},
        "expected_rung": 1,
        "expected_levels": [0, 1],
        "expected_decision": "propose",
        "expected_rule": "page_to_cursor",
        "patch": True,
        "patch_must_contain": "repeated_cursor",
        "covers": "test_03 (loop guard added by the L1 rule, not frontier loop analysis)",
    },
    {
        "scenario": "nested_path_shift",
        "spec": "atlaspms",
        "drift_class": "PAGINATION_CHANGE",
        "delta": "nested",
        "extra": {
            "operation": "list_reservations",
            "old_items_path": "$.data",
            "new_items_path": "$.data.records",
        },
        "expected_rung": 1,
        "expected_levels": [0, 1],
        "expected_decision": "propose",
        "expected_rule": "nested_path_shift",
        "patch": True,
        "covers": "test_03 (synthetic delta + oracle invariants)",
    },
    {
        "scenario": "page_to_cursor",
        "spec": "atlaspms",
        "drift_class": "PAGINATION_CHANGE",
        "delta": "page_to_cursor",
        "extra": {"target_strategy": "cursor"},
        "expected_rung": 1,
        "expected_levels": [0, 1],
        "expected_decision": "propose",
        "expected_rule": "page_to_cursor",
        "patch": True,
        "covers": "test_03 (headline) + live ladder proof",
    },
    {
        "scenario": "rate_limit_429",
        "spec": "atlaspms",
        "drift_class": "RATE_LIMIT",
        "delta": "generic",
        "extra": {},
        "expected_rung": 1,
        "expected_levels": [1],
        "expected_decision": "propose",
        "expected_rule": "retry_backoff_only",
        "patch": False,
        "covers": "transport: backoff only, no patch, 0 calls",
    },
    {
        "scenario": "transient_503",
        "spec": "atlaspms",
        "drift_class": "TRANSIENT",
        "delta": "generic",
        "extra": {},
        "expected_rung": 1,
        "expected_levels": [1],
        "expected_decision": "propose",
        "expected_rule": "retry_backoff_only",
        "patch": False,
        "covers": "transport: backoff only, no patch, 0 calls",
    },
    {
        "scenario": "token_expired",
        "spec": "atlaspms",
        "drift_class": "AUTH_EXPIRED",
        "delta": "generic",
        "extra": {},
        "expected_rung": 4,
        "expected_levels": [4],
        "expected_decision": "escalate",
        "expected_rule": None,
        "patch": False,
        "covers": "auth: bearer spec has no refresh permitted -> escalate, 0 calls (REQ-023 catalogue)",
    },
    {
        "scenario": "token_revoked",
        "spec": "atlaspms",
        "drift_class": "AUTH_REVOKED",
        "delta": "generic",
        "extra": {},
        "expected_rung": 4,
        "expected_levels": [4],
        "expected_decision": "escalate",
        "expected_rule": None,
        "patch": False,
        "covers": "test_05 (Blocked, escalate, no patch)",
    },
    {
        "scenario": "endpoint_removed",
        "spec": "atlaspms",
        "drift_class": "CAPABILITY_REMOVED",
        "delta": "generic",
        "extra": {},
        "expected_rung": 4,
        "expected_levels": [4],
        "expected_decision": "escalate",
        "expected_rule": None,
        "patch": False,
        "covers": "test_05 (Blocked, escalate, never patched around)",
    },
    {
        "scenario": "gross_to_net",
        "spec": "harborpms",
        "drift_class": "SEMANTIC_VALUE_DRIFT",
        "delta": "generic",
        "extra": {},
        "expected_rung": 4,
        "expected_levels": [0, 1, 2, 3, 4],
        "expected_decision": "escalate",
        "expected_rule": None,
        "patch": False,
        "semantic_climb": True,
        "expected_check": "money_mismatch",
        "covers": "test_05 (must not be fixed below L3: L3 frontier call recorded, proposal rejected, L4)",
    },
    {
        "scenario": "invalid_patch_trap",
        "spec": "harborpms",
        "drift_class": "SEMANTIC_VALUE_DRIFT",
        "delta": "generic",
        "extra": {},
        "expected_rung": 4,
        "expected_levels": [0, 1, 2, 3, 4],
        "expected_decision": "escalate",
        "expected_rule": None,
        "patch": False,
        "semantic_climb": True,
        "expected_check": "invalid_patch_trap",
        "covers": "test_05 (plausible-but-wrong L3 proposal rejected by the verifier, L4)",
    },
    {
        "scenario": "status_vocab_change",
        "spec": "atlaspms",
        "drift_class": "STATUS_VOCAB",
        "delta": "generic",
        "extra": {},
        "expected_rung": 4,
        "expected_levels": [0, 4],
        "expected_decision": "escalate",
        "expected_rule": None,
        "patch": False,
        "covers": "test_05 (unknown enum -> sync Blocked; ladder never guesses a mapping)",
    },
    {
        "scenario": "tz_boundary_shift",
        "spec": "atlaspms",
        "drift_class": "SEMANTIC_VALUE_DRIFT",
        "delta": "generic",
        "extra": {},
        "expected_rung": 4,
        "expected_levels": [0, 4],
        "expected_decision": "escalate",
        "expected_rule": None,
        "patch": False,
        "covers": "test_05 (Degraded business-date invariant, escalates with bundle)",
    },
    {
        "scenario": "duplicate_late_records",
        "spec": "atlaspms",
        "drift_class": "DUPLICATE_DELIVERY",
        "delta": "generic",
        "extra": {},
        "expected_rung": 4,
        "expected_levels": [0, 4],
        "expected_decision": "escalate",
        "expected_rule": None,
        "patch": False,
        "covers": "no dedup rule exists: escalate rather than invent one",
    },
    {
        "scenario": "meridian_cursor_renamed",
        "spec": "meridianpms",
        "drift_class": "PAGINATION_CHANGE",
        "delta": "meridian_cursor",
        "extra": {"new_next_path": "$.NextCursor"},
        "expected_rung": 1,
        "expected_levels": [0, 1],
        "expected_decision": "propose",
        "expected_rule": "cursor_next_path_shift",
        "patch": True,
        "covers": "test_meridian_faults (firmware-style layout change, zero frontier)",
    },
    {
        "scenario": "meridian_state_renamed",
        "spec": "meridianpms",
        "drift_class": "SCHEMA_RENAME",
        "delta": "meridian_state",
        "extra": {
            "entity": "Reservation",
            "field": "status",
            "new_field": "Status",
            "new_path": "$.Status",
            "rename": {"entity": "Reservation", "old_field": "status", "new_field": "Status"},
        },
        "expected_rung": 1,
        "expected_levels": [0, 1],
        "expected_decision": "propose",
        "expected_rule": "rename_field",
        "patch": True,
        "covers": "test_meridian_faults (renamed field, zero frontier)",
    },
]

_DELTAS = {
    "page_to_cursor": _page_to_cursor_delta,
    "rename": _rename_delta,
    "offset_to_cursor": _offset_to_cursor_delta,
    "nested": _nested_delta,
    "generic": _generic_delta,
    "meridian_cursor": lambda: FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=True,
        keys_changed=False,
        types_changed=False,
        changed_entities=["Reservation"],
        pagination_details={"list_reservations": {"previous": {"next_path": "$.Cursor"}, "current": {"next_path": "$.NextCursor"}}},
        keys_details={},
        types_details={},
    ),
    "meridian_state": lambda: FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=False,
        keys_changed=False,
        types_changed=True,
        changed_entities=["Reservation"],
        pagination_details={},
        keys_details={},
        types_details={"Reservation": {"previous": {"status": "str", "other": "str"}, "current": {"Status": "str", "other": "str"}}},
    ),
}


def _spec_for(row: dict[str, Any]) -> dict[str, Any]:
    name = row["spec"]
    if name == "atlaspms":
        return _load_reference_spec("atlaspms")
    if name == "harborpms":
        return _load_reference_spec("harborpms")
    if name == "offset_atlas":
        return _offset_spec()
    if name == "meridianpms":
        return _meridian_spec()
    raise AssertionError(f"unknown spec {name!r}")


def _extra_for(row: dict[str, Any]) -> dict[str, Any]:
    extra = row["extra"]
    if extra == "rename":
        return dict(_RENAME_EXTRA)
    assert isinstance(extra, dict)
    return dict(extra)


def _row_id(row: dict[str, Any]) -> str:
    return str(row["scenario"])


def _run_semantic_climb_row(ctrl: RepairController, session: Any, row: dict[str, Any]) -> None:
    """Full L0 -> L1 -> L2 -> L3 -> verifier-rejection -> L4 climb for one row.

    Wires the replay provider (no live calls, no network): the L3 frontier
    call is recorded in the usage ledger and as a ModelCall row, the L3
    proposal is checked against the verifier oracle (must fail), and the run
    ends at L4 with no patch. Every other matrix row must stay at 0
    frontier calls; only these rows may spend frontier tokens.
    """
    from autobridge.llm.provider import Budget, ReplayProvider  # noqa: PLC0415

    scenario = str(row["scenario"])
    result = ctrl.handle(
        spec=_spec_for(row),
        drift_class=row["drift_class"],
        signals=[],
        fingerprint_delta=_DELTAS[row["delta"]](),
        extra=_extra_for(row),
        session=session,
        hypotheses=_gross_net_hypotheses(),
        enumerator_yield_count=1,
        provider=ReplayProvider(recordings_dir=str(REPO_ROOT / "tests" / "fixtures" / "replay")),
        budget=Budget(),
        scenario=scenario,
        validate_against=_load_reference_spec("harborpms"),
        run_id=f"matrix-{scenario}",
    )
    session.commit()
    repair = session.query(RepairRun).order_by(RepairRun.created_at.desc()).first()
    assert repair is not None

    # The rung: exact match — above is a failure, below means a skipped rung.
    assert result.ladder_level_reached == row["expected_rung"], (
        f"{scenario}: rung {result.ladder_level_reached} != {row['expected_rung']} "
        f"trace {result.ladder_trace}"
    )
    assert result.decision == row["expected_decision"], (
        f"{scenario}: decision {result.decision} trace {result.ladder_trace}"
    )
    assert result.rule_id == row["expected_rule"], (
        f"{scenario}: rule {result.rule_id} trace {result.ladder_trace}"
    )
    assert not result.candidate_patch, f"{scenario}: expected no patch"

    # The L3 frontier call was recorded and paid for (replay-labelled).
    assert result.model_usage.get("frontier_tokens", 0) > 0, scenario
    assert result.model_usage.get("small_tokens", 0) > 0, scenario
    assert result.model_usage.get("calls", 0) == 2, scenario
    calls = session.query(ModelCall).order_by(ModelCall.created_at).all()
    assert len(calls) == 2, scenario
    assert sum(1 for c in calls if c.tier == "frontier") == 1, scenario
    assert sum(1 for c in calls if c.tier == "small") == 1, scenario
    assert all(c.purpose.endswith("(replay test fixture)") for c in calls), scenario

    # No skipped or extra rungs: the full climb, exactly.
    levels = [
        t.get("level")
        for t in result.ladder_trace
        if isinstance(t, dict) and isinstance(t.get("level"), int)
    ]
    assert levels == row["expected_levels"], (
        f"{scenario}: levels {levels} != {row['expected_levels']} trace {result.ladder_trace}"
    )

    # The rung ledger agrees: attempts match the trace, frontier is nonzero.
    attempts = rung_ledger(list(result.ladder_trace), list(calls))
    assert [a["rung"] for a in attempts] == row["expected_levels"], scenario
    totals = ledger_totals(attempts)
    assert totals["calls"] == 2 and totals["frontier_tokens"] > 0, scenario

    # The L3 proposal is recorded on its rung; the verifier rejects it.
    l3 = next(t for t in result.ladder_trace if isinstance(t, dict) and t.get("level") == 3)
    assert l3.get("action") == "frontier_generate", f"{scenario}: {l3}"
    proposal = l3.get("proposal")
    assert isinstance(proposal, list) and proposal, f"{scenario}: no L3 proposal recorded"
    from autobridge.spec.patch import apply_patch  # noqa: PLC0415
    from verifier.oracle import oracle_verdict  # noqa: PLC0415

    harbor_ref = _load_reference_spec("harborpms")
    patched = apply_patch(harbor_ref, proposal)
    verdict, _, failed = oracle_verdict(scenario, patched)
    assert verdict == "fail", f"{scenario}: verifier passed the L3 proposal"
    names = [f.get("name", "") for f in failed]
    assert row["expected_check"] in names, f"{scenario}: {names}"

    # L4 ends with one operator question; the stored bundle asks it.
    bundle = repair.escalation_bundle or {}
    assert str(bundle.get("question", "")).endswith("?"), f"{scenario}: {bundle}"

    # Every reason is a one-line, non-empty string.
    for attempt in attempts:
        assert attempt["reason"] and "\n" not in attempt["reason"], scenario


@pytest.mark.parametrize("row", MATRIX, ids=_row_id)
def test_matrix_in_process(row: dict[str, Any]) -> None:
    """Pin the rung, decision, rule, calls and trace levels for one scenario."""
    os.environ.setdefault("ATLAS_API_KEY", "test-token")
    SessionFactory = _mem_session()
    ctrl = RepairController(knowledge_cache=KnowledgeCache())
    with SessionFactory() as session:
        if row.get("semantic_climb"):
            _run_semantic_climb_row(ctrl, session, row)
            return
        result = ctrl.handle(
            spec=_spec_for(row),
            drift_class=row["drift_class"],
            signals=[],
            fingerprint_delta=_DELTAS[row["delta"]](),
            extra=_extra_for(row),
            session=session,
        )
        session.commit()
        repair = session.query(RepairRun).order_by(RepairRun.created_at.desc()).first()
        assert repair is not None

        # The rung: exact match — above is a failure, below means a skipped rung.
        assert result.ladder_level_reached == row["expected_rung"], (
            f"{row['scenario']}: rung {result.ladder_level_reached} != {row['expected_rung']} "
            f"trace {result.ladder_trace}"
        )
        assert result.decision == row["expected_decision"], (
            f"{row['scenario']}: decision {result.decision} trace {result.ladder_trace}"
        )
        assert result.rule_id == row["expected_rule"], (
            f"{row['scenario']}: rule {result.rule_id} trace {result.ladder_trace}"
        )
        if row["patch"]:
            assert result.candidate_patch, f"{row['scenario']}: expected a patch"
        else:
            assert not result.candidate_patch, f"{row['scenario']}: expected no patch"

        # Zero model calls on every row: no provider is wired in this path.
        assert result.model_usage.get("frontier_tokens", 0) == 0, row["scenario"]
        assert result.model_usage.get("small_tokens", 0) == 0, row["scenario"]
        assert result.model_usage.get("calls", 0) == 0, row["scenario"]
        assert session.query(ModelCall).count() == 0, row["scenario"]

        # No skipped or extra rungs: exact trace levels.
        levels = [
            t.get("level")
            for t in result.ladder_trace
            if isinstance(t, dict) and isinstance(t.get("level"), int)
        ]
        assert levels == row["expected_levels"], (
            f"{row['scenario']}: levels {levels} != {row['expected_levels']} trace {result.ladder_trace}"
        )

        # The rung ledger agrees: attempts match the trace, totals are zero.
        attempts = rung_ledger(list(result.ladder_trace), [])
        assert [a["rung"] for a in attempts] == row["expected_levels"], row["scenario"]
        totals = ledger_totals(attempts)
        assert totals["calls"] == 0 and totals["frontier_tokens"] == 0, row["scenario"]

        # Patch shape guard for the loop-guard row.
        if row.get("patch_must_contain"):
            assert row["patch_must_contain"] in json.dumps(result.candidate_patch), row["scenario"]

        # Every reason is a one-line, non-empty string.
        for attempt in attempts:
            assert attempt["reason"] and "\n" not in attempt["reason"], row["scenario"]


def test_matrix_refusal_zero_calls() -> None:
    """The v1 refusal of Meridian docs costs 0 frontier and 0 small-model calls."""
    result = build_draft_spec(
        MERIDIAN_YAML, source="simulators/docs/meridian-openapi.yaml", connector_id="meridianpms"
    )
    assert not result.success
    assert result.draft_spec is None
    assert (result.model_usage.get("calls", 0)) == 0
    assert (result.model_usage.get("frontier_tokens", 0)) == 0
    assert (result.model_usage.get("small_tokens", 0)) == 0
    assert any(t.get("stage") == "vocabulary_guard" for t in result.trace)


# ---------------------------------------------------------------------------
# Live stack (REQ-010 style): reset -> inject -> reset repeatability plus a
# zero-frontier ledger, all through the control API.
# ---------------------------------------------------------------------------


def _control_request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    from tests.e2e.conftest import service_url  # noqa: PLC0415

    url = service_url("control", path)
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("AUTOBRIDGE_CONTROL_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload or {}).encode() if method != "GET" else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        assert resp.status == 200, f"{method} {path} -> {resp.status}"
        return json.loads(resp.read().decode())


@pytest.mark.live
def test_matrix_live_reset_repeatability(e2e_stack: str) -> None:
    """Live reset cycle: identical outcomes across resets, frontier ledger zero."""
    assert e2e_stack
    # Reset once to clear whatever the shared stack holds, then take the
    # steady-state empty reset as the baseline: a second reset must match it.
    _control_request("POST", "/demo/reset", {})
    first = _control_request("POST", "/demo/reset", {})
    assert first.get("labelled") is True
    # Expected entries come from the same registry the reset endpoint uses
    # (REQ-022 owner check 2026-10-29): adding a simulator updates both.
    assert [e["target"] for e in first.get("resets", [])] == expected_reset_targets()
    assert all(e.get("status") == 200 for e in first["resets"]), first["resets"]
    assert all(v == 0 for v in first["removed"].values()), first["removed"]

    injected = _control_request(
        "POST", "/demo/inject-fault", {"target": "sim-atlas", "scenario_id": "page_to_cursor"}
    )
    assert injected.get("labelled") is True
    admin = injected.get("admin_response") or {}
    assert admin.get("_http_status") == 200, injected

    second = _control_request("POST", "/demo/reset", {})
    assert second["session_id"] != first["session_id"]
    # Same scenario state in, same reset out: repeatable.
    assert second["removed"] == first["removed"], (first, second)
    assert second["model_usage"]["archived"] == 0
    assert second["knowledge_cache"] == {"entries_before": 0, "entries_after": 0}

    usage = _control_request("GET", "/metrics/model-usage", {})
    assert usage["total_calls"] == 0, usage
    assert usage["frontier_tokens"] == 0, usage


def _gross_net_hypotheses() -> list[Any]:
    """Documented gross/net pair (ids + priors feed the replay prompt hashes)."""
    from autobridge.discovery.docs_reader import Provenance  # noqa: PLC0415
    from autobridge.discovery.hypotheses import Hypothesis  # noqa: PLC0415

    prov = (Provenance(source="demo:gross_to_net", line=1, snippet="total_amount reads net"),)
    return [
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
            claim="total_amount tracks net",
            candidate_mapping_fragment={"family": "amount", "field": "total_amount"},
            prior=0.39,
            evidence_for=prov,
            field="total_amount",
        ),
    ]


def test_matrix_gross_to_net_climbs_through_l3() -> None:
    """gross_to_net must climb L0 -> L1 -> L2 -> L3 -> trap -> L4 (owner check).

    The deterministic ladder alone would stop at L4 with no calls; the
    semantic climb goes L0 miss -> L1 no rule -> L2 below threshold ->
    L3 frontier proposal (replay) -> verifier rejection -> L4. Every other
    matrix row resolves with 0 frontier calls.
    """
    _assert_semantic_climb("gross_to_net", ["sync-harbor-gross_to_net", "probe-harbor-amounts"])


def test_matrix_invalid_patch_trap_climbs_through_l3() -> None:
    """invalid_patch_trap: same full climb; the verifier rejects the L3 proposal."""
    _assert_semantic_climb("invalid_patch_trap", ["matrix-invalid_patch_trap"])


def _assert_semantic_climb(scenario: str, evidence_ids: list[str]) -> None:
    from autobridge.llm.provider import Budget, ReplayProvider  # noqa: PLC0415
    from autobridge.repair.escalation import escalation_for_no_fix  # noqa: PLC0415
    from autobridge.repair.knowledge import KnowledgeCache  # noqa: PLC0415
    from autobridge.repair.ladder import run_frontier_climb  # noqa: PLC0415

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)
    provider = ReplayProvider(recordings_dir=str(REPO_ROOT / "tests" / "fixtures" / "replay"))
    with SessionFactory() as session:
        climb = run_frontier_climb(
            spec={"spec_version": 1, "connector": {"id": "matrix", "vendor": "harbor"}},
            drift_class="SEMANTIC_VALUE_DRIFT",
            hypotheses=_gross_net_hypotheses(),
            enumerator_yield_count=1,
            provider=provider,
            budget=Budget(),
            session=session,
            run_id=f"matrix-{scenario}",
            knowledge_cache=KnowledgeCache(),
            fingerprint_delta={"semantic": scenario},
            vendor="harbor",
            scenario=scenario,
            validate_against=_load_reference_spec("harborpms"),
        )
        session.commit()
        levels = [a["rung"] for a in climb["attempts"]]
        assert levels == [0, 1, 2, 3], climb["attempts"]
        # L3 was attempted and paid for (replay-labelled, never silent).
        assert climb["usage"]["frontier_tokens"] > 0
        assert climb["usage"]["small_tokens"] > 0
        l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
        assert l3["action"] == "frontier_generate"
        # The proposal is the documented decoy: rejected, never applied.
        assert climb["proposal"] == [
            {"op": "replace", "path": "/entities/Reservation/fields/total_amount/path", "value": "$.net_amount"}
        ]
        rows = session.query(ModelCall).order_by(ModelCall.created_at).all()
        assert len(rows) == 2
        assert all(r.purpose.endswith("(replay test fixture)") for r in rows)
        assert sum(1 for r in rows if r.tier == "frontier") == 1
        # The verifier rejects the L3 proposal (in-process oracle, same
        # rule the live /verify enforces): invalid_patch_trap.
        from autobridge.spec.patch import apply_patch  # noqa: PLC0415
        from verifier.oracle import oracle_verdict  # noqa: PLC0415

        harbor_ref = _load_reference_spec("harborpms")
        patched = apply_patch(harbor_ref, climb["proposal"])
        verdict, _, failed = oracle_verdict(scenario, patched)
        assert verdict == "fail", f"{scenario}: verifier passed the decoy proposal"
        names = [f.get("name", "") for f in failed]
        expected_check = "money_mismatch" if scenario == "gross_to_net" else "invalid_patch_trap"
        assert expected_check in names, f"{scenario}: {names}"
        # L4: one operator question; the full climb is [0, 1, 2, 3, 4].
        attempts = list(climb["attempts"]) + [
            {
                "rung": 4,
                "action": "escalate",
                "reason": "invalid_patch_trap: plausible-but-wrong proposal rejected; one operator question",
                "calls": [],
            }
        ]
        assert [a["rung"] for a in attempts] == [0, 1, 2, 3, 4]
        bundle = escalation_for_no_fix(
            f"{scenario}: trap rejected",
            evidence_ids=evidence_ids,
            question="Harbor totals now read net instead of gross — gross or new field?",
        )
        assert bundle.question.endswith("?")
