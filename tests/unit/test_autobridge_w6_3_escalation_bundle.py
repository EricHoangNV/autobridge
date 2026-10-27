"""P1.W6.3: Escalation bundle format + storage on RepairRun.

Verifies:
- EscalationBundle format: reason + one precise question (single '?' ending) + evidence_ids + suggested_action
- to_dict / from_dict round-trip and validation
- RepairRun stores escalation_bundle JSON and persists via controller
- Every ladder escalate path produces a bundle (CAPABILITY_REMOVED, AUTH_REVOKED, AUTH_EXPIRED, unknown)
- BudgetExhausted path produces bundle with one question
- Job handler propagates bundle
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from autobridge.control.models import Base, RepairRun, session_factory
from autobridge.llm.provider import BudgetExhausted
from autobridge.repair.controller import RepairController
from autobridge.repair.escalation import (
    EscalationBundle,
    escalation_for_no_fix,
    escalation_from_budget_exhausted,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mem_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return session_factory(engine)()


def _minimal_spec(vendor="atlas", auth_type="bearer", token_url=None):
    auth = {"type": auth_type, "credential_ref": "TEST_TOKEN"}
    if token_url:
        auth["token_url"] = token_url
    return {
        "spec_version": 1,
        "connector": {"id": "conn-1", "vendor": vendor},
        "source": {
            "base_url": "http://example.com",
            "auth": auth,
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "listReservations": {
                "path": "/reservations",
                "method": "GET",
                "pagination": {"strategy": "offset", "page_param": "offset", "size_param": "limit"},
            }
        },
        "entities": {"Reservation": {"key": "id", "fields": {}}},
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "5m", "operation": "listReservations"}},
    }


# ---------------------------------------------------------------------------
# Bundle format
# ---------------------------------------------------------------------------


def test_bundle_format_has_one_question():
    b = escalation_for_no_fix("CAPABILITY_REMOVED: x removed", evidence_ids=["ev-1"])
    assert b.reason
    assert b.question
    assert b.question.strip().endswith("?")
    assert b.question.count("?") == 1
    assert b.evidence_ids == ("ev-1",)
    assert b.suggested_action


def test_bundle_to_dict_round_trip():
    b = EscalationBundle(
        reason="AUTH_REVOKED: credential revoked — escalate with no patch",
        question="Credential was revoked — should a new credential be configured or should the connector be disabled?",
        evidence_ids=("ev-1", "ev-2"),
        evidence_payloads=(),
        suggested_action="manual review",
    )
    d = b.to_dict()
    assert d["reason"] == b.reason
    assert d["question"] == b.question
    assert d["evidence_ids"] == ["ev-1", "ev-2"]
    assert "suggested_action" in d
    # Round trip
    b2 = EscalationBundle.from_dict(d)
    assert b2 == b


def test_bundle_from_dict_validates_single_question():
    base = {
        "reason": "test",
        "question": "Which mapping is correct?",
        "evidence_ids": [],
        "suggested_action": "manual",
    }
    # Valid
    assert EscalationBundle.from_dict(base).question == base["question"]
    # Empty reason
    with pytest.raises(ValueError, match="reason"):
        EscalationBundle.from_dict({**base, "reason": ""})
    # Empty question
    with pytest.raises(ValueError, match="question"):
        EscalationBundle.from_dict({**base, "question": ""})
    # Not ending with ?
    with pytest.raises(ValueError, match="\\?"):
        EscalationBundle.from_dict({**base, "question": "no question"})
    # Multiple ?
    with pytest.raises(ValueError, match="single"):
        EscalationBundle.from_dict({**base, "question": "a? b?"})


def test_bundle_from_dict_rejects_two_questions():
    with pytest.raises(ValueError):
        EscalationBundle.from_dict({"reason": "x", "question": "a? b?"})


def test_budget_exhausted_bundle_has_one_question():
    exc = BudgetExhausted("max_probes 5 exceeded")
    b = escalation_from_budget_exhausted(exc, evidence_ids=["e1"], context="test")
    assert b.question.count("?") == 1
    assert b.question.strip().endswith("?")
    d = b.to_dict()
    b2 = EscalationBundle.from_dict(d)
    assert b2.reason == b.reason


def test_budget_token_bundle_has_one_question():
    exc = BudgetExhausted("max_frontier_tokens 10 exceeded")
    b = escalation_from_budget_exhausted(exc, evidence_ids=[], context="L3 drift")
    assert b.question.count("?") == 1
    assert b.question.strip().endswith("?")


def test_budget_generic_bundle_has_one_question():
    exc = BudgetExhausted("max_calls 3 exceeded")
    b = escalation_from_budget_exhausted(exc)
    assert b.question.count("?") == 1
    assert b.question.strip().endswith("?")


# ---------------------------------------------------------------------------
# RepairRun storage
# ---------------------------------------------------------------------------


def test_repair_run_stores_bundle_directly():
    session = _mem_session()
    bundle = escalation_for_no_fix("No fix for UNKNOWN", evidence_ids=["ev-1"])
    d = bundle.to_dict()
    run = RepairRun(
        drift_event_ids=["de-1"],
        ladder_level_reached=4,
        decision="escalate",
        model_usage={"frontier_tokens": 0},
        escalation_bundle=d,
    )
    session.add(run)
    session.commit()
    fetched = session.get(RepairRun, run.id)
    assert fetched is not None
    assert fetched.escalation_bundle == d
    # Validate round trip
    restored = EscalationBundle.from_dict(fetched.escalation_bundle)
    assert restored.reason == bundle.reason
    assert restored.question == bundle.question
    assert restored.evidence_ids == bundle.evidence_ids
    session.close()


def test_repair_run_without_bundle_nullable():
    session = _mem_session()
    run = RepairRun(
        drift_event_ids=[],
        ladder_level_reached=1,
        decision="propose",
        model_usage={},
        escalation_bundle=None,
    )
    session.add(run)
    session.commit()
    assert session.get(RepairRun, run.id).escalation_bundle is None
    session.close()


def test_repair_run_escalate_bundle_non_null_for_escalate_decision():
    # Integration: controller must store bundle for escalate
    session = _mem_session()
    spec = _minimal_spec()
    controller = RepairController()
    result = controller.handle(
        spec=spec,
        drift_class="CAPABILITY_REMOVED",
        signals=[],
        fingerprint_delta=None,
        session=session,
    )
    assert result.decision == "escalate"
    assert result.escalation_bundle is not None
    assert result.escalation_bundle.question.count("?") == 1
    session.commit()
    # Fetch persisted RepairRun
    runs = session.query(RepairRun).all()
    assert len(runs) == 1
    stored = runs[0]
    assert stored.decision == "escalate"
    assert stored.escalation_bundle is not None
    # Must be valid bundle format
    restored = EscalationBundle.from_dict(stored.escalation_bundle)
    assert restored.reason
    assert restored.question
    assert restored.question.strip().endswith("?")
    session.close()


def test_controller_stores_bundle_for_auth_revoked():
    session = _mem_session()
    spec = _minimal_spec()
    controller = RepairController()
    result = controller.handle(
        spec=spec,
        drift_class="AUTH_REVOKED",
        signals=[],
        session=session,
    )
    assert result.decision == "escalate"
    assert result.escalation_bundle is not None
    session.commit()
    run = session.query(RepairRun).one()
    assert run.escalation_bundle is not None
    b = EscalationBundle.from_dict(run.escalation_bundle)
    assert "AUTH_REVOKED" in b.reason
    assert b.question.count("?") == 1
    session.close()


def test_controller_stores_bundle_for_auth_expired_no_refresh():
    session = _mem_session()
    spec = _minimal_spec(auth_type="bearer")
    controller = RepairController()
    result = controller.handle(
        spec=spec,
        drift_class="AUTH_EXPIRED",
        signals=[],
        session=session,
    )
    assert result.decision == "escalate"
    assert result.escalation_bundle is not None
    session.commit()
    run = session.query(RepairRun).one()
    assert run.escalation_bundle is not None
    b = EscalationBundle.from_dict(run.escalation_bundle)
    assert "AUTH_EXPIRED" in b.reason
    assert b.question.endswith("?")
    session.close()


def test_controller_no_bundle_for_propose():
    session = _mem_session()
    spec = _minimal_spec()
    controller = RepairController()
    # RATE_LIMIT is backoff-only propose, not escalate, so bundle should be None
    result = controller.handle(
        spec=spec,
        drift_class="RATE_LIMIT",
        signals=[],
        session=session,
    )
    assert result.decision == "propose"
    assert result.escalation_bundle is None
    session.commit()
    run = session.query(RepairRun).one()
    assert run.escalation_bundle is None
    assert run.decision == "propose"
    session.close()


def test_ladder_unknown_escalate_has_bundle():
    from autobridge.repair.ladder import run_ladder

    spec = _minimal_spec()
    result = run_ladder(spec=spec, drift_class="UNKNOWN", signals=[])
    assert result.decision == "escalate"
    assert result.escalation_bundle is not None
    assert result.escalation_bundle.question.count("?") == 1
    assert result.escalation_reason is not None
    # Bundle reason matches escalation_reason
    assert result.escalation_bundle.reason == result.escalation_reason


def test_ladder_endpoint_removed_escalate_has_bundle_and_evidence():
    from autobridge.repair.ladder import run_ladder

    spec = _minimal_spec()
    result = run_ladder(
        spec=spec,
        drift_class="CAPABILITY_REMOVED",
        signals=[{"evidence_ids": ["ev-1", "ev-2"]}],
    )
    assert result.decision == "escalate"
    assert result.escalation_bundle is not None
    assert result.escalation_bundle.evidence_ids == ("ev-1", "ev-2")
    assert result.escalation_bundle.question.count("?") == 1
    # Also check stored dict would validate
    d = result.escalation_bundle.to_dict()
    EscalationBundle.from_dict(d)


def test_job_handler_includes_bundle():
    from autobridge.repair.controller import repair_job_handler

    session = _mem_session()

    class FakeCtx:
        def __init__(self, sess):
            self.session = sess
            self.payload = {"drift_class": "CAPABILITY_REMOVED", "vendor": "atlas"}
            self.run_id = None

    ctx = FakeCtx(session)
    out = repair_job_handler(ctx)
    assert out["decision"] == "escalate"
    assert "escalation_bundle" in out
    assert out["escalation_bundle"]["question"].count("?") == 1
    # Also stored
    run = session.get(RepairRun, out["repair_run_id"])
    assert run is not None
    assert run.escalation_bundle is not None
    b = EscalationBundle.from_dict(run.escalation_bundle)
    assert b.question.count("?") == 1
    session.close()


def test_job_handler_propose_no_bundle():
    from autobridge.repair.controller import repair_job_handler

    session = _mem_session()

    class FakeCtx:
        def __init__(self, sess):
            self.session = sess
            self.payload = {
                "drift_class": "RATE_LIMIT",
                "vendor": "atlas",
                "signals": [{"drift_class": "RATE_LIMIT", "evidence_ids": []}],
            }
            self.run_id = None

    ctx = FakeCtx(session)
    out = repair_job_handler(ctx)
    # RATE_LIMIT -> propose (backoff_only)
    assert out["decision"] == "propose"
    assert "escalation_bundle" not in out or out.get("escalation_bundle") is None
    session.close()
