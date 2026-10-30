"""REQ-049: the model may propose one change for a human decision.

Covers item 7's engine half: prompts carry no scenario wording; the L3
answer parses as escalate+proposal / escalate-only / malformed proposal;
the proposal patch passes the apply + allowed-pointer check with one
retry; a business-decision approval mints r2 without learning; deny
keeps the error. UI tests live beside the components.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import pytest
from sqlalchemy import create_engine

from autobridge.control.models import (
    Connector,
    DriftEvent,
    RepairRun,
    init_db,
    session_factory,
)
from autobridge.discovery.bootstrap import (
    apply_documented_page_totals,
    build_draft_spec,
    documented_page_totals,
)
from autobridge.engine.releases import (
    ReleaseError,
    approve_business_decision,
    create_release,
)
from autobridge.llm.provider import Budget, Completion, FakeProvider, Usage
from autobridge.repair import semantic as _semantic
from autobridge.repair.controller import RepairController
from autobridge.repair.escalation import (
    EscalationBundle,
    escalation_for_no_fix,
)
from autobridge.repair.knowledge import KnowledgeCache
from autobridge.repair.ladder import _parse_l3_response, run_frontier_climb

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PROMPT_DIR = REPO_ROOT / "config" / "prompts"

#: Scenario-specific wording the generic proposal feature must never use
#: (REQ-049 item 4: the prompt explains the option generically).
_FORBIDDEN = [r"\bgross\b", r"\bnet\b", r"totals?", r"harbor", r"scenario"]


def _harbor_like_spec() -> dict[str, Any]:
    return {
        "spec_version": 1,
        "connector": {"id": "harbor", "vendor": "harbor", "runtime_target": "cloud"},
        "source": {
            "base_url": "https://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "rate_limit": {"max_rps": 5, "retry": {"statuses": [429, 503], "backoff": "exponential", "max_attempts": 3}},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "params": {},
                "pagination": {
                    "strategy": "cursor",
                    "cursor_param": "cursor",
                    "next_path": "$.meta.next_cursor",
                    "items_path": "$.data",
                    "stop_when": ["null_cursor"],
                },
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {
                    "reservation_id": {"path": "$.reservation_id", "transform": []},
                    "total_amount": {
                        "path": "$.total_amount",
                        "transform": [{"name": "money", "args": {"scale": 2}}],
                    },
                },
                "checks": [
                    {"kind": "unique_key"},
                    {"kind": "no_loss", "compare_to": "$.total"},
                    {"kind": "money_invariant", "field": "total_amount", "compare_to": "$.gross_sum"},
                ],
            },
            "FolioCharge": {
                "from": "list_reservations",
                "key": "$.charge_id",
                "fields": {
                    "charge_id": {"path": "$.charge_id", "transform": []},
                    "amount": {
                        "path": "$.amount",
                        "transform": [{"name": "money", "args": {"scale": 2}}],
                    },
                },
                "checks": [
                    {"kind": "unique_key"},
                    {"kind": "money_invariant", "field": "amount", "compare_to": "$.gross_sum"},
                ],
            },
        },
        "capabilities": {"read": ["Reservation", "FolioCharge"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {}},
    }


def _accept_net_proposal() -> list[dict[str, Any]]:
    """A converging accept-the-vendor-change: retarget the totals assertion."""
    return [
        {"op": "replace", "path": "/entities/Reservation/checks/2/compare_to", "value": "$.total"},
        {"op": "replace", "path": "/entities/FolioCharge/checks/1/compare_to", "value": "$.total"},
    ]


def _escalate_text(proposal: dict[str, Any] | None = None, *, question: str = "Should the new behaviour stand?") -> str:
    body: dict[str, Any] = {"escalate": question, "rationale": "Evidence does not settle it."}
    if proposal is not None:
        body["proposal"] = proposal
    return json.dumps(body)


# ---------------------------------------------------------------------------
# Prompts carry no scenario wording
# ---------------------------------------------------------------------------


def test_l3_prompts_contain_no_scenario_wording() -> None:
    for name in ("l3_patch.md", "l3_patch_retry.md"):
        text = (PROMPT_DIR / name).read_text(encoding="utf-8")
        for pattern in _FORBIDDEN:
            assert not re.search(pattern, text, re.IGNORECASE), f"{name} matches {pattern!r}"


def test_proposal_plumbing_names_no_scenario_fields() -> None:
    """The generic proposal code never names the scenario's fields.

    Inspects the proposal feature's own functions (parser, validator,
    retry, bundle, approval) — the wire-field reads elsewhere (e.g. the
    evidence builder extracting a page total the docs document) are the
    generic mechanism, not scenario wording.
    """
    import inspect  # noqa: PLC0415

    from autobridge.engine import releases as _releases  # noqa: PLC0415
    from autobridge.repair import controller as _controller  # noqa: PLC0415
    from autobridge.repair import escalation as _escalation  # noqa: PLC0415
    from autobridge.repair import ladder as _ladder  # noqa: PLC0415

    sources = [
        inspect.getsource(_ladder._parse_escalation_proposal),
        inspect.getsource(_ladder._validate_escalation_proposal),
        inspect.getsource(_ladder._retry_escalation_proposal),
        inspect.getsource(_escalation.escalation_for_no_fix),
        inspect.getsource(_releases.approve_business_decision),
    ]
    handle = _controller.RepairController._handle_semantic_climb
    sources.append(inspect.getsource(handle))
    text = "\n".join(sources).lower()
    for word in ("gross_sum", "net_amount", "total_amount", "harbor"):
        assert word not in text, f"scenario wording {word!r} in proposal plumbing"


# ---------------------------------------------------------------------------
# Parse escalate+proposal / escalate-only / malformed proposal
# ---------------------------------------------------------------------------


def test_parse_escalate_with_valid_proposal() -> None:
    parsed = _parse_l3_response(
        _escalate_text(
            {"label": "Accept net", "effect": "Approve retargets the totals check.", "patch": _accept_net_proposal()}
        )
    )
    assert parsed["kind"] == "escalate"
    assert parsed["question"] == "Should the new behaviour stand?"
    assert parsed["proposal"] == _accept_net_proposal()
    assert parsed["proposal_label"] == "Accept net"
    assert parsed["proposal_effect"] == "Approve retargets the totals check."
    assert parsed["detail"] == "parsed"


def test_parse_escalate_only_has_no_proposal() -> None:
    parsed = _parse_l3_response(_escalate_text())
    assert parsed["kind"] == "escalate"
    assert parsed["proposal"] is None
    assert parsed["proposal_label"] == ""
    assert parsed["proposal_effect"] == ""


@pytest.mark.parametrize(
    "proposal",
    [
        "just a string",
        {"label": "", "effect": "e", "patch": [{"op": "replace", "path": "/a", "value": 1}]},
        {"label": "L", "effect": "", "patch": [{"op": "replace", "path": "/a", "value": 1}]},
        {"label": "L", "effect": "e", "patch": []},
        {"label": "L", "effect": "e", "patch": [{"op": "replace", "value": 1}]},
        {"label": "L", "effect": "e", "patch": "not a list"},
    ],
)
def test_parse_malformed_proposal_dropped_and_labelled(proposal: Any) -> None:
    """A malformed proposal is dropped, labelled, and the escalation stands."""
    parsed = _parse_l3_response(_escalate_text(proposal))
    assert parsed["kind"] == "escalate"
    assert parsed["proposal"] is None
    assert "dropped" in parsed["detail"]
    assert parsed["question"] == "Should the new behaviour stand?"


# ---------------------------------------------------------------------------
# Climb: adopt, retry, drop
# ---------------------------------------------------------------------------


def _hypotheses() -> list[Any]:
    from autobridge.discovery.docs_reader import Provenance  # noqa: PLC0415
    from autobridge.discovery.hypotheses import Hypothesis  # noqa: PLC0415

    return [
        Hypothesis(
            id="amount:total_amount:net",
            family="amount",
            claim="total_amount tracks net",
            candidate_mapping_fragment={"family": "amount", "field": "total_amount"},
            prior=0.39,
            evidence_for=(Provenance(source="s", line=1, snippet="net"),),
            field="total_amount",
        )
    ]


def _evidence_for(paths: list[str]) -> dict[str, Any]:
    return {
        "patch_target": {
            "document": "connector spec",
            "entity": "Reservation",
            "allowed_ops": ["replace", "add"],
            "allowed_pointers": list(paths),
        }
    }


def _frontier_fake(first: str, retry: str) -> FakeProvider:
    def _answer(_messages: Any, **kw: Any) -> Completion:
        purpose = str(kw.get("purpose") or "")
        text = retry if purpose == "l3-generate-patch-retry" else first
        return Completion(text=text, usage=Usage(10, 10))

    return FakeProvider(responses={"frontier": _answer})


def test_climb_adopts_valid_escalation_proposal() -> None:
    pointers = _semantic.allowed_l3_pointers(_harbor_like_spec(), "Reservation")
    assert "/entities/Reservation/checks/2/compare_to" in pointers
    provider = _frontier_fake(
        _escalate_text(
            {"label": "Accept net", "effect": "Retarget the totals check.", "patch": _accept_net_proposal()}
        ),
        _escalate_text(),
    )
    climb = run_frontier_climb(
        spec=_harbor_like_spec(),
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=_hypotheses(),
        enumerator_yield_count=1,
        provider=provider,
        budget=Budget(),
        knowledge_cache=KnowledgeCache(),
        fingerprint_delta=None,
        vendor="harbor",
        scenario="gross_to_net",
        validate_against=_harbor_like_spec(),
        evidence=_evidence_for(pointers),
    )
    assert climb["model_escalated"] is True
    assert climb["proposal"] == _accept_net_proposal()
    assert climb["proposal_label"] == "Accept net"
    assert climb["proposal_effect"] == "Retarget the totals check."
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3["action"] == "model_escalation"
    assert l3["proposal_validated"] is True
    assert "proposal_dropped" not in l3


def test_climb_retries_invalid_proposal_then_adopts_fix() -> None:
    pointers = _semantic.allowed_l3_pointers(_harbor_like_spec(), "Reservation")
    bad = [{"op": "replace", "path": "/entities/Reservation/fields/nope/path", "value": "$.x"}]
    provider = _frontier_fake(
        _escalate_text({"label": "Accept net", "effect": "Fix paths.", "patch": bad}),
        _escalate_text({"label": "Accept net", "effect": "Retarget the totals check.", "patch": _accept_net_proposal()}),
    )
    climb = run_frontier_climb(
        spec=_harbor_like_spec(),
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=_hypotheses(),
        enumerator_yield_count=1,
        provider=provider,
        budget=Budget(),
        knowledge_cache=KnowledgeCache(),
        fingerprint_delta=None,
        vendor="harbor",
        scenario="gross_to_net",
        validate_against=_harbor_like_spec(),
        evidence=_evidence_for(pointers),
    )
    assert climb["model_escalated"] is True
    assert climb["proposal"] == _accept_net_proposal()
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3["retried"] is True
    assert l3["proposal_validated"] is True


def test_climb_drops_still_invalid_proposal_and_stays_plain_escalation() -> None:
    pointers = _semantic.allowed_l3_pointers(_harbor_like_spec(), "Reservation")
    bad = [{"op": "replace", "path": "/elsewhere", "value": 1}]
    provider = _frontier_fake(
        _escalate_text({"label": "Accept net", "effect": "Fix paths.", "patch": bad}),
        _escalate_text({"label": "Accept net", "effect": "Still bad.", "patch": bad}),
    )
    climb = run_frontier_climb(
        spec=_harbor_like_spec(),
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=_hypotheses(),
        enumerator_yield_count=1,
        provider=provider,
        budget=Budget(),
        knowledge_cache=KnowledgeCache(),
        fingerprint_delta=None,
        vendor="harbor",
        scenario="gross_to_net",
        validate_against=_harbor_like_spec(),
        evidence=_evidence_for(pointers),
    )
    assert climb["model_escalated"] is True
    assert climb["proposal"] is None
    assert climb["model_question"] != ""
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3["action"] == "model_escalation"
    assert "proposal_dropped" in l3


# ---------------------------------------------------------------------------
# Bundle round-trip
# ---------------------------------------------------------------------------


def test_escalation_bundle_carries_proposal() -> None:
    bundle = escalation_for_no_fix(
        "r",
        question="Should the new behaviour stand?",
        proposal={"label": "Accept net", "effect": "Retarget.", "patch": _accept_net_proposal()},
    )
    assert bundle.proposal is not None and bundle.proposal["label"] == "Accept net"
    restored = EscalationBundle.from_dict(bundle.to_dict())
    assert restored.proposal == bundle.proposal


def test_escalation_bundle_plain_has_no_proposal() -> None:
    bundle = escalation_for_no_fix("r", question="Should the new behaviour stand?")
    assert bundle.proposal is None
    assert "proposal" not in bundle.to_dict()


def test_escalation_bundle_rejects_malformed_proposal() -> None:
    with pytest.raises(ValueError, match="proposal"):
        EscalationBundle.from_dict(
            {"reason": "r", "question": "Stand?", "proposal": {"label": "x"}}
        )


# ---------------------------------------------------------------------------
# Controller threads the proposal; the engine never releases it
# ---------------------------------------------------------------------------


def _session_factory(tmp_path: pathlib.Path):  # type: ignore[no-untyped-def]
    engine = create_engine(f"sqlite:///{tmp_path}/req049.db")
    init_db(engine)
    return session_factory(engine)


def _connector_with_release(factory: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    with factory() as s:
        conn = Connector(
            name="harborpms",
            base_url="http://sim-harbor:8000",
            credential_ref="HARBOR_API_KEY",
            target_entities=["Reservation", "FolioCharge"],
            runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.commit()
        r1 = create_release(s, connector_id=conn.id, spec=_harbor_like_spec())
        s.commit()
        conn.active_release_id = r1.id
        drift = DriftEvent(
            connector_id=conn.id,
            drift_class="SEMANTIC_VALUE_DRIFT",
            severity="high",
            source_sync_run_id=None,
            evidence_ids=[],
        )
        s.add(drift)
        s.commit()
        return {"connector_id": conn.id, "release_id": r1.id, "drift_id": drift.id}


def test_controller_escalation_carries_proposal_without_candidate(tmp_path: pathlib.Path) -> None:
    factory = _session_factory(tmp_path)
    ids = _connector_with_release(factory)
    pointers = _semantic.allowed_l3_pointers(_harbor_like_spec(), "Reservation")
    provider = _frontier_fake(
        _escalate_text(
            {"label": "Accept net", "effect": "Retarget the totals check.", "patch": _accept_net_proposal()}
        ),
        _escalate_text(),
    )
    controller = RepairController(knowledge_cache=KnowledgeCache())
    with factory() as s:
        result = controller._handle_semantic_climb(
            spec=_harbor_like_spec(),
            drift_class="SEMANTIC_VALUE_DRIFT",
            signals_list=[],
            fingerprint_delta=None,
            extra_dict={},
            vendor_val="harbor",
            drift_event_ids=[ids["drift_id"]],
            session=s,
            hypotheses=_hypotheses(),
            enumerator_yield_count=1,
            provider=provider,
            budget=Budget(),
            run_id="test-run-1",
            scenario="gross_to_net",
            validate_against=_harbor_like_spec(),
            evidence=_evidence_for(pointers),
        )
        s.commit()
        assert result.decision == "escalate"
        assert result.candidate_patch is None
        bundle = result.escalation_bundle
        assert bundle is not None
        as_dict = bundle.to_dict()
        assert as_dict["proposal"]["label"] == "Accept net"
        assert as_dict["proposal"]["effect"] == "Retarget the totals check."
        assert as_dict["proposal"]["patch"] == _accept_net_proposal()
        # The engine never releases an L3 proposal on its own: no candidate.
        from autobridge.control.models import ConnectorRelease

        candidates = (
            s.query(ConnectorRelease)
            .filter(ConnectorRelease.provenance_repair_run_id == result.repair_run_id)
            .all()
        )
        assert candidates == []
        # The rung records the model's label/effect for the Approve button.
        trace = list(result.ladder_trace or [])
        l3 = next(e for e in trace if e.get("level") == 3)
        assert l3["proposal_label"] == "Accept net"
        assert l3["proposal_effect"] == "Retarget the totals check."


# ---------------------------------------------------------------------------
# Business-decision approval
# ---------------------------------------------------------------------------


def _escalated_repair(factory: Any, ids: dict[str, Any], *, proposal: Any) -> str:  # type: ignore[no-untyped-def]
    with factory() as s:
        bundle: dict[str, Any] = {
            "reason": "gross_to_net: semantic drift, no verified fix",
            "question": "Should the new behaviour stand?",
            "evidence_ids": [],
            "suggested_action": "manual review",
        }
        if proposal is not None:
            bundle["proposal"] = proposal
        run = RepairRun(
            drift_event_ids=[ids["drift_id"]],
            decision="escalate",
            model_usage={"frontier_tokens": 10, "small_tokens": 0},
            vendor="harbor",
            drift_class="SEMANTIC_VALUE_DRIFT",
            escalation_bundle=bundle,
        )
        s.add(run)
        s.commit()
        return run.id


def test_approve_business_decision_mints_r2_without_learning(tmp_path: pathlib.Path) -> None:
    factory = _session_factory(tmp_path)
    ids = _connector_with_release(factory)
    cache = KnowledgeCache()
    run_id = _escalated_repair(
        factory,
        ids,
        proposal={"label": "Accept net", "effect": "Retarget.", "patch": _accept_net_proposal()},
    )
    with factory() as s:
        candidate, approval = approve_business_decision(
            s, repair_run_id=run_id, actor="operator", knowledge_cache=cache
        )
        s.commit()
        assert candidate.version == 2
        assert candidate.parent_release_id == ids["release_id"]
        assert candidate.provenance_repair_run_id == run_id
        conn = s.get(Connector, ids["connector_id"])
        assert conn is not None and conn.active_release_id == candidate.id
        assert approval.decision == "approve"
        assert approval.actor == "operator"
        assert approval.from_release_id == ids["release_id"]
        assert approval.to_release_id == candidate.id
        # Rollback to r1 stays available: the parent chain is intact.
        assert candidate.parent_release_id is not None
    # Business decisions are never learned: the next identical drift asks again.
    assert cache.lookup("harbor", {"semantic": "x"}, drift_class="SEMANTIC_VALUE_DRIFT") is None


def test_approve_business_decision_twice_is_rejected(tmp_path: pathlib.Path) -> None:
    factory = _session_factory(tmp_path)
    ids = _connector_with_release(factory)
    run_id = _escalated_repair(
        factory,
        ids,
        proposal={"label": "Accept net", "effect": "Retarget.", "patch": _accept_net_proposal()},
    )
    with factory() as s:
        approve_business_decision(s, repair_run_id=run_id, actor="operator")
        s.commit()
        with pytest.raises(ReleaseError, match="already approved"):
            approve_business_decision(s, repair_run_id=run_id, actor="operator")


@pytest.mark.parametrize("decision", ["propose", "climbing"])
def test_approve_business_decision_rejects_non_escalate(tmp_path: pathlib.Path, decision: str) -> None:
    factory = _session_factory(tmp_path)
    ids = _connector_with_release(factory)
    with factory() as s:
        run = RepairRun(drift_event_ids=[ids["drift_id"]], decision=decision, model_usage={})
        s.add(run)
        s.commit()
        run_id = run.id
    with factory() as s, pytest.raises(ReleaseError, match="not escalate"):
        approve_business_decision(s, repair_run_id=run_id, actor="operator")


def test_approve_business_decision_needs_a_proposal(tmp_path: pathlib.Path) -> None:
    factory = _session_factory(tmp_path)
    ids = _connector_with_release(factory)
    run_id = _escalated_repair(factory, ids, proposal=None)
    with factory() as s, pytest.raises(ReleaseError, match="no proposal|carries no proposal"):
        approve_business_decision(s, repair_run_id=run_id, actor="operator")


def test_approve_business_decision_rejects_non_applying_patch(tmp_path: pathlib.Path) -> None:
    factory = _session_factory(tmp_path)
    ids = _connector_with_release(factory)
    run_id = _escalated_repair(
        factory,
        ids,
        proposal={
            "label": "Bad",
            "effect": "Does not apply.",
            "patch": [{"op": "replace", "path": "/entities/Nope/fields/x/path", "value": "$.y"}],
        },
    )
    with factory() as s, pytest.raises(ReleaseError, match="does not apply|invalid spec"):
        approve_business_decision(s, repair_run_id=run_id, actor="operator")


# ---------------------------------------------------------------------------
# Discovery: documented totals
# ---------------------------------------------------------------------------


def test_documented_totals_add_money_checks() -> None:
    text = (REPO_ROOT / "simulators" / "docs" / "harborpms.md").read_text(encoding="utf-8")
    entities = {
        "Reservation": {
            "fields": {
                "total_amount": {"path": "$.total_amount", "transform": [{"name": "money", "args": {"scale": 2}}]},
                "guest_name": {"path": "$.guest_name", "transform": []},
            },
            "checks": [{"kind": "unique_key"}],
        },
        "FolioCharge": {
            "fields": {
                "amount": {"path": "$.amount", "transform": [{"name": "money", "args": {"scale": 2}}]},
            },
            "checks": [],
        },
    }
    added = apply_documented_page_totals(text, entities)
    by_entity = {(a["entity"], a["field"]): a for a in added}
    assert by_entity[("Reservation", "total_amount")]["compare_to"] == "$.gross_sum"
    assert by_entity[("FolioCharge", "amount")]["compare_to"] == "$.gross_sum"
    assert {"kind": "money_invariant", "field": "total_amount", "compare_to": "$.gross_sum"} in entities["Reservation"]["checks"]


def test_documented_totals_never_guessed() -> None:
    entities = {
        "Reservation": {
            "fields": {
                "total_amount": {"path": "$.total_amount", "transform": [{"name": "money", "args": {"scale": 2}}]},
            },
            "checks": [],
        }
    }
    for name in ("atlas-openapi.yaml", "meridian-openapi.yaml", "fias-pabx.md", "legacystay.md"):
        text = (REPO_ROOT / "simulators" / "docs" / name).read_text(encoding="utf-8")
        assert documented_page_totals(text, entities) == [], name
    assert documented_page_totals("", entities) == []
    assert documented_page_totals("no totals here, sum of nothing", {}) == []


def test_harbor_docs_build_carries_totals_check() -> None:
    text = (REPO_ROOT / "simulators" / "docs" / "harborpms.md").read_text(encoding="utf-8")
    result = build_draft_spec(text, source="simulators/docs/harborpms.md", spec_vocabulary="v1")
    assert result.success, result.visible_errors
    checks = result.draft_spec["entities"]["Reservation"]["checks"]
    assert {"kind": "money_invariant", "field": "total_amount", "compare_to": "$.gross_sum"} in checks
    folio_checks = result.draft_spec["entities"]["FolioCharge"]["checks"]
    assert {"kind": "money_invariant", "field": "amount", "compare_to": "$.gross_sum"} in folio_checks
    details = [t.get("detail") for t in result.trace if t.get("stage") == "page_total_check"]
    assert any("check added from the docs: totals must match meta.gross_sum" in str(d) for d in details)


def test_seed_creates_no_harbor() -> None:
    from bench.demo_seed import SEED_CONNECTORS, SEED_NAMES  # noqa: PLC0415

    assert "harborpms" not in SEED_NAMES
    # REQ-050: decks 6/7 build Meridian and the PABX live; the seed is Atlas only.
    assert sorted(SEED_NAMES) == ["atlaspms"]
    assert all("reference_spec" not in s for s in SEED_CONNECTORS)


# ---------------------------------------------------------------------------
# Routes: preview + approve-proposal + deny
# ---------------------------------------------------------------------------


@pytest.fixture()
def route_client(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from autobridge.control.app import app  # noqa: PLC0415
    from autobridge.control.deps import get_db  # noqa: PLC0415
    from autobridge.control.models import Base as _Base  # noqa: PLC0415
    from autobridge.control.models import session_factory as _factory  # noqa: PLC0415

    monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("AUTOBRIDGE_SYNC_INTERVAL_S", "10")
    engine = create_engine(f"sqlite:///{tmp_path}/req049_routes.db")
    _Base.metadata.create_all(engine)
    factory = _factory(engine)

    def override_db():  # type: ignore[no-untyped-def]
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        yield client, factory
    finally:
        app.dependency_overrides.clear()


def _route_seed(factory: Any, *, proposal: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    with factory() as s:
        conn = Connector(
            name="harborpms",
            base_url="http://sim-harbor:8000",
            credential_ref="HARBOR_API_KEY",
            target_entities=["Reservation", "FolioCharge"],
            runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.commit()
        r1 = create_release(s, connector_id=conn.id, spec=_harbor_like_spec())
        s.commit()
        conn.active_release_id = r1.id
        drift = DriftEvent(
            connector_id=conn.id,
            drift_class="SEMANTIC_VALUE_DRIFT",
            severity="high",
            source_sync_run_id=None,
            evidence_ids=[],
        )
        s.add(drift)
        s.commit()
        bundle: dict[str, Any] = {
            "reason": "gross_to_net: semantic drift, no verified fix",
            "question": "Should the new behaviour stand?",
            "evidence_ids": [],
            "suggested_action": "manual review",
        }
        if proposal is not None:
            bundle["proposal"] = proposal
        run = RepairRun(
            drift_event_ids=[drift.id],
            decision="escalate",
            model_usage={"frontier_tokens": 10, "small_tokens": 0},
            vendor="harbor",
            drift_class="SEMANTIC_VALUE_DRIFT",
            escalation_bundle=bundle,
        )
        s.add(run)
        s.commit()
        return {"connector_id": conn.id, "release_id": r1.id, "repair_id": run.id}


def test_preview_business_decision_names_proposal(route_client: Any) -> None:
    client, factory = route_client
    ids = _route_seed(
        factory,
        proposal={"label": "Accept net", "effect": "Retarget.", "patch": _accept_net_proposal()},
    )
    body = client.get(f"/repairs/{ids['repair_id']}/approve-preview").json()
    assert body["approvable"] is True
    assert body["approve_lines"][0].startswith("Approve: Accept net")
    joined = "\n".join(body["approve_lines"])
    assert "business decision by the operator" in joined
    assert "never learned" in joined or "not kept as learned knowledge" in joined
    assert "Rollback to r1 stays available" in joined
    assert body["facts"]["business_decision"] is True
    assert body["facts"]["proposal"]["label"] == "Accept net"
    assert any("Deny" in line for line in body["reject_lines"])


def test_preview_plain_escalation_not_approvable(route_client: Any) -> None:
    client, factory = route_client
    ids = _route_seed(factory, proposal=None)
    body = client.get(f"/repairs/{ids['repair_id']}/approve-preview").json()
    assert body["approvable"] is False
    assert "escalate" in body["reason"]


def test_approve_proposal_route_mints_r2(route_client: Any) -> None:
    client, factory = route_client
    ids = _route_seed(
        factory,
        proposal={"label": "Accept net", "effect": "Retarget.", "patch": _accept_net_proposal()},
    )
    rec = client.post(
        f"/repairs/{ids['repair_id']}/approve-proposal", json={"connector_id": ids["connector_id"]}
    )
    assert rec.status_code == 200, rec.text
    approval = rec.json()
    assert approval["decision"] == "approve"
    r2_id = approval["to_release_id"]
    r2 = client.get(f"/releases/{r2_id}").json()
    assert r2["parent_release_id"] == ids["release_id"]
    detail = client.get(f"/connectors/{ids['connector_id']}").json()
    assert detail["active_release_id"] == r2_id
    # The repair row records escalate + approve: a business decision.
    rep = client.get(f"/repairs/{ids['repair_id']}").json()
    assert rep["decision"] == "escalate"
    assert rep["approval"] is not None and rep["approval"]["decision"] == "approve"
    # Approving again would mint a duplicate: rejected.
    again = client.post(
        f"/repairs/{ids['repair_id']}/approve-proposal", json={"connector_id": ids["connector_id"]}
    )
    assert again.status_code == 422


def test_approve_proposal_route_rejects_without_proposal(route_client: Any) -> None:
    client, factory = route_client
    ids = _route_seed(factory, proposal=None)
    rec = client.post(
        f"/repairs/{ids['repair_id']}/approve-proposal", json={"connector_id": ids["connector_id"]}
    )
    assert rec.status_code == 422


def test_deny_keeps_error_and_drift_open(route_client: Any) -> None:
    client, factory = route_client
    ids = _route_seed(
        factory,
        proposal={"label": "Accept net", "effect": "Retarget.", "patch": _accept_net_proposal()},
    )
    rec = client.post(f"/repairs/{ids['repair_id']}/reject", json={})
    assert rec.status_code == 200, rec.text
    assert rec.json()["decision"] == "reject"
    releases = client.get(f"/releases?connector_id={ids['connector_id']}").json()
    assert len(releases) == 1, "deny creates no release"
    drift = client.get(f"/drift?connector_id={ids['connector_id']}").json()
    assert any(isinstance(d, dict) and d.get("status") == "open" for d in drift), "drift stays open"


# ---------------------------------------------------------------------------
# Knowledge Decisions section (REQ-049 item 9)
# ---------------------------------------------------------------------------


def _approve_fix(factory: Any, ids: dict[str, Any]) -> str:  # type: ignore[no-untyped-def]
    """A propose repair with rule + candidate, approved (learned)."""
    from autobridge.engine.releases import approve_release, create_release_from_patch  # noqa: PLC0415

    patch = [{"op": "replace", "path": "/entities/Reservation/fields/total_amount/path", "value": "$.total_amount"}]
    with factory() as s:
        run = RepairRun(
            drift_event_ids=[ids["drift_id"]],
            decision="propose",
            model_usage={"frontier_tokens": 0, "small_tokens": 0},
            vendor="harbor",
            drift_class="PAGINATION_CHANGE",
            drift_signature="sig-1",
            fingerprint_delta={"pagination": {"strategy": {"old": "page", "new": "cursor"}}},
            candidate_patch=patch,
            rule_id="page_to_cursor",
        )
        s.add(run)
        s.commit()
        candidate = create_release_from_patch(
            s,
            connector_id=ids["connector_id"],
            base_release_id=ids["release_id"],
            patch=patch,
            provenance_repair_run_id=run.id,
        )
        s.commit()
        approval = approve_release(
            s,
            repair_run_id=run.id,
            actor="operator",
            from_release_id=ids["release_id"],
            to_release_id=candidate.id,
            connector_id=ids["connector_id"],
        )
        s.commit()
        return approval.id


def test_knowledge_lists_approve_fix_as_learned(tmp_path: pathlib.Path) -> None:
    from autobridge.control.routes.capabilities import knowledge_payload  # noqa: PLC0415

    factory = _session_factory(tmp_path)
    ids = _connector_with_release(factory)
    approval_id = _approve_fix(factory, ids)
    with factory() as s:
        payload = knowledge_payload(s)
    assert len(payload["entries"]) == 1
    by_id = {d["id"]: d for d in payload["decisions"]}
    decision = by_id[approval_id]
    assert decision["kind"] == "approve"
    assert decision["business_decision"] is False
    assert decision["learned"] is True
    assert decision["actor"] == "operator"
    assert decision["connector_name"] == "harborpms"
    assert decision["repair_run_id"]
    assert "page_to_cursor" in decision["summary"] and "r2" in decision["summary"]


def test_knowledge_lists_business_decision_unlearned(tmp_path: pathlib.Path) -> None:
    from autobridge.control.routes.capabilities import knowledge_payload  # noqa: PLC0415

    factory = _session_factory(tmp_path)
    ids = _connector_with_release(factory)
    run_id = _escalated_repair(
        factory,
        ids,
        proposal={"label": "Accept net", "effect": "Retarget the totals check.", "patch": _accept_net_proposal()},
    )
    with factory() as s:
        approve_business_decision(s, repair_run_id=run_id, actor="operator")
        s.commit()
        payload = knowledge_payload(s)
    # A business decision never appears as learned knowledge ...
    assert payload["entries"] == []
    business = [d for d in payload["decisions"] if d["business_decision"]]
    assert len(business) == 1
    assert business[0]["kind"] == "approve"
    assert business[0]["learned"] is False
    assert business[0]["summary"].startswith("Business decision:")
    assert "Retarget the totals check." in business[0]["summary"]
    assert business[0]["connector_name"] == "harborpms"


def test_knowledge_lists_deny_and_rollback(tmp_path: pathlib.Path) -> None:
    from autobridge.control.routes.capabilities import knowledge_payload  # noqa: PLC0415
    from autobridge.engine.releases import reject_release, rollback_release  # noqa: PLC0415

    factory = _session_factory(tmp_path)
    ids = _connector_with_release(factory)
    run_id = _escalated_repair(factory, ids, proposal=None)
    with factory() as s:
        reject_release(s, repair_run_id=run_id, actor="op", from_release_id=None, to_release_id=None)
        s.commit()
        r2 = create_release(s, connector_id=ids["connector_id"], spec=_harbor_like_spec(),
                            parent_release_id=ids["release_id"])
        s.commit()
        conn = s.get(Connector, ids["connector_id"])
        assert conn is not None
        conn.active_release_id = r2.id
        s.commit()
        rollback_release(
            s, connector_id=ids["connector_id"], target_release_id=ids["release_id"],
            actor="op2", repair_run_id=run_id,
        )
        s.commit()
        payload = knowledge_payload(s)
    by_kind = {d["kind"]: d for d in payload["decisions"]}
    assert by_kind["reject"]["summary"].startswith("Denied:")
    assert by_kind["rollback"]["summary"] == "Rolled back r2 → r3"
    assert by_kind["rollback"]["actor"] == "op2"
    # Newest first: the rollback (created last) leads.
    assert payload["decisions"][0]["kind"] == "rollback"


def test_knowledge_decision_connector_falls_back_to_release(tmp_path: pathlib.Path) -> None:
    """An approval on a drift-less repair still names the connector (via the release)."""
    from autobridge.control.routes.capabilities import knowledge_payload  # noqa: PLC0415
    from autobridge.engine.releases import approve_release, create_release_from_patch  # noqa: PLC0415

    factory = _session_factory(tmp_path)
    ids = _connector_with_release(factory)
    with factory() as s:
        run = RepairRun(drift_event_ids=[], decision="propose", model_usage={})
        s.add(run)
        s.commit()
        candidate = create_release_from_patch(
            s, connector_id=ids["connector_id"], base_release_id=ids["release_id"],
            patch=[{"op": "replace", "path": "/entities/Reservation/fields/total_amount/path", "value": "$.x"}],
            provenance_repair_run_id=run.id,
        )
        s.commit()
        approve_release(
            s, repair_run_id=run.id, actor="op", from_release_id=ids["release_id"],
            to_release_id=candidate.id, connector_id=ids["connector_id"],
        )
        s.commit()
        payload = knowledge_payload(s)
    assert len(payload["decisions"]) == 1
    assert payload["decisions"][0]["connector_name"] == "harborpms"


def test_knowledge_decision_plain_reject_summary(tmp_path: pathlib.Path) -> None:
    from autobridge.control.routes.capabilities import knowledge_payload  # noqa: PLC0415
    from autobridge.engine.releases import reject_release  # noqa: PLC0415

    factory = _session_factory(tmp_path)
    ids = _connector_with_release(factory)
    with factory() as s:
        run = RepairRun(
            drift_event_ids=[ids["drift_id"]], decision="propose", model_usage={},
            rule_id="page_to_cursor", candidate_patch=[{"op": "replace", "path": "/a", "value": "b"}],
        )
        s.add(run)
        s.commit()
        reject_release(s, repair_run_id=run.id, actor="op", from_release_id=None, to_release_id=None)
        s.commit()
        payload = knowledge_payload(s)
    assert payload["decisions"][0]["summary"] == "Rejected the proposed patch"
