"""Fair benchmark context — REQ-046: every arm gets the same context.

The engine arm and both frontier arms resolve the same
:class:`bench.context.BenchContext` bundle for a case (by content hash):
the ConnectorSpec JSON Schema, the vocabulary version's capabilities,
one complete example spec for a *different* vendor, the vendor docs, the
visible tests and a precise answer format. Nothing from ``verifier/``
or hidden cases enters any arm.
"""

from __future__ import annotations

import json
from typing import Any

import yaml


def _harness_inputs(scenario_id: str = "page_to_cursor", trial: int = 0) -> Any:
    from bench.harness import HarnessConfig, make_trial_inputs

    return make_trial_inputs(HarnessConfig(), scenario_id, trial)


def test_bench_prompts_load_with_front_matter() -> None:
    from bench.bench_prompts import bench_prompt_ref, ensure_bench_prompts_loaded

    loaded = ensure_bench_prompts_loaded()
    assert set(loaded) == {"frontier_oneshot", "frontier_agent"}
    for prompt_id in ("frontier_oneshot", "frontier_agent"):
        ref = bench_prompt_ref(prompt_id)
        assert ref["id"] == prompt_id
        assert ref["version"].strip()
        assert len(ref["hash"]) == 64
        template = loaded[prompt_id]
        assert template.tier == "frontier"
        assert "schema_json" in template.inputs
        assert "example_spec_yaml" in template.inputs
        assert "docs_text" in template.inputs
        assert "answer_format" in template.inputs


def test_context_bundle_has_engine_context() -> None:
    from bench.ablation import ablation_case_for_scenario
    from bench.context import build_bench_context

    case = ablation_case_for_scenario("page_to_cursor")
    inputs = _harness_inputs("page_to_cursor")
    ctx = build_bench_context(
        scenario_id=case.scenario_id,
        connector_id=case.connector_id,
        spec=case.spec,
        docs_ref=case.docs_ref,
        visible_tests=tuple(inputs.visible_tests),
    )
    # The ConnectorSpec JSON Schema: answers validate against it.
    schema = json.loads(ctx.schema_json)
    assert schema.get("type") == "object"
    assert "spec_version" in schema.get("properties", {})
    # Vocabulary capabilities: methods, auth, pagination, transforms, checks.
    caps = json.loads(ctx.capabilities_json)
    for key in ("methods", "auth_types", "pagination_strategies", "transforms", "checks", "rules"):
        assert caps[key], f"capabilities missing {key}"
    assert "page_to_cursor" in [r["id"] for r in caps["rules"]]
    assert ctx.vocabulary_version == "v1"  # atlas reference spec is spec_version 1
    # Worked example: a different vendor, complete, never the case's own.
    assert ctx.example_vendor != case.connector_id
    example = yaml.safe_load(ctx.example_spec_yaml)
    assert isinstance(example, dict)
    assert "operations" in example and "entities" in example
    # Vendor docs + visible tests + answer format (REQ-060: patch-or-escalate).
    assert ctx.docs_text.strip()
    assert list(ctx.visible_tests) == list(inputs.visible_tests)
    assert "patch" in ctx.answer_format and "escalate" in ctx.answer_format
    assert len(ctx.content_hash()) == 64


def test_fair_context_same_hash_all_arms() -> None:
    """Engine + both frontier arms agree on the context hash (per case)."""
    from bench.ablation import ablation_case_for_scenario
    from bench.context import build_bench_context

    for scenario_id in ("page_to_cursor", "gross_to_net", "meridian_cursor_renamed"):
        case = ablation_case_for_scenario(scenario_id)
        inputs = _harness_inputs(scenario_id)
        hashes = {
            build_bench_context(
                scenario_id=case.scenario_id,
                connector_id=case.connector_id,
                spec=case.spec,
                docs_ref=case.docs_ref,
                visible_tests=tuple(inputs.visible_tests),
            ).content_hash()
            for _ in ("engine", "frontier_oneshot", "frontier_agent")
        }
        assert len(hashes) == 1, f"context diverged for {scenario_id}"


def test_context_never_holds_hidden_material() -> None:
    """No verifier/hidden-case content enters any arm's bundle."""
    from bench.ablation import ablation_case_for_scenario, ablation_scenarios
    from bench.context import build_bench_context

    for scenario_id in ablation_scenarios():
        case = ablation_case_for_scenario(scenario_id)
        inputs = _harness_inputs(scenario_id)
        ctx = build_bench_context(
            scenario_id=case.scenario_id,
            connector_id=case.connector_id,
            spec=case.spec,
            docs_ref=case.docs_ref,
            visible_tests=tuple(inputs.visible_tests),
        )
        blob = ctx.schema_json + ctx.capabilities_json + ctx.example_spec_yaml + ctx.docs_text
        assert "hidden_cases" not in blob
        assert "verifier/hidden" not in blob
        assert "SHA256SUMS" not in blob
        assert ctx.example_vendor != case.connector_id


def test_agent_sandbox_holds_same_files_and_brief_names_them() -> None:
    from bench.ablation import ablation_case_for_scenario
    from bench.bench_prompts import get_bench_prompt
    from bench.context import (
        SANDBOX_FILES,
        build_bench_context,
        render_frontier_agent_brief,
        write_agent_sandbox,
    )

    case = ablation_case_for_scenario("page_to_cursor")
    inputs = _harness_inputs("page_to_cursor")
    ctx = build_bench_context(
        scenario_id=case.scenario_id,
        connector_id=case.connector_id,
        spec=case.spec,
        docs_ref=case.docs_ref,
        visible_tests=tuple(inputs.visible_tests),
    )
    brief = render_frontier_agent_brief(inputs, ctx)
    text = "\n".join(m["content"] for m in brief)
    for name in SANDBOX_FILES:
        assert name in text, f"brief must name sandbox file {name}"
    assert ctx.example_vendor in text
    assert case.connector_id not in text or ctx.example_vendor in text

    import tempfile

    with tempfile.TemporaryDirectory(prefix="bench-fair-") as tmp:
        written = write_agent_sandbox(ctx, tmp)
        assert written == list(SANDBOX_FILES)
        import pathlib

        assert (pathlib.Path(tmp) / "schema.json").read_text() == ctx.schema_json
        assert (pathlib.Path(tmp) / "capabilities.json").read_text() == ctx.capabilities_json
        assert (pathlib.Path(tmp) / "example_spec.yaml").read_text() == ctx.example_spec_yaml
        assert (pathlib.Path(tmp) / "vendor_docs.md").read_text() == ctx.docs_text
    # The brief template itself renders every declared input.
    assert get_bench_prompt("frontier_agent").id == "frontier_agent"


def test_oneshot_file_prompt_carries_schema_and_foreign_example() -> None:
    from bench.ablation import ablation_case_for_scenario
    from bench.context import build_bench_context, messages_to_text, render_frontier_oneshot_prompt

    case = ablation_case_for_scenario("gross_to_net")
    inputs = _harness_inputs("gross_to_net")
    ctx = build_bench_context(
        scenario_id=case.scenario_id,
        connector_id=case.connector_id,
        spec=case.spec,
        docs_ref=case.docs_ref,
        visible_tests=tuple(inputs.visible_tests),
    )
    text = messages_to_text(render_frontier_oneshot_prompt(inputs, ctx))
    assert "spec_version" in text  # the schema
    assert "credential_ref" in text  # auth rule, no values
    assert ctx.example_vendor in text
    assert "Answer format" in text or "answer" in text.lower()


def test_arms_record_prompt_ref_and_same_context_hash() -> None:
    """Live arm check: both frontier arms + engine arm agree per trial."""
    from autobridge.llm.provider import FakeProvider
    from bench.ablation import (
        ablation_case_for_scenario,
        run_engine_arm,
        run_frontier_agent_arm,
        run_frontier_oneshot_arm,
    )
    from bench.baseline_coding_agent import FakeOpenCodeDriver

    inputs = _harness_inputs("page_to_cursor")
    case = ablation_case_for_scenario("page_to_cursor")
    one = run_frontier_oneshot_arm(inputs, provider=FakeProvider(), case=case)
    agent = run_frontier_agent_arm(inputs, driver=FakeOpenCodeDriver(), case=case)
    engine = run_engine_arm(inputs, case, ablation="autobridge_cold", provider=FakeProvider())
    assert one.arm == "frontier_oneshot" and one.prompt is not None
    assert one.prompt["id"] == "frontier_oneshot"
    assert agent.arm == "frontier_agent" and agent.prompt is not None
    assert agent.prompt["id"] == "frontier_agent"
    assert one.context_hash and agent.context_hash and engine.context_hash
    assert one.context_hash == agent.context_hash == engine.context_hash


def test_llm_fallback_uses_full_context_for_real_scenarios() -> None:
    """REQ-046: run_baseline_via_llm_provider is fixed, not starved."""
    from autobridge.llm.provider import FakeProvider
    from bench.harness import run_baseline_via_llm_provider

    provider = FakeProvider(responses={"ConnectorSpec": "connector:\n  id: x"})
    full = run_baseline_via_llm_provider(_harness_inputs("page_to_cursor"), provider=provider)
    assert full.ran is True
    assert full.prompt is not None and full.prompt["id"] == "frontier_oneshot"
    assert full.context_hash
    # Unknown/synthetic ids keep the legacy path so unit tests stay green.
    legacy = run_baseline_via_llm_provider(
        _harness_inputs("no_such_scenario_xyz"), provider=FakeProvider()
    )
    assert legacy.ran is True
    assert legacy.prompt is None


def test_scorecard_records_bench_prompts() -> None:
    from bench.harness import HarnessConfig, make_trial_inputs, run_baseline_arm
    from bench.scoring import score_bench, validate_scorecard_schema

    cfg = HarnessConfig()
    inputs = make_trial_inputs(cfg, "page_to_cursor", 0)
    arm = run_baseline_arm(inputs, driver=None)
    card = score_bench(
        {"trials": [{"scenario_id": "page_to_cursor", "trial": 0, "matched": True, "arms": {"baseline": arm}, "winner": "baseline", "loss_notes": []}], "config": {"seed": 1}},
        verify_fn=lambda _spec, _sid: {"verdict": "pass", "regression": "pass"},
    )
    validate_scorecard_schema(card)  # prompts may be absent/empty on legacy arms
    assert isinstance(card["prompts"], dict)
