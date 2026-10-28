"""Ablation arms for REQ-024 B: engine vs. untrained frontier model.

Every arm that calls a frontier model uses the same ``FRONTIER_MODEL``
through the same provider (REQ-021 item 5), so the only difference between
arms is the engine. All arms run under the same matched budget
(``HarnessInputs``: docs volume, sandbox URL, credentials, visible tests,
token ceiling, wall clock) — the multi-arm self-test
:func:`assert_all_inputs_equal` extends :func:`harness.assert_inputs_equal`
to every arm.

Arms (REQ-024 B2):

* ``frontier_oneshot`` — the model alone, one prompt with the docs
  (:func:`one_shot.run_one_shot_arm` via the frontier provider).
* ``frontier_agent`` — the model as a coding agent with tools (the
  untrained-frontier baseline): :func:`harness.run_baseline_arm` driven by
  the OpenCode headless driver (default) with the same frontier model.
* ``engine_no_knowledge`` — AutoBridge with L0 and L1 disabled (ladder,
  verifier gate and budgets kept, so only the model repairs).
* ``autobridge_cold`` — full AutoBridge, empty learned knowledge.
* ``autobridge_warm`` — full AutoBridge after learning on the dev split
  only (never on held-out).

``c`` vs ``d`` isolates what the engine's built-in knowledge (rules +
known fixes) adds; ``d`` vs ``e`` isolates learning; ``b`` vs ``d`` is the
headline. Metrics per arm, scenario and overall carry mean + 95 % interval
over trials (:func:`summarize_ablation`); the learning curve
(:func:`run_learning_curve`) injects the same drift five times in a row.

Trust boundary (plan §1.2): this module reads ``simulators/scenarios/*.yaml``
and ``generated/connectors/*/reference/spec.yaml`` from the filesystem
only — the sanctioned bench exception — and never imports ``verifier`` or
``simulators``. Grading goes through the verifier's HTTP hidden mode via a
caller-supplied ``verify_fn`` (cf. :func:`scoring.verify_via_http`).
"""

from __future__ import annotations

import copy
import dataclasses
import math
import pathlib
import statistics
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

import yaml

if TYPE_CHECKING:
    from collections.abc import Callable

    from autobridge.bench.harness import ArmResult as _ArmResult

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCENARIOS_DIR = REPO_ROOT / "simulators" / "scenarios"
GENERATED_DIR = REPO_ROOT / "generated" / "connectors"
CASES_PATH = REPO_ROOT / "config" / "ablation_cases.yaml"

AblationArm = Literal[
    "frontier_oneshot",
    "frontier_agent",
    "engine_no_knowledge",
    "autobridge_cold",
    "autobridge_warm",
]

#: The five REQ-024 ablation arms, in canonical order.
ABLATION_ARMS: tuple[str, ...] = (
    "frontier_oneshot",
    "frontier_agent",
    "engine_no_knowledge",
    "autobridge_cold",
    "autobridge_warm",
)

#: Arms that call the frontier model (must share FRONTIER_MODEL).
FRONTIER_ARMS: tuple[str, ...] = ("frontier_oneshot", "frontier_agent")

#: Engine arms (RepairController in-process; ladder + gate + budgets kept).
ENGINE_ARMS: tuple[str, ...] = ("engine_no_knowledge", "autobridge_cold", "autobridge_warm")

#: Minimum trials for a publishable 95 % interval (REQ-024 B3).
MIN_TRIALS_FOR_INTERVAL = 5


# ---------------------------------------------------------------------------
# Case library: scenario -> repair inputs (mirrors the model-usage matrix)
# ---------------------------------------------------------------------------


def _load_reference_spec(connector_id: str) -> dict[str, Any]:
    path = GENERATED_DIR / connector_id / "reference" / "spec.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"missing reference spec {path}")
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


def _docs_spec(docs_path: str, connector_id: str, vocabulary: str = "v2") -> dict[str, Any]:
    """Build a draft spec from a docs file (generic; ids come from data)."""
    from autobridge.discovery.bootstrap import build_draft_spec  # noqa: PLC0415

    text = (REPO_ROOT / docs_path).read_text(encoding="utf-8")
    result = build_draft_spec(
        text,
        source=docs_path,
        connector_id=connector_id,
        spec_vocabulary=vocabulary,
    )
    if not result.success or result.draft_spec is None:
        raise RuntimeError(f"docs spec build failed for {docs_path}: {result.visible_errors}")
    return dict(result.draft_spec)


def _fd(**kwargs: Any) -> Any:
    from autobridge.drift.fingerprint import FingerprintDelta  # noqa: PLC0415

    return FingerprintDelta(**kwargs)


def _delta_pagination_strategy(previous: str, current: str, operations: list[str] | None = None) -> Any:
    ops = operations or ["list_reservations", "list_folios"]
    return _fd(
        fingerprint_changed=True,
        pagination_changed=True,
        keys_changed=False,
        types_changed=False,
        changed_entities=[],
        pagination_details={
            op: {"previous": {"strategy": previous}, "current": {"strategy": current}} for op in ops
        },
        keys_details={},
        types_details={},
    )


def _delta_rename(entity: str, old: str, new: str) -> Any:
    return _fd(
        fingerprint_changed=True,
        pagination_changed=False,
        keys_changed=False,
        types_changed=True,
        changed_entities=[entity],
        pagination_details={},
        keys_details={},
        types_details={entity: {"previous": {old: "str", "other": "str"}, "current": {new: "str", "other": "str"}}},
    )


def _delta_cursor_next_path(operation: str, previous: str, current: str) -> Any:
    return _fd(
        fingerprint_changed=True,
        pagination_changed=True,
        keys_changed=False,
        types_changed=False,
        changed_entities=["Reservation"],
        pagination_details={operation: {"previous": {"next_path": previous}, "current": {"next_path": current}}},
        keys_details={},
        types_details={},
    )


def _offset_variant_spec(base: str) -> dict[str, Any]:
    """Reference spec with page pagination rewritten to offset (generic)."""
    spec = copy.deepcopy(_load_reference_spec(base))
    for op in spec.get("operations", {}).values():
        pag = op.get("pagination") if isinstance(op, dict) else None
        if isinstance(pag, dict) and pag.get("strategy") == "page":
            pag["strategy"] = "offset"
    return spec


def _delta_generic() -> Any:
    return _fd(
        fingerprint_changed=True,
        pagination_changed=False,
        keys_changed=False,
        types_changed=False,
        changed_entities=[],
        pagination_details={},
        keys_details={},
        types_details={},
    )


def _gross_net_hypotheses() -> list[Any]:
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
        ),
        Hypothesis(
            id="net_is_canonical",
            family="amount",
            claim="total_amount tracks net",
            candidate_mapping_fragment={"family": "amount", "field": "net_amount"},
            prior=0.39,
            evidence_for=prov,
        ),
    ]


#: Case data lives in config/ablation_cases.yaml (vendor names stay out of
#: engine source per the trust-boundary guard). The builders below are
#: generic machinery dispatched by the ``kind`` fields of that file.
def _load_case_data() -> dict[str, dict[str, Any]]:
    doc = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8")) or {}
    cases = doc.get("cases")
    if not isinstance(cases, dict) or not cases:
        raise RuntimeError(f"no ablation cases in {CASES_PATH}")
    return cases


def _build_spec(spec_cfg: dict[str, Any]) -> dict[str, Any]:
    kind = str(spec_cfg.get("kind", ""))
    if kind == "generated":
        return _load_reference_spec(str(spec_cfg["connector"]))
    if kind == "generated_offset":
        return _offset_variant_spec(str(spec_cfg["base"]))
    if kind == "docs":
        return _docs_spec(
            str(spec_cfg["path"]),
            str(spec_cfg["connector"]),
            str(spec_cfg.get("vocabulary", "v2")),
        )
    raise KeyError(f"unknown ablation spec kind {kind!r}")


def _build_delta(delta_cfg: dict[str, Any]) -> Any:
    kind = str(delta_cfg.get("kind", ""))
    if kind == "generic":
        return _delta_generic()
    if kind == "pagination_strategy":
        return _delta_pagination_strategy(
            str(delta_cfg["previous"]),
            str(delta_cfg["current"]),
            list(delta_cfg.get("operations") or ["list_reservations", "list_folios"]),
        )
    if kind == "rename":
        return _delta_rename(str(delta_cfg["entity"]), str(delta_cfg["old"]), str(delta_cfg["new"]))
    if kind == "cursor_next_path":
        return _delta_cursor_next_path(
            str(delta_cfg["operation"]), str(delta_cfg["previous"]), str(delta_cfg["current"])
        )
    raise KeyError(f"unknown ablation delta kind {kind!r}")


def _hypotheses_for(name: str | None) -> tuple[Any, ...]:
    if name is None:
        return ()
    if name == "gross_net":
        return tuple(_gross_net_hypotheses())
    raise KeyError(f"unknown ablation hypotheses {name!r}")


def _case_entry(scenario_id: str) -> dict[str, Any]:
    cases = _load_case_data()
    try:
        entry = cases[scenario_id]
    except KeyError:
        raise KeyError(f"no ablation case for scenario {scenario_id!r}") from None
    if not isinstance(entry, dict):
        raise KeyError(f"malformed ablation case for scenario {scenario_id!r}")
    return entry


def expected_outcome_for(scenario_id: str) -> str:
    """Correct outcome class for a scenario (answer key lives in the YAML catalog)."""
    return str(_case_entry(scenario_id).get("expected", "no_patch"))


@dataclass(frozen=True, slots=True)
class AblationCase:
    """Repair inputs for one ablation scenario (filesystem sources only)."""

    scenario_id: str
    spec: dict[str, Any]
    drift_class: str
    fingerprint_delta: Any
    extra: dict[str, Any]
    hypotheses: tuple[Any, ...] = ()
    enumerator_yield_count: int | None = None
    validate_against: dict[str, Any] | None = None


def ablation_case_for_scenario(scenario_id: str) -> AblationCase:
    """Build repair inputs for a dev HTTP scenario.

    Reads ``simulators/scenarios/<id>.yaml`` to confirm the scenario is a
    dev HTTP fault (fias_tcp transport is skipped: the bench arms are HTTP
    only), then assembles the spec + delta + extra the ladder needs.
    Raises :class:`KeyError` for unknown scenarios and :class:`ValueError`
    for skipped (non-HTTP) ones.
    """
    try:
        entry = _case_entry(scenario_id)
    except KeyError:
        raise KeyError(f"no ablation case for scenario {scenario_id!r}") from None
    yaml_path = SCENARIOS_DIR / f"{scenario_id}.yaml"
    if yaml_path.is_file():
        doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if doc.get("transport", "http") not in ("http", "https", None) or str(doc.get("transport", "http")) == "fias_tcp":
            raise ValueError(f"scenario {scenario_id!r} is not an HTTP bench fault (skipped)")
    spec_cfg = entry.get("spec")
    delta_cfg = entry.get("delta")
    if not isinstance(spec_cfg, dict) or not isinstance(delta_cfg, dict):
        raise KeyError(f"malformed ablation case for scenario {scenario_id!r}")
    va_cfg = entry.get("validate_against")
    return AblationCase(
        scenario_id=scenario_id,
        spec=_build_spec(spec_cfg),
        drift_class=str(entry["drift_class"]),
        fingerprint_delta=_build_delta(delta_cfg),
        extra=dict(entry.get("extra") or {}),
        hypotheses=_hypotheses_for(entry.get("hypotheses")),
        enumerator_yield_count=entry.get("enumerator_yield_count"),
        validate_against=_build_spec(va_cfg) if isinstance(va_cfg, dict) else None,
    )


def ablation_scenarios() -> list[str]:
    """Dev HTTP scenarios with an ablation case, in stable order."""
    return sorted(_load_case_data())


# ---------------------------------------------------------------------------
# Provider factory: same model on every arm (REQ-024 B1)
# ---------------------------------------------------------------------------


def ablation_provider(prefer: str = "auto") -> Any:
    """Return the frontier-tier provider every model-calling arm shares.

    ``prefer="auto"`` (default): live :class:`MuseProvider` when
    ``FRONTIER_API_KEY`` is set, else :class:`ReplayProvider` over the
    committed recordings (missing recording fails loudly — never
    invented). ``prefer="fake"``: :class:`FakeProvider` for unit tests
    and offline rehearsal. ``prefer="live"``: live provider or raise.
    """
    from autobridge.llm.provider import FakeProvider, MuseProvider, ReplayProvider  # noqa: PLC0415

    if prefer == "fake":
        return FakeProvider()
    from autobridge.config import frontier_api_key  # noqa: PLC0415

    if prefer == "live" or (prefer == "auto" and frontier_api_key()):
        if prefer == "live" and not frontier_api_key():
            raise RuntimeError("ablation_provider(live): FRONTIER_API_KEY is not set")
        return MuseProvider()
    return ReplayProvider(recordings_dir=str(REPO_ROOT / "tests" / "fixtures" / "replay"))


# ---------------------------------------------------------------------------
# Arm runners (all share one HarnessInputs; see run_ablation_trial)
# ---------------------------------------------------------------------------


def run_frontier_oneshot_arm(inputs: Any, *, provider: Any | None = None) -> Any:
    """Arm a (``frontier_oneshot``): the model alone, one prompt with docs."""
    from autobridge.bench.one_shot import run_one_shot_arm  # noqa: PLC0415

    result = run_one_shot_arm(inputs, provider=provider or ablation_provider())
    # run_one_shot_arm labels arm="one_shot"; relabel to the ablation name.
    return dataclasses.replace(result, arm="frontier_oneshot")


def frontier_agent_driver() -> Any:
    """Select the frontier-agent driver (REQ-024 B1).

    ``BASELINE_DRIVER`` env (default ``"opencode"``); ``claude`` / ``codex``
    / ``llm`` stay available. Offline (no ``FRONTIER_API_KEY``) the CLI
    kinds fall back to :class:`FakeOpenCodeDriver` — labeled ``fake-*`` in
    every result so rehearsal numbers are never mistaken for live ones.
    """
    import os as _os

    from autobridge.bench.baseline_coding_agent import (  # noqa: PLC0415
        FakeOpenCodeDriver,
        OpenCodeCliDriver,
        get_baseline_driver,
    )

    kind = _os.environ.get("BASELINE_DRIVER", "").strip() or "opencode"
    if kind == "opencode":
        from autobridge.config import frontier_api_key  # noqa: PLC0415

        return OpenCodeCliDriver() if frontier_api_key() else FakeOpenCodeDriver()
    if kind in ("claude", "codex", "codex_cli", "claude_code"):
        from autobridge.config import frontier_api_key  # noqa: PLC0415

        if not frontier_api_key():
            return FakeOpenCodeDriver()
    return get_baseline_driver(kind)


def run_frontier_agent_arm(inputs: Any, *, driver: Any | None = None) -> Any:
    """Arm b (``frontier_agent``): the model as a coding agent with tools."""
    from autobridge.bench.harness import run_baseline_arm  # noqa: PLC0415

    return run_baseline_arm(inputs, driver=driver or frontier_agent_driver(), arm="frontier_agent")


def run_engine_arm(
    inputs: Any,
    case: AblationCase,
    *,
    ablation: Literal["engine_no_knowledge", "autobridge_cold", "autobridge_warm"],
    provider: Any | None = None,
    knowledge: Any | None = None,
    session: Any | None = None,
) -> Any:
    """Run an engine arm (c/d/e) in-process through RepairController.

    The ladder, verifier gate and budgets are kept for every engine arm;
    only the knowledge differs: ``engine_no_knowledge`` disables L0+L1,
    ``autobridge_cold`` starts from an empty cache, ``autobridge_warm``
    reuses the caller-supplied (dev-split-learned) cache.
    """
    from autobridge.bench.harness import ArmResult  # noqa: PLC0415
    from autobridge.llm.provider import Budget  # noqa: PLC0415
    from autobridge.repair.controller import RepairController  # noqa: PLC0415
    from autobridge.repair.knowledge import KnowledgeCache  # noqa: PLC0415

    if ablation not in ENGINE_ARMS:
        raise ValueError(f"unknown engine ablation {ablation!r}")
    cache = knowledge if knowledge is not None else KnowledgeCache()
    ctrl = RepairController(knowledge_cache=cache)
    token_ceiling = int(getattr(inputs, "token_ceiling", 0) or 20000)
    wall_clock = float(getattr(inputs, "wall_clock_s", 0) or 300.0)
    budget = Budget(max_frontier_tokens=token_ceiling, max_small_tokens=token_ceiling, max_wall=wall_clock)
    # Mirror the model-usage matrix: zero-call rows wire no provider, so any
    # model call is a failure. Only semantic drifts (hypotheses) climb with a
    # provider; everything else runs the deterministic ladder (L0/L1).
    needs_model = bool(case.hypotheses)
    active_provider = (provider or ablation_provider()) if needs_model else None
    start = time.monotonic()
    try:
        result = ctrl.handle(
            spec=case.spec,
            drift_class=case.drift_class,
            signals=[],
            fingerprint_delta=case.fingerprint_delta,
            extra=case.extra,
            vendor=None,
            session=session,
            hypotheses=list(case.hypotheses) or None,
            enumerator_yield_count=case.enumerator_yield_count,
            provider=active_provider,
            budget=budget,
            scenario=case.scenario_id,
            validate_against=case.validate_against,
            run_id=f"ablation-{ablation}-{case.scenario_id}-{getattr(inputs, 'trial', 0)}",
            enable_knowledge=ablation != "engine_no_knowledge",
            enable_rules=ablation != "engine_no_knowledge",
        )
        elapsed_ms = (time.monotonic() - start) * 1000.0
        usage = dict(result.model_usage or {})
        frontier = int(usage.get("frontier_tokens", 0) or 0)
        small = int(usage.get("small_tokens", 0) or 0)
        calls = int(usage.get("calls", 0) or 0)
        cost_raw = usage.get("estimated_cost", usage.get("cost_usd"))
        cost = None
        if cost_raw is not None:
            try:
                cost = Decimal(str(cost_raw))
            except Exception:
                cost = None
        patch = result.candidate_patch
        return ArmResult(
            arm=ablation,
            inputs=inputs,
            success=result.decision in ("propose", "escalate"),
            elapsed_ms=elapsed_ms,
            input_tokens=small,
            output_tokens=frontier,
            frontier_tokens=frontier,
            cost_usd=cost,
            tool_calls=0,
            model_calls=calls,
            verdict=None,
            score=None,
            failed_checks=None,
            error=None,
            model=getattr(provider, "frontier_model", None) or "engine-ladder",
            spec={
                "candidate_patch": list(patch) if patch else [],
                "decision": result.decision,
                "ladder_level_reached": result.ladder_level_reached,
                "rule_id": result.rule_id,
            },
        )
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000.0
        return ArmResult(
            arm=ablation,
            inputs=inputs,
            success=False,
            elapsed_ms=elapsed_ms,
            error=f"{type(exc).__name__}: {exc}"[:1000],
        )


# ---------------------------------------------------------------------------
# Multi-arm trial: one inputs object, pairwise self-test over every arm
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AblationTrialResult:
    """All five arms for one scenario × trial, with matched-budget audit."""

    scenario_id: str
    trial: int
    seed: int
    inputs: Any
    results: dict[str, Any] = field(default_factory=dict)
    matched: bool = True
    matched_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "trial": self.trial,
            "seed": self.seed,
            "matched": self.matched,
            "matched_error": self.matched_error,
            "inputs_fingerprint": self.inputs.fingerprint(),
            "arms": {name: r.to_dict() for name, r in self.results.items()},
        }


def assert_all_inputs_equal(named: dict[str, Any]) -> None:
    """Pairwise :func:`harness.assert_inputs_equal` over every arm's inputs.

    Extends the matched-budget self-test (REQ-024 B2) from two arms to all
    five: any differing field raises naming the arm pair and the field.
    """
    from autobridge.bench.harness import assert_inputs_equal  # noqa: PLC0415

    names = list(named)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = named[names[i]], named[names[j]]
            try:
                assert_inputs_equal(a, b)
            except AssertionError as exc:
                raise AssertionError(f"ablation arms {names[i]!r} vs {names[j]!r} differ: {exc}") from exc


def run_ablation_trial(
    config: Any,
    scenario_id: str,
    trial: int,
    *,
    runners: dict[str, Callable[[Any], Any]] | None = None,
    case: AblationCase | None = None,
    provider: Any | None = None,
    knowledge_warm: Any | None = None,
) -> AblationTrialResult:
    """Run one scenario × trial across all five ablation arms.

    Builds :class:`HarnessInputs` once, clones it per arm, asserts pairwise
    equality (the self-test), then runs each arm. With *runners* given
    (unit tests, offline rehearsal) each runner is ``(inputs) -> ArmResult``;
    otherwise the default runners wire the shared frontier provider, the
    OpenCode/fake driver by env, and in-process engine arms (warm arm uses
    *knowledge_warm* when given).
    """
    from autobridge.bench.harness import make_trial_inputs  # noqa: PLC0415

    inputs = make_trial_inputs(config, scenario_id, trial)
    per_arm: dict[str, Any] = {arm: make_trial_inputs(config, scenario_id, trial) for arm in ABLATION_ARMS}
    matched = True
    matched_error: str | None = None
    try:
        assert_all_inputs_equal(per_arm)
    except AssertionError as exc:
        matched = False
        matched_error = str(exc)

    case_obj = case if case is not None else ablation_case_for_scenario(scenario_id)
    prov = provider or ablation_provider()
    defaults: dict[str, Callable[[Any], Any]] = {
        "frontier_oneshot": lambda inp: run_frontier_oneshot_arm(inp, provider=prov),
        "frontier_agent": lambda inp: run_frontier_agent_arm(inp),
        "engine_no_knowledge": lambda inp: run_engine_arm(inp, case_obj, ablation="engine_no_knowledge", provider=prov),
        "autobridge_cold": lambda inp: run_engine_arm(inp, case_obj, ablation="autobridge_cold", provider=prov),
        "autobridge_warm": lambda inp: run_engine_arm(
            inp, case_obj, ablation="autobridge_warm", provider=prov, knowledge=knowledge_warm
        ),
    }
    active = runners or defaults
    results: dict[str, Any] = {}
    for arm in ABLATION_ARMS:
        fn = active.get(arm)
        if fn is None:
            continue
        try:
            res = fn(per_arm[arm])
        except Exception as exc:
            # An arm that raises is a failed arm, never a failed trial:
            # record it (the harness scores losses, never drops rows).
            from autobridge.bench.harness import ArmResult  # noqa: PLC0415

            res = ArmResult(arm=arm, inputs=per_arm[arm], success=False, error=f"{type(exc).__name__}: {exc}"[:1000])  # type: ignore[arg-type]
        # Allow dict-like runners; coerce.
        if not hasattr(res, "arm"):
            from autobridge.bench.harness import ArmResult  # noqa: PLC0415

            res = ArmResult(arm=arm, inputs=per_arm[arm], success=bool(res))  # type: ignore[arg-type]
        results[arm] = res
    return AblationTrialResult(
        scenario_id=scenario_id,
        trial=trial,
        seed=inputs.seed,
        inputs=inputs,
        results=results,
        matched=matched,
        matched_error=matched_error,
    )


def _mem_session() -> Any:
    """In-memory sqlite session for RepairRun/ModelCall rows (tests/rehearsal)."""
    from sqlalchemy import create_engine  # noqa: PLC0415
    from sqlalchemy.orm import sessionmaker  # noqa: PLC0415

    from autobridge.control.models import Base  # noqa: PLC0415

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def warm_knowledge(scenario_ids: list[str], *, provider: Any | None = None) -> Any:
    """Learn on the dev split only: run each fixable case once, keep cache.

    Returns the :class:`KnowledgeCache` the ``autobridge_warm`` arm uses.
    Only dev-split scenarios are learned (never held-out); repairs that do
    not propose cache nothing.
    """
    from autobridge.repair.controller import RepairController  # noqa: PLC0415
    from autobridge.repair.knowledge import KnowledgeCache  # noqa: PLC0415

    cache = KnowledgeCache()
    ctrl = RepairController(knowledge_cache=cache)
    prov = provider or ablation_provider()
    for sid in scenario_ids:
        try:
            case = ablation_case_for_scenario(sid)
        except (KeyError, ValueError):
            continue
        # A session is required: handle() only caches verified propose
        # patches (L0 future hits) when it can persist the RepairRun.
        session = _mem_session()
        try:
            ctrl.handle(
                spec=case.spec,
                drift_class=case.drift_class,
                fingerprint_delta=case.fingerprint_delta,
                extra=case.extra,
                session=session,
                hypotheses=list(case.hypotheses) or None,
                enumerator_yield_count=case.enumerator_yield_count,
                provider=prov if case.hypotheses else None,
                scenario=sid,
                validate_against=case.validate_against,
                run_id=f"warmup-{sid}",
            )
            session.commit()
        except Exception:
            continue
        finally:
            session.close()
    return cache


# ---------------------------------------------------------------------------
# Metrics: mean + 95 % interval per arm, scenario and overall (REQ-024 B3)
# ---------------------------------------------------------------------------


def _mean_ci95(values: list[float]) -> dict[str, Any]:
    """Mean with a normal-approximation 95 % interval (None when n == 0)."""
    n = len(values)
    if n == 0:
        return {"mean": None, "ci95_lo": None, "ci95_hi": None, "n": 0}
    mean = statistics.fmean(values)
    if n == 1:
        return {"mean": mean, "ci95_lo": mean, "ci95_hi": mean, "n": 1}
    sd = statistics.pstdev(values)
    half = 1.96 * sd / math.sqrt(n)
    return {"mean": mean, "ci95_lo": mean - half, "ci95_hi": mean + half, "n": n}


@dataclass(frozen=True, slots=True)
class AblationGrade:
    """One arm's graded outcome for one trial (verifier-judged only)."""

    arm: str
    scenario_id: str
    trial: int
    verified_completion: int  # 1 iff verifier passed the produced spec
    wrong_patch_released: int  # 1 iff a bad patch reached a release (0 required)
    correct_escalation: int  # 1 iff escalated exactly when the scenario says so
    frontier_tokens: int
    small_tokens: int
    cost_usd: float | None
    elapsed_ms: float
    model: str | None = None


def grade_ablation_arm(
    arm: str,
    trial: AblationTrialResult,
    outcome: Any | None,
    *,
    expected: str,
) -> AblationGrade:
    """Grade one arm from its result plus the verifier outcome.

    *outcome* is a :class:`scoring.VerifyOutcome` (or None when the
    verifier was unreachable — scored as not verified, never guessed).
    *expected* is the scenario's correct outcome class: ``"repair"``
    (verifier-accepted patch), ``"no_patch"`` (escalate, backoff-only or
    refresh with no spec change released) or ``"degraded_question"``
    (Degraded with one operator question, no patch).
    """
    res: _ArmResult | None = trial.results.get(arm)
    verified = 0
    wrong_release = 0
    if outcome is not None and res is not None:
        passed = bool(getattr(outcome, "passed", False))
        regression_failed = bool(getattr(outcome, "regression_failed", False))
        verified = 1 if (res.success and passed and not regression_failed) else 0
        wrong_release = 1 if (passed and regression_failed) else 0
    patch: list[Any] = []
    arm_spec = getattr(res, "spec", None) if res is not None else None
    if isinstance(arm_spec, dict):
        raw_patch = arm_spec.get("candidate_patch")
        if isinstance(raw_patch, list) and raw_patch:
            patch = raw_patch
    correct_esc = (1 if verified else 0) if expected == "repair" else (0 if patch else 1)
    cost = float(res.cost_usd) if res is not None and res.cost_usd is not None else None
    return AblationGrade(
        arm=arm,
        scenario_id=trial.scenario_id,
        trial=trial.trial,
        verified_completion=verified,
        wrong_patch_released=wrong_release,
        correct_escalation=correct_esc,
        frontier_tokens=int(res.frontier_tokens) if res is not None else 0,
        small_tokens=int(res.input_tokens) if res is not None else 0,
        cost_usd=cost,
        elapsed_ms=float(res.elapsed_ms) if res is not None else 0.0,
        model=res.model if res is not None else None,
    )


def grade_ablation_trial(trial: AblationTrialResult, *, verifier_url: str | None = None) -> list[AblationGrade]:
    """Grade all arms of one trial (verifier-judged only, never guessed).

    Arms that released a non-empty candidate patch are verified over HTTP
    (patched spec = case spec + patch); every other arm gets outcome None.
    A ``verifier_unreachable`` transport failure degrades that arm to
    unverified rather than fail-guessed.
    """
    from autobridge.bench.scoring import normalize_verify_response, verify_via_http  # noqa: PLC0415

    case = None
    try:
        case = ablation_case_for_scenario(trial.scenario_id)
    except (KeyError, ValueError):
        case = None
    expected = expected_outcome_for(trial.scenario_id) if case is not None else "no_patch"
    grades: list[AblationGrade] = []
    for arm in ABLATION_ARMS:
        res = trial.results.get(arm)
        outcome = None
        patch: list[Any] = []
        spec = getattr(res, "spec", None) if res is not None else None
        if isinstance(spec, dict):
            raw_patch = spec.get("candidate_patch")
            if isinstance(raw_patch, list) and raw_patch:
                patch = raw_patch
        if patch and case is not None and verifier_url:
            try:
                from autobridge.spec.patch import apply_patch  # noqa: PLC0415

                patched = apply_patch(dict(case.spec), patch)
                payload = verify_via_http(patched, trial.scenario_id, verifier_url=verifier_url)
                failed = payload.get("failed_checks") if isinstance(payload, dict) else None
                names = [f.get("name") for f in failed] if isinstance(failed, list) else []
                if not any(isinstance(n, str) and "verifier_unreachable" in n for n in names):
                    outcome = normalize_verify_response(payload if isinstance(payload, dict) else {})
            except Exception:
                outcome = None
        grades.append(grade_ablation_arm(arm, trial, outcome, expected=expected))
    return grades


def run_ablation_harness(
    config: Any,
    scenario_ids: list[str],
    *,
    provider: Any | None = None,
    verifier_url: str | None = None,
    warm_scenario_ids: list[str] | None = None,
) -> tuple[list[AblationTrialResult], list[AblationGrade], dict[str, Any]]:
    """Run every ablation arm over every scenario × trial (REQ-024 B2/B3).

    Builds one :class:`HarnessInputs` per scenario × trial (pairwise
    self-test over all five arms), warms the ``autobridge_warm`` cache on
    the dev split only, grades every arm (verifier-judged when reachable)
    and returns ``(trials, grades, summary)``.
    """
    prov = provider or ablation_provider()
    warm = warm_knowledge(list(warm_scenario_ids if warm_scenario_ids is not None else scenario_ids), provider=prov)
    trials: list[AblationTrialResult] = []
    skipped: list[str] = []
    for sid in scenario_ids:
        try:
            ablation_case_for_scenario(sid)
        except (KeyError, ValueError) as exc:
            # No ablation case (baseline) or non-HTTP fault (fias_tcp — the
            # bench arms are HTTP only): skip loudly, never fail the run.
            skipped.append(f"{sid} ({exc})")
            continue
        for t in range(int(getattr(config, "trials_per_scenario", 1) or 1)):
            trials.append(run_ablation_trial(config, sid, t, provider=prov, knowledge_warm=warm))
    grades: list[AblationGrade] = []
    for trial in trials:
        grades.extend(grade_ablation_trial(trial, verifier_url=verifier_url))
    summary = summarize_ablation(grades)
    summary["verifier_url"] = verifier_url
    summary["trials"] = len(trials)
    summary["skipped_scenarios"] = sorted(skipped)
    return trials, grades, summary


def write_ablation_results(
    trials: list[AblationTrialResult],
    summary: dict[str, Any],
    output_dir: str | pathlib.Path,
) -> list[pathlib.Path]:
    """Write ``ablation.jsonl`` (one line per trial) + ``_ablation_summary.json``."""
    import json as _json

    out_dir = pathlib.Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    trials_path = out_dir / "ablation.jsonl"
    with trials_path.open("w", encoding="utf-8") as fh:
        for trial in trials:
            fh.write(_json.dumps(trial.to_dict(), default=str, sort_keys=True) + "\n")
    summary_path = out_dir / "_ablation_summary.json"
    summary_path.write_text(_json.dumps(summary, default=str, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return [trials_path, summary_path]


def render_ablation_table(summary: dict[str, Any]) -> str:
    """One table per the demo: arm | verified rate | correct esc | frontier tokens | cost."""
    per_arm = summary.get("per_arm", {})

    def _fmt(block: dict[str, Any], key: str) -> str:
        cell = block.get(key, {})
        if not isinstance(cell, dict) or cell.get("mean") is None:
            return "—"
        mean = cell["mean"]
        lo, hi = cell.get("ci95_lo"), cell.get("ci95_hi")
        if isinstance(mean, float):
            if lo is None or hi is None:
                return f"{mean:.3f}"
            return f"{mean:.3f} [{lo:.3f},{hi:.3f}]"
        return str(mean)

    lines = [
        "| arm | verified_completion_rate | correct_escalation_rate | frontier_tokens (mean [95% CI]) | cost_usd (mean [95% CI]) | wrong_patches |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for arm in ABLATION_ARMS:
        block = per_arm.get(arm, {})
        lines.append(
            f"| {arm} | {_fmt(block, 'verified_completion_rate')} | {_fmt(block, 'correct_escalation_rate')} | "
            f"{_fmt(block, 'frontier_tokens')} | {_fmt(block, 'cost_usd')} | {block.get('wrong_patches_released', '—')} |"
        )
    return "\n".join(lines)


def summarize_ablation(grades: list[AblationGrade]) -> dict[str, Any]:
    """Aggregate grades per arm, per (arm, scenario) and overall.

    Every numeric metric is ``{"mean": …, "ci95_lo": …, "ci95_hi": …,
    "n": …}`` over trials (REQ-024 B3). Wrong-patch counts are summed
    (must be 0 for AutoBridge arms).
    """
    by_arm: dict[str, list[AblationGrade]] = {}
    by_arm_scenario: dict[tuple[str, str], list[AblationGrade]] = {}
    for g in grades:
        by_arm.setdefault(g.arm, []).append(g)
        by_arm_scenario.setdefault((g.arm, g.scenario_id), []).append(g)

    def _block(items: list[AblationGrade]) -> dict[str, Any]:
        def _rate(values: list[float]) -> dict[str, Any]:
            # Rates live in [0, 1]: clamp the normal-approximation interval.
            cell = _mean_ci95(values)
            for key in ("ci95_lo", "ci95_hi"):
                if isinstance(cell.get(key), float):
                    cell[key] = min(1.0, max(0.0, cell[key]))
            return cell

        def _nonneg(values: list[float]) -> dict[str, Any]:
            # Tokens, cost and latency cannot be negative: clamp the lower bound.
            cell = _mean_ci95(values)
            if isinstance(cell.get("ci95_lo"), float):
                cell["ci95_lo"] = max(0.0, cell["ci95_lo"])
            return cell

        return {
            "verified_completion_rate": _rate([float(g.verified_completion) for g in items]),
            "wrong_patches_released": sum(g.wrong_patch_released for g in items),
            "correct_escalation_rate": _rate([float(g.correct_escalation) for g in items]),
            "frontier_tokens": _nonneg([float(g.frontier_tokens) for g in items]),
            "small_tokens": _nonneg([float(g.small_tokens) for g in items]),
            "cost_usd": _nonneg([g.cost_usd for g in items if g.cost_usd is not None]),
            "elapsed_ms": _nonneg([g.elapsed_ms for g in items]),
            "n": len(items),
        }

    return {
        "per_arm": {arm: _block(items) for arm, items in sorted(by_arm.items())},
        "per_arm_scenario": {
            f"{arm}/{scenario}": _block(items) for (arm, scenario), items in sorted(by_arm_scenario.items())
        },
        "overall": _block(grades),
        "min_trials_for_interval": MIN_TRIALS_FOR_INTERVAL,
    }


# ---------------------------------------------------------------------------
# Learning curve: same drift 5x in a row (REQ-024 B4)
# ---------------------------------------------------------------------------


def run_learning_curve(
    scenario_id: str = "page_to_cursor",
    *,
    repeats: int = 5,
    provider: Any | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """Inject the same drift ``repeats`` times; frontier tokens per repair.

    Arms: ``frontier_agent`` (fake driver pays every time), engine arms
    ``autobridge_cold`` (first repair via the ladder, then L0 hits) and
    ``autobridge_warm`` (pre-warmed on the dev split: L0 from the start).
    Returns the raw rows plus per-arm totals; :func:`write_learning_curve_md`
    renders the file. Rows carry frontier tokens, the rung reached and the
    decision, so learning is visible even when every token count is 0
    (deterministic repairs cost no model tokens by design — the ladder
    learns the *route*, L1 rule once, then L0 hits).
    """
    from autobridge.bench.baseline_coding_agent import FakeOpenCodeDriver  # noqa: PLC0415
    from autobridge.bench.harness import HarnessConfig, make_trial_inputs  # noqa: PLC0415
    from autobridge.llm.provider import Usage  # noqa: PLC0415

    prov = provider or ablation_provider()
    case = ablation_case_for_scenario(scenario_id)
    cfg = HarnessConfig(seed=seed)
    driver = FakeOpenCodeDriver(
        default_usage=Usage(input_tokens=500, output_tokens=1500),
        default_model="fake-opencode",
    )
    from autobridge.repair.knowledge import KnowledgeCache  # noqa: PLC0415

    cold_cache: Any = KnowledgeCache()
    warm_cache: Any = warm_knowledge([scenario_id], provider=prov)
    cold_session = _mem_session()
    warm_session = _mem_session()
    rows: list[dict[str, Any]] = []
    try:
        for rep in range(repeats):
            inputs = make_trial_inputs(cfg, scenario_id, rep)
            agent_res = run_frontier_agent_arm(inputs, driver=driver)
            cold_res = run_engine_arm(
                inputs, case, ablation="autobridge_cold", provider=prov, knowledge=cold_cache, session=cold_session
            )
            warm_res = run_engine_arm(
                inputs, case, ablation="autobridge_warm", provider=prov, knowledge=warm_cache, session=warm_session
            )

            def _rung(res: Any) -> Any:
                spec = getattr(res, "spec", None)
                return spec.get("ladder_level_reached") if isinstance(spec, dict) else None

            def _decision(res: Any) -> Any:
                spec = getattr(res, "spec", None)
                return spec.get("decision") if isinstance(spec, dict) else None

            rows.append(
                {
                    "repeat": rep + 1,
                    "frontier_agent_frontier_tokens": int(agent_res.frontier_tokens),
                    "autobridge_cold_frontier_tokens": int(cold_res.frontier_tokens),
                    "autobridge_warm_frontier_tokens": int(warm_res.frontier_tokens),
                    "autobridge_cold_rung": _rung(cold_res),
                    "autobridge_warm_rung": _rung(warm_res),
                    "autobridge_cold_decision": _decision(cold_res),
                    "autobridge_warm_decision": _decision(warm_res),
                }
            )
            cold_session.commit()
            warm_session.commit()
    finally:
        cold_session.close()
        warm_session.close()
    totals = {
        arm: sum(r[f"{arm}_frontier_tokens"] for r in rows)
        for arm in ("frontier_agent", "autobridge_cold", "autobridge_warm")
    }
    return {
        "scenario_id": scenario_id,
        "repeats": repeats,
        "rows": rows,
        "totals": totals,
        "provenance": {
            "frontier_agent_driver": type(driver).__name__,
            "frontier_agent_model": driver.default_model,
            "engine_provider": type(prov).__name__,
            "engine_frontier_model": getattr(prov, "frontier_model", None),
        },
    }


def write_learning_curve_md(result: dict[str, Any], path: str | pathlib.Path) -> pathlib.Path:
    """Render the learning-curve result as ``experiments/learning_curve.md``."""
    out = pathlib.Path(path)
    prov = result.get("provenance", {})
    lines = [
        "# Learning curve (REQ-024 B4)",
        "",
        f"Scenario: `{result['scenario_id']}` — the same drift injected {result['repeats']} times in a row.",
        "Metric: frontier tokens per repair for the agent baseline (`frontier_agent`),",
        "full AutoBridge from empty knowledge (`autobridge_cold`), and AutoBridge after",
        "learning on the dev split (`autobridge_warm`).",
        "",
        "Provenance: "
        f"frontier_agent via {prov.get('frontier_agent_driver')} "
        f"(model `{prov.get('frontier_agent_model')}`); engine arms via "
        f"{prov.get('engine_provider')} (frontier `{prov.get('engine_frontier_model')}`). "
        "Fake-labelled rows are rehearsal, not publication numbers: rerun with",
        "`FRONTIER_API_KEY` set for live tokens.",
        "",
        "Expected: the agent pays model tokens on every repair (it has no",
        "memory across repairs); the engine pays 0 throughout (deterministic",
        "repairs cost no model tokens by design) and learning shows in the",
        "rung row: cold repairs via the ladder once, then L0 hits; warm is",
        "L0 from the start.",
        "",
        "| repeat | frontier_agent | autobridge_cold | autobridge_warm |",
        "| --- | --- | --- | --- |",
    ]
    for row in result["rows"]:
        lines.append(
            f"| {row['repeat']} | {row['frontier_agent_frontier_tokens']} | "
            f"{row['autobridge_cold_frontier_tokens']} | {row['autobridge_warm_frontier_tokens']} |"
        )
    lines += [
        "",
        "Rung reached per repeat (learning is the route: first repair via the",
        "ladder, then L0 hits):",
        "",
        "| repeat | cold rung | cold decision | warm rung | warm decision |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in result["rows"]:
        lines.append(
            f"| {row['repeat']} | {row.get('autobridge_cold_rung')} | "
            f"{row.get('autobridge_cold_decision')} | {row.get('autobridge_warm_rung')} | "
            f"{row.get('autobridge_warm_decision')} |"
        )
    lines += [
        "",
        "Totals: "
        + ", ".join(f"{arm}={total}" for arm, total in sorted(result["totals"].items())),
        "",
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# CLI: python -m autobridge.bench.ablation_curve (make bench-ablation)
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Generate ``experiments/learning_curve.md`` (REQ-024 B4)."""
    import argparse

    parser = argparse.ArgumentParser(description="AutoBridge ablation learning curve (REQ-024 B4)")
    parser.add_argument("--scenario", default="page_to_cursor", help="Dev scenario to repeat-drift")
    parser.add_argument("--repeats", type=int, default=5, help="Repeat count (default: 5)")
    parser.add_argument("--seed", type=int, default=42, help="Seed")
    parser.add_argument(
        "--out", default="experiments/learning_curve.md", help="Markdown output path"
    )
    parser.add_argument(
        "--provider",
        choices=("auto", "fake", "live"),
        default="auto",
        help="Frontier provider selection (default: auto)",
    )
    args = parser.parse_args(argv)
    result = run_learning_curve(
        args.scenario,
        repeats=args.repeats,
        provider=ablation_provider(args.provider),
        seed=args.seed,
    )
    path = write_learning_curve_md(result, args.out)
    print(f"learning curve: {args.scenario} x{args.repeats} -> {path}", flush=True)
    print(f"  totals: {result['totals']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
