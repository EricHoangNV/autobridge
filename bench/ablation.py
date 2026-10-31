"""Ablation arms for REQ-024 B: engine vs. untrained frontier model.

Every arm that calls a frontier model uses the same ``FRONTIER_MODEL``
through the same provider (REQ-021 item 5), so the only difference between
arms is the engine. All arms run under the same matched budget
(``HarnessInputs``: docs volume, sandbox URL, credentials, visible tests,
token ceiling, wall clock) — the multi-arm self-test
:func:`assert_all_inputs_equal` extends :func:`harness.assert_inputs_equal`
to every arm, and :func:`assert_arm_task_setup_equal` proves every arm
resolved the same task setup (pre-drift spec) from its inputs.

Arms (REQ-024 B2):

* ``frontier_oneshot`` — the model alone, one prompt with docs
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

REQ-024 owner check on f32b4b4b: every arm gets exactly the same inputs
(the pre-drift spec, the docs, the fault served for the scenario,
budgets). The engine arms run the real path end to end — sync against
the served fault → detector → classifier → hypothesis engine → ladder →
verifier gate — with no case-supplied drift class, delta, hypotheses or
yield count. ``config/ablation_cases.yaml`` holds only equal inputs plus
grading-only data; :func:`ablation_case_for_scenario` refuses cases that
smuggle diagnostics. Trust boundary: the harness (top-level ``bench/``,
outside ``src/autobridge``) reads the filesystem and drives simulator
serving code the way the tests do, but never imports ``verifier`` —
answer keys stay in their package; grading goes through the verifier's
HTTP hidden mode via a caller-supplied ``verify_fn``
(cf. :func:`scoring.verify_via_http`).
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

    from bench.harness import ArmResult as _ArmResult

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
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


def describe_arm_availability() -> list[dict[str, str]]:
    """Say up front which arms can run and why (REQ-028).

    ``frontier_oneshot`` needs a frontier provider: live
    (:class:`MuseProvider`) when ``FRONTIER_API_KEY`` is set, else replay
    over the committed recordings (one missing recording fails that
    scenario loudly, never invented), else the fake provider (offline
    rehearsal — tokens stay 0 and rows are labelled accordingly).
    ``frontier_agent`` needs the ``opencode`` CLI on PATH (the supported
    sandbox path); without it the harness uses ``FakeOpenCodeDriver`` and
    labels rows ``fake-*`` so rehearsal numbers are never mistaken for
    live ones. The three engine arms always run (in-process, no key, no
    CLI). Returned rows are also stored on the ablation summary.
    """
    import shutil as _shutil

    from autobridge.config import frontier_api_key, frontier_model, opencode_model  # noqa: PLC0415

    rows: list[dict[str, str]] = []
    key = bool(frontier_api_key())
    model = frontier_model()
    replay_dir = REPO_ROOT / "tests" / "fixtures" / "replay"
    replay_count = sum(1 for _ in replay_dir.glob("*.json")) if replay_dir.is_dir() else 0
    if key:
        oneshot = f"live ({model})"
    elif replay_count:
        oneshot = f"replay ({replay_count} recordings; missing recording fails loudly)"
    else:
        oneshot = "fake provider (offline rehearsal; 0 frontier tokens)"
    rows.append({"arm": "frontier_oneshot", "mode": oneshot})
    cli = _shutil.which("opencode")
    # REQ-061 item 1: the agent runs opencode_model() (OPENCODE_MODEL, else
    # derived from FRONTIER_MODEL), so name that model here.
    agent_model = opencode_model()
    if cli and key:
        agent = f"live opencode CLI ({cli}, model {agent_model})"
    elif cli:
        agent = "opencode CLI present but no FRONTIER_API_KEY: fake driver (rows labelled fake-*)"
    else:
        agent = "no opencode CLI on PATH: fake driver (rows labelled fake-*); run inside the sandbox for the live agent arm"
    rows.append({"arm": "frontier_agent", "mode": agent})
    for arm in ENGINE_ARMS:
        rows.append({"arm": arm, "mode": "in-process (always runnable)"})
    return rows


# ---------------------------------------------------------------------------
# Case library: equal inputs per scenario (REQ-024 owner check on f32b4b4b)
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


def _offset_variant_spec(base: str) -> dict[str, Any]:
    """Reference spec with page pagination rewritten to offset (generic).

    The variant is a valid pre-drift spec: offset strategy with explicit
    offset/limit params, so it syncs for real (the arm path validates).
    """
    spec = copy.deepcopy(_load_reference_spec(base))
    for op in spec.get("operations", {}).values():
        pag = op.get("pagination") if isinstance(op, dict) else None
        if isinstance(pag, dict) and pag.get("strategy") == "page":
            pag["strategy"] = "offset"
            pag["offset_param"] = "offset"
            pag["limit_param"] = "limit"
            pag["page_param"] = None
    return spec


#: Diagnostic keys the case file must NEVER carry (grading-only ``expected``
#: is the sole exception). The loader refuses cases containing them, so a
#: pre-diagnosed drift label, delta, hypothesis set, yield count, rule hint
#: or post-drift reference cannot reach an arm.
_FORBIDDEN_CASE_KEYS = frozenset(
    {
        "drift_class",
        "delta",
        "hypotheses",
        "enumerator_yield_count",
        "extra",
        "validate_against",
    }
)

#: Derived drift classes that justify a model climb (phase 2). Deterministic
#: first (plan §2.5): every other class — structural, auth, transport,
#: capability, duplicate, unknown-with-nothing-observed — runs the
#: deterministic ladder only, with zero model calls.
_MODEL_AMBIGUITY_CLASSES = frozenset({"SEMANTIC_VALUE_DRIFT"})

#: Case data lives in config/ablation_cases.yaml (vendor names stay out of
#: engine source per the trust-boundary guard). The builders below are
#: generic machinery dispatched by the ``kind`` fields of that file; every
#: vendor-specific string comes from this file at runtime.
def _load_case_data() -> dict[str, dict[str, Any]]:
    doc = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8")) or {}
    cases = doc.get("cases")
    if not isinstance(cases, dict) or not cases:
        raise RuntimeError(f"no ablation cases in {CASES_PATH}")
    for scenario_id, entry in cases.items():
        if not isinstance(entry, dict):
            raise RuntimeError(f"malformed ablation case for {scenario_id!r}")
        smuggled = _FORBIDDEN_CASE_KEYS.intersection(entry)
        if smuggled:
            raise RuntimeError(
                f"ablation case {scenario_id!r} smuggles diagnostics {sorted(smuggled)}: "
                "the case file holds equal inputs + grading-only data (REQ-024 owner check)"
            )
        spec_cfg = entry.get("spec")
        if not isinstance(spec_cfg, dict) or "kind" not in spec_cfg:
            raise RuntimeError(f"ablation case {scenario_id!r} needs a spec source")
        docs_ref = entry.get("docs")
        if not isinstance(docs_ref, str) or not docs_ref:
            raise RuntimeError(f"ablation case {scenario_id!r} needs a docs ref")
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


def _spec_connector_id(spec_cfg: dict[str, Any]) -> str:
    """Connector id the pre-drift spec belongs to (fault-serving vendor)."""
    kind = str(spec_cfg.get("kind", ""))
    if kind == "generated":
        return str(spec_cfg["connector"])
    if kind == "generated_offset":
        return str(spec_cfg["base"])
    if kind == "docs":
        return str(spec_cfg["connector"])
    raise KeyError(f"unknown ablation spec kind {kind!r}")


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
    """Correct outcome class for a scenario (answer key lives in the YAML catalog).

    GRADING ONLY — arms never call this. The expected class judges arm
    results; it is never an arm input.
    """
    return str(_case_entry(scenario_id).get("expected", "no_patch"))


@dataclass(frozen=True, slots=True)
class AblationCase:
    """Equal task setup for one ablation scenario (shared by every arm).

    Holds ONLY what every arm gets: the scenario id, the pre-drift spec
    under repair, the connector it belongs to (fault-serving vendor) and
    the vendor docs the task is described in. No drift class, delta,
    hypotheses, yield count, rule hint or post-drift reference — the
    engine arms derive those from observation.
    """

    scenario_id: str
    spec: dict[str, Any]
    connector_id: str
    docs_ref: str


def ablation_case_for_scenario(scenario_id: str) -> AblationCase:
    """Equal task setup for a dev HTTP scenario (spec + docs, no diagnostics).

    Reads ``simulators/scenarios/<id>.yaml`` to confirm the scenario is a
    dev HTTP fault (fias_tcp transport is skipped: the bench arms are HTTP
    only), then loads the pre-drift spec from the case's spec source.
    Raises :class:`KeyError` for unknown scenarios, :class:`ValueError`
    for skipped (non-HTTP) ones and :class:`RuntimeError` for cases that
    smuggle diagnostics.
    """
    try:
        entry = _case_entry(scenario_id)
    except KeyError:
        raise KeyError(f"no ablation case for {scenario_id!r}") from None
    yaml_path = SCENARIOS_DIR / f"{scenario_id}.yaml"
    if yaml_path.is_file():
        doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if doc.get("transport", "http") not in ("http", "https", None) or str(doc.get("transport", "http")) == "fias_tcp":
            raise ValueError(f"scenario {scenario_id!r} is not an HTTP bench fault (skipped)")
    spec_cfg = entry.get("spec")
    if not isinstance(spec_cfg, dict):
        raise KeyError(f"malformed ablation case for {scenario_id!r}")
    return AblationCase(
        scenario_id=scenario_id,
        spec=_build_spec(spec_cfg),
        connector_id=_spec_connector_id(spec_cfg),
        docs_ref=str(entry["docs"]),
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


def run_frontier_oneshot_arm(inputs: Any, *, provider: Any | None = None, docs_loader: Any | None = None, case: AblationCase | None = None) -> Any:
    """Arm a (``frontier_oneshot``): the model alone, one prompt with full context.

    REQ-046: the prompt is the file-based ``frontier_oneshot`` prompt
    rendered over the shared context bundle (schema, capabilities,
    different-vendor example, vendor docs, visible tests, answer format)
    — exactly the context the engine has. *docs_loader* is kept for unit
    tests that inject docs text; when given it takes the legacy path.
    *case* overrides the scenario lookup (tests).
    """
    from bench.context import build_bench_context  # noqa: PLC0415
    from bench.one_shot import run_one_shot_arm  # noqa: PLC0415

    if docs_loader is not None:
        result = run_one_shot_arm(inputs, provider=provider or ablation_provider(), docs_loader=docs_loader)
        return dataclasses.replace(result, arm="frontier_oneshot")
    case_obj = case
    if case_obj is None:
        try:
            case_obj = ablation_case_for_scenario(str(getattr(inputs, "scenario_id", "")))
        except Exception:
            case_obj = None
    context = None
    if case_obj is not None:
        context = build_bench_context(
            scenario_id=case_obj.scenario_id,
            connector_id=case_obj.connector_id,
            spec=case_obj.spec,
            docs_ref=case_obj.docs_ref,
            visible_tests=tuple(getattr(inputs, "visible_tests", ()) or ()),
            seed=int(getattr(inputs, "seed", 0) or 0),
        )
    result = run_one_shot_arm(inputs, provider=provider or ablation_provider(), context=context)
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

    from bench.baseline_coding_agent import (  # noqa: PLC0415
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


def _norm_model_id(model: Any) -> str:
    """Bare model id (``provider/model`` → ``model``) for identity comparison."""
    return str(model or "").strip().split("/")[-1].strip()


def preflight_frontier_agent(*, timeout_s: float = 300.0, driver: Any | None = None) -> Any:
    """One live ``opencode run`` before the ablation's agent rows (REQ-061 item 4).

    When the agent arm would run live (an :class:`OpenCodeCliDriver`, i.e.
    ``FRONTIER_API_KEY`` is set and ``BASELINE_DRIVER`` selects opencode),
    this runs a minimal prompt and requires text, the model id and token
    usage back.  Anything missing raises ``RuntimeError`` naming the
    reason, so the run stops here instead of producing a full table of
    empty agent rows.  Fake/offline drivers (and non-opencode kinds) skip
    silently and return ``None``; a passing run returns the result.
    """
    from bench.baseline_coding_agent import OpenCodeCliDriver  # noqa: PLC0415

    drv = driver if driver is not None else frontier_agent_driver()
    if not isinstance(drv, OpenCodeCliDriver):
        return None
    try:
        res = drv.run("Reply with exactly: ok", timeout_s=timeout_s)
    except Exception as exc:
        raise RuntimeError(
            "bench ablation: frontier-agent preflight failed "
            f"({type(exc).__name__}: {exc}); refusing to run agent rows "
            "that would all come back empty"
        ) from exc
    problems: list[str] = []
    if not (getattr(res, "text", "") or "").strip():
        stream_error = (getattr(res, "stream_error", "") or "").strip()
        problems.append(
            "empty text" + (f" (stream error: {stream_error[:500]})" if stream_error else "")
        )
    model = (getattr(res, "model", "") or "").strip()
    if not model or model == "unknown":
        problems.append(f"no model id (got {model!r})")
    usage = getattr(res, "usage", None)
    total = int(getattr(usage, "input_tokens", 0) or 0) + int(getattr(usage, "output_tokens", 0) or 0)
    if total <= 0:
        problems.append("no token usage reported")
    if problems:
        raise RuntimeError(
            "bench ablation: frontier-agent preflight failed "
            f"({'; '.join(problems)}); refusing to run agent rows "
            "that would all come back empty"
        )
    print(
        f"bench harness: frontier-agent preflight ok "
        f"(model {model}, {total} tokens)",
        flush=True,
    )
    return res


def run_frontier_agent_arm(inputs: Any, *, driver: Any | None = None, case: AblationCase | None = None) -> Any:
    """Arm b (``frontier_agent``): the model as a coding agent with tools.

    REQ-046: the brief is the file-based ``frontier_agent`` prompt over
    the shared context bundle, and the sandbox holds the same files the
    engine has (schema, capabilities, different-vendor example, vendor
    docs); the brief names them. *case* overrides the scenario lookup
    (tests).

    REQ-052: on a live run (``FRONTIER_API_KEY`` set) the recorded model
    must be the model the run itself reports, and it must equal
    ``FRONTIER_MODEL`` — otherwise the row fails loudly as
    ``model_mismatch`` instead of scoring a different model under the
    frontier arm's name. Offline (fake driver) rows keep their ``fake-*``
    model labels and skip the check.
    """
    from bench.context import build_bench_context  # noqa: PLC0415
    from bench.harness import run_baseline_arm  # noqa: PLC0415

    case_obj = case
    if case_obj is None:
        try:
            case_obj = ablation_case_for_scenario(str(getattr(inputs, "scenario_id", "")))
        except Exception:
            case_obj = None
    context = None
    if case_obj is not None:
        context = build_bench_context(
            scenario_id=case_obj.scenario_id,
            connector_id=case_obj.connector_id,
            spec=case_obj.spec,
            docs_ref=case_obj.docs_ref,
            visible_tests=tuple(getattr(inputs, "visible_tests", ()) or ()),
            seed=int(getattr(inputs, "seed", 0) or 0),
        )
    res = run_baseline_arm(
        inputs, driver=driver or frontier_agent_driver(), arm="frontier_agent", context=context
    )
    try:
        from autobridge.config import frontier_api_key, frontier_model  # noqa: PLC0415

        if frontier_api_key() and getattr(res, "ran", False):
            expected = frontier_model()
            actual = getattr(res, "model", None) or ""
            # REQ-060 item 4: the run reports the model it actually used
            # (session row, ``provider/model`` form); it matches when the
            # bare ids agree. "unknown"/"" still pass (the run said nothing).
            if actual.strip() and _norm_model_id(actual) not in ("unknown", "") and _norm_model_id(actual) != _norm_model_id(expected):
                import dataclasses as _dataclasses  # noqa: PLC0415

                return _dataclasses.replace(
                    res,
                    ran=False,
                    outcome="model_mismatch",
                    error=(
                        f"model mismatch: frontier_agent ran {actual!r}, "
                        f"expected FRONTIER_MODEL={expected!r} "
                        "(set OPENCODE_MODEL to the same provider/model)"
                    )[:1000],
                )
    except Exception:
        pass
    return res


@dataclass(frozen=True, slots=True)
class DerivedDiagnostics:
    """What the engine itself observed for one scenario × trial.

    Produced by :func:`derive_diagnostics` from the served fault only —
    never from case labels. ``drift_class`` comes from the classifier over
    real sync signals, ``fingerprint_delta`` from the detector (faulted vs
    baseline observation), ``hypotheses`` from the engine's own hypothesis
    enumerator over the vendor docs.
    """

    drift_class: str
    fingerprint_delta: Any
    signals: tuple[Any, ...]
    hypotheses: tuple[Any, ...] = ()
    classification_reason: str = ""


def derive_from_summaries(
    spec: dict[str, Any],
    *,
    baseline_summary: dict[str, Any],
    faulted_summary: dict[str, Any],
) -> DerivedDiagnostics:
    """Detect + classify from two sync observations (no case labels in).

    Shared core for ablation (served sim scenarios) and beacon (served
    public instances): the detector diffs the faulted observation against
    the baseline observation, and the classifier names the drift class
    from real sync signals.
    """
    from autobridge.drift.classifier import classify  # noqa: PLC0415
    from autobridge.drift.fingerprint import compute_fingerprint, diff_fingerprints  # noqa: PLC0415
    from autobridge.drift.signals import signals_from_sync, spec_with_baseline_wire_keys  # noqa: PLC0415

    prev_fp = compute_fingerprint(
        spec, baseline_summary.get("operation_results", {}), baseline_summary.get("canonical_records", {})
    )
    curr_fp = compute_fingerprint(
        spec, faulted_summary.get("operation_results", {}), faulted_summary.get("canonical_records", {})
    )
    delta = diff_fingerprints(prev_fp, curr_fp)
    # Detection-only scoping (REQ-028): the pre-drift spec never went
    # through a build probe, so scope its wire-schema detection to the keys
    # the baseline observation actually served — the same scoping the live
    # build probe provides. Derived from observed responses only. The
    # repair itself still runs on the unannotated equal-input spec.
    detection_spec = spec_with_baseline_wire_keys(spec, baseline_summary.get("operation_results", {}))
    signals = signals_from_sync(
        faulted_summary, spec=detection_spec, previous_fingerprint=prev_fp, current_fingerprint=curr_fp
    )
    classification = classify(signals)
    return DerivedDiagnostics(
        drift_class=classification.drift_class,
        fingerprint_delta=delta,
        signals=tuple(signals),
        classification_reason=classification.reason,
    )


def derive_diagnostics(
    spec: dict[str, Any],
    *,
    scenario_id: str,
    connector_id: str,
    seed: int,
) -> DerivedDiagnostics:
    """Sync against the served fault; detect; classify (no case labels in).

    The real end-to-end path: the fault for ``scenario_id`` is served
    (:mod:`bench.faults`), the pre-drift ``spec`` syncs against it and
    against the baseline shape, and :func:`derive_from_summaries`
    detects + classifies. Nothing here reads the case catalog beyond the
    spec/docs sources the trial resolved.
    """
    from autobridge.engine.sync_run import execute_sync  # noqa: PLC0415
    from bench.faults import fake_env, fault_client_for  # noqa: PLC0415

    with fake_env():
        baseline_summary = execute_sync(spec, fault_client_for("baseline", seed=seed, connector_id=connector_id))
        faulted_summary = execute_sync(
            spec, fault_client_for(scenario_id, seed=seed, connector_id=connector_id)
        )
    return derive_from_summaries(spec, baseline_summary=baseline_summary, faulted_summary=faulted_summary)


def enumerate_hypotheses(*, docs_ref: str, scenario_id: str) -> tuple[Any, ...]:  # noqa: ARG001 - scenario labels the docs read, not a diagnostic
    """Enumerate repair hypotheses with the engine's own hypothesis engine.

    Docs-driven (W5): read the vendor docs, classify them, enumerate
    across all families. Deterministic, no model, no network. Returns ()
    when docs cannot be read — the arm then escalates without a climb
    instead of guessing.
    """
    from autobridge.discovery.classifiers import classify_all  # noqa: PLC0415
    from autobridge.discovery.docs_reader import read_docs_text  # noqa: PLC0415
    from autobridge.discovery.hypotheses import HypothesisEnumerator  # noqa: PLC0415

    try:
        text = (REPO_ROOT / docs_ref).read_text(encoding="utf-8")
        iface = read_docs_text(text, docs_ref)
        classification = classify_all(iface)
        return tuple(HypothesisEnumerator().enumerate_all(iface, classification))
    except Exception:
        return ()


def pre_drift_spec_for(scenario_id: str) -> dict[str, Any]:
    """Pre-drift spec every arm receives for *scenario_id* (equal input).

    The same resolver serves frontier and engine arms: the frontier arms
    build from the docs, the engine arms repair this spec. The resolved
    spec is task setup, not a diagnosis.
    """
    return dict(ablation_case_for_scenario(scenario_id).spec)


def assert_arm_task_setup_equal(named: dict[str, dict[str, Any]]) -> None:
    """Every arm resolved the same pre-drift spec (self-test).

    Extends the matched-budget contract: any arm holding a different
    starting spec fails loudly, naming the arm pair.
    """
    import json as _json

    names = list(named)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a = _json.dumps(named[names[i]], sort_keys=True, default=str)
            b = _json.dumps(named[names[j]], sort_keys=True, default=str)
            if a != b:
                raise AssertionError(
                    f"ablation arms {names[i]!r} vs {names[j]!r} resolved different pre-drift specs"
                )


def _default_derive(
    spec: dict[str, Any], case: AblationCase, inputs: Any
) -> DerivedDiagnostics:
    """Default arm serving: ablation sim scenarios (equal inputs only)."""
    return derive_diagnostics(
        spec,
        scenario_id=case.scenario_id,
        connector_id=case.connector_id,
        seed=int(getattr(inputs, "seed", 0) or 0),
    )


def run_engine_arm(
    inputs: Any,
    case: AblationCase,
    *,
    ablation: Literal["engine_no_knowledge", "autobridge_cold", "autobridge_warm"],
    provider: Any | None = None,
    knowledge: Any | None = None,
    session: Any | None = None,
    derive: Callable[[dict[str, Any], AblationCase, Any], DerivedDiagnostics] | None = None,
) -> Any:
    """Run an engine arm (c/d/e) end to end through RepairController.

    The real path, with no case-supplied diagnostics: sync the pre-drift
    spec against the served fault → detector → classifier → the engine's
    own hypothesis enumerator → ladder → verifier gate (grading). Phase 1
    runs the deterministic ladder (never a model call); phase 2 climbs
    with the shared frontier provider only when the derived drift class
    is semantically ambiguous (``SEMANTIC_VALUE_DRIFT``) and the
    enumerator yielded hypotheses.

    ``derive`` overrides the serving step (ablation serves sim scenarios;
    the beacon run serves public instances) — it must derive from equal
    inputs only (spec + public serving context), never from diagnostics.
    The ladder, verifier gate and budgets are kept for every engine arm;
    only the knowledge differs: ``engine_no_knowledge`` disables L0+L1,
    ``autobridge_cold`` starts from an empty cache, ``autobridge_warm``
    reuses the caller-supplied (dev-split-learned) cache.
    """
    from autobridge.llm.provider import Budget  # noqa: PLC0415
    from autobridge.repair.controller import RepairController  # noqa: PLC0415
    from autobridge.repair.knowledge import KnowledgeCache  # noqa: PLC0415
    from bench.harness import ArmResult  # noqa: PLC0415

    if ablation not in ENGINE_ARMS:
        raise ValueError(f"unknown engine ablation {ablation!r}")
    cache = knowledge if knowledge is not None else KnowledgeCache()
    ctrl = RepairController(knowledge_cache=cache)
    token_ceiling = int(getattr(inputs, "token_ceiling", 0) or 0)
    wall_clock = float(getattr(inputs, "wall_clock_s", 0) or 300.0)
    # REQ-060 item 3: token_ceiling 0 = no limit (Budget normalizes 0 to
    # unlimited); the wall clock stays.
    budget = Budget(max_frontier_tokens=token_ceiling, max_small_tokens=token_ceiling, max_wall=wall_clock)
    start = time.monotonic()
    try:
        # Observe the fault: sync → detect → classify (engine-derived).
        # The default serves ablation sim scenarios; beacon passes its own
        # public-descriptor serving. Either way: observation, never labels.
        derive_fn = derive or _default_derive
        diag = derive_fn(case.spec, case, inputs)
        run_id = f"ablation-{ablation}-{case.scenario_id}-{getattr(inputs, 'trial', 0)}"
        common = dict(
            spec=case.spec,
            drift_class=diag.drift_class,
            signals=list(diag.signals),
            fingerprint_delta=diag.fingerprint_delta,
            extra={},
            vendor=None,
            session=session,
            budget=budget,
            scenario=case.scenario_id,
            run_id=run_id,
            enable_knowledge=ablation != "engine_no_knowledge",
            enable_rules=ablation != "engine_no_knowledge",
        )
        # Phase 1: deterministic ladder first (never a model call).
        result = ctrl.handle(provider=None, hypotheses=None, enumerator_yield_count=None, **common)
        # Phase 2: model climb only for derived semantic ambiguity with
        # engine-enumerated hypotheses (deterministic first, models last).
        if result.decision != "propose" and diag.drift_class in _MODEL_AMBIGUITY_CLASSES:
            hyps = enumerate_hypotheses(docs_ref=case.docs_ref, scenario_id=case.scenario_id)
            if hyps:
                active_provider = provider or ablation_provider()
                result = ctrl.handle(
                    provider=active_provider,
                    hypotheses=list(hyps),
                    enumerator_yield_count=len(hyps),
                    **common,
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
        # REQ-046: the engine arm holds the same shared context bundle the
        # frontier arms render (schema, capabilities, different-vendor
        # example, docs, visible tests) — record its hash so the
        # fair-context test proves all arms agree. Engine arms render no
        # bench prompt (deterministic ladder; model calls use the engine's
        # own l2/l3 file prompts), so prompt stays None.
        engine_context_hash: str | None = None
        try:
            from bench.context import build_bench_context as _build_ctx  # noqa: PLC0415

            engine_context_hash = _build_ctx(
                scenario_id=case.scenario_id,
                connector_id=case.connector_id,
                spec=case.spec,
                docs_ref=case.docs_ref,
                visible_tests=tuple(getattr(inputs, "visible_tests", ()) or ()),
                seed=int(getattr(inputs, "seed", 0) or 0),
            ).content_hash()
        except Exception:
            engine_context_hash = None
        return ArmResult(
            arm=ablation,
            inputs=inputs,
            ran=result.decision in ("propose", "escalate"),
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
            prompt=None,
            context_hash=engine_context_hash,
        )
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000.0
        return ArmResult(
            arm=ablation,
            inputs=inputs,
            ran=False,
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
    from bench.harness import assert_inputs_equal  # noqa: PLC0415

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
    from bench.harness import make_trial_inputs  # noqa: PLC0415

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
    prov = provider if provider is not None else ablation_provider()
    # REQ-046: bench prompts load once at bench start (file edit + rerun
    # to change); every arm renders the shared context from bench.context.
    from bench.bench_prompts import ensure_bench_prompts_loaded  # noqa: PLC0415

    ensure_bench_prompts_loaded()

    defaults: dict[str, Callable[[Any], Any]] = {
        "frontier_oneshot": lambda inp: run_frontier_oneshot_arm(inp, provider=prov, case=case_obj),
        "frontier_agent": lambda inp: run_frontier_agent_arm(inp, case=case_obj),
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
            from bench.harness import ArmResult  # noqa: PLC0415

            res = ArmResult(arm=arm, inputs=per_arm[arm], ran=False, error=f"{type(exc).__name__}: {exc}"[:1000])  # type: ignore[arg-type]
        # Allow dict-like runners; coerce.
        if not hasattr(res, "arm"):
            from bench.harness import ArmResult  # noqa: PLC0415

            res = ArmResult(arm=arm, inputs=per_arm[arm], ran=bool(res))  # type: ignore[arg-type]
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


def warm_provenance(cache: Any) -> dict[str, Any]:
    """What the warm cache learned and how (REQ-030 2b provenance).

    Every entry records the approval that taught it (approval id, actor,
    release id) because ``warm_knowledge`` learns through the real approve
    path. The harness summary and the learning curve carry this so the
    warm arm's provenance says how its knowledge was learned.
    """
    entries = []
    try:
        stored = cache.all_entries() if hasattr(cache, "all_entries") else []
    except Exception:
        stored = []
    for entry in stored:
        entries.append(
            {
                "vendor": getattr(entry, "vendor", ""),
                "drift_class": getattr(entry, "drift_class", ""),
                "rule_id": getattr(entry, "rule_id", ""),
                "approval_id": getattr(entry, "approval_id", ""),
                "actor": getattr(entry, "actor", ""),
                "release_id": getattr(entry, "release_id", ""),
            }
        )
    return {"learned_via": "approve_release", "fixes": entries}


def warm_knowledge(
    scenario_ids: list[str],
    *,
    provider: Any | None = None,
    verifier_url: str | None = None,
    verify: Any | None = None,
) -> Any:
    """Learn on the dev split only: run each fixable case once, keep cache.

    Returns the :class:`KnowledgeCache` the ``autobridge_warm`` arm uses.
    Only dev-split scenarios are learned (never held-out). REQ-030 2b: a
    fix is learned only once it is approved — each proposed dev-split fix
    is verified (``verify`` callable or ``verifier_url``, same verdict the
    grading uses) and then approved through the real ``approve_release``
    path with this cache, so the entry records its approval. Unverified
    fixes are never learned: without a verifier the warm arm stays cold
    (provenance says so; warm is never worse than cold).
    """
    from autobridge.repair.knowledge import KnowledgeCache  # noqa: PLC0415
    from bench.harness import HarnessConfig, make_trial_inputs  # noqa: PLC0415

    cache = KnowledgeCache()
    prov = provider or ablation_provider()
    cfg = HarnessConfig()
    for sid in scenario_ids:
        try:
            case = ablation_case_for_scenario(sid)
        except (KeyError, ValueError, RuntimeError):
            continue
        session = _mem_session()
        try:
            res = run_engine_arm(
                make_trial_inputs(cfg, sid, 0),
                case,
                ablation="autobridge_cold",
                provider=prov,
                knowledge=KnowledgeCache(),
                session=session,
            )
            spec = getattr(res, "spec", None) or {}
            if spec.get("decision") != "propose" or not spec.get("candidate_patch"):
                continue  # nothing verified to learn (escalation/backoff)
            # The arm's RepairRun row (persisted by the controller) carries
            # the vendor, drift class and delta the approve path learns.
            from sqlalchemy import select as _select  # noqa: PLC0415

            from autobridge.control.models import RepairRun as _RepairRun  # noqa: PLC0415

            repair = (
                session.execute(_select(_RepairRun).order_by(_RepairRun.created_at.desc()).limit(1))
                .scalars()
                .first()
            )
            if repair is None:
                continue
            _approve_warm_fix(
                session,
                sid,
                case,
                {
                    "candidate_patch": list(spec.get("candidate_patch") or []),
                    "rule_id": spec.get("rule_id", ""),
                    "repair_run_id": repair.id,
                    "vendor": repair.vendor or "unknown",
                },
                cache,
                verifier_url=verifier_url,
                verify=verify,
            )
            session.commit()
        except Exception:
            continue
        finally:
            session.close()
    return cache


def _approve_warm_fix(
    session: Any,
    scenario_id: str,
    case: Any,
    arm_spec: dict[str, Any],
    cache: Any,
    *,
    verifier_url: str | None = None,
    verify: Any | None = None,
) -> dict[str, Any]:
    """Verify then approve one dev-split fix into the warm cache.

    Raises when the fix is not verified (unreachable verifier, failed
    verdict) — the caller skips learning and the provenance shows why.
    """
    from autobridge.control.models import Connector, ConnectorRelease  # noqa: PLC0415
    from autobridge.engine.releases import approve_release, create_release_from_patch  # noqa: PLC0415
    from autobridge.spec.patch import apply_patch  # noqa: PLC0415

    patch = list(arm_spec.get("candidate_patch") or [])
    patched = apply_patch(dict(case.spec), patch)
    if verify is None and verifier_url:
        from bench.scoring import normalize_verify_response, verify_via_http  # noqa: PLC0415

        def _via_http(spec: Any, sid: str) -> bool:
            payload = verify_via_http(spec, sid, verifier_url=verifier_url)
            outcome = normalize_verify_response(payload if isinstance(payload, dict) else {})
            failed = getattr(outcome, "failed_checks", None)
            names = [f.get("name") for f in failed] if isinstance(failed, list) else []
            return bool(getattr(outcome, "passed", False)) and not any(
                isinstance(n, str) and "verifier_unreachable" in n for n in names
            )

        verify = _via_http
    if verify is None:
        raise RuntimeError(f"warm learning needs a verifier (no verifier_url for {scenario_id})")
    if not verify(patched, scenario_id):
        raise RuntimeError(f"warm learning skipped: verifier failed the {scenario_id} fix")
    repair_run_id = arm_spec.get("repair_run_id", "")
    vendor = arm_spec.get("vendor", "") or "unknown"
    conn = Connector(
        name=f"warm-{scenario_id}",
        base_url="http://warm.invalid",
        credential_ref="WARM_TOKEN",
        target_entities=[],
        runtime_target={"kind": "local_process"},
    )
    # Vendor on the connector record feeds nothing here; the repair row's
    # vendor (from the arm path) is the attribution the cache learns.
    session.add(conn)
    session.flush()
    base = ConnectorRelease(connector_id=conn.id, version=1, spec=dict(case.spec), signature="0" * 64)
    session.add(base)
    session.flush()
    candidate = create_release_from_patch(
        session,
        connector_id=conn.id,
        base_release_id=base.id,
        patch=patch,
        provenance_repair_run_id=repair_run_id or None,
        verifier_url=None,  # already verifier-judged above; the gate is the grading path
    )
    session.flush()
    approval = approve_release(
        session,
        repair_run_id=repair_run_id,
        actor="bench:warm",
        from_release_id=base.id,
        to_release_id=candidate.id,
        connector_id=conn.id,
        knowledge_cache=cache,
    )
    session.flush()
    return {
        "scenario_id": scenario_id,
        "vendor": vendor,
        "rule_id": arm_spec.get("rule_id", ""),
        "approval_id": approval.id,
        "actor": approval.actor,
        "release_id": candidate.id,
    }


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
    # REQ-052 item 3: every frontier attempt graded with the reason.
    produced: int = 0  # 1 iff the arm produced a patch/escalation/spec at all
    applied: int = 0  # 1 iff it applied (patch/proposal applied, spec validated)
    verdict: str | None = None  # "pass" | "fail" | None (no verifier / no output)
    failed_checks: tuple[str, ...] = ()  # verifier failed-check names
    outcome_label: str | None = None  # truncated|timed_out|no_output|escalated|unverified|error|None


def _looks_like_full_spec(spec: Any) -> bool:
    """A produced ConnectorSpec (not an engine candidate-patch payload)."""
    return isinstance(spec, dict) and (
        "operations" in spec or "spec_version" in spec or "connector" in spec
    )


def _spec_applies(spec: Any, case_spec: dict[str, Any] | None) -> int:
    """1 iff the arm's output applied: patch applies, or spec validates.

    REQ-060: a direct ``candidate_patch`` decides first (it is what the
    verifier judges); an escalation's ``proposal_patch`` is checked only
    when there is no direct patch. A full produced spec validates
    against the schema (legacy whole-spec answers).
    """
    if not isinstance(spec, dict):
        return 0
    direct = spec.get("candidate_patch")
    if isinstance(direct, list) and direct:
        if case_spec is None:
            return 0
        try:
            from autobridge.spec.patch import apply_patch  # noqa: PLC0415

            apply_patch(dict(case_spec), list(direct))
            return 1
        except Exception:
            return 0
    proposal = spec.get("proposal_patch")
    if isinstance(proposal, list) and proposal:
        if case_spec is None:
            return 0
        try:
            from autobridge.spec.patch import apply_patch  # noqa: PLC0415

            apply_patch(dict(case_spec), list(proposal))
            return 1
        except Exception:
            return 0
    if _looks_like_full_spec(spec):
        try:
            from autobridge.spec.models import ConnectorSpec  # noqa: PLC0415

            ConnectorSpec(**spec)
            return 1
        except Exception:
            return 0
    return 0


def grade_ablation_arm(
    arm: str,
    trial: AblationTrialResult,
    outcome: Any | None,
    *,
    expected: str,
    produced: int | None = None,
    applied: int | None = None,
    outcome_label: str | None = None,
) -> AblationGrade:
    """Grade one arm from its result plus the verifier outcome.

    *outcome* is a :class:`scoring.VerifyOutcome` (or None when the
    verifier was unreachable — scored as not verified, never guessed).
    *expected* is the scenario's correct outcome class: ``"repair"``
    (verifier-accepted patch), ``"no_patch"`` (escalate, backoff-only or
    refresh with no spec change released) or ``"degraded_question"``
    (Degraded with one operator question, no patch).

    REQ-052 + REQ-060: *produced* (did it produce a patch, an
    escalation or a spec at all), *applied* (did the patch/proposal
    apply, or the spec validate) and the verifier *verdict* + failed
    checks land on the grade. A frontier patch is applied to the
    pre-drift spec and goes to the verifier like an engine patch; an
    escalation on an unfixable scenario counts as held back, on a
    fixable one as not fixed. Every arm × scenario row ends with a
    verdict or an explicit label
    (truncated/timed_out/no_output/escalated/unverified/error).
    """
    res: _ArmResult | None = trial.results.get(arm)
    verified = 0
    wrong_release = 0
    verdict: str | None = None
    failed_names: tuple[str, ...] = ()
    if outcome is not None and res is not None:
        passed = bool(getattr(outcome, "passed", False))
        regression_failed = bool(getattr(outcome, "regression_failed", False))
        verified = 1 if (res.ran and passed and not regression_failed) else 0
        wrong_release = 1 if (passed and regression_failed) else 0
        verdict = "pass" if passed else "fail"
        failed = getattr(outcome, "failed_checks", None)
        if isinstance(failed, list):
            failed_names = tuple(
                str(f.get("name")) for f in failed if isinstance(f, dict) and f.get("name")
            )
    patch: list[Any] = []
    arm_spec = getattr(res, "spec", None) if res is not None else None
    answer_kind: str | None = None
    if isinstance(arm_spec, dict):
        answer_kind = arm_spec.get("answer_kind")
        raw_patch = arm_spec.get("candidate_patch")
        if isinstance(raw_patch, list) and raw_patch:
            patch = raw_patch
    if produced is None:
        # REQ-060: a frontier patch, a frontier escalation, or a legacy
        # whole spec all count as produced attempts; silence does not.
        # An explicit (even empty) patch answer counts — the arm answered
        # the repair question.
        produced = 1 if (patch or (answer_kind in ("patch", "escalate")) or _looks_like_full_spec(arm_spec)) else 0
    if applied is None:
        case_spec: dict[str, Any] | None = None
        try:
            case_spec = dict(ablation_case_for_scenario(trial.scenario_id).spec)
        except Exception:
            case_spec = None
        applied = _spec_applies(arm_spec, case_spec) if produced else 0
    if expected == "repair":
        correct_esc = 1 if verified else 0
    else:
        # REQ-060 item 2: an escalation on an unfixable scenario counts
        # as held back; a patch where none belongs does not (and a
        # verifier-passing patch there is still not a hold-back).
        correct_esc = 1 if (answer_kind == "escalate" or produced == 0) else 0
    label = outcome_label or (res.outcome if res is not None else None)
    if label is None and verdict is None:
        label = "unverified" if (res is not None and res.ran) else "error"
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
        produced=produced,
        applied=applied,
        verdict=verdict,
        failed_checks=failed_names,
        outcome_label=label,
    )


def grade_ablation_trial(trial: AblationTrialResult, *, verifier_url: str | None = None) -> list[AblationGrade]:
    """Grade all arms of one trial (verifier-judged only, never guessed).

    Engine arms that released a non-empty candidate patch are verified
    over HTTP (patched spec = case spec + patch); frontier arms that
    produced a JSON Patch are applied to the pre-drift spec and verified
    the same way (REQ-060 item 2: graded like an engine patch); legacy
    full-spec answers are verified directly. An escalation carries no
    patch, so it is never verified — on an unfixable scenario it counts
    as held back, on a fixable one as not fixed. Every other arm gets
    outcome None. A ``verifier_unreachable`` transport failure degrades
    that arm to unverified rather than fail-guessed.

    REQ-052: the verdict + failed checks are written back onto the
    trial's arm rows, so every arm × scenario row carries a verdict or
    an explicit label (truncated/timed_out/no_output/unverified/error).
    """
    import dataclasses as _dataclasses  # noqa: PLC0415

    from bench.scoring import normalize_verify_response, verify_via_http  # noqa: PLC0415

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
        full_spec: dict[str, Any] | None = None
        spec = getattr(res, "spec", None) if res is not None else None
        if isinstance(spec, dict):
            raw_patch = spec.get("candidate_patch")
            if isinstance(raw_patch, list) and raw_patch:
                patch = raw_patch
            elif _looks_like_full_spec(spec):
                full_spec = spec
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
        elif full_spec is not None and verifier_url:
            # REQ-052 item 3: the frontier attempt is graded like any
            # other — the produced spec goes to the verifier, whose
            # verdict + failed checks name why it passed or failed.
            try:
                payload = verify_via_http(full_spec, trial.scenario_id, verifier_url=verifier_url)
                failed = payload.get("failed_checks") if isinstance(payload, dict) else None
                names = [f.get("name") for f in failed] if isinstance(failed, list) else []
                if not any(isinstance(n, str) and "verifier_unreachable" in n for n in names):
                    outcome = normalize_verify_response(payload if isinstance(payload, dict) else {})
            except Exception:
                outcome = None
        grades.append(grade_ablation_arm(arm, trial, outcome, expected=expected))
    # Write verdicts + labels back onto the trial rows (the JSONL row is
    # what the per-scenario table reads; grades carry the aggregates).
    for grade in grades:
        res = trial.results.get(grade.arm)
        if res is None:
            continue
        trial.results[grade.arm] = _dataclasses.replace(
            res,
            verdict=grade.verdict,
            failed_checks=[{"name": name} for name in grade.failed_checks] or None,
            outcome=grade.outcome_label or res.outcome,
        )
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
    for row in describe_arm_availability():
        print(f"bench harness: arm {row['arm']}: {row['mode']}", flush=True)
    # REQ-061 item 4: one live opencode run must return text, the model id
    # and token usage before the agent rows run — otherwise stop here with
    # the reason instead of filling the table with empty rows.
    preflight_frontier_agent()
    warm = warm_knowledge(
        list(warm_scenario_ids if warm_scenario_ids is not None else scenario_ids),
        provider=prov,
        verifier_url=verifier_url,
    )
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
    # REQ-052 item 3: per-scenario table of what each frontier arm
    # produced and why it failed or passed (verdict + failed checks, or
    # the explicit truncated/timed_out/no_output label).
    by_grade = {(g.arm, g.scenario_id, g.trial): g for g in grades}
    detail: list[dict[str, Any]] = []
    for trial in trials:
        for name in FRONTIER_ARMS:
            res = trial.results.get(name)
            if res is None:
                continue
            grade = by_grade.get((name, trial.scenario_id, trial.trial))
            detail.append(
                {
                    "scenario_id": trial.scenario_id,
                    "trial": trial.trial,
                    "arm": name,
                    "model": getattr(res, "model", None),
                    "ran": bool(getattr(res, "ran", False)),
                    "produced": grade.produced if grade else None,
                    "applied": grade.applied if grade else None,
                    "verdict": grade.verdict if grade else None,
                    "failed_checks": list(grade.failed_checks) if grade else [],
                    "outcome_label": (grade.outcome_label if grade else None)
                    or getattr(res, "outcome", None),
                    "frontier_tokens": int(getattr(res, "frontier_tokens", 0) or 0),
                    "error": getattr(res, "error", None),
                }
            )
    summary["frontier_detail"] = detail
    summary["verifier_url"] = verifier_url
    summary["trials"] = len(trials)
    summary["skipped_scenarios"] = sorted(skipped)
    summary["warm_provenance"] = warm_provenance(warm)
    # REQ-060 item 3: per-arm min / median / max total tokens so the
    # owner can pick a limit from the results.
    summary["total_tokens_stats"] = total_tokens_stats(trials)
    # REQ-046: bench prompt provenance on the summary too (id/version/hash
    # per frontier arm, shared context hash agreement across all arms).
    prompt_refs: dict[str, Any] = {}
    context_by_arm: dict[str, Any] = {}
    for trial in trials:
        for name, res in trial.results.items():
            if name not in prompt_refs and getattr(res, "prompt", None) is not None:
                prompt_refs[name] = res.prompt
            if name not in context_by_arm and getattr(res, "context_hash", None) is not None:
                context_by_arm[name] = res.context_hash
    summary["prompts"] = prompt_refs
    summary["context_hashes"] = context_by_arm
    summary["context_matched"] = len(set(context_by_arm.values())) <= 1
    return trials, grades, summary


def total_tokens_stats(trials: list[AblationTrialResult]) -> dict[str, dict[str, Any]]:
    """Per-arm min / median / max total tokens (REQ-060 item 3).

    Totals come from the recorded rows (input + output per arm row), so
    the owner can pick a token limit from the results. Reasoning tokens
    ride per row on the trial records.
    """
    totals_by_arm: dict[str, list[float]] = {}
    for trial in trials:
        for name, res in trial.results.items():
            totals_by_arm.setdefault(name, []).append(
                float(int(getattr(res, "input_tokens", 0) or 0) + int(getattr(res, "output_tokens", 0) or 0))
            )
    return {
        arm: {
            "min": min(values),
            "median": statistics.median(values),
            "max": max(values),
            "n": len(values),
        }
        for arm, values in sorted(totals_by_arm.items())
        if values
    }


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
        "arm_availability": describe_arm_availability(),
        # REQ-052 item 3: per-arm quality — fixable fixed / unfixable
        # held back / wrong patches shipped, plus truncated / timed_out /
        # no_output counts, so the scorecard measures the model, not the
        # harness. Expected classes come from the grading-only catalog
        # (arms never see them).
        "quality": {
            arm: _quality_block([g for g in grades if g.arm == arm]) for arm in ABLATION_ARMS
        },
    }


def _quality_block(items: list[AblationGrade]) -> dict[str, Any]:
    """Counts for one arm: fixed / held back / wrong + label counts."""
    fixable = [g for g in items if _expected_for(g.scenario_id) == "repair"]
    unfixable = [g for g in items if _expected_for(g.scenario_id) != "repair"]
    labels = [g.outcome_label or "" for g in items]
    return {
        "fixable_total": len(fixable),
        "fixable_fixed": sum(g.verified_completion for g in fixable),
        "unfixable_total": len(unfixable),
        "unfixable_held_back": sum(g.correct_escalation for g in unfixable),
        "wrong_patches_shipped": sum(g.wrong_patch_released for g in items),
        "produced": sum(g.produced for g in items),
        "applied": sum(g.applied for g in items),
        "escalated": sum(1 for label in labels if label == "escalated"),
        "truncated": sum(1 for label in labels if label == "truncated"),
        "timed_out": sum(1 for label in labels if label == "timed_out"),
        "no_output": sum(1 for label in labels if label == "no_output"),
        "unverified": sum(1 for label in labels if label == "unverified"),
        "error": sum(1 for label in labels if label in ("error", "budget_exhausted", "model_mismatch")),
        "n": len(items),
    }


def _expected_for(scenario_id: str) -> str:
    """Grading-only expected class; "no_patch" when the scenario is unknown."""
    try:
        return expected_outcome_for(scenario_id)
    except (KeyError, ValueError, RuntimeError):
        return "no_patch"


def render_frontier_quality_table(summary: dict[str, Any]) -> str:
    """Per-arm quality table (REQ-052 item 3): fixed / held back / labels."""
    quality = summary.get("quality", {})
    lines = [
        "| arm | fixable fixed | unfixable held back | wrong shipped | produced | applied | truncated | timed_out | no_output |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for arm in ABLATION_ARMS:
        block = quality.get(arm, {})
        lines.append(
            f"| {arm} | {block.get('fixable_fixed', '—')}/{block.get('fixable_total', '—')} | "
            f"{block.get('unfixable_held_back', '—')}/{block.get('unfixable_total', '—')} | "
            f"{block.get('wrong_patches_shipped', '—')} | {block.get('produced', '—')} | "
            f"{block.get('applied', '—')} | {block.get('truncated', '—')} | "
            f"{block.get('timed_out', '—')} | {block.get('no_output', '—')} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Learning curve: same drift 5x in a row (REQ-024 B4)
# ---------------------------------------------------------------------------


def _is_fake_driver(driver: Any) -> bool:
    """A rehearsal driver (never a live model call)."""
    name = type(driver).__name__
    if "Fake" in name:
        return True
    try:
        return str(getattr(driver, "default_model", "") or "").startswith("fake-")
    except Exception:
        return False


def run_learning_curve(
    scenario_id: str = "page_to_cursor",
    *,
    repeats: int = 5,
    provider: Any | None = None,
    seed: int = 42,
    verifier_url: str | None = None,
    verify: Any | None = None,
    driver: Any | None = None,
) -> dict[str, Any]:
    """Inject the same drift ``repeats`` times; frontier tokens per repair.

    Arms: ``frontier_agent`` (fake driver pays every time), engine arms
    ``autobridge_cold`` (empty knowledge) and ``autobridge_warm``
    (pre-warmed on the dev split through the approve path). Rows carry
    frontier tokens, the rung reached and the decision. Honest end-to-end
    (REQ-027 sub-step 2): the arms derive everything from observation.
    REQ-030 2b: proposing caches nothing, so cold repeats re-derive at L1
    (0 model tokens each) until an approval teaches the fix — L0 hits
    only after approval. Warm hits L0 from the start only when its dev-split
    fixes verified (``verifier_url`` or ``verify``); without a verifier it
    stays cold and the provenance says so.
    Returns the raw rows plus per-arm totals; :func:`write_learning_curve_md`
    renders the file. Fake-labelled rows are rehearsal, not publication
    numbers: rerun with ``FRONTIER_API_KEY`` set for live tokens.

    REQ-052 item 4: with ``FRONTIER_API_KEY`` set the agent column comes
    from the live agent driver (``frontier_agent_driver``), never the
    fake — passing an explicit fake driver on a keyed run raises
    ``RuntimeError``. Each repeat also records whether the agent's repair
    verified (``verifier_url`` or ``verify``; ``"unverified"`` otherwise).
    """
    from autobridge.config import frontier_api_key  # noqa: PLC0415
    from autobridge.llm.provider import Usage  # noqa: PLC0415
    from bench.baseline_coding_agent import FakeOpenCodeDriver  # noqa: PLC0415
    from bench.harness import HarnessConfig, make_trial_inputs  # noqa: PLC0415

    prov = provider or ablation_provider()
    if driver is None:
        driver = (
            frontier_agent_driver()
            if frontier_api_key()
            else FakeOpenCodeDriver(
                default_usage=Usage(input_tokens=500, output_tokens=1500),
                default_model="fake-opencode",
            )
        )
    elif frontier_api_key() and _is_fake_driver(driver):
        raise RuntimeError(
            "run_learning_curve: FRONTIER_API_KEY is set, so the agent column "
            "must come from the live agent driver, not "
            f"{type(driver).__name__} (rehearsal numbers would masquerade as live)"
        )
    verify_fn = verify
    if verify_fn is None and verifier_url:
        from bench.scoring import normalize_verify_response, verify_via_http  # noqa: PLC0415

        def verify_fn(spec: Any, sid: str) -> bool:
            payload = verify_via_http(spec, sid, verifier_url=verifier_url)
            outcome = normalize_verify_response(payload if isinstance(payload, dict) else {})
            failed = getattr(outcome, "failed_checks", None)
            names = [f.get("name") for f in failed] if isinstance(failed, list) else []
            return bool(getattr(outcome, "passed", False)) and not any(
                isinstance(n, str) and "verifier_unreachable" in n for n in names
            )

    case = ablation_case_for_scenario(scenario_id)
    cfg = HarnessConfig(seed=seed)
    from autobridge.repair.knowledge import KnowledgeCache  # noqa: PLC0415

    cold_cache: Any = KnowledgeCache()
    warm_cache: Any = warm_knowledge([scenario_id], provider=prov, verifier_url=verifier_url, verify=verify)
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
                    "frontier_agent_verified": _verify_agent_spec(agent_res, verify_fn, scenario_id),
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
            # REQ-060 item 5: every driver exposes default_model
            # (OpenCodeCliDriver included) — getattr, never a crash.
            "frontier_agent_driver": type(driver).__name__,
            "frontier_agent_model": getattr(driver, "default_model", getattr(driver, "model", None)),
            "engine_provider": type(prov).__name__,
            "engine_frontier_model": getattr(prov, "frontier_model", None),
            "warm": warm_provenance(warm_cache),
            "verifier_url": verifier_url,
        },
    }


def _verify_agent_spec(agent_res: Any, verify_fn: Any | None, scenario_id: str) -> Any:
    """Whether the agent's produced spec verified: True/False/"unverified".

    REQ-052 item 4 (quality first): the learning curve reports, per
    repeat, whether the agent repair verified — never a bare token
    count. ``"unverified"`` when no verifier is available or the agent
    produced no spec.
    """
    spec = getattr(agent_res, "spec", None)
    if not isinstance(spec, dict) or not spec or verify_fn is None:
        return "unverified"
    try:
        return bool(verify_fn(spec, scenario_id))
    except Exception:
        return "unverified"


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
        "`FRONTIER_API_KEY` set for live tokens (the harness refuses the fake driver when a key is set).",
        "",
        "Quality first: the agent_verified column says whether each agent repair verified",
        "(True/False; `unverified` when no verifier was available or no spec was produced).",
        "",
        "Expected (REQ-030 2b: proposing caches nothing): the agent pays",
        "model tokens on every repair (it has no memory across repairs).",
        "Cold repeats re-derive at L1 with 0 model tokens each — L0 hits",
        "only after an approval teaches the fix. Warm hits L0 from the",
        "start only when its dev-split fixes verified (see provenance);",
        "without a verifier it repeats cold exactly.",
        "",
        "| repeat | frontier_agent | autobridge_cold | autobridge_warm | agent_verified |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in result["rows"]:
        lines.append(
            f"| {row['repeat']} | {row['frontier_agent_frontier_tokens']} | "
            f"{row['autobridge_cold_frontier_tokens']} | {row['autobridge_warm_frontier_tokens']} | "
            f"{row.get('frontier_agent_verified', 'unverified')} |"
        )
    lines += [
        "",
        "Rung reached per repeat (learning is the route once the detector",
        "gap closes: first repair via the ladder, then L0 hits):",
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
    verified = sum(1 for row in result["rows"] if row.get("frontier_agent_verified") is True)
    lines += [
        f"Agent repairs verified: {verified}/{result['repeats']} "
        "(`unverified` rows had no verifier or no produced spec).",
        "",
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# CLI: python -m bench.ablation_curve (make bench-ablation)
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
