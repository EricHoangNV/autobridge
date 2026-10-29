"""W8.5 — idempotent/reproducible bench, scorecard pipeline, heldout gate (plan §2.10).

``make bench`` must be idempotent and reproducible from seeds, check the
heldout SHA before the run, and produce ``experiments/scorecard.md`` with
wins and losses.  These tests pin that contract without Docker:

* canonical comparison ignores wall-clock timing (which always varies);
  everything else must be byte-identical for the same seed.
* ``score_and_report`` writes ``_scorecard.json`` + ``scorecard.md`` that
  pass ``validate_scorecard_schema``.
* ``main`` runs the full pipeline (harness → score → report), honors
  ``--visibility`` / ``BENCH_VISIBILITY`` and ``--no-score`` /
  ``BENCH_NO_SCORE``, and fails closed (exit 2) on heldout SHA mismatch.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from bench.harness import (
    ArmResult,
    HarnessConfig,
    HarnessInputs,
    assert_results_reproducible,
    canonical_json_bytes,
    canonical_payload,
    check_heldout_sha,
    list_scenarios,
    main,
    run_harness,
    score_and_report,
    write_results,
)
from bench.scoring import validate_scorecard_schema


def _fixed_runners(ab_ms: float = 10.0, bl_ms: float = 20.0):
    """Deterministic runners (fixed elapsed) so canonical bytes can match."""

    def ab(inp: HarnessInputs) -> ArmResult:
        return ArmResult(arm="autobridge", inputs=inp, success=True, elapsed_ms=ab_ms)

    def bl(inp: HarnessInputs) -> ArmResult:
        return ArmResult(arm="baseline", inputs=inp, success=True, elapsed_ms=bl_ms)

    return ab, bl


def _verify_all(verdict: str):
    def fn(_spec: Any, _scenario_id: str) -> dict[str, Any]:
        if verdict == "pass":
            return {"verdict": "pass", "score": 1.0, "failed_checks": [], "regression": "pass"}
        return {
            "verdict": "fail",
            "score": 0.25,
            "failed_checks": [{"name": "record_set_mismatch", "count": 3}],
            "regression": "pass",
        }

    return fn


# --- canonical_payload ---


def test_canonical_payload_drops_timing_nested() -> None:
    payload = {
        "scenario_id": "baseline",
        "elapsed_ms": 12.5,
        "started_at": 111.0,
        "finished_at": 112.0,
        "generated_at": "2026-10-27T00:00:00",
        "nested": {"elapsed_ms": 1.0, "verdict": "pass", "items": [{"started_at": 2.0, "n": 1}]},
    }
    out = canonical_payload(payload)
    assert out == {
        "scenario_id": "baseline",
        "nested": {"verdict": "pass", "items": [{"n": 1}]},
    }


def test_canonical_json_bytes_sorted_and_stable() -> None:
    a = canonical_json_bytes({"b": 1, "a": {"elapsed_ms": 5.0, "x": 2}})
    b = canonical_json_bytes({"a": {"x": 2}, "b": 1})
    assert a == b


# --- assert_results_reproducible ---


def test_reproducible_same_seed_fixed_runners() -> None:
    cfg = HarnessConfig(seed=42, scenarios=("baseline",), trials_per_scenario=1)
    ab, bl = _fixed_runners()
    r1 = run_harness(cfg, autobridge_runner=ab, baseline_runner=bl)
    r2 = run_harness(cfg, autobridge_runner=ab, baseline_runner=bl)
    assert_results_reproducible(r1, r2)  # must not raise


def test_reproducible_different_seed_raises() -> None:
    cfg1 = HarnessConfig(seed=42, scenarios=("baseline",), trials_per_scenario=1)
    cfg2 = HarnessConfig(seed=99, scenarios=("baseline",), trials_per_scenario=1)
    ab, bl = _fixed_runners()
    r1 = run_harness(cfg1, autobridge_runner=ab, baseline_runner=bl)
    r2 = run_harness(cfg2, autobridge_runner=ab, baseline_runner=bl)
    with pytest.raises(AssertionError):
        assert_results_reproducible(r1, r2)


def test_reproducible_divergent_outcomes_raise() -> None:
    cfg = HarnessConfig(seed=42, scenarios=("baseline",), trials_per_scenario=1)
    ok_ab, ok_bl = _fixed_runners()

    def failing_ab(inp: HarnessInputs) -> ArmResult:
        return ArmResult(arm="autobridge", inputs=inp, success=False, elapsed_ms=10.0, error="boom")

    r1 = run_harness(cfg, autobridge_runner=ok_ab, baseline_runner=ok_bl)
    r2 = run_harness(cfg, autobridge_runner=failing_ab, baseline_runner=ok_bl)
    with pytest.raises(AssertionError):
        assert_results_reproducible(r1, r2)


def test_write_results_byte_identical_on_rerun(tmp_path: pathlib.Path) -> None:
    """Idempotency: same seed overwrites the same files with identical bytes."""
    cfg = HarnessConfig(seed=7, scenarios=("baseline", "rename_guest_id"), trials_per_scenario=1)
    ab, bl = _fixed_runners()
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    r1 = run_harness(cfg, autobridge_runner=ab, baseline_runner=bl)
    r2 = run_harness(cfg, autobridge_runner=ab, baseline_runner=bl)
    p1 = write_results(r1, output_dir=out1)
    p2 = write_results(r2, output_dir=out2)
    assert [p.name for p in p1] == [p.name for p in p2]
    for a, b in zip(p1, p2, strict=True):
        assert a.read_bytes() == b.read_bytes()


# --- score_and_report ---


def test_score_and_report_writes_validated_scorecard(tmp_path: pathlib.Path) -> None:
    cfg = HarnessConfig(seed=42, scenarios=("baseline",), trials_per_scenario=1)
    ab, bl = _fixed_runners()
    bench = run_harness(cfg, autobridge_runner=ab, baseline_runner=bl)
    out = tmp_path / "runs"
    scorecard = score_and_report(
        bench,
        _verify_all("pass"),
        output_dir=out,
        scorecard_path=tmp_path / "scorecard.md",
    )
    validate_scorecard_schema(scorecard)  # must not raise
    scored_json = json.loads((out / "_scorecard.json").read_text(encoding="utf-8"))
    validate_scorecard_schema(scored_json)
    md = (tmp_path / "scorecard.md").read_text(encoding="utf-8")
    assert "## Wins" in md and "## Losses" in md
    assert scorecard["wins"], "all-pass run must list wins"


def test_score_and_report_lists_losses(tmp_path: pathlib.Path) -> None:
    cfg = HarnessConfig(seed=42, scenarios=("baseline",), trials_per_scenario=1)
    ab, bl = _fixed_runners()
    bench = run_harness(cfg, autobridge_runner=ab, baseline_runner=bl)
    scorecard = score_and_report(
        bench,
        _verify_all("fail"),
        output_dir=tmp_path / "runs",
        scorecard_path=tmp_path / "scorecard.md",
    )
    assert scorecard["losses"], "all-fail run must list losses (never drop rows)"
    md = (tmp_path / "scorecard.md").read_text(encoding="utf-8")
    assert "## Losses" in md


# --- main pipeline ---


def test_main_full_pipeline_writes_scorecard(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOBRIDGE_BENCH_SKIP_PROBE", "1")
    # Hermetic: the pipeline would otherwise POST the verifier hidden mode
    # over real HTTP (socket.getaddrinfo on localhost), which on macOS
    # poisons later forked children (REQ-022 DNS-hermetic fix). The URL
    # plumbing is still exercised; only the transport is faked.
    from bench import scoring as scoring_mod

    def _fake_verify(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {"verdict": "pass", "score": 1.0, "failed_checks": [], "regression": "pass"}

    monkeypatch.setattr(scoring_mod, "verify_via_http", _fake_verify)
    out = tmp_path / "runs"
    sc = tmp_path / "scorecard.md"
    ret = main(
        [
            "--scenarios", "baseline",
            "--trials", "1",
            "--seed", "42",
            "--output-dir", str(out),
            "--scorecard", str(sc),
            "--verifier-url", "http://localhost:9",
        ]
    )
    assert ret == 0
    assert (out / "baseline--trial-0.jsonl").exists()
    assert (out / "_scorecard.json").exists()
    assert sc.exists()
    validate_scorecard_schema(json.loads((out / "_scorecard.json").read_text(encoding="utf-8")))


def test_main_no_score_flag_skips_scorecard(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    ret = main(["--scenarios", "baseline", "--output-dir", str(tmp_path / "runs"), "--no-score"])
    assert ret == 0
    assert not (tmp_path / "runs" / "_scorecard.json").exists()
    assert not pathlib.Path("experiments/scorecard.md").exists()


def test_main_no_score_env_skips_scorecard(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BENCH_NO_SCORE", "1")
    ret = main(["--scenarios", "baseline", "--output-dir", str(tmp_path / "runs")])
    assert ret == 0
    assert not (tmp_path / "runs" / "_scorecard.json").exists()


def test_main_visibility_env_selects_split(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # REQ-007: a heldout run without HELDOUT_DIR is refused (exit 2) —
    # the manifest is frozen, so private seeds are required.
    monkeypatch.delenv("HELDOUT_DIR", raising=False)
    monkeypatch.setenv("BENCH_VISIBILITY", "heldout")
    monkeypatch.setenv("BENCH_NO_SCORE", "1")
    out = tmp_path / "runs"
    ret = main(["--output-dir", str(out)])
    assert ret == 2
    # The heldout split itself is still listed from the public catalog (dev metadata only).
    expected = {f"{sid}--trial-0.jsonl" for sid in list_scenarios(visibility="heldout")}
    assert expected, "heldout split must be non-empty (W2 escrow)"


def test_main_invalid_visibility_rejected(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # CLI choice → argparse exits 2
    with pytest.raises(SystemExit) as exc:
        main(["--scenarios", "baseline", "--output-dir", str(tmp_path), "--visibility", "bogus"])
    assert exc.value.code == 2
    # Env value → manual validation returns 2
    monkeypatch.setenv("BENCH_VISIBILITY", "bogus")
    assert main(["--scenarios", "baseline", "--output-dir", str(tmp_path), "--no-score"]) == 2


# --- heldout SHA gate ---


def test_check_heldout_sha_ok_and_missing_and_mismatch(tmp_path: pathlib.Path) -> None:
    import hashlib

    # Missing manifest → no-op (path with no freeze commitment).
    missing_manifest = tmp_path / "empty" / "SHA256SUMS"
    check_heldout_sha(tmp_path / "empty", manifest_path=missing_manifest)
    (tmp_path / "empty").mkdir()
    check_heldout_sha(tmp_path / "empty", manifest_path=missing_manifest)

    heldout = tmp_path / "heldout"
    heldout.mkdir()
    data = heldout / "case.yaml"
    data.write_bytes(b"id: trap\n")
    sha = hashlib.sha256(b"id: trap\n").hexdigest()
    manifest = heldout / "SHA256SUMS"
    manifest.write_text(f"{sha}  case.yaml\n")
    check_heldout_sha(heldout, manifest_path=manifest)  # must not raise

    data.write_bytes(b"id: tampered\n")
    with pytest.raises(AssertionError, match="SHA mismatch"):
        check_heldout_sha(heldout, manifest_path=manifest)
