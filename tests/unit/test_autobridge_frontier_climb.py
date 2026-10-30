# ruff: noqa: TC003
"""Frontier climb + replay tests (REQ-021 owner check after sub-step D).

The hard semantic drift must climb for real: L0 miss -> L1 no rule ->
L2 small-model rank below threshold -> L3 frontier call (tokens + cost in
the ledger) proposing a patch -> trap rejection -> L4. With no frontier key
the run uses replay (redacted recordings under demo/recordings/); a missing
recording fails loudly; FRONTIER_MODE=off records an explicit skip.
"""

from __future__ import annotations

import pathlib
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine

from autobridge.control.models import Base, ModelCall, session_factory
from autobridge.discovery.docs_reader import Provenance
from autobridge.discovery.hypotheses import Hypothesis
from autobridge.llm.provider import (
    Budget,
    BudgetedProvider,
    Completion,
    FakeProvider,
    MuseProvider,
    ReplayMissing,
    ReplayProvider,
    Usage,
    estimate_cost,
    prompt_hash,
    resolve_frontier_provider,
)
from autobridge.repair.knowledge import KnowledgeCache
from autobridge.repair.ladder import L2_CONFIDENCE_THRESHOLD, run_frontier_climb

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _prov() -> tuple[Provenance, ...]:
    return (Provenance(source="demo:gross_to_net", line=1, snippet="total_amount reads net"),)


def gross_net_hypotheses() -> list[Hypothesis]:
    """The documented pair (ids + priors feed the replay prompt hashes)."""
    return [
        Hypothesis(
            id="gross_is_canonical",
            family="amount",
            claim="total_amount must stay gross",
            candidate_mapping_fragment={"family": "amount", "field": "total_amount"},
            prior=0.41,
            evidence_for=_prov(),
            field="total_amount",
        ),
        Hypothesis(
            id="net_is_canonical",
            family="amount",
            claim="total_amount tracks net",
            candidate_mapping_fragment={"family": "amount", "field": "total_amount"},
            prior=0.39,
            evidence_for=_prov(),
            field="total_amount",
        ),
    ]


def _delta() -> dict[str, Any]:
    return {"semantic": "gross_to_net"}


def _spec() -> dict[str, Any]:
    return {"spec_version": 1, "connector": {"id": "demo", "vendor": "harbor"}}


# ---------------------------------------------------------------------------
# Replay provider
# ---------------------------------------------------------------------------


def _l2_pair_messages() -> list[dict[str, str]]:
    """The L2 prompt the engine renders for the documented pair (REQ-045).

    Built through the loader, exactly as :func:`_call_small_model_rank`
    renders it — so these tests pin the real prompt, not a copy of it.
    """
    from autobridge.llm.prompts import get_prompt, render_prompt
    from autobridge.repair.ladder import _hypotheses_block, _shared_evidence_block

    template = get_prompt("l2_rank")
    return render_prompt(
        template,
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses_block=_hypotheses_block(gross_net_hypotheses()),
        evidence_block=_shared_evidence_block("SEMANTIC_VALUE_DRIFT", None),
    )


def test_prompt_hash_stable() -> None:
    messages = [{"role": "user", "content": "hi"}]
    assert prompt_hash(messages) == prompt_hash(list(messages))
    assert len(prompt_hash(messages)) == 64


def test_replay_hit_returns_replayed_completion() -> None:
    p = ReplayProvider(recordings_dir=str(REPO_ROOT / "tests" / "fixtures" / "replay"))
    assert p.model_for_tier("frontier") == "muse-spark-1.3"
    l2 = _l2_pair_messages()
    out = p.complete(l2, tier="small", purpose="l2-rank-hypotheses")
    assert out.replayed is True
    assert out.text == '["gross_is_canonical", "net_is_canonical"]'
    assert (out.usage.input_tokens, out.usage.output_tokens) == (96, 12)
    with pytest.raises(ValueError, match="ultra"):
        p.model_for_tier("ultra")


def test_replay_miss_fails_loudly(tmp_path: pathlib.Path) -> None:
    p = ReplayProvider(recordings_dir=str(tmp_path))
    with pytest.raises(ReplayMissing, match="no replay recording"):
        p.complete([{"role": "user", "content": "unknown prompt"}], tier="small")


def test_replay_purpose_labelled_in_ledger(tmp_path: pathlib.Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path}/replay.db")
    Base.metadata.create_all(engine)
    factory = session_factory(engine)
    p = ReplayProvider(recordings_dir=str(REPO_ROOT / "tests" / "fixtures" / "replay"))
    l2 = _l2_pair_messages()
    with factory() as s:
        budgeted = BudgetedProvider(ReplayProvider(recordings_dir=str(REPO_ROOT / "tests" / "fixtures" / "replay")), Budget(), session=s, run_id="run-1")
        budgeted.complete(l2, tier="small", purpose="l2-rank-hypotheses")
        s.commit()
        row = s.query(ModelCall).one()
        assert row.purpose == "l2-rank-hypotheses (replay test fixture)"
        assert row.provider == "ReplayProvider"
        assert (row.input_tokens, row.output_tokens) == (96, 12)
        assert row.cost_estimate == estimate_cost("small", Usage(96, 12), model=row.model)
    _ = p


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------


def test_resolve_off_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRONTIER_MODE", "off")
    provider, mode = resolve_frontier_provider()
    assert provider is None and mode == "off"


def test_resolve_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRONTIER_MODE", "replay")
    provider, mode = resolve_frontier_provider()
    assert isinstance(provider, ReplayProvider) and mode == "replay"


def test_resolve_auto_without_key_uses_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRONTIER_MODE", "auto")
    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)
    provider, mode = resolve_frontier_provider()
    assert isinstance(provider, ReplayProvider) and mode == "replay"


def test_resolve_auto_with_key_uses_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRONTIER_MODE", "auto")
    monkeypatch.setenv("FRONTIER_API_KEY", "meta-key-for-test")
    provider, mode = resolve_frontier_provider()
    assert isinstance(provider, MuseProvider) and mode == "live"


def test_resolve_live_without_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRONTIER_MODE", "live")
    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FRONTIER_API_KEY"):
        resolve_frontier_provider()


def test_resolve_bad_mode_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRONTIER_MODE", "turbo")
    with pytest.raises(ValueError, match="FRONTIER_MODE"):
        resolve_frontier_provider()


# ---------------------------------------------------------------------------
# The climb
# ---------------------------------------------------------------------------


def test_climb_records_l0_to_l3_then_proposes_decoy(tmp_path: pathlib.Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path}/climb.db")
    Base.metadata.create_all(engine)
    factory = session_factory(engine)
    provider = ReplayProvider(recordings_dir=str(REPO_ROOT / "tests" / "fixtures" / "replay"))
    with factory() as s:
        climb = run_frontier_climb(
            spec=_spec(),
            drift_class="SEMANTIC_VALUE_DRIFT",
            hypotheses=gross_net_hypotheses(),
            enumerator_yield_count=1,
            provider=provider,
            budget=Budget(),
            session=s,
            run_id="climb-1",
            knowledge_cache=KnowledgeCache(),
            fingerprint_delta=_delta(),
            vendor="harbor",
        )
        s.commit()
        levels = [a["rung"] for a in climb["attempts"]]
        assert levels == [0, 1, 2, 3], climb["attempts"]
        assert climb["resolved_rung"] == 3
        assert climb["l2_confidence"] == pytest.approx(0.41)
        assert climb["ranked_ids"][0] == "gross_is_canonical"
        assert climb["usage"]["frontier_tokens"] == 1210
        assert climb["usage"]["small_tokens"] == 108
        assert climb["usage"]["calls"] == 2
        assert climb["proposal"] == [
            {"op": "replace", "path": "/entities/Reservation/fields/total_amount/path", "value": "$.net_amount"}
        ]
        rows = s.query(ModelCall).order_by(ModelCall.created_at).all()
        assert len(rows) == 2
        assert rows[0].purpose == "l2-rank-hypotheses (replay test fixture)"
        assert rows[1].purpose == "l3-generate-patch (replay test fixture)"
        assert rows[1].tier == "frontier"
        assert rows[1].cost_estimate == estimate_cost("frontier", Usage(1150, 60), model=rows[1].model)
        assert rows[1].cost_estimate > Decimal("0")
        for attempt in climb["attempts"]:
            assert attempt["reason"] and "\n" not in attempt["reason"]


def test_climb_off_records_plain_skip() -> None:
    climb = run_frontier_climb(
        spec=_spec(),
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=gross_net_hypotheses(),
        enumerator_yield_count=1,
        provider=None,
        knowledge_cache=KnowledgeCache(),
        fingerprint_delta=_delta(),
        vendor="harbor",
    )
    levels = [a["rung"] for a in climb["attempts"]]
    assert levels == [0, 1, 2]
    assert climb["attempts"][-1]["action"] == "l2_skipped"
    assert "no provider" in climb["attempts"][-1]["reason"]
    assert climb["resolved_rung"] == 1
    assert climb["usage"]["calls"] == 0


def test_climb_confident_l2_needs_no_frontier() -> None:
    hyps = gross_net_hypotheses()
    hyps[0] = Hypothesis(
        id="gross_is_canonical", family="amount", claim="gross",
        candidate_mapping_fragment={"family": "amount"}, prior=0.90, evidence_for=_prov(),
    )
    fake = FakeProvider(responses={"default": Completion(text='["gross_is_canonical", "net_is_canonical"]', usage=Usage(4, 4))})
    climb = run_frontier_climb(
        spec=_spec(),
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=hyps,
        enumerator_yield_count=1,
        provider=fake,
        budget=Budget(),
        knowledge_cache=KnowledgeCache(),
        fingerprint_delta=_delta(),
        vendor="harbor",
    )
    assert [a["rung"] for a in climb["attempts"]] == [0, 1, 2]
    assert climb["resolved_rung"] == 2
    assert climb["usage"]["frontier_tokens"] == 0
    assert "0.90" in climb["attempts"][-1]["reason"]


def test_l2_threshold_default() -> None:
    assert L2_CONFIDENCE_THRESHOLD == 0.70


# ---------------------------------------------------------------------------
# record-frontier (capture script)
# ---------------------------------------------------------------------------


def test_record_frontier_requires_key(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    from bench.record_frontier import record_scenario

    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="FRONTIER_API_KEY"):
        record_scenario("gross_to_net", tmp_path)


def test_record_frontier_rejects_unknown_scenario(tmp_path: pathlib.Path) -> None:
    from bench.record_frontier import record_scenario

    with pytest.raises(SystemExit, match="unknown SCENARIO"):
        record_scenario("nope", tmp_path)


def test_live_l3_prompt_picks_newest_semantic_escalation_with_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-039: the capture replays the exact prompt the live climb recorded."""
    import bench.record_frontier as rf

    old = {
        "id": "rep-old",
        "drift_class": "SEMANTIC_VALUE_DRIFT",
        "decision": "escalate",
        "created_at": "2026-10-29T20:00:00Z",
        "ladder_trace": [{"level": 3, "action": "l3_skipped", "messages": [{"role": "user", "content": "old"}]}],
    }
    new = {
        "id": "rep-new",
        "drift_class": "SEMANTIC_VALUE_DRIFT",
        "decision": "escalate",
        "created_at": "2026-10-29T21:00:00Z",
        "ladder_trace": [{"level": 3, "action": "frontier_generate", "messages": [{"role": "user", "content": "new"}]}],
    }
    other = {
        "id": "rep-other",
        "drift_class": "PAGINATION_CHANGE",
        "decision": "propose",
        "created_at": "2026-10-29T22:00:00Z",
        "ladder_trace": [],
    }
    monkeypatch.setattr(rf, "_api_get", lambda _base, _path: [other, old, new])
    repair_id, drift_class, messages = rf._live_l3_prompt("http://localhost:8101")
    assert repair_id == "rep-new"
    assert drift_class == "SEMANTIC_VALUE_DRIFT"
    assert messages == [{"role": "user", "content": "new"}]


def test_live_l3_prompt_fails_loudly_without_a_live_climb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No live climb yet: the capture says to break first, never invents."""
    import bench.record_frontier as rf

    monkeypatch.setattr(rf, "_api_get", lambda _base, _path: [])
    with pytest.raises(SystemExit, match="no live semantic repair"):
        rf._live_l3_prompt("http://localhost:8101")


# ---------------------------------------------------------------------------
# Recorded rejection: proposals that do not apply/parse/validate (owner check)
# ---------------------------------------------------------------------------


def _harbor_ref() -> dict[str, Any]:
    import yaml as _yaml

    return _yaml.safe_load((REPO_ROOT / "generated" / "connectors" / "harborpms" / "reference" / "spec.yaml").read_text(encoding="utf-8"))


def _climb_with_texts(l3_text: str, validate_against: Any = "harbor") -> dict[str, Any]:
    fake = FakeProvider(
        responses={
            "small": Completion(
                text='["gross_is_canonical", "net_is_canonical"]', usage=Usage(4, 4)
            ),
            "frontier": Completion(text=l3_text, usage=Usage(10, 10)),
        }
    )
    return run_frontier_climb(
        spec={"spec_version": 1, "connector": {"id": "t", "vendor": "harbor"}},
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=gross_net_hypotheses(),
        enumerator_yield_count=1,
        provider=fake,
        budget=Budget(),
        knowledge_cache=KnowledgeCache(),
        fingerprint_delta={"semantic": "gross_to_net"},
        vendor="harbor",
        scenario="gross_to_net",
        validate_against=_harbor_ref() if validate_against == "harbor" else validate_against,
    )


def test_l3_non_applying_patch_is_recorded_rejection() -> None:
    """REQ-044 item 4: one retry with the apply error, then escalate."""
    climb = _climb_with_texts('[{"op": "replace", "path": "/entities/Reservation/fields/nope/path", "value": "x"}]')
    assert climb["proposal"] is None
    assert climb["resolved_rung"] == 3
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3["action"] == "proposal_rejected"
    assert "does not apply" in l3["reason"]
    assert l3["raw_preview"]
    # The same bad patch comes back on the single retry (the fake answers
    # every tier identically), so both calls are billed and the error that
    # triggered the retry is recorded.
    assert l3["retry_error"] and "does not apply" in l3["retry_error"]
    assert "retry failed too" in l3["reason"]
    assert climb["usage"]["frontier_tokens"] == 40
    assert climb["usage"]["frontier_calls"] == 2


def test_l3_malformed_json_is_recorded_not_raised() -> None:
    climb = _climb_with_texts("not json{{{")
    assert climb["proposal"] is None
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3["action"] == "frontier_generate"
    assert l3["raw_preview"] == "not json{{{"
    # The run continues to the caller for escalation instead of crashing.
    assert [a["rung"] for a in climb["attempts"]] == [0, 1, 2, 3]


def test_l3_out_of_spec_path_rejected() -> None:
    climb = _climb_with_texts('[{"op": "replace", "path": "/does/not/exist", "value": 1}]')
    assert climb["proposal"] is None
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3["action"] == "proposal_rejected"


def test_l3_valid_proposal_passes_validation() -> None:
    climb = _climb_with_texts(
        '[{"op": "replace", "path": "/entities/Reservation/fields/total_amount/path", "value": "$.net_amount"}]'
    )
    assert climb["proposal"] == [
        {"op": "replace", "path": "/entities/Reservation/fields/total_amount/path", "value": "$.net_amount"}
    ]
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3["action"] == "frontier_generate"
    assert l3.get("proposal_validated") is True


def test_l3_prose_without_patch_records_detail_not_skipped() -> None:
    """Owner check 2026-10-28: provider text with no patch is a real call
    with a recorded detail — never a silent drop, never a skip."""
    climb = _climb_with_texts(
        "I cannot determine a safe mapping. Net vs gross needs an operator decision."
    )
    assert climb["proposal"] is None
    assert climb["resolved_rung"] == 3
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3["action"] == "frontier_generate"
    assert l3.get("parse_detail") == "no JSON patch or escalation found in response"
    assert l3["raw_preview"].startswith("I cannot determine")
    assert climb["usage"]["frontier_tokens"] == 20
    assert "\n" not in l3["reason"]


def test_l3_fenced_patch_inside_prose_is_extracted() -> None:
    """Reasoning-style answers (prose + fenced patch) still yield a patch."""
    text = (
        "The drift maps net_amount onto total_amount, so restore the gross path:\n"
        "```json\n"
        '[{"op": "replace", "path": "/entities/Reservation/fields/total_amount/path", "value": "$.net_amount"}]\n'
        "```\n"
        "This needs verifier confirmation before release."
    )
    climb = _climb_with_texts(text)
    assert climb["proposal"] == [
        {"op": "replace", "path": "/entities/Reservation/fields/total_amount/path", "value": "$.net_amount"}
    ]
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3["action"] == "frontier_generate"
    assert l3.get("proposal_validated") is True


def test_l3_empty_response_records_reason() -> None:
    """An empty live reply is diagnosed on the rung, not mislabelled."""
    climb = _climb_with_texts("   ")
    assert climb["proposal"] is None
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3["action"] == "frontier_generate"
    assert "empty response" in l3["reason"]
    assert "empty response" in l3.get("parse_detail", "")
    assert "raw_preview" not in l3


# ---------------------------------------------------------------------------
# Reasoning-token split on the L3 rung (REQ-027 owner check)
# ---------------------------------------------------------------------------


def test_l3_empty_response_names_reasoning_spend() -> None:
    """Owner check 2026-10-29: an empty L3 reply after a thinking budget is
    "thought hard, said nothing" — the rung reason names the reasoning
    tokens instead of a bare "empty response"."""
    fake = FakeProvider(
        responses={
            "small": Completion(
                text='["gross_is_canonical", "net_is_canonical"]', usage=Usage(4, 4)
            ),
            "frontier": Completion(text="   ", usage=Usage(319, 4319, reasoning_tokens=4000)),
        }
    )
    climb = run_frontier_climb(
        spec={"spec_version": 1, "connector": {"id": "t", "vendor": "harbor"}},
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=gross_net_hypotheses(),
        enumerator_yield_count=1,
        provider=fake,
        budget=Budget(),
        knowledge_cache=KnowledgeCache(),
        fingerprint_delta={"semantic": "gross_to_net"},
        vendor="harbor",
        scenario="gross_to_net",
        validate_against=_harbor_ref(),
    )
    assert climb["proposal"] is None
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert "4,000 reasoning tokens" in l3["reason"]
    assert l3["reasoning_tokens"] == 4000
    assert l3["model_usage_delta"]["frontier_reasoning"] == 4000
    assert climb["usage"]["frontier_reasoning"] == 4000
    # Reasoning is a sub-count: frontier totals still balance.
    assert climb["usage"]["frontier_tokens"] == 319 + 4319


def test_l3_requests_output_room_for_reasoning() -> None:
    """The frontier L3 call budgets room for thinking *and* an answer
    (owner check: 4k max_tokens returned an empty message)."""
    seen: dict[str, Any] = {}

    def _capture(
        messages: Any, *, max_tokens: int = 1024, tier: str = "small", purpose: str = "", tools: Any = None
    ) -> Completion:
        _ = (messages, tier, purpose, tools)
        seen["max_tokens"] = max_tokens
        return Completion(text="   ", usage=Usage(10, 10))

    fake = FakeProvider(
        responses={
            "small": Completion(
                text='["gross_is_canonical", "net_is_canonical"]', usage=Usage(4, 4)
            ),
            "frontier": _capture,
        }
    )
    run_frontier_climb(
        spec={"spec_version": 1, "connector": {"id": "t", "vendor": "harbor"}},
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=gross_net_hypotheses(),
        enumerator_yield_count=1,
        provider=fake,
        budget=Budget(),
        knowledge_cache=KnowledgeCache(),
        fingerprint_delta={"semantic": "gross_to_net"},
        vendor="harbor",
        scenario="gross_to_net",
        validate_against=_harbor_ref(),
    )
    assert seen["max_tokens"] == 131072


def test_replay_round_trips_reasoning_tokens(tmp_path: pathlib.Path) -> None:
    """Recordings keep the reasoning split, so replayed climbs report the
    same "thought hard, said nothing" diagnosis as the live run."""
    import json as _json

    recording = {
        "text": "   ",
        "tool_calls": [],
        "input_tokens": 319,
        "output_tokens": 4319,
        "reasoning_tokens": 4000,
        "model": "muse-spark-1.3",
        "captured_at": "2026-10-29",
    }
    (tmp_path / "r.json").write_text(_json.dumps({"abc": recording}), encoding="utf-8")
    from autobridge.llm.provider import prompt_hash as _prompt_hash

    messages = [{"role": "user", "content": "x"}]
    p = ReplayProvider(recordings_dir=str(tmp_path))
    p._recordings = {_prompt_hash(messages): recording}
    out = p.complete(messages, tier="frontier")
    assert out.usage.reasoning_tokens == 4000
    assert out.usage.total_tokens == 319 + 4319


# ---------------------------------------------------------------------------
# AR-4-3 / REQ-036: the controller derives the L3 gate count
# ---------------------------------------------------------------------------


def test_controller_derives_l3_gate_count_from_surviving_hypotheses() -> None:
    """`RepairController.handle` without an explicit count passes
    ``len(surviving hypotheses)`` to the climb — never a default 1 — and the
    L3 rung reason names that counted number."""
    from autobridge.repair.controller import RepairController

    ctrl = RepairController(knowledge_cache=KnowledgeCache())
    fake = FakeProvider(
        responses={
            "small": Completion(
                text='["gross_is_canonical", "net_is_canonical"]', usage=Usage(4, 4)
            ),
            "frontier": Completion(text="not json{{{", usage=Usage(10, 10)),
        }
    )
    # Two surviving hypotheses, no explicit count: L2 ranks (unconfident at
    # 0.41), then L3 is skipped by the gate naming the counted 2.
    res = ctrl.handle(
        spec=_spec(),
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=gross_net_hypotheses(),
        enumerator_yield_count=None,
        provider=fake,
        budget=Budget(),
        fingerprint_delta=_delta(),
        vendor="harbor",
    )
    assert res.decision == "escalate"
    l3 = next(t for t in res.ladder_trace if t.get("level") == 3)
    assert l3["action"] == "l3_skipped"
    assert "enumerator yielded 2 >= 2" in l3["reason"], l3["reason"]
    assert int(res.model_usage.get("frontier_tokens", 0)) == 0

    # One surviving hypothesis, no explicit count: L2 is skipped and L3 is
    # invoked by the gate naming the counted 1.
    hyps = gross_net_hypotheses()
    res1 = ctrl.handle(
        spec=_spec(),
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=[hyps[0]],
        enumerator_yield_count=None,
        provider=fake,
        budget=Budget(),
        fingerprint_delta=_delta(),
        vendor="harbor",
    )
    l3_1 = next(t for t in res1.ladder_trace if t.get("level") == 3)
    assert "enumerator yielded 1 < 2" in l3_1["reason"], l3_1["reason"]
    assert int(res1.model_usage.get("frontier_tokens", 0)) > 0

    # An explicit caller count still wins over the derived one.
    res2 = ctrl.handle(
        spec=_spec(),
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=[hyps[0]],
        enumerator_yield_count=5,
        provider=fake,
        budget=Budget(),
        fingerprint_delta=_delta(),
        vendor="harbor",
    )
    l3_2 = next(t for t in res2.ladder_trace if t.get("level") == 3)
    assert l3_2["action"] == "l3_skipped"
    assert "enumerator yielded 5 >= 2" in l3_2["reason"], l3_2["reason"]


# ---------------------------------------------------------------------------
# REQ-044: well-posed L3 prompt (spec pointers, raw record, escalate)
# ---------------------------------------------------------------------------


def test_l3_envelope_patch_parses_with_rationale() -> None:
    climb = _climb_with_texts(
        '{"patch": [{"op": "replace", "path": "/entities/Reservation/fields/total_amount/path", '
        '"value": "$.net_amount"}], "rationale": "Map total_amount to the net wire field."}'
    )
    assert climb["proposal"] == [
        {"op": "replace", "path": "/entities/Reservation/fields/total_amount/path", "value": "$.net_amount"}
    ]
    assert climb["model_rationale"] == "Map total_amount to the net wire field."
    assert climb["model_escalated"] is False
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3["action"] == "frontier_generate"
    assert l3["model_rationale"] == "Map total_amount to the net wire field."
    assert "model rationale" in l3["reason"]


def test_l3_fenced_envelope_parses() -> None:
    climb = _climb_with_texts(
        'Some thinking.\n```json\n{"escalate": "Should total_amount track gross or net?", '
        '"rationale": "Gross vs net is a business decision."}\n```'
    )
    assert climb["proposal"] is None
    assert climb["model_escalated"] is True
    assert climb["model_question"] == "Should total_amount track gross or net?"
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3["action"] == "model_escalation"
    assert l3["model_question"] == "Should total_amount track gross or net?"


def test_l3_envelope_garbage_details() -> None:
    from autobridge.repair.ladder import _parse_l3_response

    parsed = _parse_l3_response('{"foo": 1, "rationale": "thinking out loud"}')
    assert parsed["kind"] == "none"
    assert parsed["detail"] == "response JSON is neither a patch nor an escalation object"
    parsed = _parse_l3_response('{"patch": [{"op": "add"}], "rationale": "x"}')
    assert parsed["kind"] == "none"
    assert "not a patch list" in parsed["detail"]
    parsed = _parse_l3_response('{"escalate": 42}')
    assert parsed["kind"] == "none"
    assert "not a question string" in parsed["detail"]


def test_l3_retry_succeeds_after_apply_error() -> None:
    """First patch invents /mappings/..., the retry fixes the pointer."""
    bad = '[{"op": "replace", "path": "/mappings/total_amount", "value": "$.net_amount"}]'
    good = (
        '{"patch": [{"op": "replace", "path": "/entities/Reservation/fields/total_amount/path", '
        '"value": "$.net_amount"}], "rationale": "Same fix, addressed at the spec member."}'
    )
    seen: list[str] = []

    def _frontier(messages: Any, **kwargs: Any) -> Completion:
        _ = kwargs
        seen.append(str(messages[1]["content"]))
        return Completion(text=bad if len(seen) == 1 else good, usage=Usage(10, 10))

    fake = FakeProvider(
        responses={
            "small": Completion(text='["gross_is_canonical", "net_is_canonical"]', usage=Usage(4, 4)),
            "frontier": _frontier,
        }
    )
    climb = run_frontier_climb(
        spec=_spec(),
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=gross_net_hypotheses(),
        enumerator_yield_count=1,
        provider=fake,
        budget=Budget(),
        knowledge_cache=KnowledgeCache(),
        fingerprint_delta=_delta(),
        vendor="harbor",
        scenario="gross_to_net",
        validate_against=_harbor_ref(),
    )
    assert climb["proposal"] == [
        {"op": "replace", "path": "/entities/Reservation/fields/total_amount/path", "value": "$.net_amount"}
    ]
    assert climb["usage"]["frontier_calls"] == 2
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3["retried"] is True
    assert l3["retry_error"] and "/mappings/total_amount" in l3["retry_error"]
    assert "retry proposed a patch" in l3["reason"]
    # The retry prompt carries the rejection — the model is told what failed.
    assert "/mappings/total_amount" in seen[1]


def test_l3_escalate_answer_resolves_l4_with_model_question() -> None:
    """An {"escalate": ...} answer is a valid L3 outcome: L4 carries the
    model's question, labelled as the model's, instead of the generic one."""
    from autobridge.repair.controller import RepairController

    ctrl = RepairController(knowledge_cache=KnowledgeCache())
    fake = FakeProvider(
        responses={
            "small": Completion(text='["gross_is_canonical", "net_is_canonical"]', usage=Usage(4, 4)),
            "frontier": Completion(
                text='{"escalate": "Should total_amount track gross or net?", '
                '"rationale": "Gross vs net is a business decision no evidence settles."}',
                usage=Usage(10, 10),
            ),
        }
    )
    res = ctrl.handle(
        spec=_spec(),
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=gross_net_hypotheses(),
        enumerator_yield_count=1,
        provider=fake,
        budget=Budget(),
        fingerprint_delta=_delta(),
        vendor="harbor",
    )
    assert res.decision == "escalate"
    assert res.ladder_level_reached == 4
    assert res.escalation_bundle is not None
    assert res.escalation_bundle.question == "Should total_amount track gross or net?"
    l3 = next(t for t in res.ladder_trace if t.get("level") == 3)
    assert l3["action"] == "model_escalation"
    l4 = next(t for t in res.ladder_trace if t.get("level") == 4)
    assert "human decision" in l4["reason"]
    assert "Should total_amount track gross or net?" in l4["reason"]


def test_l3_prompt_names_spec_pointers_raw_sample_no_secret() -> None:
    """The prompt states the patch target, lists exact JSON Pointers, shows
    a redacted raw vendor record — and carries no secret value."""
    from autobridge.repair import semantic as _sem

    spec = {
        "spec_version": 3,
        "connector": {"id": "harbor", "vendor": "harbor"},
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
        },
    }
    summary = {
        "outcome": "Degraded",
        "unmet_criteria": ["money_invariant"],
        "canonical_records": {
            "Reservation": [{"reservation_id": "r1", "total_amount": "90.00"}],
        },
        "operation_results": {
            "list_reservations": {
                "raw_pages": [
                    {
                        "gross_sum": "300.00",
                        "reservations": [
                            {
                                "reservation_id": "r1",
                                "total_amount": 90.0,
                                "gross_amount": 100.0,
                                "api_token": "SECRET-XYZ-123",
                                "guest_email": "guest@example.com",
                            }
                        ],
                    }
                ],
            },
        },
    }
    hyps, _ = _sem.enumerate_money_hypotheses(spec, source="s")
    survivors = _sem.filter_wire_consistent(hyps, invariant_failed=True)
    climbed, _ = _sem.select_climbed(survivors)
    evidence = _sem.build_l3_evidence(spec, summary, climbed)
    assert evidence["patch_target"]["document"] == "connector spec"
    assert "/entities/Reservation/fields/total_amount/path" in evidence["patch_target"]["allowed_pointers"]
    assert evidence["raw_records"][0]["gross_amount"] == 100.0
    assert evidence["raw_records"][0]["api_token"] == "<redacted>"
    fake = FakeProvider(
        responses={
            "small": Completion(text='["x"]', usage=Usage(1, 1)),
            "frontier": Completion(
                text='{"escalate": "Should total_amount track gross or net?", "rationale": "x"}',
                usage=Usage(10, 10),
            ),
        }
    )
    climb = run_frontier_climb(
        spec=spec,
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=[climbed],
        enumerator_yield_count=1,
        provider=fake,
        budget=Budget(),
        knowledge_cache=KnowledgeCache(),
        fingerprint_delta=None,
        vendor="harbor",
        validate_against=spec,
        evidence=evidence,
    )
    user_text = climb["l3_messages"][1]["content"]
    assert "/entities/Reservation/fields/total_amount/path" in user_text
    assert "gross_amount" in user_text
    assert "SECRET-XYZ-123" not in user_text
    assert "guest@example.com" not in user_text
    assert "Answer with one JSON object" in user_text
    assert climb["l3_messages"][0]["content"].startswith("You repair API connectors")
    assert "Answer with one JSON object" in climb["l3_messages"][0]["content"]
    assert climb["model_escalated"] is True


def test_l3_envelope_replay_round_trip(tmp_path: Any) -> None:
    """The new prompt shape replays: capture the prompt from a fake climb,
    record an envelope answer for its hash, and replay the proposal."""
    import json as _json

    from autobridge.llm.provider import ReplayProvider, prompt_hash

    fake = FakeProvider(
        responses={
            "small": Completion(text='["gross_is_canonical", "net_is_canonical"]', usage=Usage(4, 4)),
            "frontier": Completion(text="probe", usage=Usage(1, 1)),
        }
    )
    probe = run_frontier_climb(
        spec=_spec(),
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=gross_net_hypotheses(),
        enumerator_yield_count=1,
        provider=fake,
        budget=Budget(),
        knowledge_cache=KnowledgeCache(),
        fingerprint_delta=_delta(),
        vendor="harbor",
        scenario="gross_to_net",
        validate_against=_harbor_ref(),
    )
    key = prompt_hash(probe["l3_messages"])
    recording = {
        "text": '{"patch": [{"op": "replace", "path": "/entities/Reservation/fields/total_amount/path", '
        '"value": "$.net_amount"}], "rationale": "Replayed capture."}',
        "tool_calls": [],
        "input_tokens": 1150,
        "output_tokens": 60,
        "model": "muse-spark-1.3",
        "captured_at": "synthetic-test-fixture",
    }
    l2_messages = _l2_pair_messages()
    l2_recording = {
        "text": '["gross_is_canonical", "net_is_canonical"]',
        "tool_calls": [],
        "input_tokens": 96,
        "output_tokens": 12,
        "model": "muse-spark-1.3",
        "captured_at": "synthetic-test-fixture",
    }
    (tmp_path / "r.json").write_text(
        _json.dumps({key: recording, prompt_hash(l2_messages): l2_recording}), encoding="utf-8"
    )
    replay = ReplayProvider(recordings_dir=str(tmp_path))
    climb = run_frontier_climb(
        spec=_spec(),
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=gross_net_hypotheses(),
        enumerator_yield_count=1,
        provider=replay,
        budget=Budget(),
        knowledge_cache=KnowledgeCache(),
        fingerprint_delta=_delta(),
        vendor="harbor",
        scenario="gross_to_net",
        validate_against=_harbor_ref(),
    )
    assert climb["proposal"] == [
        {"op": "replace", "path": "/entities/Reservation/fields/total_amount/path", "value": "$.net_amount"}
    ]
    assert climb["model_rationale"] == "Replayed capture."
