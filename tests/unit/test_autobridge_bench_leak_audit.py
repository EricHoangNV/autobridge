"""REQ-024 A — held-out integrity: leak audit, isolation check, reveal guard, scorecard leak line.

The audit script is stdlib-only and hermetic: tests build a temp
``HELDOUT_DIR`` plus a temp git repo (never the real directory, never the
committed manifest for the pass path) and import the script in-process.
No network, no DNS.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
from typing import Any

import pytest

from bench.harness import _load_leak_audit
from bench.report import render_scorecard_markdown
from bench.scoring import score_bench, validate_scorecard_schema

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
NONCE = "abcdef0123456789beef"


def _load_script(name: str):
    path = SCRIPTS / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", "_mod"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AUDIT = _load_script("heldout_leak_audit.py")
ISOLATION = _load_script("heldout_isolation_check.py")


def _fake_heldout(tmp_path: pathlib.Path, seeds: dict[str, int], nonce: str = NONCE) -> tuple[pathlib.Path, pathlib.Path]:
    """Temp HELDOUT_DIR with seed files + matching manifest (bytes-stable)."""
    root = tmp_path / "holdout"
    root.mkdir()
    lines = []
    for sid, seed in seeds.items():
        blob = f"id: {sid}\nseed: {seed}\nnonce: {nonce}\n".encode()
        (root / f"{sid}.yaml").write_bytes(blob)
        lines.append(f"{hashlib.sha256(blob).hexdigest()}  {sid}.yaml\n")
    manifest = root / "SHA256SUMS"
    manifest.write_text("".join(lines))
    return root, manifest


def _git_run(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=60)


@pytest.fixture()
def git_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    if shutil.which("git") is None:
        pytest.skip("git binary required")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_run(repo, "init", "-q", ".")
    _git_run(repo, "config", "user.email", "t@t")
    _git_run(repo, "config", "user.name", "t")
    (repo / "clean.txt").write_text("nothing secret here\n")
    _git_run(repo, "add", "-A")
    _git_run(repo, "commit", "-qm", "init")
    return repo


def _audit_kw(repo: pathlib.Path, holdout: pathlib.Path, manifest: pathlib.Path) -> dict[str, Any]:
    return {"repo": repo, "heldout_dir": holdout, "manifest": manifest, "worktrees_dirs": []}


# --- clean tree: exit 0, counts on every line ---


def test_audit_clean_tree_ok(tmp_path: pathlib.Path, git_repo: pathlib.Path, capsys: Any) -> None:
    holdout, manifest = _fake_heldout(tmp_path, {"demo_a": 975311})
    result = AUDIT.audit(**_audit_kw(git_repo, holdout, manifest))
    assert result["ok"] is True
    assert result["sources"]["git-blobs"]["hits"] == 0
    assert result["sources"]["project"]["hits"] == 0
    assert result["sources"]["transcripts"]["files"] == 0
    rc = AUDIT.main(["--heldout-dir", str(holdout), "--manifest", str(manifest),
                     "--repo", str(git_repo), "--worktrees-dir", str(tmp_path / "nope")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "transcripts: 0 hits in 0 files" in out
    assert "git-blobs: 0 hits in" in out
    assert NONCE not in out


def test_audit_manifest_quote_in_transcript_passes(tmp_path: pathlib.Path, git_repo: pathlib.Path) -> None:
    """Quoting the public manifest (hashes) in a transcript is not a leak.

    Owner check 2026-10-29: manifest sha256 values are public by design,
    so they are never searched — only raw bytes and nonces are.
    """
    holdout, manifest = _fake_heldout(tmp_path, {"demo_a": 975311})
    logs = git_repo / "ralph-logs"
    logs.mkdir()
    (logs / "loop-1.jsonl").write_text(
        json.dumps({"text": f"freeze manifest:\n{manifest.read_text()}"}) + "\n"
    )
    result = AUDIT.audit(**_audit_kw(git_repo, holdout, manifest))
    assert result["ok"] is True
    for source in result["sources"].values():
        assert source["hits"] == 0
        assert source["hits_by_kind"] == {"raw": 0, "nonce": 0}


def test_audit_worktree_manifest_copy_excluded(tmp_path: pathlib.Path, git_repo: pathlib.Path) -> None:
    """A manifest copy under .worktrees is excluded by content, never a hit."""
    holdout, manifest = _fake_heldout(tmp_path, {"demo_a": 975311})
    wdir = tmp_path / "wt"
    (wdir / "verifier" / "hidden_cases" / "heldout").mkdir(parents=True)
    (wdir / "verifier" / "hidden_cases" / "heldout" / "SHA256SUMS").write_bytes(
        manifest.read_bytes()
    )
    result = AUDIT.audit(
        repo=git_repo, heldout_dir=holdout, manifest=manifest, worktrees_dirs=[wdir]
    )
    assert result["ok"] is True
    assert result["sources"]["worktrees"]["hits"] == 0
    assert result["excluded"]["public_manifest_files"] >= 1


def test_audit_hit_kinds_reported_per_kind(
    tmp_path: pathlib.Path, git_repo: pathlib.Path, capsys: Any
) -> None:
    """A FAIL says what leaked: per-kind (raw/nonce) counts in stdout and JSON."""
    holdout, manifest = _fake_heldout(tmp_path, {"demo_a": 975311})
    logs = git_repo / "ralph-logs"
    logs.mkdir()
    (logs / "loop-1.jsonl").write_text(json.dumps({"text": f"nonce {NONCE} here"}) + "\n")
    (git_repo / "notes.md").write_bytes((holdout / "demo_a.yaml").read_bytes())
    result = AUDIT.audit(**_audit_kw(git_repo, holdout, manifest))
    assert result["ok"] is False
    assert result["sources"]["transcripts"]["hits_by_kind"] == {"raw": 0, "nonce": 1}
    # The transcript itself lives inside the project tree, so the project
    # scan sees it too (nonce) alongside the raw-bytes copy in notes.md.
    assert result["sources"]["project"]["hits"] == 2
    assert result["sources"]["project"]["hits_by_kind"] == {"raw": 1, "nonce": 1}
    out_path = tmp_path / "audit.json"
    rc = AUDIT.main(["--heldout-dir", str(holdout), "--manifest", str(manifest),
                     "--repo", str(git_repo), "--worktrees-dir", str(tmp_path / "nope"),
                     "--write", str(out_path)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "nonce: 1" in out
    assert "raw: 1" in out
    assert NONCE not in out  # counts only, never secret values
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["sources"]["transcripts"]["hits_by_kind"] == {"raw": 0, "nonce": 1}
    assert payload["sources"]["project"]["hits_by_kind"] == {"raw": 1, "nonce": 1}
    assert NONCE not in out_path.read_text(encoding="utf-8")


def test_audit_transcript_hit(tmp_path: pathlib.Path, git_repo: pathlib.Path, capsys: Any) -> None:
    holdout, manifest = _fake_heldout(tmp_path, {"demo_a": 975311})
    logs = git_repo / "ralph-logs"
    logs.mkdir()
    (logs / "loop-1.jsonl").write_text(json.dumps({"text": f"saw {NONCE} once"}) + "\n")
    result = AUDIT.audit(**_audit_kw(git_repo, holdout, manifest))
    assert result["ok"] is False
    assert result["sources"]["transcripts"]["hits"] == 1
    rc = AUDIT.main(["--heldout-dir", str(holdout), "--manifest", str(manifest),
                     "--repo", str(git_repo), "--worktrees-dir", str(tmp_path / "nope")])
    assert rc == 1
    out = capsys.readouterr().out
    assert "transcripts: 1 hits in 1 files" in out
    assert NONCE not in out  # counts only, never secret values


def test_audit_project_file_hit(tmp_path: pathlib.Path, git_repo: pathlib.Path) -> None:
    holdout, manifest = _fake_heldout(tmp_path, {"demo_a": 975311})
    (git_repo / "notes.md").write_text(f"seed file says {NONCE}\n")
    result = AUDIT.audit(**_audit_kw(git_repo, holdout, manifest))
    assert result["ok"] is False
    assert result["sources"]["project"]["hits"] == 1


def test_audit_history_hit_survives_removal(tmp_path: pathlib.Path, git_repo: pathlib.Path) -> None:
    holdout, manifest = _fake_heldout(tmp_path, {"demo_a": 975311})
    (git_repo / "leak.txt").write_text(f"oops {NONCE}\n")
    _git_run(git_repo, "add", "-A")
    _git_run(git_repo, "commit", "-qm", "leak")
    (git_repo / "leak.txt").unlink()
    _git_run(git_repo, "add", "-A")
    _git_run(git_repo, "commit", "-qm", "remove leak")
    result = AUDIT.audit(**_audit_kw(git_repo, holdout, manifest))
    assert result["ok"] is False
    assert result["sources"]["project"]["hits"] == 0  # working tree clean …
    assert result["sources"]["git-blobs"]["hits"] == 1  # … history is not
    assert result["sources"]["git-blobs"]["hits_by_kind"] == {"raw": 0, "nonce": 1}


def test_audit_manifest_does_not_self_hit(tmp_path: pathlib.Path, git_repo: pathlib.Path) -> None:
    holdout, manifest = _fake_heldout(tmp_path, {"demo_a": 975311})
    # A copy of the manifest inside the scanned tree must not flag its own hashes.
    (git_repo / "SHA256SUMS").write_bytes(manifest.read_bytes())
    _git_run(git_repo, "add", "-A")
    _git_run(git_repo, "commit", "-qm", "manifest copy")
    result = AUDIT.audit(
        repo=git_repo, heldout_dir=holdout, manifest=git_repo / "SHA256SUMS", worktrees_dirs=[]
    )
    assert result["ok"] is True
    assert result["excluded"]["public_manifest_blobs"] >= 1


def test_audit_revealed_folder_excluded_but_reported(tmp_path: pathlib.Path, git_repo: pathlib.Path) -> None:
    holdout, manifest = _fake_heldout(tmp_path, {"demo_a": 975311})
    revealed = git_repo / "verifier" / "hidden_cases" / "heldout" / "revealed"
    revealed.mkdir(parents=True)
    (revealed / "demo_a.yaml").write_bytes((holdout / "demo_a.yaml").read_bytes())
    result = AUDIT.audit(**_audit_kw(git_repo, holdout, manifest))
    assert result["ok"] is True
    assert result["sources"]["project"]["hits"] == 0
    assert result["excluded"]["post_reveal_files"] == 1


def test_audit_refuses_heldout_inside_repo(tmp_path: pathlib.Path, git_repo: pathlib.Path) -> None:
    inner = git_repo / "inner_holdout"
    holdout, manifest = _fake_heldout(tmp_path, {"demo_a": 1})
    inner.mkdir()
    for child in holdout.iterdir():
        (inner / child.name).write_bytes(child.read_bytes())
    rc = AUDIT.main(["--heldout-dir", str(inner), "--manifest", str(inner / "SHA256SUMS"),
                     "--repo", str(git_repo)])
    assert rc == 2


def test_audit_missing_env_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HELDOUT_DIR", raising=False)
    assert AUDIT.main(["--repo", "."]) == 2


def test_audit_write_json_has_counts_not_secrets(tmp_path: pathlib.Path, git_repo: pathlib.Path) -> None:
    holdout, manifest = _fake_heldout(tmp_path, {"demo_a": 975311})
    out = tmp_path / "audit.json"
    rc = AUDIT.main(["--heldout-dir", str(holdout), "--manifest", str(manifest),
                     "--repo", str(git_repo), "--worktrees-dir", str(tmp_path / "nope"),
                     "--write", str(out)])
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["ok"] is True
    assert "sources" in payload
    assert NONCE not in text
    assert "975311" not in text


# --- isolation check ---


def test_isolation_check_no_env_ok(capsys: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HELDOUT_DIR", raising=False)
    assert ISOLATION.main() == 0
    out = capsys.readouterr().out
    assert "HELDOUT_DIR: not set" in out
    assert "heldout isolation evidence" in out


def test_isolation_check_refuses_inside_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    import bench.harness as _h  # noqa: PLC0415

    repo_root = pathlib.Path(_h.__file__).resolve().parents[1]
    monkeypatch.setenv("HELDOUT_DIR", str(repo_root / "some-inner-dir"))
    assert ISOLATION.main() == 2


# --- scorecard leak line ---


def _bench_dict() -> dict[str, Any]:
    return {
        "config": {"seed": 11},
        "trials": [
            {"scenario_id": "baseline", "trial": 0, "matched": True,
             "autobridge": {"arm": "autobridge", "ran": True, "spec": None},
             "baseline": {"arm": "baseline", "ran": True, "spec": None}},
        ],
    }


def _pass_payload() -> dict[str, Any]:
    return {"verdict": "pass", "regression": "none"}


def test_scorecard_renders_leak_audit_clean() -> None:
    leak = {"ok": True, "sources": {
        "transcripts": {"hits": 0, "files": 109},
        "git-blobs": {"hits": 0, "blobs": 2140},
        "worktrees": {"hits": 0, "files": 312},
        "project": {"hits": 0, "files": 4001}}}
    card = score_bench(_bench_dict(), lambda _spec, _sid: _pass_payload(), leak_audit=leak)
    validate_scorecard_schema(card)
    md = render_scorecard_markdown(card)
    assert "Leak audit: clean" in md
    assert "0 hits in 109 files" in md


def test_scorecard_renders_leak_audit_not_run() -> None:
    card = score_bench(_bench_dict(), lambda _spec, _sid: _pass_payload())
    validate_scorecard_schema(card)
    assert "Leak audit: not run" in render_scorecard_markdown(card)


def test_scorecard_rejects_non_dict_leak_audit() -> None:
    card = score_bench(_bench_dict(), lambda _spec, _sid: _pass_payload())
    card["leak_audit"] = "clean"
    with pytest.raises(AssertionError):
        validate_scorecard_schema(card)


def test_load_leak_audit_missing_invalid(tmp_path: pathlib.Path) -> None:
    assert _load_leak_audit(tmp_path) is None
    bad = tmp_path / "_leak_audit.json"
    bad.write_text("{not json")
    assert _load_leak_audit(tmp_path) is None
    bad.write_text("[1, 2]")
    assert _load_leak_audit(tmp_path) is None
    bad.write_text(json.dumps({"ok": True}))
    assert _load_leak_audit(tmp_path) == {"ok": True}


# --- reveal guard (REQ-024 A4): no revealed seeds until the owner publishes ---


def test_revealed_folder_absent_or_valid() -> None:
    """Until the owner reveal commit, `revealed/` must not exist; after the
    reveal every file in it must match the frozen SHA256SUMS."""
    import bench.harness as _h  # noqa: PLC0415

    repo_root = pathlib.Path(_h.__file__).resolve().parents[1]
    revealed = repo_root / "verifier" / "hidden_cases" / "heldout" / "revealed"
    manifest = repo_root / "verifier" / "hidden_cases" / "heldout" / "SHA256SUMS"
    if not revealed.is_dir():
        return
    entries = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if parts:
            entries[parts[-1]] = parts[0]
    assert entries, "frozen manifest must list seed files"
    for child in sorted(revealed.iterdir()):
        if not child.is_file():
            continue
        actual = hashlib.sha256(child.read_bytes()).hexdigest()
        assert entries.get(child.name) == actual, f"revealed {child.name} does not match frozen SHA256SUMS"


def test_verify_heldout_reveal_target_refuses_without_reveal() -> None:
    import bench.harness as _h  # noqa: PLC0415

    repo_root = pathlib.Path(_h.__file__).resolve().parents[1]
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    assert "verify-heldout-reveal" in makefile
    assert "revealed" in makefile and "SHA256SUMS" in makefile or "check_heldout_sha" in makefile
