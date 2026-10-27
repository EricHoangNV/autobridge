"""REQ-007 — private heldout seeds: gate, loading, sim push, verify override, no-leak.

Unit tests build a temp ``HELDOUT_DIR`` with fake seeds and a matching
temp manifest (never the real directory, never the committed manifest
for the pass path).
"""

from __future__ import annotations

import hashlib
import json
import pathlib  # noqa: TC003
from typing import Any

import pytest

from autobridge.bench.harness import (
    HarnessConfig,
    heldout_manifest_sha,
    load_heldout_seeds,
    push_simulator_seed,
    run_harness,
    score_and_report,
    write_bench_summary,
    write_results,
)
from autobridge.bench.scoring import (
    validate_scorecard_schema,
    verify_via_http,
    wrap_verify_fn_with_seeds,
)

PRIVATE_SEED = 975311


def _heldout_dir(tmp_path: pathlib.Path, seeds: dict[str, int], nonce: str = "deadbeef01") -> tuple[pathlib.Path, pathlib.Path]:
    """Build a temp HELDOUT_DIR with seed files + matching manifest."""
    root = tmp_path / "heldout"
    root.mkdir()
    lines = []
    for sid, seed in seeds.items():
        blob = f"id: {sid}\nseed: {seed}\nnonce: {nonce}\n".encode()
        (root / f"{sid}.yaml").write_bytes(blob)
        lines.append(f"{hashlib.sha256(blob).hexdigest()}  {sid}.yaml\n")
    manifest = root / "SHA256SUMS"
    manifest.write_text("".join(lines))
    return root, manifest


# --- loading ---


def test_load_heldout_seeds_valid(tmp_path: pathlib.Path) -> None:
    root, manifest = _heldout_dir(tmp_path, {"endpoint_removed": 11, "token_revoked": 22})
    assert load_heldout_seeds(root, manifest_path=manifest) == {"endpoint_removed": 11, "token_revoked": 22}


def test_load_heldout_seeds_rejects_mismatch(tmp_path: pathlib.Path) -> None:
    root, manifest = _heldout_dir(tmp_path, {"endpoint_removed": 11})
    (root / "endpoint_removed.yaml").write_bytes(b"id: endpoint_removed\nseed: 99\nnonce: x\n")
    with pytest.raises(AssertionError, match="mismatch"):
        load_heldout_seeds(root, manifest_path=manifest)


def test_load_heldout_seeds_rejects_missing_file(tmp_path: pathlib.Path) -> None:
    root, manifest = _heldout_dir(tmp_path, {"endpoint_removed": 11})
    (root / "endpoint_removed.yaml").unlink()
    with pytest.raises(AssertionError, match="missing"):
        load_heldout_seeds(root, manifest_path=manifest)


def test_load_heldout_seeds_requires_heldout_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.delenv("HELDOUT_DIR", raising=False)
    with pytest.raises(AssertionError, match="HELDOUT_DIR not set"):
        load_heldout_seeds(None, manifest_path=tmp_path / "SHA256SUMS")


def test_load_heldout_seeds_rejects_bad_shape(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "heldout"
    root.mkdir()
    bad = root / "s1.yaml"
    sha = hashlib.sha256(b"junk").hexdigest()
    manifest = root / "SHA256SUMS"
    manifest.write_text(f"{sha}  s1.yaml\n")
    # Not a mapping with id/seed/nonce
    bad.write_bytes(b"junk")
    with pytest.raises(AssertionError, match="mapping"):
        load_heldout_seeds(root, manifest_path=manifest)
    bad.write_text("id: other\nseed: 5\nnonce: n\n")
    (root / "s1.yaml").write_bytes(b"id: other\nseed: 5\nnonce: n\n")
    manifest.write_text(f"{hashlib.sha256((root / 's1.yaml').read_bytes()).hexdigest()}  s1.yaml\n")
    with pytest.raises(AssertionError, match="does not match filename"):
        load_heldout_seeds(root, manifest_path=manifest)
    (root / "s1.yaml").write_bytes(b"id: s1\nseed: -3\nnonce: n\n")
    manifest.write_text(f"{hashlib.sha256((root / 's1.yaml').read_bytes()).hexdigest()}  s1.yaml\n")
    with pytest.raises(AssertionError, match="non-negative"):
        load_heldout_seeds(root, manifest_path=manifest)


def test_heldout_manifest_sha_missing_is_none(tmp_path: pathlib.Path) -> None:
    assert heldout_manifest_sha(tmp_path / "nope") is None


# --- simulator push ---


class _Resp:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


def test_push_simulator_seed_posts_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, json: Any = None, headers: Any = None, timeout: Any = None) -> _Resp:  # noqa: ARG001
        calls.append({"url": url, "json": json, "headers": headers})
        return _Resp(200)

    monkeypatch.setattr("httpx.post", fake_post)
    push_simulator_seed("token_revoked", PRIVATE_SEED, admin_url="http://sim:8001")
    assert calls == [
        {
            "url": "http://sim:8001/admin/scenario",
            "json": {"id": "token_revoked", "seed": PRIVATE_SEED},
            "headers": {"X-Admin-Network": "admin-net"},
        }
    ]


def test_push_simulator_seed_rejects_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp(500))  # noqa: ARG005
    with pytest.raises(RuntimeError, match="token_revoked"):
        push_simulator_seed("token_revoked", 1, admin_url="http://sim:8001")


def test_push_simulator_seed_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> _Resp:  # noqa: ARG001
        raise ConnectionError("down")

    monkeypatch.setattr("httpx.post", boom)
    with pytest.raises(RuntimeError, match="unreachable"):
        push_simulator_seed("token_revoked", 1, admin_url="http://sim:8001")


# --- verify wrapper ---


def test_wrap_verify_fn_two_arg_fallback() -> None:
    seen: list[tuple[Any, str]] = []

    def two_arg(spec: Any, scenario_id: str) -> dict[str, Any]:
        seen.append((spec, scenario_id))
        return {"verdict": "pass", "score": 1.0, "failed_checks": [], "regression": "pass"}

    wrapped = wrap_verify_fn_with_seeds(two_arg, {"s1": PRIVATE_SEED})
    assert wrapped(None, "s1")["verdict"] == "pass"
    assert seen == [(None, "s1")]
    # Scenario without a private seed passes through untouched.
    assert wrapped(None, "other")["verdict"] == "pass"


def test_wrap_verify_fn_receives_seed() -> None:
    seen: list[tuple[Any, str, Any]] = []

    def three_arg(spec: Any, scenario_id: str, seed: Any = None) -> dict[str, Any]:  # noqa: ARG001
        seen.append((spec, scenario_id, seed))
        return {"verdict": "pass", "score": 1.0, "failed_checks": [], "regression": "pass"}

    wrapped = wrap_verify_fn_with_seeds(three_arg, {"s1": PRIVATE_SEED})
    wrapped({"a": 1}, "s1")
    assert seen == [({"a": 1}, "s1", PRIVATE_SEED)]


def test_verify_via_http_seed_body(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[Any] = []

    def fake_post(url: str, json: Any = None, timeout: Any = None) -> Any:  # noqa: ARG001
        bodies.append(json)

        class R:
            status_code = 200

            @staticmethod
            def json() -> dict[str, Any]:
                return {"verdict": "pass", "score": 1.0, "failed_checks": [], "regression": "pass"}

        return R()

    monkeypatch.setattr("httpx.post", fake_post)
    verify_via_http(None, "s1", verifier_url="http://x", seed=PRIVATE_SEED)
    assert bodies[0]["seed"] == PRIVATE_SEED
    verify_via_http(None, "s1", verifier_url="http://x")
    assert "seed" not in bodies[1]


# --- main wiring: heldout run pushes seeds, scores with override, leaks nothing ---


def test_main_heldout_wires_private_seeds(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import autobridge.bench.harness as harness

    out = tmp_path / "runs"
    sc = tmp_path / "scorecard.md"
    monkeypatch.setattr(harness, "check_heldout_sha", lambda *a, **k: None)  # noqa: ARG005
    monkeypatch.setattr(harness, "load_heldout_seeds", lambda *a, **k: {"baseline": PRIVATE_SEED})  # noqa: ARG005
    monkeypatch.setattr(harness, "heldout_manifest_sha", lambda *a, **k: "abc123")  # noqa: ARG005
    pushes: list[dict[str, Any]] = []

    def fake_post(url: str, json: Any = None, headers: Any = None, timeout: Any = None) -> _Resp:  # noqa: ARG001
        pushes.append({"url": url, "json": json})
        return _Resp(200)

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://sim-admin:8001")
    monkeypatch.delenv("HELDOUT_DIR", raising=False)

    ret = harness.main(
        [
            "--scenarios", "baseline",
            "--visibility", "heldout",
            "--heldout-dir", str(tmp_path),
            "--output-dir", str(out),
            "--scorecard", str(sc),
            "--verifier-url", "http://localhost:9",
        ]
    )
    assert ret == 0
    # Private seed reached the simulator admin plane ...
    sim_pushes = [c for c in pushes if c["url"].endswith("/admin/scenario")]
    assert sim_pushes == [
        {"url": "http://sim-admin:8001/admin/scenario", "json": {"id": "baseline", "seed": PRIVATE_SEED}}
    ]
    # ... and the verifier hidden-mode call (seed override, never echoed).
    verify_calls = [c for c in pushes if c["url"].endswith("/verify")]
    assert verify_calls, "expected verifier hidden-mode calls"
    assert all(c["json"].get("seed") == PRIVATE_SEED for c in verify_calls)
    # ... but appears in no output and no stdout.
    needle = str(PRIVATE_SEED)
    for p in list(out.glob("*.jsonl")) + [out / "_summary.json", out / "_scorecard.json", sc]:
        assert needle not in p.read_text(encoding="utf-8"), f"seed leaked in {p.name}"
    assert needle not in capsys.readouterr().out
    card = json.loads((out / "_scorecard.json").read_text(encoding="utf-8"))
    validate_scorecard_schema(card)
    assert card["heldout"] is True
    assert card["heldout_manifest_sha"] == "abc123"
    md = sc.read_text(encoding="utf-8")
    assert "private (manifest abc123)" in md


def test_main_heldout_push_failure_is_error(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import autobridge.bench.harness as harness

    monkeypatch.setattr(harness, "check_heldout_sha", lambda *a, **k: None)  # noqa: ARG005
    monkeypatch.setattr(harness, "load_heldout_seeds", lambda *a, **k: {"baseline": PRIVATE_SEED})  # noqa: ARG005
    monkeypatch.setattr(harness, "heldout_manifest_sha", lambda *a, **k: "abc123")  # noqa: ARG005

    def boom(*a: Any, **k: Any) -> _Resp:  # noqa: ARG001
        raise ConnectionError("sim down")

    monkeypatch.setattr("httpx.post", boom)
    monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://sim-admin:8001")
    ret = harness.main(
        ["--scenarios", "baseline", "--visibility", "heldout", "--output-dir", str(tmp_path / "o"), "--no-score"]
    )
    assert ret == 1


def test_scorecard_header_dev_run(tmp_path: pathlib.Path) -> None:
    cfg = HarnessConfig(seed=42, scenarios=("baseline",), trials_per_scenario=1)
    bench = run_harness(cfg)
    card = score_and_report(
        bench, lambda _s, _i: {"verdict": "pass", "score": 1.0, "failed_checks": [], "regression": "pass"},
        output_dir=tmp_path / "runs",
        scorecard_path=tmp_path / "scorecard.md",
    )
    validate_scorecard_schema(card)
    assert card["heldout"] is False
    assert "public YAML seeds" in (tmp_path / "scorecard.md").read_text(encoding="utf-8")


def test_private_seed_absent_from_all_artifacts(tmp_path: pathlib.Path) -> None:
    cfg = HarnessConfig(seed=42, scenarios=("baseline",), trials_per_scenario=1)
    bench = run_harness(cfg)
    seen: list[tuple[Any, str, Any]] = []

    def three_arg(spec: Any, scenario_id: str, seed: Any = None) -> dict[str, Any]:  # noqa: ARG001
        seen.append((spec, scenario_id, seed))
        return {"verdict": "pass", "score": 1.0, "failed_checks": [], "regression": "pass"}

    out = tmp_path / "runs"
    card = score_and_report(
        bench,
        three_arg,
        output_dir=out,
        scorecard_path=tmp_path / "scorecard.md",
        heldout_seeds={"baseline": PRIVATE_SEED},
        heldout_manifest_sha="feedface",
    )
    assert seen and seen[0][2] == PRIVATE_SEED  # seed reached the oracle call
    needle = str(PRIVATE_SEED)
    write_results(bench, output_dir=out)
    write_bench_summary(bench, out / "_summary.json")
    for p in list(out.glob("*.jsonl")) + [out / "_summary.json", out / "_scorecard.json", tmp_path / "scorecard.md"]:
        assert needle not in p.read_text(encoding="utf-8"), f"seed leaked in {p.name}"
    assert card["heldout_manifest_sha"] == "feedface"
