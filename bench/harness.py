"""Benchmark harness — matched-budget per scenario × trial (P1.W8.1, plan §2.10).

For each scenario × trial the harness runs two arms with **identical** inputs:

* **AutoBridge** via the control API (``POST /connectors`` → ``/build`` → ``/sync``
  → ``/verify``).
* **Baseline** inside the *same* ``runtime-sandbox`` image (``autobridge-bench``
  or ``runtime-sandbox`` container) with the same docs volume, sandbox URL,
  credentials (refs only), visible tests, token ceiling and wall-clock.

A harness self-test :func:`assert_inputs_equal` proves both arms received
byte-identical :class:`HarnessInputs` for the same trial.  :func:`run_trial`
calls it before executing either arm; :func:`verify_matched_trial` is the
post-hoc audit used by scoring.

Design notes
------------
* Secrets are never in :class:`HarnessInputs` — only ``credential_ref``
  (env key names) are carried; values are read at runtime from the
  environment inside each arm (DIRECTIVES §6).
* ``token_ceiling`` and ``wall_clock_s`` are the *global* budgets enforced
  by :class:`autobridge.llm.provider.Budget` for AutoBridge and by the CLI
  driver's ``timeout_s`` for the baseline — the harness passes the same value
  to both.
* The harness is deterministic from ``seed``: same seed →
  identical ``HarnessInputs`` and scenario seed.
* ``make bench`` idempotency (W8.5) is prepared here: results are written to
  ``experiments/benchmark_runs/<scenario>--<trial>.jsonl`` with the
  ``seed`` in the payload, and ``check_heldout_sha`` guards the heldout split.

Baseline drivers (P1.W8.2, plan §2.10 + §9)
--------------------------------------------
The baseline arm is pluggable via the drivers in
:mod:`bench.baseline_coding_agent`:

* ``claude_code_headless`` — :class:`ClaudeCodeDriver` drives
  ``claude -p <prompt> --output-format json`` with usage/token capture
  (``input_tokens`` / ``output_tokens`` / ``cost_usd`` / ``model``).
  Unit tests use :class:`FakeClaudeDriver` and ``scripts/fake_claude.py``
  (W1 fake) so ``make test`` never needs a real API key.
* ``codex_cli`` — :class:`CodexCliDriver` adapter stub for
  ``codex exec --json <prompt>`` with identical usage capture
  (``output`` / ``usage`` / ``model``).  Mirrors the Claude driver API;
  tests use :class:`FakeCodexDriver` / ``scripts/fake_codex.py``.
* **Fallback (documented, plan §9)** — when neither CLI is available
  (no binary, no key, offline CI) the baseline is routed through
  :class:`autobridge.llm.provider.LLMProvider` with an equivalent
  single-turn agent loop: ``BaselineLLMDriver`` (or
  :func:`run_baseline_via_llm_provider`) wraps the provider in a
  :class:`Budget` using the same ``token_ceiling`` / ``wall_clock_s``
  as AutoBridge, so the matched-budget self-test still holds.
  ``make bench`` selects the driver via ``BASELINE_DRIVER`` env
  (``opencode`` | ``claude`` | ``codex`` | ``llm``) or the ``get_baseline_driver()``
  factory — documented in ``docs/benchmark.md``.

  Both arms therefore receive identical
  ``docs_volume`` / ``sandbox_url`` / ``sandbox_image`` / ``credential_refs``
  / ``visible_tests`` / ``token_ceiling`` / ``wall_clock_s``, asserted by
  :func:`assert_inputs_equal` pre-run and :func:`verify_matched_trial`
  post-run.

Secondary diagnostic control — ``one_shot_generator`` (P1.W8.3, plan §2.10)
------------------------------------------------------------------------
:mod:`bench.one_shot` is the secondary control: a single
:class:`LLMProvider` completion (``OneShotGenerator``) that must produce a
``ConnectorSpec`` YAML in one pass, with the *same* :class:`HarnessInputs`
the other two arms receive.  It goes through :class:`Budget` /
:class:`BudgetedProvider` so token/wall ceilings are identical, and cost is
via :func:`estimate_cost` (Decimal).  Helpers
:func:`build_one_shot_prompt` / :func:`extract_spec_yaml` /
:func:`try_parse_spec_yaml` normalize prompt + YAML extraction;
:func:`run_one_shot_arm` returns ``ArmResult(arm="one_shot")`` for scoring;
:func:`run_trial_with_one_shot` runs all three arms under a three-way
matched audit.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Constants — defaults match compose + simulator catalog
# ---------------------------------------------------------------------------

DEFAULT_SANDBOX_IMAGE = "autobridge-runtime-sandbox"
DEFAULT_SANDBOX_URL = "http://runtime-sandbox:8000"
DEFAULT_SANDBOX_URL_HOST = "http://localhost:8103"  # host-mapped when running outside Docker
DEFAULT_DOCS_VOLUME = "simulators/docs"
DEFAULT_CREDENTIAL_REFS: tuple[str, ...] = ("ATLAS_API_KEY", "HARBOR_API_KEY")
DEFAULT_VISIBLE_TESTS: tuple[str, ...] = ("baseline",)
DEFAULT_TOKEN_CEILING = 20_000
#: REQ-052: the agent arm must have time to finish a real attempt, under
#: the same budget rule as every other arm — one wall-clock for all, so
#: the matched-budget self-test still holds. 600 s fits a live agent run;
#: keyed runs that need more pass --wall-clock (or BENCH_WALL_CLOCK).
DEFAULT_WALL_CLOCK_S = 600.0
DEFAULT_CONTROL_API_URL = "http://control:8000"
DEFAULT_CONTROL_API_URL_HOST = "http://localhost:8101"
DEFAULT_VERIFIER_URL = "http://verifier:8000"
DEFAULT_VERIFIER_URL_HOST = "http://localhost:8104"

#: Env var carrying the control-plane write token (REQ-012). Sent as
#: ``Authorization: Bearer <token>`` on control API writes when set.
CONTROL_TOKEN_ENV_VAR = "AUTOBRIDGE_CONTROL_TOKEN"


def control_headers() -> dict[str, str]:
    """Auth headers for control API calls (empty when no token is set)."""
    token = os.environ.get(CONTROL_TOKEN_ENV_VAR, "")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _is_in_container() -> bool:
    return pathlib.Path("/.dockerenv").exists() or pathlib.Path("/run/.containerenv").exists()


def _resolve_default_url(docker_url: str, host_url: str) -> str:
    return docker_url if _is_in_container() else host_url
DEFAULT_TRIALS_PER_SCENARIO = 1
DEFAULT_SEED = 42

# RuntimeTarget that both arms share (mirrors control/schemas.py ConnectorCreate).
DEFAULT_RUNTIME_TARGET: dict[str, Any] = {"kind": "docker_sandbox", "timeout_s": 60, "memory_mb": 512}


# ---------------------------------------------------------------------------
# Harness config — global harness settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    """Global harness settings shared by every scenario × trial."""

    sandbox_image: str = DEFAULT_SANDBOX_IMAGE
    sandbox_url: str = DEFAULT_SANDBOX_URL
    docs_volume: str = DEFAULT_DOCS_VOLUME
    credential_refs: tuple[str, ...] = DEFAULT_CREDENTIAL_REFS
    visible_tests: tuple[str, ...] = DEFAULT_VISIBLE_TESTS
    token_ceiling: int = DEFAULT_TOKEN_CEILING
    wall_clock_s: float = DEFAULT_WALL_CLOCK_S
    control_api_url: str = field(default_factory=lambda: _resolve_default_url(DEFAULT_CONTROL_API_URL, DEFAULT_CONTROL_API_URL_HOST))
    verifier_url: str = field(default_factory=lambda: _resolve_default_url(DEFAULT_VERIFIER_URL, DEFAULT_VERIFIER_URL_HOST))
    runtime_target: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_RUNTIME_TARGET))
    trials_per_scenario: int = DEFAULT_TRIALS_PER_SCENARIO
    seed: int = DEFAULT_SEED
    scenarios: tuple[str, ...] | None = None  # None = all dev scenarios

    def __post_init__(self) -> None:
        if self.token_ceiling <= 0:
            raise ValueError("token_ceiling must be > 0")
        if self.wall_clock_s <= 0:
            raise ValueError("wall_clock_s must be > 0")
        if self.trials_per_scenario <= 0:
            raise ValueError("trials_per_scenario must be > 0")


# ---------------------------------------------------------------------------
# Per-trial inputs — must be identical for both arms
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HarnessInputs:
    """Inputs passed to both arms for a single scenario × trial.

    Every field here is part of the matched-budget contract.  Both arms
    receive the *same* :class:`HarnessInputs` instance (or an equal copy).
    ``assert_inputs_equal`` is the harness self-test that enforces it.
    """

    scenario_id: str
    trial: int
    seed: int
    sandbox_image: str
    sandbox_url: str
    docs_volume: str
    docs_ref: str
    credential_refs: tuple[str, ...]
    visible_tests: tuple[str, ...]
    token_ceiling: int
    wall_clock_s: float
    control_api_url: str
    verifier_url: str
    runtime_target: dict[str, Any]

    def fingerprint(self) -> str:
        """Deterministic hash of the inputs (excluding scenario/trial/seed identity if desired).

        The fingerprint is over *all* fields so two inputs from the same
        scenario × trial are byte-identical iff their fingerprints match.
        """
        payload = {
            k: (tuple(v) if isinstance(v, tuple) else v)
            for k, v in dataclasses.asdict(self).items()
        }
        # Ensure runtime_target dict is stable
        payload["runtime_target"] = dict(sorted(self.runtime_target.items()))
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def budget_fields(self) -> dict[str, Any]:
        """Return the six budget-related fields that the harness self-test checks.

        These are the fields named in the task: docs volume, sandbox URL,
        credentials, visible tests, token ceiling, wall-clock (plus
        sandbox_image which is the same runtime-sandbox image).
        """
        return {
            "docs_volume": self.docs_volume,
            "docs_ref": self.docs_ref,
            "sandbox_url": self.sandbox_url,
            "sandbox_image": self.sandbox_image,
            "credential_refs": self.credential_refs,
            "visible_tests": self.visible_tests,
            "token_ceiling": self.token_ceiling,
            "wall_clock_s": self.wall_clock_s,
        }


def docs_ref_for_scenario(scenario_id: str, docs_volume: str = DEFAULT_DOCS_VOLUME) -> str:
    """Return the docs ref for *scenario_id*.

    The harness mounts *docs_volume* read-only into both arms.  The ref
    points at the vendor docs relevant to the scenario (Atlas or Harbor).
    For vendor-agnostic scenarios the volume root is returned.
    """
    # Trust boundary (DIRECTIVES §2): the engine package never imports
    # simulators; the docs ref is derived from the volume layout alone.
    return f"{docs_volume}/{scenario_id}"


def list_scenarios(visibility: str | None = "dev") -> list[str]:
    """List scenario ids from ``simulators/scenarios/*.yaml`` filtered by visibility.

    ``visibility`` may be ``"dev"``, ``"heldout"`` or ``None`` (all).
    The FIAS on-premise family (``transport: fias_tcp``, REQ-022 D) is
    skipped: the bench arms run HTTP vendor APIs, while link guarantees
    are graded by the FIAS e2e via ``verifier/fias_checks.py``.
    Returns an empty list when the scenario directory is unavailable
    (the engine package never imports simulators; DIRECTIVES §2).
    """
    root = pathlib.Path(__file__).resolve().parents[1] / "simulators" / "scenarios"
    ids: list[str] = []
    if root.is_dir():
        for p in sorted(root.glob("*.yaml")):
            try:
                import yaml

                data = yaml.safe_load(p.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                if str(data.get("transport", "http")) != "http":
                    continue
                sid = str(data.get("id", p.stem))
                vis = str(data.get("visibility", "dev"))
                if visibility is None or vis == visibility:
                    ids.append(sid)
            except Exception:
                # Fallback: use filename stem
                if visibility is None:
                    ids.append(p.stem)
                continue
        if ids:
            return ids
    return []


def make_trial_inputs(
    config: HarnessConfig,
    scenario_id: str,
    trial: int,
) -> HarnessInputs:
    """Build the matched inputs for one scenario × trial.

    Deterministic from ``(config.seed, scenario_id, trial)`` — same args
    always return an equal :class:`HarnessInputs`.
    """
    if not scenario_id:
        raise ValueError("scenario_id must be non-empty")
    if trial < 0:
        raise ValueError("trial must be >= 0")
    # Derive per-trial seed deterministically
    # Use stable hash of scenario_id + trial so ordering doesn't matter
    h = hashlib.sha256(f"{config.seed}:{scenario_id}:{trial}".encode()).hexdigest()
    derived_seed = int(h[:8], 16) % (2**31)
    return HarnessInputs(
        scenario_id=scenario_id,
        trial=trial,
        seed=derived_seed,
        sandbox_image=config.sandbox_image,
        sandbox_url=config.sandbox_url,
        docs_volume=config.docs_volume,
        docs_ref=docs_ref_for_scenario(scenario_id, config.docs_volume),
        credential_refs=config.credential_refs,
        visible_tests=config.visible_tests,
        token_ceiling=config.token_ceiling,
        wall_clock_s=config.wall_clock_s,
        control_api_url=config.control_api_url,
        verifier_url=config.verifier_url,
        runtime_target=dict(config.runtime_target),
    )


# ---------------------------------------------------------------------------
# Harness self-test: both arms get identical inputs
# ---------------------------------------------------------------------------


def assert_inputs_equal(a: HarnessInputs, b: HarnessInputs) -> None:
    """Harness self-test: assert *a* and *b* are identical (plan §2.10).

    Checks every field of :class:`HarnessInputs`.  On mismatch raises
    :class:`AssertionError` naming the first differing field.  This is the
    assertion required by P1.W8.1: both arms get identical docs volume,
    sandbox URL, credentials, visible tests, token ceiling and wall-clock
    (plus sandbox image, control/verifier URLs and runtime target).

    Callers should call this both pre-run (to prove inputs were built equal)
    and post-run (audit trail in :class:`TrialResult`).
    """
    if a is b:
        return
    # Compare field by field for a clear error message
    for f in dataclasses.fields(HarnessInputs):
        av = getattr(a, f.name)
        bv = getattr(b, f.name)
        if av != bv:
            raise AssertionError(
                f"harness inputs differ on {f.name!r}: {av!r} != {bv!r} "
                f"(scenario={a.scenario_id!r} trial={a.trial})"
            )


def is_matched(a: HarnessInputs, b: HarnessInputs) -> bool:
    """Return True if *a* and *b* are identical (no raise)."""
    try:
        assert_inputs_equal(a, b)
        return True
    except AssertionError:
        return False


def assert_budget_fields_equal(a: HarnessInputs, b: HarnessInputs) -> None:
    """Assert only the six task-named budget fields are equal (subset check).

    This is the narrow self-test: docs volume, sandbox URL, credentials,
    visible tests, token ceiling, wall-clock must be equal for both arms.
    """
    budget_a = a.budget_fields()
    budget_b = b.budget_fields()
    for k in budget_a:
        if budget_a[k] != budget_b[k]:
            raise AssertionError(f"budget field {k!r} differs: {budget_a[k]!r} != {budget_b[k]!r}")


# ---------------------------------------------------------------------------
# Arm / Trial / Bench result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArmResult:
    """Result of one arm for one scenario × trial."""

    arm: Literal[
        "autobridge",
        "baseline",
        "one_shot",
        "frontier_oneshot",
        "frontier_agent",
        "engine_no_knowledge",
        "autobridge_cold",
        "autobridge_warm",
    ]
    inputs: HarnessInputs
    #: The arm ran to a decision. REQ-028: named ``ran``, not ``success`` —
    #: it is NOT a verifier pass (see ``bench.scoring`` for grading).
    ran: bool
    elapsed_ms: float = 0.0
    # Token / cost accounting (mirrors llm.Usage)
    input_tokens: int = 0
    output_tokens: int = 0
    frontier_tokens: int = 0
    cost_usd: Decimal | None = None
    # Usage counters for W8.4 scoring (defaults keep old constructors green)
    tool_calls: int = 0
    model_calls: int = 0
    # Verifier outcome (when available)
    verdict: str | None = None
    score: float | None = None
    failed_checks: list[dict[str, Any]] | None = None
    # Error / model id when failed
    error: str | None = None
    model: str | None = None
    # Optional produced spec (baseline / one_shot)
    spec: dict[str, Any] | None = None
    # REQ-046: bench prompt reference (id/version/hash of the file prompt
    # the arm rendered) + content hash of the shared context bundle every
    # arm received. None on legacy/synthetic paths (unit tests).
    prompt: dict[str, str] | None = None
    context_hash: str | None = None
    # REQ-052: explicit outcome label for rows with no verifier verdict —
    # "truncated" (output cap cut the answer off), "timed_out" (wall-clock
    # hit before a real attempt could finish), "no_output" (ran but
    # produced no parseable spec), "model_mismatch" (live agent ran a
    # different model than FRONTIER_MODEL). None means the row carries a
    # verdict (or is an engine arm graded by decision). The scorecard
    # counts labels per arm so quality numbers measure the model, never
    # the harness.
    outcome: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "arm": self.arm,
            "scenario_id": self.inputs.scenario_id,
            "trial": self.inputs.trial,
            "seed": self.inputs.seed,
            "ran": self.ran,
            "elapsed_ms": self.elapsed_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "frontier_tokens": self.frontier_tokens,
            "cost_usd": str(self.cost_usd) if self.cost_usd is not None else None,
            "tool_calls": self.tool_calls,
            "model_calls": self.model_calls,
            "verdict": self.verdict,
            "score": self.score,
            "failed_checks": self.failed_checks,
            "error": self.error,
            "model": self.model,
        "inputs_fingerprint": self.inputs.fingerprint(),
        "prompt": dict(self.prompt) if self.prompt is not None else None,
        "context_hash": self.context_hash,
        "outcome": self.outcome,
        }
        # Include budget fields for audit
        d["budget"] = {
            "docs_volume": self.inputs.docs_volume,
            "sandbox_url": self.inputs.sandbox_url,
            "sandbox_image": self.inputs.sandbox_image,
            "credential_refs": list(self.inputs.credential_refs),
            "visible_tests": list(self.inputs.visible_tests),
            "token_ceiling": self.inputs.token_ceiling,
            "wall_clock_s": self.inputs.wall_clock_s,
        }
        return d


@dataclass(frozen=True, slots=True)
class TrialResult:
    """Both arms for one scenario × trial, with matched-budget audit.

    ``one_shot`` is the optional secondary diagnostic control arm
    (P1.W8.3, plan §2.10): when present it received the same
    :class:`HarnessInputs` as the other two arms.
    """

    scenario_id: str
    trial: int
    seed: int
    inputs: HarnessInputs
    autobridge: ArmResult
    baseline: ArmResult
    matched: bool
    matched_error: str | None = None
    one_shot: ArmResult | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "scenario_id": self.scenario_id,
            "trial": self.trial,
            "seed": self.seed,
            "matched": self.matched,
            "matched_error": self.matched_error,
            "inputs": dataclasses.asdict(self.inputs),
            "inputs_fingerprint": self.inputs.fingerprint(),
            "autobridge": self.autobridge.to_dict(),
            "baseline": self.baseline.to_dict(),
        }
        if self.one_shot is not None:
            d["one_shot"] = self.one_shot.to_dict()
        return d


@dataclass
class BenchResult:
    """Aggregate result of a harness run (per scenario × trial)."""

    config: HarnessConfig
    trials: list[TrialResult] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": dataclasses.asdict(self.config),
            "trials": [t.to_dict() for t in self.trials],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_trials": len(self.trials),
            "matched_trials": sum(1 for t in self.trials if t.matched),
        }

    def all_matched(self) -> bool:
        return all(t.matched for t in self.trials)


# ---------------------------------------------------------------------------
# Helpers — verifying a trial's matched budget
# ---------------------------------------------------------------------------


def verify_matched_trial(trial: TrialResult) -> None:
    """Assert trial's arms had identical inputs (post-hoc audit).

    Raises :class:`AssertionError` if ``trial.matched`` is False.
    When the optional ``one_shot`` arm is present it is included in the
    three-way audit.
    """
    if not trial.matched:
        raise AssertionError(
            f"trial {trial.scenario_id!r} trial={trial.trial} not matched: {trial.matched_error}"
        )
    # Also assert the actual inputs objects are equal
    assert_inputs_equal(trial.autobridge.inputs, trial.baseline.inputs)
    if trial.one_shot is not None:
        assert_inputs_equal(trial.baseline.inputs, trial.one_shot.inputs)


def _repo_root() -> pathlib.Path:
    """Repo root resolved from this file (never from cwd — tests chdir)."""
    return pathlib.Path(__file__).resolve().parents[1]


#: Env var pointing at the owner-held private heldout seed directory (REQ-007).
HELDOUT_DIR_ENV = "HELDOUT_DIR"

#: Env var for the simulator admin plane used to push private seeds (REQ-007).
SIMULATOR_ADMIN_URL_ENV = "SIMULATOR_ADMIN_URL"


def committed_heldout_manifest_path() -> pathlib.Path:
    """Committed SHA256SUMS that pins the private heldout files (REQ-007)."""
    return _repo_root() / "verifier" / "hidden_cases" / "heldout" / "SHA256SUMS"


def _read_manifest_entries(manifest: pathlib.Path) -> list[tuple[str, str]]:
    """Parse sha256sum-format lines into (sha, rel) pairs (skips blanks/comments)."""
    entries: list[tuple[str, str]] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        entries.append((parts[0], parts[1]))
    return entries


def check_heldout_sha(heldout_dir: str | pathlib.Path | None = None, manifest_path: str | pathlib.Path | None = None) -> None:
    """Verify private heldout files in HELDOUT_DIR against the committed manifest (REQ-007).

    The manifest (``verifier/hidden_cases/heldout/SHA256SUMS``) pins the
    owner-held private seed files.  Files are read from *heldout_dir*
    (default ``$HELDOUT_DIR``) — never from the repo folder — and compared
    by sha256.  A missing manifest is a no-op (heldout not frozen).
    Once the manifest exists the directory is required and any missing
    file or sha mismatch raises ``AssertionError`` (the CLI maps it to
    exit 2 and refuses the heldout run).
    """
    manifest = pathlib.Path(manifest_path) if manifest_path else committed_heldout_manifest_path()
    if not manifest.is_file():
        return
    import hashlib as _hashlib

    root = (
        pathlib.Path(heldout_dir)
        if heldout_dir
        else (pathlib.Path(os.environ[HELDOUT_DIR_ENV]) if os.environ.get(HELDOUT_DIR_ENV) else None)
    )
    if root is None:
        raise AssertionError("heldout SHA check: HELDOUT_DIR not set (refusing heldout run without private seeds)")
    for expected_sha, rel in _read_manifest_entries(manifest):
        target = root / rel
        if not target.is_file():
            raise AssertionError(f"heldout SHA check: missing file {rel}")
        actual = _hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected_sha:
            raise AssertionError(f"heldout SHA mismatch for {rel}: {actual} != {expected_sha}")
    return


def heldout_manifest_sha(manifest_path: str | pathlib.Path | None = None) -> str | None:
    """Sha256 hex of the committed manifest file itself (scorecard header); None when unfrozen."""
    manifest = pathlib.Path(manifest_path) if manifest_path else committed_heldout_manifest_path()
    if not manifest.is_file():
        return None
    import hashlib as _hashlib

    return _hashlib.sha256(manifest.read_bytes()).hexdigest()


def load_heldout_seeds(
    heldout_dir: str | pathlib.Path | None = None, manifest_path: str | pathlib.Path | None = None
) -> dict[str, int]:
    """Check the gate, then load ``{scenario_id: private seed}`` from HELDOUT_DIR (REQ-007).

    Each ``<scenario_id>.yaml`` must map ``id`` (str, matching the file
    stem), ``seed`` (non-negative int) and ``nonce`` (str).  Seeds are
    secrets: this function never prints, logs or serializes them — the
    caller keeps them out of JSONL, scorecards and SSE events.
    """
    check_heldout_sha(heldout_dir=heldout_dir, manifest_path=manifest_path)
    manifest = pathlib.Path(manifest_path) if manifest_path else committed_heldout_manifest_path()
    heldout_env = os.environ.get(HELDOUT_DIR_ENV, "").strip() or None
    root = (
        pathlib.Path(heldout_dir)
        if heldout_dir
        else (pathlib.Path(heldout_env) if heldout_env else None)
    )
    if root is None:
        raise AssertionError("heldout SHA check: HELDOUT_DIR not set (refusing heldout run without private seeds)")
    import yaml as _yaml

    seeds: dict[str, int] = {}
    for _, rel in _read_manifest_entries(manifest):
        if not rel.endswith(".yaml"):
            continue
        data = _yaml.safe_load((root / rel).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise AssertionError(f"heldout seed file {rel} must be a mapping")
        sid = data.get("id")
        seed = data.get("seed")
        nonce = data.get("nonce")
        if not isinstance(sid, str) or not sid:
            raise AssertionError(f"heldout seed file {rel} needs a str 'id'")
        if pathlib.Path(rel).stem != sid:
            raise AssertionError(f"heldout seed file {rel} id {sid!r} does not match filename")
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise AssertionError(f"heldout seed file {rel} needs a non-negative int 'seed'")
        if not isinstance(nonce, str) or not nonce:
            raise AssertionError(f"heldout seed file {rel} needs a str 'nonce'")
        seeds[sid] = seed
    if not seeds:
        raise AssertionError("heldout SHA check: manifest lists no seed files")
    return seeds


def push_simulator_seed(
    scenario_id: str, seed: int, *, admin_url: str, timeout_s: float = 5.0
) -> None:
    """Push a private seed to the simulator admin plane (REQ-007).

    ``POST {admin_url}/admin/scenario`` with the admin-net header.  Raises
    on transport or HTTP error.  Never logs the seed — only the scenario id
    appears in error text.
    """
    import httpx as _httpx

    try:
        resp = _httpx.post(
            admin_url.rstrip("/") + "/admin/scenario",
            json={"id": scenario_id, "seed": seed},
            headers={"X-Admin-Network": "admin-net"},
            timeout=timeout_s,
        )
    except Exception as exc:
        raise RuntimeError(f"simulator admin unreachable for scenario {scenario_id!r}: {exc}") from exc
    if resp.status_code != 200:
        raise RuntimeError(f"simulator admin rejected scenario {scenario_id!r}: HTTP {resp.status_code}")
    return


# ---------------------------------------------------------------------------
# Runners — AutoBridge via control API, baseline via CLI driver
# ---------------------------------------------------------------------------


def _build_autobridge_prompt(inputs: HarnessInputs) -> str:
    """Build a prompt describing the task for error messages (not sent to model)."""
    return (
        f"Build a ConnectorSpec for scenario {inputs.scenario_id!r} "
        f"using docs at {inputs.docs_ref!r} and sandbox {inputs.sandbox_url!r}"
    )


def run_autobridge_arm(
    inputs: HarnessInputs,
    *,
    client: Any | None = None,
    control_api_url: str | None = None,
    timeout_s: float | None = None,
) -> ArmResult:
    """Run the AutoBridge arm for *inputs* via the control API.

    If *client* is given it is a ``httpx.Client`` or ``httpx.AsyncClient``
    (or any object with ``post``/``get``).  When ``None`` a new
    ``httpx.Client`` is created for ``control_api_url``.
    On any error an :class:`ArmResult` with ``ran=False`` is returned
    (never raises), so the harness can score losses.
    """
    url = (control_api_url or inputs.control_api_url).rstrip("/")
    timeout = timeout_s if timeout_s is not None else inputs.wall_clock_s
    start = time.monotonic()

    # Try HTTP path only when a real client/url is reachable; otherwise
    # return a synthetic ran so unit tests without Docker stay green.
    # The caller may pass a fake client that records calls.
    try:
        if client is not None:
            # Caller provided a fake/real httpx-like client
            # Minimal flow: create connector → build → poll
            # For bench we delegate to the caller's client; we just prove
            # the inputs were passed through.
            _post_kwargs: dict[str, Any] = {
                "json": {
                    "name": f"bench-{inputs.scenario_id}-{inputs.trial}",
                    "base_url": inputs.sandbox_url,
                    "credential_ref": inputs.credential_refs[0] if inputs.credential_refs else "BENCH_API_KEY",
                    "vendor_docs_ref": inputs.docs_ref,
                    "target_entities": list(inputs.visible_tests) or ["Reservation"],
                    "runtime_target": inputs.runtime_target,
                },
                "timeout": timeout,
            }
            # REQ-012: send the control write token when set. The headers
            # kwarg is only passed then, so header-unaware fake clients keep
            # working when auth is disabled.
            _headers = control_headers()
            if _headers:
                _post_kwargs["headers"] = _headers
            resp = client.post(f"{url}/connectors", **_post_kwargs)
            # If client returns error, capture it
            elapsed = (time.monotonic() - start) * 1000.0
            if hasattr(resp, "status_code") and resp.status_code not in (200, 201):
                return ArmResult(
                    arm="autobridge",
                    inputs=inputs,
                    ran=False,
                    elapsed_ms=elapsed,
                    error=f"control API {resp.status_code}: {getattr(resp, 'text', '')[:500]}",
                )
            return ArmResult(
                arm="autobridge",
                inputs=inputs,
                ran=True,
                elapsed_ms=elapsed,
                model="autobridge-ladder",
            )
        # No client — synthetic run (unit tests / dry-run mode).
        # We still respect wall-clock by sleeping 0 and return ran.
        elapsed = (time.monotonic() - start) * 1000.0
        return ArmResult(
            arm="autobridge",
            inputs=inputs,
            ran=True,
            elapsed_ms=elapsed,
            model="autobridge-synthetic",
        )
    except Exception as exc:
        elapsed = (time.monotonic() - start) * 1000.0
        return ArmResult(
            arm="autobridge",
            inputs=inputs,
            ran=False,
            elapsed_ms=elapsed,
            error=f"{type(exc).__name__}: {exc}",
        )


def run_baseline_arm(
    inputs: HarnessInputs,
    *,
    driver: Any | None = None,
    prompt_template: str | None = None,
    arm: str = "baseline",
    context: Any | None = None,
    sandbox_dir: str | None = None,
) -> ArmResult:
    """Run the baseline arm for *inputs* via the coding-agent CLI driver.

    *driver* may be a :class:`ClaudeCodeDriver`, :class:`FakeClaudeDriver`,
    or any object with ``run(prompt, cwd=None) -> ClaudeCliResult``.
    When ``None`` a synthetic baseline result is returned (no subprocess).

    The baseline receives the *same* docs volume, sandbox URL, credentials
    (as refs), visible tests and token/wall-clock ceiling as AutoBridge —
    the harness proves this via :func:`assert_inputs_equal` before calling
    either runner.

    REQ-046: when *context* (a :class:`bench.context.BenchContext`) is
    given, the prompt is the file-based agent brief
    (``config/prompts/bench/frontier_agent.md``) rendered over the shared
    context, and the sandbox holds the same files the engine has (written
    by :func:`bench.context.write_agent_sandbox` to *sandbox_dir* or a
    temp dir; the driver runs with ``cwd`` there). The result records the
    bench prompt ref and the context hash. Without *context* the legacy
    one-paragraph brief is used (unit tests / synthetic paths only).
    """
    start = time.monotonic()
    # REQ-046 full-context path: file-based brief over the shared bundle,
    # sandbox files on disk, driver runs with cwd there. prompt_template is
    # honored as an override only on the legacy path below.
    prompt_ref: dict[str, str] | None = None
    context_hash: str | None = None
    cwd: Any | None = None
    _temp_dir: Any | None = None
    if context is not None:
        from bench.bench_prompts import bench_prompt_ref, ensure_bench_prompts_loaded  # noqa: PLC0415
        from bench.context import (  # noqa: PLC0415
            messages_to_text,
            render_frontier_agent_brief,
            write_agent_sandbox,
        )

        ensure_bench_prompts_loaded()
        prompt = messages_to_text(render_frontier_agent_brief(inputs, context))
        prompt_ref = bench_prompt_ref("frontier_agent")
        context_hash = context.content_hash()
        import tempfile  # noqa: PLC0415

        if sandbox_dir is not None:
            write_agent_sandbox(context, sandbox_dir)
            cwd = sandbox_dir
        else:
            _temp_dir = tempfile.TemporaryDirectory(prefix="bench-agent-")  # noqa: SIM115
            write_agent_sandbox(context, _temp_dir.name)
            cwd = _temp_dir.name
    else:
        # Legacy one-paragraph brief (unit tests / synthetic paths only —
        # real bench runs always pass a context; see bench.context).
        _ = prompt_template  # reserved for W8.2 prompt engineering
        prompt = (
            f"Build a ConnectorSpec YAML for scenario {inputs.scenario_id!r} "
            f"using vendor docs at {inputs.docs_ref!r} (volume {inputs.docs_volume!r}). "
            f"Target sandbox: {inputs.sandbox_url!r} image {inputs.sandbox_image!r}. "
            f"Credential refs: {', '.join(inputs.credential_refs)}. "
            f"Visible tests: {', '.join(inputs.visible_tests)}. "
            f"Token ceiling: {inputs.token_ceiling}, wall-clock: {inputs.wall_clock_s}s."
        )
        if prompt_template is not None:
            prompt = prompt_template
    try:
        if driver is not None:
            try:
                # REQ-052: the agent gets the same wall-clock budget as
                # every other arm (inputs.wall_clock_s), so a real attempt
                # has time to finish; a hit is labelled timed_out, never
                # a failure to fix.
                result = driver.run(prompt, cwd=cwd, timeout_s=inputs.wall_clock_s)
            except TypeError:
                try:
                    # Drivers whose run() takes no timeout (very old fakes).
                    result = driver.run(prompt, cwd=cwd)
                except TypeError:
                    result = driver.run(prompt)
            elapsed = (time.monotonic() - start) * 1000.0
            # Extract usage from ClaudeCliResult shape
            input_tokens = getattr(getattr(result, "usage", None), "input_tokens", 0) or 0
            output_tokens = getattr(getattr(result, "usage", None), "output_tokens", 0) or 0
            frontier = input_tokens + output_tokens
            cost = getattr(result, "cost_usd", None)
            if cost is not None:
                try:
                    cost = Decimal(str(cost))
                except Exception:
                    cost = None
            # REQ-052: record the model the run itself reports (driver
            # result, then the driver's configured model, then its fake
            # default) — never a hardcoded default label. "unknown" says
            # the run reported nothing; the ablation fails a live run
            # whose model differs from FRONTIER_MODEL.
            actual_model = (
                getattr(result, "model", None)
                or getattr(driver, "model", None)
                or getattr(driver, "default_model", None)
                or "unknown"
            )
            # REQ-052 item 3: parse the agent's answer for grading — did it
            # produce a spec at all? (The verifier judges it in
            # grade_ablation_trial; here we only record what came back.)
            produced_spec: dict[str, Any] | None = None
            outcome: str | None = None
            try:
                from bench.one_shot import try_parse_spec_yaml  # noqa: PLC0415

                produced_spec = try_parse_spec_yaml(getattr(result, "text", "") or "")
            except Exception:
                produced_spec = None
            if produced_spec is None:
                outcome = "no_output"
            return ArmResult(
                arm=arm,  # type: ignore[arg-type]
                inputs=inputs,
                ran=True,
                elapsed_ms=elapsed,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                frontier_tokens=frontier,
                cost_usd=cost,
                model=actual_model,
                tool_calls=int(getattr(result, "tool_calls", 0) or 0),
                model_calls=1,
                prompt=prompt_ref,
                context_hash=context_hash,
                spec=produced_spec,
                outcome=outcome,
            )
        elapsed = (time.monotonic() - start) * 1000.0
        return ArmResult(
            arm=arm,  # type: ignore[arg-type]
            inputs=inputs,
            ran=True,
            elapsed_ms=elapsed,
            model="baseline-synthetic",
        )
    except Exception as exc:
        elapsed = (time.monotonic() - start) * 1000.0
        err_msg = f"{type(exc).__name__}: {exc}"
        timed_out = "timed out" in err_msg.lower() or "timeoutexpired" in err_msg.lower()
        return ArmResult(
            arm=arm,  # type: ignore[arg-type]
            inputs=inputs,
            ran=False,
            elapsed_ms=elapsed,
            error=err_msg[:1000],
            outcome="timed_out" if timed_out else None,
        )


# Fallback baseline via LLMProvider (plan §9: route through LLMProvider with
# an equivalent agent loop when the CLI is unavailable).
def _frontier_output_cap() -> int:
    """Per-call output room shared by every frontier arm (REQ-052).

    The engine's L3 cap (``FRONTIER_MAX_TOKENS``, default 131072) — the
    one-shot and the LLM fallback get the same room, so a cut-off answer
    measures the model, never our cap. Falls back to the default when
    config is unavailable (unit tests).
    """
    try:
        from autobridge.config import frontier_max_tokens  # noqa: PLC0415

        return frontier_max_tokens()
    except Exception:
        return 131072


def run_baseline_via_llm_provider(
    inputs: HarnessInputs,
    *,
    provider: Any | None = None,
    context: Any | None = None,
) -> ArmResult:
    """Fallback baseline: route through :class:`LLMProvider` directly.

    This is the documented fallback when the headless CLI is unavailable
    (plan §9).  It uses the same token/wall-clock ceiling via
    :class:`Budget` so the harness self-test still holds.

    REQ-046: the model gets the full shared context, not a one-liner.
    When *context* (a :class:`bench.context.BenchContext`) is given it is
    rendered through the file-based one-shot prompt
    (``config/prompts/bench/frontier_oneshot.md``). Otherwise the context
    is resolved from the scenario's ablation case (all real bench
    scenarios have one); only unknown/synthetic scenario ids with no
    ablation case keep the legacy minimal prompt (unit tests). The
    ablation never calls this function — it drives
    :func:`run_baseline_arm` with the OpenCode/fake driver — but any
    direct caller now gets the same fair context either way.
    """
    start = time.monotonic()
    if provider is None:
        elapsed = (time.monotonic() - start) * 1000.0
        return ArmResult(
            arm="baseline",
            inputs=inputs,
            ran=True,
            elapsed_ms=elapsed,
            model="baseline-llm-fallback-synthetic",
        )
    # Real provider path: one completion with budget
    try:
        from autobridge.llm.provider import Budget, BudgetedProvider

        budget = Budget(
            max_frontier_tokens=inputs.token_ceiling,
            max_small_tokens=inputs.token_ceiling,
            max_wall=inputs.wall_clock_s,
        )
        budgeted = BudgetedProvider(provider, budget)
        prompt_ref: dict[str, str] | None = None
        context_hash: str | None = None
        resolved = context
        if resolved is None:
            try:
                from bench.ablation import (  # noqa: PLC0415
                    ablation_case_for_scenario,
                )
                from bench.context import build_bench_context  # noqa: PLC0415

                case = ablation_case_for_scenario(inputs.scenario_id)
                resolved = build_bench_context(
                    scenario_id=inputs.scenario_id,
                    connector_id=case.connector_id,
                    spec=case.spec,
                    docs_ref=inputs.docs_ref,
                    visible_tests=tuple(inputs.visible_tests),
                )
            except Exception:
                resolved = None
        if resolved is not None:
            from bench.bench_prompts import (  # noqa: PLC0415
                bench_prompt_ref,
                ensure_bench_prompts_loaded,
            )
            from bench.context import (  # noqa: PLC0415
                messages_to_text,
                render_frontier_oneshot_prompt,
            )

            ensure_bench_prompts_loaded()
            messages = render_frontier_oneshot_prompt(inputs, resolved)
            prompt = messages_to_text(messages)
            prompt_ref = bench_prompt_ref("frontier_oneshot")
            context_hash = resolved.content_hash()
            purpose = "bench-baseline-full-context"
        else:
            # Unknown/synthetic scenario with no ablation case (unit tests):
            # legacy minimal prompt, labelled as such.
            prompt = f"Build a ConnectorSpec for {inputs.scenario_id!r} from {inputs.docs_ref!r}"
            purpose = "bench-baseline"
        comp = budgeted.complete(
            [{"role": "user", "content": prompt}],
            max_tokens=min(inputs.token_ceiling, _frontier_output_cap()),
            tier="frontier",
            purpose=purpose,
        )
        elapsed = (time.monotonic() - start) * 1000.0
        return ArmResult(
            arm="baseline",
            inputs=inputs,
            ran=True,
            elapsed_ms=elapsed,
            input_tokens=comp.usage.input_tokens,
            output_tokens=comp.usage.output_tokens,
            frontier_tokens=comp.usage.total_tokens,
            model="baseline-llm-fallback",
            tool_calls=len(comp.tool_calls),
            model_calls=1,
            prompt=prompt_ref,
            context_hash=context_hash,
        )
    except Exception as exc:
        elapsed = (time.monotonic() - start) * 1000.0
        return ArmResult(
            arm="baseline",
            inputs=inputs,
            ran=False,
            elapsed_ms=elapsed,
            error=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# Trial + harness orchestration (the core matched-budget loop)
# ---------------------------------------------------------------------------


def run_trial(
    config: HarnessConfig,
    scenario_id: str,
    trial: int,
    *,
    autobridge_runner: Any | None = None,
    baseline_runner: Any | None = None,
) -> TrialResult:
    """Run one scenario × trial with matched-budget guarantee.

    1. Build :class:`HarnessInputs` once via :func:`make_trial_inputs`.
    2. Clone it for both arms (or pass the same object) and assert
       :func:`assert_inputs_equal` — the harness self-test.
    3. Run both arms (each receives the identical inputs).
    4. Return :class:`TrialResult` with ``matched=True`` if the audit passed.

    *autobridge_runner* / *baseline_runner* are callables
    ``(HarnessInputs) -> ArmResult``.  When ``None`` the default
    synthetic runners are used (no network), which keeps unit tests green
    without Docker.

    This function never raises except for the harness self-test
    (which is intentional — a mismatch is a harness bug and must fail
    loudly).
    """
    # Single construction — both arms share the same inputs object
    inputs = make_trial_inputs(config, scenario_id, trial)
    inputs_a = inputs
    inputs_b = make_trial_inputs(config, scenario_id, trial)

    # Self-test: both arms must get identical inputs
    matched = True
    matched_error: str | None = None
    try:
        assert_inputs_equal(inputs_a, inputs_b)
        assert_budget_fields_equal(inputs_a, inputs_b)
    except AssertionError as exc:
        matched = False
        matched_error = str(exc)

    # Run both arms (each gets the identical inputs)
    if autobridge_runner is not None:
        ab_result = autobridge_runner(inputs_a)
        if not isinstance(ab_result, ArmResult):
            # Allow runners that return dict-like; coerce
            ab_result = ArmResult(arm="autobridge", inputs=inputs_a, ran=bool(ab_result))
    else:
        ab_result = run_autobridge_arm(inputs_a)

    if baseline_runner is not None:
        bl_result = baseline_runner(inputs_b)
        if not isinstance(bl_result, ArmResult):
            bl_result = ArmResult(arm="baseline", inputs=inputs_b, ran=bool(bl_result))
    else:
        bl_result = run_baseline_arm(inputs_b)

    return TrialResult(
        scenario_id=scenario_id,
        trial=trial,
        seed=inputs.seed,
        inputs=inputs,
        autobridge=ab_result,
        baseline=bl_result,
        matched=matched,
        matched_error=matched_error,
    )


def run_harness(
    config: HarnessConfig | None = None,
    *,
    scenarios: list[str] | None = None,
    autobridge_runner: Any | None = None,
    baseline_runner: Any | None = None,
) -> BenchResult:
    """Run the harness for every scenario × trial in *config*.

    This is the top-level loop: for each ``scenario`` and each ``trial``
    in ``range(trials_per_scenario)`` call :func:`run_trial`.

    The harness is idempotent: running it twice with the same ``seed``
    yields identical :class:`TrialResult` inputs (and, with fakes,
    identical outputs).

    ``scenarios`` overrides ``config.scenarios`` when given.
    """
    cfg = config or HarnessConfig()
    scenario_ids = scenarios if scenarios is not None else (
        list(cfg.scenarios) if cfg.scenarios is not None else list_scenarios(visibility="dev")
    )

    result = BenchResult(config=cfg, started_at=time.time())

    for scenario_id in scenario_ids:
        for trial in range(cfg.trials_per_scenario):
            trial_result = run_trial(
                cfg,
                scenario_id,
                trial,
                autobridge_runner=autobridge_runner,
                baseline_runner=baseline_runner,
            )
            result.trials.append(trial_result)

    result.finished_at = time.time()
    return result


# ---------------------------------------------------------------------------
# Output — JSONL for experiments/benchmark_runs/
# ---------------------------------------------------------------------------


def write_results(
    bench_result: BenchResult,
    output_dir: str | pathlib.Path | None = None,
) -> list[pathlib.Path]:
    """Write each :class:`TrialResult` as one JSONL line.

    Returns the list of written file paths.  One file per trial:
    ``<scenario_id>--trial-<n>.jsonl``.
    """
    out_dir = pathlib.Path(output_dir) if output_dir else pathlib.Path("experiments/benchmark_runs")
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[pathlib.Path] = []
    for trial in bench_result.trials:
        fname = f"{trial.scenario_id}--trial-{trial.trial}.jsonl"
        fpath = out_dir / fname
        payload = trial.to_dict()
        # Redact any credential value that might have leaked (defense in depth)
        # Inputs only carry refs, but we scrub values just in case.
        line = json.dumps(payload, default=str, sort_keys=True)
        fpath.write_text(line + "\n", encoding="utf-8")
        written.append(fpath)
    return written


def write_bench_summary(bench_result: BenchResult, path: str | pathlib.Path | None = None) -> pathlib.Path:
    """Write a JSON summary of the harness run (for scorecard generation)."""
    out = pathlib.Path(path) if path else pathlib.Path("experiments/benchmark_runs/_summary.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bench_result.to_dict(), default=str, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Reproducibility — canonical comparison ignoring wall-clock timing (P1.W8.5)
# ---------------------------------------------------------------------------

#: Payload keys that vary run to run even with the same seed (measured
#: wall-clock durations and timestamps).  Idempotency / reproducibility
#: comparisons drop these; everything else must be byte-identical for the
#: same seed (plan §2.10: "reproducible from seeds").
VOLATILE_KEYS: tuple[str, ...] = ("elapsed_ms", "started_at", "finished_at", "generated_at")


def canonical_payload(payload: Any) -> Any:
    """Return *payload* with volatile timing keys removed (recursively).

    Used to prove two bench runs are reproducible: same seed + same code
    must yield identical canonical bytes.  Only wall-clock measurements
    and timestamps are excluded — inputs, seeds, fingerprints, verdicts,
    token counts and costs must all match.
    """
    if isinstance(payload, dict):
        return {k: canonical_payload(v) for k, v in payload.items() if k not in VOLATILE_KEYS}
    if isinstance(payload, list):
        return [canonical_payload(v) for v in payload]
    if isinstance(payload, tuple):
        return [canonical_payload(v) for v in payload]
    return payload


def canonical_json_bytes(payload: Any) -> bytes:
    """Canonical JSON bytes for *payload* (sorted keys, timing excluded)."""
    return json.dumps(canonical_payload(payload), default=str, sort_keys=True).encode("utf-8")


def assert_results_reproducible(first: BenchResult, second: BenchResult) -> None:
    """Assert two harness runs are reproducible (same seed → same results).

    Raises :class:`AssertionError` naming the divergence.  Timing fields
    (``elapsed_ms``, ``started_at``/``finished_at``) are excluded — live
    wall-clock always varies; what must match is every input, seed,
    fingerprint, budget field and arm fact.
    """
    if first.config != second.config:
        raise AssertionError(f"bench configs differ: {first.config!r} != {second.config!r}")
    if len(first.trials) != len(second.trials):
        raise AssertionError(f"trial counts differ: {len(first.trials)} != {len(second.trials)}")
    a = canonical_json_bytes([t.to_dict() for t in first.trials])
    b = canonical_json_bytes([t.to_dict() for t in second.trials])
    if a != b:
        raise AssertionError(
            "bench results differ for the same seed (canonical payloads mismatch "
            "after excluding timing fields) — harness is not reproducible"
        )


# ---------------------------------------------------------------------------
# Scoring + report wiring — ``make bench`` produces experiments/scorecard.md
# ---------------------------------------------------------------------------


def _load_leak_audit(output_dir: str | pathlib.Path) -> dict[str, Any] | None:
    """Load the counts-only leak-audit JSON when the owner wrote one (REQ-024 A1).

    Returns ``None`` when absent or invalid (scorecard renders "not run").
    Never raises — a broken audit file must not fail scoring.
    """
    import json as _json  # noqa: PLC0415

    candidate = pathlib.Path(output_dir) / "_leak_audit.json"
    if not candidate.is_file():
        return None
    try:
        data = _json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def score_and_report(
    bench_result: BenchResult,
    verify_fn: Any | None = None,
    *,
    output_dir: str | pathlib.Path = "experiments/benchmark_runs",
    scorecard_path: str | pathlib.Path = "experiments/scorecard.md",
    verifier_url: str | None = None,
    verifier_timeout_s: float = 10.0,
    heldout_seeds: dict[str, int] | None = None,
    heldout_manifest_sha: str | None = None,
    leak_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score *bench_result* via verifier hidden mode and write scorecard files.

    Writes ``<output_dir>/_scorecard.json`` (schema-validated) and renders
    *scorecard_path* (default ``experiments/scorecard.md``) listing wins
    **and** losses.  Returns the scorecard dict.

    Trust boundary (DIRECTIVES §2): verification goes through *verify_fn*
    (``(spec, scenario_id) -> dict``).  When ``None`` the scorer calls the
    verifier's ``POST /verify`` hidden mode over HTTP — an unreachable
    verifier records losses, never raises.

    REQ-007: when *heldout_seeds* is given, each heldout scenario is
    verified with its private seed (via the ``seed`` override, never
    recorded in the scorecard), and the scorecard header records the
    manifest sha — never the seeds.

    REQ-024 A1: when *leak_audit* is ``None``, ``<output_dir>/_leak_audit.json``
    (written by ``scripts/heldout_leak_audit.py --write``) is picked up
    automatically when present; the JSON carries counts only, never secrets.
    """
    from bench.report import write_scorecard, write_scorecard_json  # noqa: PLC0415
    from bench.scoring import (  # noqa: PLC0415
        score_bench,
        verify_via_http,
        wrap_verify_fn_with_seeds,
    )

    url = verifier_url or bench_result.config.verifier_url
    fn = verify_fn
    if fn is None:
        import functools  # noqa: PLC0415

        fn = functools.partial(verify_via_http, verifier_url=url, timeout_s=verifier_timeout_s)
    if heldout_seeds:
        fn = wrap_verify_fn_with_seeds(fn, heldout_seeds)
    if leak_audit is None:
        leak_audit = _load_leak_audit(output_dir)
    scorecard = score_bench(
        bench_result,
        fn,
        heldout=bool(heldout_seeds),
        heldout_manifest_sha=heldout_manifest_sha,
        leak_audit=leak_audit,
    )
    out_dir = pathlib.Path(output_dir)
    write_scorecard_json(scorecard, out_dir / "_scorecard.json")
    write_scorecard(scorecard, scorecard_path)
    return scorecard


def _probe_services(cfg: HarnessConfig, timeout_s: float = 3.0) -> str | None:
    import httpx

    for label, url in (("control API", cfg.control_api_url), ("verifier", cfg.verifier_url)):
        probe = url.rstrip("/") + "/healthz"
        try:
            resp = httpx.get(probe, timeout=timeout_s)
            if resp.status_code != 200:
                return f"bench harness: {label} unreachable at {url} (healthz {resp.status_code})"
        except Exception as exc:
            return f"bench harness: {label} unreachable at {url} ({type(exc).__name__}: {exc})"
    return None


def _guard_zero_tool_calls(bench_result: BenchResult) -> str | None:
    if not bench_result.trials:
        return None
    for arm in ("autobridge", "baseline"):
        total = sum(getattr(getattr(t, arm), "tool_calls", 0) or 0 for t in bench_result.trials)
        if total == 0:
            url = bench_result.config.control_api_url if arm == "autobridge" else bench_result.config.verifier_url
            return f"bench harness: {arm} has 0 tool calls across {len(bench_result.trials)} trials (url {url}) — refusing empty scorecard"
    return None


# ---------------------------------------------------------------------------
# CLI entrypoint — ``python -m bench.harness``
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for ``make bench``.

    Steps (W8.5 + REQ-007):
    1. Heldout gate — dev runs skip it; ``heldout``/``all`` runs verify
       ``$HELDOUT_DIR`` against the committed manifest and load private
       seeds (exit 2 on missing env, missing file or sha mismatch).
    2. Run harness for dev scenarios (or ``BENCH_SCENARIOS`` /
       ``--scenarios``; ``BENCH_VISIBILITY`` / ``--visibility`` selects
       the dev/heldout split — the Nov 3 heldout run uses heldout).
       Heldout scenarios are pushed to the simulator admin plane with
       their private seed first (``$SIMULATOR_ADMIN_URL``; skipped when
       unset, fatal on push failure so live runs never score wrong data).
    3. Write ``experiments/benchmark_runs/*.jsonl`` (idempotent:
       same seed overwrites the same files with identical canonical bytes;
       private seeds never appear in the payload).
    4. Score via verifier hidden mode + render ``experiments/scorecard.md``
       with wins and losses (skip with ``--no-score`` / ``BENCH_NO_SCORE=1``).
       Heldout runs verify with the private seed override and record the
       manifest sha in the scorecard header — never the seeds.
    5. Print summary to stdout (scenario ids and manifest sha only).
    """
    import argparse

    parser = argparse.ArgumentParser(description="AutoBridge bench harness (matched budget)")
    parser.add_argument("--scenarios", nargs="*", default=None, help="Scenario ids (default: all dev)")
    parser.add_argument("--trials", type=int, default=None, help="Trials per scenario")
    parser.add_argument("--seed", type=int, default=None, help="Seed")
    parser.add_argument("--output-dir", type=str, default="experiments/benchmark_runs")
    parser.add_argument(
        "--visibility",
        choices=("dev", "heldout", "all"),
        default=None,
        help="Scenario visibility split (default: dev; env BENCH_VISIBILITY)",
    )
    parser.add_argument(
        "--heldout-dir",
        type=str,
        default=None,
        help="Private heldout seed dir (default: $HELDOUT_DIR; required for heldout/all)",
    )
    parser.add_argument("--scorecard", type=str, default="experiments/scorecard.md", help="Scorecard markdown path")
    parser.add_argument("--no-score", action="store_true", help="Skip scoring/report (harness JSONL only)")
    parser.add_argument(
        "--leak-audit",
        type=str,
        default=None,
        help="Counts-only leak-audit JSON (scripts/heldout_leak_audit.py --write); copied to <output-dir>/_leak_audit.json",
    )
    parser.add_argument("--token-ceiling", type=int, default=None)
    parser.add_argument("--wall-clock", type=float, default=None)
    parser.add_argument("--control-api-url", type=str, default=None)
    parser.add_argument("--verifier-url", type=str, default=None)
    parser.add_argument(
        "--arms",
        choices=("matched", "ablation"),
        default=None,
        help="Arm set (default: matched; env BENCH_ARMS). 'ablation' runs the REQ-024 "
        "five-arm comparison (frontier_oneshot, frontier_agent, engine_no_knowledge, "
        "autobridge_cold, autobridge_warm) on the dev split.",
    )
    args = parser.parse_args(argv)

    # Env overrides (for Docker Compose)
    env_scenarios = os.environ.get("BENCH_SCENARIOS", "")
    env_seed = os.environ.get("BENCH_SEED", "")
    env_trials = os.environ.get("BENCH_TRIALS", "")
    env_visibility = os.environ.get("BENCH_VISIBILITY", "")
    env_no_score = os.environ.get("BENCH_NO_SCORE", "")
    env_heldout_dir = os.environ.get(HELDOUT_DIR_ENV, "")

    visibility = args.visibility or (env_visibility.strip() or None) or "dev"
    if visibility not in ("dev", "heldout", "all"):
        print(f"invalid visibility {visibility!r} (want dev|heldout|all)", flush=True)
        return 2

    scenarios = args.scenarios
    if not scenarios and env_scenarios:
        scenarios = [s.strip() for s in env_scenarios.split(",") if s.strip()]
    if not scenarios:
        # 2. Build config (needed for nothing here; scenarios first for push filtering)
        scenarios = list_scenarios(visibility=visibility)

    # 1. Heldout gate — dev runs skip it entirely (REQ-007).
    heldout_seeds: dict[str, int] | None = None
    manifest_sha: str | None = None
    if visibility in ("heldout", "all"):
        heldout_dir = args.heldout_dir or (env_heldout_dir.strip() or None)
        try:
            check_heldout_sha(heldout_dir=heldout_dir)
            heldout_seeds = load_heldout_seeds(heldout_dir=heldout_dir)
        except AssertionError as exc:
            print(f"heldout SHA check failed: {exc}", flush=True)
            return 2
        manifest_sha = heldout_manifest_sha()
        # Push private seeds to the simulator admin plane so live arms
        # serve the same data the verifier judges (ids only in output).
        admin_url = os.environ.get(SIMULATOR_ADMIN_URL_ENV, "").strip() or None
        if admin_url:
            assert heldout_seeds is not None
            push_targets = [s for s in scenarios if s in heldout_seeds]
            for sid in push_targets:
                try:
                    push_simulator_seed(sid, heldout_seeds[sid], admin_url=admin_url)
                except RuntimeError as exc:
                    print(f"heldout simulator push failed: {exc}", flush=True)
                    return 1
        print(
            f"bench harness: heldout run on {len(scenarios)} scenarios "
            f"({sum(1 for s in scenarios if heldout_seeds and s in heldout_seeds)} private), "
            f"manifest {(manifest_sha or 'unknown')[:12]}",
            flush=True,
        )

    # 2. Build config
    cfg_kwargs: dict[str, Any] = {}
    if args.trials is not None:
        cfg_kwargs["trials_per_scenario"] = args.trials
    if args.seed is not None:
        cfg_kwargs["seed"] = args.seed
    if args.token_ceiling is not None:
        cfg_kwargs["token_ceiling"] = args.token_ceiling
    if args.wall_clock is not None:
        cfg_kwargs["wall_clock_s"] = args.wall_clock
    if args.control_api_url is not None:
        cfg_kwargs["control_api_url"] = args.control_api_url
    if args.verifier_url is not None:
        cfg_kwargs["verifier_url"] = args.verifier_url

    # Env overrides (for Docker Compose; scenarios/visibility resolved above)
    if env_trials and "trials_per_scenario" not in cfg_kwargs:
        try:  # noqa: SIM105
            cfg_kwargs["trials_per_scenario"] = int(env_trials)
        except ValueError:
            pass
    if env_seed and "seed" not in cfg_kwargs:
        try:  # noqa: SIM105
            cfg_kwargs["seed"] = int(env_seed)
        except ValueError:
            pass
    # REQ-052: wall-clock / token ceiling env overrides (sandbox keyed runs).
    env_wall = os.environ.get("BENCH_WALL_CLOCK", "").strip()
    if env_wall and "wall_clock_s" not in cfg_kwargs:
        try:  # noqa: SIM105
            cfg_kwargs["wall_clock_s"] = float(env_wall)
        except ValueError:
            pass
    env_ceiling = os.environ.get("BENCH_TOKEN_CEILING", "").strip()
    if env_ceiling and "token_ceiling" not in cfg_kwargs:
        try:  # noqa: SIM105
            cfg_kwargs["token_ceiling"] = int(env_ceiling)
        except ValueError:
            pass

    # Env URL overrides (host/container aware)
    if os.environ.get("CONTROL_API_URL") and "control_api_url" not in cfg_kwargs:
        cfg_kwargs["control_api_url"] = os.environ["CONTROL_API_URL"]
    if os.environ.get("VERIFIER_URL") and "verifier_url" not in cfg_kwargs:
        cfg_kwargs["verifier_url"] = os.environ["VERIFIER_URL"]
    # Default-in-container logic: if no explicit URL and not in container, use HOST
    if "control_api_url" not in cfg_kwargs and "verifier_url" not in cfg_kwargs and not _is_in_container():
        cfg_kwargs.setdefault("control_api_url", DEFAULT_CONTROL_API_URL_HOST)
        cfg_kwargs.setdefault("verifier_url", DEFAULT_VERIFIER_URL_HOST)

    cfg = HarnessConfig(**cfg_kwargs)

    print(f"bench harness: seed={cfg.seed} token_ceiling={cfg.token_ceiling} wall_clock={cfg.wall_clock_s}s", flush=True)
    print(f"  sandbox_image={cfg.sandbox_image} sandbox_url={cfg.sandbox_url}", flush=True)
    print(f"  docs_volume={cfg.docs_volume} control={cfg.control_api_url} verifier={cfg.verifier_url}", flush=True)

    arms_mode = args.arms or (os.environ.get("BENCH_ARMS", "").strip() or None) or "matched"
    if arms_mode == "ablation":
        from bench.ablation import (  # noqa: PLC0415
            render_ablation_table,
            render_frontier_quality_table,
            run_ablation_harness,
            write_ablation_results,
        )

        if visibility != "dev":
            print("bench harness: ablation mode runs the dev split only (heldout stays sealed)", flush=True)
            return 2
        if cfg.trials_per_scenario < 5:
            print(
                f"bench harness: ablation with {cfg.trials_per_scenario} trial(s) per scenario — "
                "intervals are illustrative below 5 (REQ-024 B3 needs >= 5)",
                flush=True,
            )
        trials, _grades, summary = run_ablation_harness(cfg, scenarios, verifier_url=cfg.verifier_url)
        paths = write_ablation_results(trials, summary, args.output_dir)
        matched = sum(1 for t in trials if t.matched)
        total = len(trials)
        print(f"bench harness: {matched}/{total} ablation trials matched, wrote {[str(p) for p in paths]}", flush=True)
        if matched != total:
            print("WARNING: some trials were not matched — harness bug", flush=True)
            return 1
        print(render_ablation_table(summary), flush=True)
        # REQ-052 item 3: per-arm frontier quality (fixed / held back /
        # truncated / timed_out / no_output) next to the rate table.
        print(render_frontier_quality_table(summary), flush=True)
        return 0

    no_score = args.no_score or env_no_score.strip() == "1"
    if not os.environ.get("AUTOBRIDGE_BENCH_SKIP_PROBE") and not no_score:
        probe_err = _probe_services(cfg)
        if probe_err:
            print(probe_err, flush=True)
            return 1

    bench_result = run_harness(cfg, scenarios=scenarios)
    paths = write_results(bench_result, output_dir=args.output_dir)
    write_bench_summary(bench_result, pathlib.Path(args.output_dir) / "_summary.json")

    matched = sum(1 for t in bench_result.trials if t.matched)
    total = len(bench_result.trials)
    print(f"bench harness: {matched}/{total} trials matched, wrote {len(paths)} files to {args.output_dir}", flush=True)
    if matched != total:
        print("WARNING: some trials were not matched — harness bug", flush=True)
        return 1
    guard_err = _guard_zero_tool_calls(bench_result)
    if guard_err and not no_score and not os.environ.get("AUTOBRIDGE_BENCH_SKIP_PROBE"):
        print(guard_err, flush=True)
        return 1
    if not no_score:
        leak_audit_arg: dict[str, Any] | None = None
        if args.leak_audit:
            import json as _cli_json  # noqa: PLC0415

            try:
                loaded = _cli_json.loads(pathlib.Path(args.leak_audit).read_text(encoding="utf-8"))
                leak_audit_arg = loaded if isinstance(loaded, dict) else None
            except (OSError, ValueError) as exc:
                print(f"bench harness: ignoring unreadable --leak-audit file: {exc}", flush=True)
        scorecard = score_and_report(
            bench_result,
            output_dir=args.output_dir,
            scorecard_path=args.scorecard,
            heldout_seeds=heldout_seeds,
            heldout_manifest_sha=manifest_sha,
            leak_audit=leak_audit_arg,
        )
        wins = len(scorecard.get("wins", []))
        losses = len(scorecard.get("losses", []))
        print(f"bench scorecard: {args.scorecard} ({wins} wins, {losses} losses)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
