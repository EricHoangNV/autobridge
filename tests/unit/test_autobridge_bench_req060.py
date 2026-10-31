"""REQ-060: frontier arms get the engine's repair task, no token limit.

Item 1: both frontier arms get the pre-drift spec + the faulted sync's
evidence (never a drift class, delta, hypotheses, expected, or
verifier material); the answer is a JSON Patch or an escalation.
Item 2: frontier patches verify like engine patches; escalations on
unfixable scenarios count as held back, on fixable ones as not fixed.
Item 3: token_ceiling 0 = no limit (default); per-row input/output/
reasoning tokens recorded; per-arm min/median/max totals in the summary.
Item 4: OpenCodeCliDriver parses the live CLI shapes (pinned by a
captured real ``opencode run`` fixture, no secrets).
Item 5: the learning curve runs with a live driver (no default_model crash).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from decimal import Decimal
from typing import Any

import pytest
import yaml

from autobridge.llm.provider import Budget, Completion, FakeProvider, Usage


def _inputs(scenario_id: str = "page_to_cursor", trial: int = 0, token_ceiling: int | None = None) -> Any:
    from bench.harness import HarnessConfig, make_trial_inputs

    cfg = HarnessConfig() if token_ceiling is None else HarnessConfig(token_ceiling=token_ceiling)
    return make_trial_inputs(cfg, scenario_id, trial)


def _case(scenario_id: str = "page_to_cursor") -> Any:
    from bench.ablation import ablation_case_for_scenario

    return ablation_case_for_scenario(scenario_id)


# ---------------------------------------------------------------------------
# Item 1: the shared repair task (spec + evidence, nothing else)
# ---------------------------------------------------------------------------


def test_repair_context_carries_spec_and_evidence() -> None:
    from bench.context import build_bench_context

    case = _case()
    inputs = _inputs()
    ctx = build_bench_context(
        scenario_id=case.scenario_id,
        connector_id=case.connector_id,
        spec=case.spec,
        docs_ref=case.docs_ref,
        visible_tests=tuple(inputs.visible_tests),
        seed=int(inputs.seed),
    )
    # The pre-drift spec under repair travels with the bundle.
    assert ctx.pre_drift_spec_yaml.strip()
    assert yaml.safe_load(ctx.pre_drift_spec_yaml) == case.spec
    # The faulted sync's evidence: outcome, unmet criteria, failed
    # checks, served response sample — and nothing else.
    assert "sync outcome:" in ctx.evidence_text
    assert "unmet criteria:" in ctx.evidence_text
    assert "failed checks:" in ctx.evidence_text
    assert "served response sample" in ctx.evidence_text
    blob = (ctx.pre_drift_spec_yaml + ctx.evidence_text).lower()
    for forbidden in ("drift_class", "drift class", "hypothes", "expected", "verifier", "hidden_cases", "sha256"):
        assert forbidden not in blob, f"task setup leaks {forbidden!r}"


def test_repair_context_same_spec_and_evidence_all_arms() -> None:
    """Engine + both frontier arms resolve the same spec + evidence hash."""
    import hashlib

    from bench.context import build_bench_context

    case = _case("gross_to_net")
    inputs = _inputs("gross_to_net")
    hashes = set()
    specs = set()
    for _ in ("engine", "frontier_oneshot", "frontier_agent"):
        ctx = build_bench_context(
            scenario_id=case.scenario_id,
            connector_id=case.connector_id,
            spec=case.spec,
            docs_ref=case.docs_ref,
            visible_tests=tuple(inputs.visible_tests),
            seed=int(inputs.seed),
        )
        hashes.add(ctx.content_hash())
        specs.add(hashlib.sha256((ctx.pre_drift_spec_yaml + ctx.evidence_text).encode()).hexdigest())
    assert len(hashes) == 1, "context diverged across arms"
    assert len(specs) == 1, "spec+evidence diverged across arms"


def test_frontier_arms_record_same_context_hash_live() -> None:
    """End to end: all three arms agree on the seeded repair context."""
    from bench.ablation import run_engine_arm, run_frontier_agent_arm, run_frontier_oneshot_arm
    from bench.baseline_coding_agent import FakeOpenCodeDriver

    inputs = _inputs()
    case = _case()
    patch_text = json.dumps({"patch": [{"op": "replace", "path": "/x", "value": 1}], "rationale": "test"})
    one = run_frontier_oneshot_arm(
        inputs, provider=FakeProvider(responses={"frontier": Completion(text=patch_text, usage=Usage(5, 10))}), case=case
    )
    agent = run_frontier_agent_arm(
        inputs, driver=FakeOpenCodeDriver(default_text=patch_text), case=case
    )
    engine = run_engine_arm(inputs, case, ablation="autobridge_cold", provider=FakeProvider())
    assert one.prompt is not None and one.prompt["id"] == "frontier_oneshot"
    assert agent.prompt is not None and agent.prompt["id"] == "frontier_agent"
    assert one.context_hash and agent.context_hash and engine.context_hash
    assert one.context_hash == agent.context_hash == engine.context_hash
    # The frontier arms answered the repair question, not the build one.
    assert one.spec is not None and one.spec.get("answer_kind") == "patch"
    assert agent.spec is not None and agent.spec.get("answer_kind") == "patch"


def test_frontier_prompt_files_describe_repair_task() -> None:
    from bench.bench_prompts import ensure_bench_prompts_loaded, get_bench_prompt

    ensure_bench_prompts_loaded()
    for prompt_id in ("frontier_oneshot", "frontier_agent"):
        template = get_bench_prompt(prompt_id)
        assert "pre_drift_spec_yaml" in template.inputs
        assert "evidence_text" in template.inputs
    oneshot = get_bench_prompt("frontier_oneshot")
    body = (oneshot.system or "") + "\n" + (oneshot.user_template or "")
    assert "patch" in body.lower()
    assert "escalat" in body.lower()


def test_agent_sandbox_writes_repair_files() -> None:
    import tempfile

    from bench.context import SANDBOX_FILES, build_bench_context, write_agent_sandbox

    assert "pre_drift_spec.yaml" in SANDBOX_FILES
    assert "evidence.md" in SANDBOX_FILES
    case = _case()
    inputs = _inputs()
    ctx = build_bench_context(
        scenario_id=case.scenario_id,
        connector_id=case.connector_id,
        spec=case.spec,
        docs_ref=case.docs_ref,
        visible_tests=tuple(inputs.visible_tests),
        seed=int(inputs.seed),
    )
    with tempfile.TemporaryDirectory(prefix="bench-req060-") as tmp:
        written = write_agent_sandbox(ctx, tmp)
        assert "pre_drift_spec.yaml" in written and "evidence.md" in written
        import pathlib

        assert yaml.safe_load((pathlib.Path(tmp) / "pre_drift_spec.yaml").read_text()) == case.spec
        assert "sync outcome" in (pathlib.Path(tmp) / "evidence.md").read_text()


# ---------------------------------------------------------------------------
# Item 1b: the repair-answer parser
# ---------------------------------------------------------------------------


def test_parse_repair_patch() -> None:
    from bench.repair_answer import parse_repair_answer

    parsed = parse_repair_answer('```json\n{"patch": [{"op": "replace", "path": "/a", "value": 1}], "rationale": "fix"}\n```')
    assert parsed["kind"] == "patch"
    assert parsed["patch"] == [{"op": "replace", "path": "/a", "value": 1}]


def test_parse_repair_bare_patch_list() -> None:
    from bench.repair_answer import parse_repair_answer

    parsed = parse_repair_answer('[{"op": "remove", "path": "/a"}]')
    assert parsed["kind"] == "patch"
    assert parsed["patch"] == [{"op": "remove", "path": "/a"}]


def test_parse_repair_escalation_with_proposal() -> None:
    from bench.repair_answer import parse_repair_answer

    parsed = parse_repair_answer(
        json.dumps(
            {
                "escalate": "Should total track net or gross?",
                "rationale": "business decision",
                "proposal": {"label": "Use net", "effect": "Maps total to net.", "patch": [{"op": "replace", "path": "/a", "value": 1}]},
            }
        )
    )
    assert parsed["kind"] == "escalate"
    assert parsed["question"] == "Should total track net or gross?"
    assert parsed["proposal"] == [{"op": "replace", "path": "/a", "value": 1}]
    assert parsed["proposal_label"] == "Use net"


def test_parse_repair_whole_spec_is_not_an_answer() -> None:
    from bench.repair_answer import parse_repair_answer

    parsed = parse_repair_answer(json.dumps({"spec_version": 1, "operations": {}, "connector": {"id": "x"}}))
    assert parsed["kind"] == "none"
    assert "whole ConnectorSpec" in parsed["detail"]


def test_parse_repair_garbage_is_none() -> None:
    from bench.repair_answer import parse_repair_answer

    assert parse_repair_answer("").get("kind") == "none"
    assert parse_repair_answer("here is my thinking with no json at all").get("kind") == "none"


# ---------------------------------------------------------------------------
# Item 2: graded the same way
# ---------------------------------------------------------------------------


def _trial_with(arm: str, spec: dict[str, Any] | None, *, scenario_id: str = "page_to_cursor") -> Any:
    from bench.ablation import AblationTrialResult
    from bench.harness import ArmResult

    inputs = _inputs(scenario_id)
    res = ArmResult(arm=arm, inputs=inputs, ran=True, spec=spec, outcome=None)  # type: ignore[arg-type]
    return AblationTrialResult(
        scenario_id=scenario_id, trial=0, seed=inputs.seed, inputs=inputs, results={arm: res}
    )


def test_frontier_patch_verified_like_engine_patch() -> None:
    """A frontier patch that verifies counts as fixed on a fixable scenario."""
    from bench.ablation import expected_outcome_for, grade_ablation_arm
    from bench.scoring import VerifyOutcome

    assert expected_outcome_for("page_to_cursor") == "repair"
    trial = _trial_with("frontier_oneshot", {"candidate_patch": [], "answer_kind": "patch"}, scenario_id="page_to_cursor")
    grade = grade_ablation_arm("frontier_oneshot", trial, VerifyOutcome(verdict="pass"), expected="repair")
    assert grade.verified_completion == 1
    assert grade.produced == 1


def test_frontier_escalation_held_back_on_unfixable() -> None:
    from bench.ablation import grade_ablation_arm

    trial = _trial_with(
        "frontier_agent",
        {"answer_kind": "escalate", "question": "Throttle or outage?", "proposal_patch": []},
        scenario_id="rate_limit_429",
    )
    grade = grade_ablation_arm("frontier_agent", trial, None, expected="no_patch")
    assert grade.produced == 1  # an escalation is a gradable attempt
    assert grade.correct_escalation == 1  # held back, as required
    assert grade.verified_completion == 0


def test_frontier_escalation_not_fixed_on_fixable() -> None:
    from bench.ablation import grade_ablation_arm

    trial = _trial_with(
        "frontier_oneshot",
        {"answer_kind": "escalate", "question": "Net or gross?", "proposal_patch": []},
    )
    grade = grade_ablation_arm("frontier_oneshot", trial, None, expected="repair")
    assert grade.correct_escalation == 0
    assert grade.verified_completion == 0


def test_frontier_patch_on_unfixable_is_not_held_back() -> None:
    from bench.ablation import grade_ablation_arm
    from bench.scoring import VerifyOutcome

    trial = _trial_with(
        "frontier_agent",
        {"candidate_patch": [{"op": "replace", "path": "/a", "value": 1}], "answer_kind": "patch"},
        scenario_id="rate_limit_429",
    )
    grade = grade_ablation_arm("frontier_agent", trial, VerifyOutcome(verdict="fail"), expected="no_patch")
    assert grade.correct_escalation == 0


def test_one_shot_arm_parses_patch_and_escalation() -> None:
    from bench.one_shot import run_one_shot_arm

    patch_text = json.dumps({"patch": [{"op": "replace", "path": "/a", "value": 1}], "rationale": "r"})
    ctx_case = _case()
    inputs = _inputs()
    from bench.context import build_bench_context

    ctx = build_bench_context(
        scenario_id=ctx_case.scenario_id,
        connector_id=ctx_case.connector_id,
        spec=ctx_case.spec,
        docs_ref=ctx_case.docs_ref,
        visible_tests=tuple(inputs.visible_tests),
        seed=int(inputs.seed),
    )
    res = run_one_shot_arm(
        inputs,
        provider=FakeProvider(responses={"frontier": Completion(text=patch_text, usage=Usage(5, 10))}),
        context=ctx,
    )
    assert res.ran is True and res.outcome is None
    assert res.spec is not None and res.spec["answer_kind"] == "patch"
    assert res.reasoning_tokens == 0

    esc_text = json.dumps({"escalate": "Net or gross?", "rationale": "business call"})
    res2 = run_one_shot_arm(
        inputs,
        provider=FakeProvider(responses={"frontier": Completion(text=esc_text, usage=Usage(5, 10))}),
        context=ctx,
    )
    assert res2.ran is True and res2.outcome == "escalated"
    assert res2.spec is not None and res2.spec["answer_kind"] == "escalate"


# ---------------------------------------------------------------------------
# Item 3: 0 = no limit
# ---------------------------------------------------------------------------


def test_zero_token_ceiling_is_no_limit() -> None:
    from bench.harness import DEFAULT_TOKEN_CEILING, HarnessConfig

    assert DEFAULT_TOKEN_CEILING == 200_000
    assert HarnessConfig().token_ceiling == 200_000
    assert HarnessConfig(token_ceiling=0).token_ceiling == 0
    budget = Budget(max_frontier_tokens=0, max_small_tokens=0)
    assert budget.max_frontier_tokens is None
    assert budget.max_small_tokens is None
    budget.record("frontier", Usage(input_tokens=10_000_000, output_tokens=20_000_000))
    budget.record("small", Usage(input_tokens=1_000_000, output_tokens=1_000_000))


def test_one_shot_arm_no_ceiling_no_budget_exhausted() -> None:
    from bench.one_shot import run_one_shot_arm

    # Ceiling 0 = no limit: far above any ceiling (would have budget-exhausted) but below
    # the per-call output cap (so the truncation heuristic stays quiet).
    big = Completion(text="no json here at all, just words", usage=Usage(input_tokens=500_000, output_tokens=60_000))
    res = run_one_shot_arm(_inputs(token_ceiling=0), provider=FakeProvider(responses={"frontier": big}))
    assert res.ran is True, res.error
    assert res.outcome == "no_output"  # legacy path (no context): no whole-spec answer
    assert "budget_exhausted" not in (res.error or "").lower()
    assert "BudgetExhausted" not in (res.error or "")
    assert res.input_tokens == 500_000 and res.output_tokens == 60_000


def test_total_tokens_stats_helper() -> None:
    from bench.ablation import AblationTrialResult, total_tokens_stats
    from bench.harness import ArmResult

    inputs = _inputs()
    trials = []
    for trial_no, (a_in, a_out, b_in, b_out) in enumerate([(10, 20, 1, 2), (30, 40, 5, 5), (50, 60, 9, 9)]):
        results = {
            "frontier_oneshot": ArmResult(arm="frontier_oneshot", inputs=inputs, ran=True, input_tokens=a_in, output_tokens=a_out),  # type: ignore[arg-type]
            "frontier_agent": ArmResult(arm="frontier_agent", inputs=inputs, ran=True, input_tokens=b_in, output_tokens=b_out),  # type: ignore[arg-type]
        }
        trials.append(AblationTrialResult(scenario_id="s", trial=trial_no, seed=1, inputs=inputs, results=results))
    stats = total_tokens_stats(trials)
    assert stats["frontier_oneshot"] == {"min": 30.0, "median": 70.0, "max": 110.0, "n": 3}
    assert stats["frontier_agent"] == {"min": 3.0, "median": 10.0, "max": 18.0, "n": 3}


def test_failed_arms_keep_prompt_and_context_hash() -> None:
    """A failed frontier arm still records what it was asked (audit trail)."""
    from bench.harness import run_baseline_arm
    from bench.one_shot import run_one_shot_arm

    case = _case()
    inputs = _inputs()
    from bench.context import build_bench_context

    ctx = build_bench_context(
        scenario_id=case.scenario_id,
        connector_id=case.connector_id,
        spec=case.spec,
        docs_ref=case.docs_ref,
        visible_tests=tuple(inputs.visible_tests),
        seed=int(inputs.seed),
    )

    class _Boom:
        def complete(self, *_a: Any, **_k: Any) -> Any:
            raise RuntimeError("provider down")

    failed = run_one_shot_arm(inputs, provider=_Boom(), context=ctx)  # type: ignore[arg-type]
    assert failed.ran is False
    assert failed.prompt is not None and failed.prompt["id"] == "frontier_oneshot"
    assert failed.context_hash == ctx.content_hash()

    from bench.baseline_coding_agent import OpenCodeCliError

    class _BoomDriver:
        model = "fake-boom"

        def run(self, _prompt: str, **_kw: Any) -> Any:
            raise OpenCodeCliError("opencode run produced no text: quota exhausted")

    failed_agent = run_baseline_arm(inputs, driver=_BoomDriver(), arm="frontier_agent", context=ctx)
    assert failed_agent.ran is False
    assert failed_agent.prompt is not None and failed_agent.prompt["id"] == "frontier_agent"
    assert failed_agent.context_hash == ctx.content_hash()


# ---------------------------------------------------------------------------
# Item 4: the agent arm parses the live CLI (captured fixture, no secrets)
# ---------------------------------------------------------------------------

_FIXTURE = "tests/fixtures/opencode/opencode_run_sample.jsonl"


def _fixture_stdout() -> str:
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    return (root / _FIXTURE).read_text(encoding="utf-8")


def test_opencode_parser_reads_live_part_shapes() -> None:
    """Pinned by a captured real ``opencode run --format json`` stream."""
    from bench.baseline_coding_agent import parse_opencode_output

    res = parse_opencode_output(_fixture_stdout())
    assert res.text == "hello"  # part.text, not the legacy shapes
    assert res.usage.input_tokens == 17792
    assert res.usage.output_tokens == 10
    assert res.usage.reasoning_tokens == 5
    assert res.session_id == "ses_e706e0280ffegd2nYGOddeRTcY"
    assert res.stream_error == ""
    assert res.cost_usd == Decimal("0.071468")


def test_opencode_session_resolution_names_model_and_totals(tmp_path: Any) -> None:
    from bench.baseline_coding_agent import resolve_opencode_session

    db = tmp_path / "opencode.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE session (id TEXT PRIMARY KEY, model TEXT, cost REAL,"
        " tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER)"
    )
    con.execute(
        "INSERT INTO session VALUES (?,?,?,?,?,?)",
        ("ses_abc", '{"id":"muse-spark-1.3","providerID":"meta"}', 0.012, 17792, 10, 5),
    )
    con.commit()
    con.close()
    resolved = resolve_opencode_session("ses_abc", db_path=str(db))
    assert resolved is not None
    assert resolved["model"] == "meta/muse-spark-1.3"
    assert resolved["input_tokens"] == 17792
    assert resolved["reasoning_tokens"] == 5
    assert resolved["cost_usd"] == Decimal("0.012")
    assert resolve_opencode_session("ses_missing", db_path=str(db)) is None
    assert resolve_opencode_session("", db_path=str(db)) is None
    assert resolve_opencode_session("ses_abc", db_path=str(tmp_path / "nope.db")) is None


def test_opencode_driver_resolves_model_from_session_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The arm records the model the run itself used (not a default label)."""
    from bench.baseline_coding_agent import OpenCodeCliDriver

    db = tmp_path / "opencode.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE session (id TEXT PRIMARY KEY, model TEXT, cost REAL,"
        " tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER)"
    )
    con.execute(
        "INSERT INTO session VALUES (?,?,?,?,?,?)",
        ("ses_e706e0280ffegd2nYGOddeRTcY", '{"id":"muse-spark-1.3","providerID":"meta"}', 0.071468, 17792, 10, 5),
    )
    con.commit()
    con.close()
    stdout = _fixture_stdout()
    monkeypatch.setattr(
        "bench.baseline_coding_agent.subprocess.run",
        lambda *a, **_kw: subprocess.CompletedProcess(args=a[0], returncode=0, stdout=stdout, stderr=""),
    )
    driver = OpenCodeCliDriver(cli_path="opencode", session_db_path=str(db))
    res = driver.run("repair the spec")
    assert res.text == "hello"
    assert res.model == "meta/muse-spark-1.3"
    assert res.usage.input_tokens == 17792
    assert res.usage.reasoning_tokens == 5


def test_opencode_driver_error_stream_raises_not_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Exit-0 error events fail loudly — never a silent no_output row."""
    from bench.baseline_coding_agent import OpenCodeCliDriver, OpenCodeCliError

    stdout = json.dumps(
        {
            "type": "error",
            "sessionID": "ses_err1",
            "error": {"name": "APIError", "data": {"message": "Incorrect API key provided: test-key-1"}},
        }
    )
    monkeypatch.setattr(
        "bench.baseline_coding_agent.subprocess.run",
        lambda *a, **_kw: subprocess.CompletedProcess(args=a[0], returncode=0, stdout=stdout, stderr=""),
    )
    driver = OpenCodeCliDriver(cli_path="opencode", session_db_path=str(tmp_path / "nope.db"))
    with pytest.raises(OpenCodeCliError, match="produced no text"):
        driver.run("repair the spec")


def test_frontier_agent_model_identity_allows_provider_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """``meta/muse-spark-1.3`` (session row) matches FRONTIER_MODEL unanimously."""
    from autobridge import config as _config
    from bench.ablation import run_frontier_agent_arm
    from bench.baseline_coding_agent import ClaudeCliResult

    class _Driver:
        model = "meta/muse-spark-1.3"

        def run(self, prompt: str, **_kw: Any) -> ClaudeCliResult:
            _ = prompt
            return ClaudeCliResult(
                text=json.dumps({"escalate": "Net or gross?", "rationale": "business call"}),
                usage=Usage(10, 20),
                model="meta/muse-spark-1.3",
            )

    monkeypatch.setattr(_config, "frontier_api_key", lambda: "key-set")
    monkeypatch.setattr(_config, "frontier_model", lambda: "muse-spark-1.3")
    res = run_frontier_agent_arm(_inputs(), driver=_Driver(), case=_case())
    assert res.ran is True and res.outcome == "escalated"
    assert res.model == "meta/muse-spark-1.3"


# ---------------------------------------------------------------------------
# Item 5: the learning curve runs with a live driver
# ---------------------------------------------------------------------------


def test_learning_curve_provenance_never_crashes_on_driver_shape() -> None:
    """REQ-060 item 5: no ``default_model`` crash for drivers without one."""
    from bench.baseline_coding_agent import ClaudeCliResult, OpenCodeCliDriver

    assert OpenCodeCliDriver(cli_path="opencode").default_model is None or isinstance(
        OpenCodeCliDriver(cli_path="opencode").default_model, str
    )

    class _ModelOnlyDriver:
        model = "meta/muse-spark-1.3"

        def run(self, prompt: str, **_kw: Any) -> ClaudeCliResult:
            _ = prompt
            return ClaudeCliResult(text="{}", usage=Usage(1, 1), model=self.model)

    from bench.ablation import run_learning_curve

    result = run_learning_curve(
        "page_to_cursor", repeats=1, provider=FakeProvider(), seed=7, driver=_ModelOnlyDriver()
    )
    assert result["provenance"]["frontier_agent_driver"] == "_ModelOnlyDriver"
    assert result["provenance"]["frontier_agent_model"] == "meta/muse-spark-1.3"
    assert len(result["rows"]) == 1
