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


def test_prompt_hash_stable() -> None:
    messages = [{"role": "user", "content": "hi"}]
    assert prompt_hash(messages) == prompt_hash(list(messages))
    assert len(prompt_hash(messages)) == 64


def test_replay_hit_returns_replayed_completion() -> None:
    p = ReplayProvider(recordings_dir=str(REPO_ROOT / "tests" / "fixtures" / "replay"))
    assert p.model_for_tier("frontier") == "muse-spark-1.3"
    l2 = [
        {"role": "system", "content": "You are a deterministic hypothesis ranker. Rank hypotheses by prior and evidence. Return JSON list of hypothesis ids in ranked order."},
        {"role": "user", "content": "hypotheses: ['gross_is_canonical', 'net_is_canonical']; priors: [0.41, 0.39]"},
    ]
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
    l2 = [
        {"role": "system", "content": "You are a deterministic hypothesis ranker. Rank hypotheses by prior and evidence. Return JSON list of hypothesis ids in ranked order."},
        {"role": "user", "content": "hypotheses: ['gross_is_canonical', 'net_is_canonical']; priors: [0.41, 0.39]"},
    ]
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
    from autobridge.bench.record_frontier import record_scenario

    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="FRONTIER_API_KEY"):
        record_scenario("gross_to_net", tmp_path)


def test_record_frontier_rejects_unknown_scenario(tmp_path: pathlib.Path) -> None:
    from autobridge.bench.record_frontier import record_scenario

    with pytest.raises(SystemExit, match="unknown SCENARIO"):
        record_scenario("nope", tmp_path)


def test_tapping_provider_records_shape() -> None:
    from autobridge.bench.record_frontier import TappingProvider

    fake = FakeProvider(
        responses={"default": Completion(text='[{"op": "replace"}]', usage=Usage(10, 20))}
    )
    tap = TappingProvider(fake, model="muse-spark-1.3")
    out = tap.complete([{"role": "user", "content": "hi"}], tier="frontier", purpose="p")
    assert out.text == '[{"op": "replace"}]'
    key = prompt_hash([{"role": "user", "content": "hi"}])
    entry = tap.recordings[key]
    assert entry["model"] == "muse-spark-1.3"
    assert (entry["input_tokens"], entry["output_tokens"]) == (10, 20)
    assert entry["captured_at"]


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
    climb = _climb_with_texts('[{"op": "replace", "path": "/entities/Reservation/fields/nope/path", "value": "x"}]')
    assert climb["proposal"] is None
    assert climb["resolved_rung"] == 3
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3["action"] == "proposal_rejected"
    assert "does not apply" in l3["reason"]
    assert l3["raw_preview"]
    assert climb["usage"]["frontier_tokens"] == 20


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
    assert l3.get("parse_detail") == "no JSON Patch found in response"
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
