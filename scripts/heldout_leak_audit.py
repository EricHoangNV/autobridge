#!/usr/bin/env python3
"""Held-out leak audit (REQ-024 A1).

Proves the private held-out seeds never leaked into anything the builder
could see: ralph-loop transcripts, the full git history, worktree
checkouts, or the project folder itself.

Reads each private file's nonce and seed **in memory only**, plus its
sha256, then scans every source for any occurrence of a nonce, a seed
file's sha256, or a seed file's raw bytes.  Output is one line per
source with counts (never secret values)::

    transcripts: 0 hits in 109 files
    git-blobs: 0 hits in 2140 blobs
    worktrees: 0 hits in 312 files
    project: 0 hits in 4001 files (excluded: 2 public-manifest blobs/files)

Exit 0 when clean, 1 on any hit, 2 on usage errors (missing
``HELDOUT_DIR``, missing manifest, or ``HELDOUT_DIR`` inside the repo).

Bare seed integers are deliberately NOT searched: seeds are small ints
that occur everywhere (false positives).  The exact raw file bytes
(which contain the ``seed:`` line verbatim) cover that case with no
false positives.

The committed manifest (``verifier/hidden_cases/heldout/SHA256SUMS``)
legitimately contains the seed files' sha256 hashes, so the manifest
file itself — and any git blob identical to a committed manifest — is
excluded from the scan and reported as excluded.  The owner-held
``HELDOUT_DIR`` must live outside the repo; pointing it inside is
refused.  The post-reveal folder
``verifier/hidden_cases/heldout/revealed/`` (owner-published after the
Nov 3 run) is likewise excluded from the project scan and reported.

Usage (owner runs it with HELDOUT_DIR)::

    HELDOUT_DIR=~/autobridge-heldout python3 scripts/heldout_leak_audit.py
    HELDOUT_DIR=~/autobridge-heldout python3 scripts/heldout_leak_audit.py --write experiments/benchmark_runs/_leak_audit.json

The ``--write`` JSON carries only counts and hit locations (never
secrets); ``score_and_report`` picks it up as the scorecard's
``leak_audit`` field.  Stdlib only, no network, no DNS.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "verifier" / "hidden_cases" / "heldout" / "SHA256SUMS"
REVEALED_DIR = pathlib.Path("verifier/hidden_cases/heldout/revealed")

MAX_BLOB_BYTES = 5_000_000
MIN_SECRET_LEN = 8
MAX_HIT_PATHS = 20

SKIP_DIRS = {".git", "node_modules", ".next", "__pycache__", ".venv", ".mypy_cache", ".pytest_cache"}

_MANIFEST_LINE = re.compile(rb"^[0-9a-f]{64} +\S+.*$")


def _fail(msg: str) -> int:
    print(f"leak-audit: {msg}", file=sys.stderr)
    return 2


def read_manifest_entries(manifest: pathlib.Path) -> list[tuple[str, str]]:
    """Parse ``SHA256SUMS`` into ``[(sha, rel), ...]``."""
    entries: list[tuple[str, str]] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            raise ValueError(f"bad manifest line: {line!r}")
        entries.append((parts[0], parts[-1]))
    if not entries:
        raise ValueError(f"manifest {manifest} lists no files")
    return entries


def load_secrets(heldout_dir: pathlib.Path, manifest: pathlib.Path) -> tuple[list[bytes], list[str]]:
    """Read private files; return ``(secret_tokens, rel_names)``.

    Tokens are the raw file bytes, the nonce bytes and the sha256 hex
    bytes — never logged, printed or serialized by any caller.
    """
    entries = read_manifest_entries(manifest)
    tokens: list[bytes] = []
    rels: list[str] = []
    for expected_sha, rel in entries:
        target = heldout_dir / rel
        if not target.is_file():
            raise FileNotFoundError(f"heldout file missing: {rel}")
        raw = target.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected_sha:
            raise ValueError(f"heldout file {rel} does not match manifest (refusing to audit against wrong data)")
        rels.append(rel)
        if len(raw) >= MIN_SECRET_LEN:
            tokens.append(raw)
        tokens.append(expected_sha.encode("ascii"))
        # Nonce: the high-entropy token inside the YAML (REQ-024: scan nonces).
        m = re.search(rb"nonce:\s*([A-Za-z0-9_+-]{8,})", raw)
        if m and len(m.group(1)) >= MIN_SECRET_LEN:
            tokens.append(m.group(1))
    # Longest first so raw-bytes kinds win over short nonces in reporting.
    tokens.sort(key=len, reverse=True)
    return tokens, rels


def _is_manifest_blob(data: bytes, rels: set[str]) -> bool:
    """True when *data* is just a committed manifest (hashes listed there are public)."""
    lines = [ln for ln in data.splitlines() if ln.strip()]
    if not lines:
        return False
    for ln in lines:
        if not _MANIFEST_LINE.match(ln):
            return False
    for ln in lines:
        rel = ln.split()[-1].decode("utf-8", "ignore")
        if rel not in rels:
            return False
    return True


def scan_bytes(data: bytes, tokens: list[bytes]) -> bool:
    """True when any secret token occurs in *data*."""
    return any(tok in data for tok in tokens)


def scan_working_tree(
    root: pathlib.Path,
    tokens: list[bytes],
    *,
    exclude_files: set[pathlib.Path],
    extra_skip_dirs: set[str] | None = None,
) -> tuple[int, list[str]]:
    """Walk *root*; return ``(files_scanned, hit_paths)`` (hit paths capped)."""
    skip = set(SKIP_DIRS) | (extra_skip_dirs or set())
    scanned = 0
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for name in sorted(filenames):
            path = pathlib.Path(dirpath) / name
            if path in exclude_files:
                continue
            try:
                if path.stat().st_size > MAX_BLOB_BYTES:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            scanned += 1
            if scan_bytes(data, tokens):
                if len(hits) < MAX_HIT_PATHS:
                    hits.append(str(path))
                else:
                    hits.append("… (more)")
                    return scanned, hits
    return scanned, hits


def git_blob_list(repo: pathlib.Path) -> list[tuple[str, int]]:
    """List ``(sha, size)`` for every blob in the repo's object store."""
    proc = subprocess.run(
        ["git", "cat-file", "--batch-all-objects", "--batch-check=%(objecttype) %(objectname) %(objectsize)"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git blob enumeration failed: {proc.stderr.strip()[:200]}")
    blobs: list[tuple[str, int]] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "blob":
            try:
                blobs.append((parts[1], int(parts[2])))
            except ValueError:
                continue
    return blobs


def scan_git_blobs(
    repo: pathlib.Path, tokens: list[bytes], rels: set[str]
) -> tuple[int, int, list[str]]:
    """Scan every git blob; return ``(blobs_scanned, excluded_manifests, hit_shas)``."""
    blobs = git_blob_list(repo)
    min_len = min((len(t) for t in tokens), default=MIN_SECRET_LEN)
    candidates = [(sha, size) for sha, size in blobs if min_len <= size <= MAX_BLOB_BYTES]
    scanned = 0
    excluded = 0
    hits: list[str] = []
    if not candidates:
        return 0, 0, []
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=repo,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdin is not None and proc.stdout is not None
    try:
        for sha, _size in candidates:
            proc.stdin.write(f"{sha}\n".encode("ascii"))
            proc.stdin.flush()
            header = proc.stdout.readline().decode("ascii", "ignore").strip().split()
            if len(header) != 3 or header[1] != "blob":
                continue
            try:
                size = int(header[2])
            except ValueError:
                continue
            data = proc.stdout.read(size + 1)[:size]
            scanned += 1
            if _is_manifest_blob(data, rels):
                excluded += 1
                continue
            if scan_bytes(data, tokens):
                if len(hits) < MAX_HIT_PATHS:
                    hits.append(sha)
                else:
                    hits.append("… (more)")
                    break
    finally:
        with contextlib.suppress(BrokenPipeError):
            proc.stdin.close()
        proc.wait(timeout=60)
    return scanned, excluded, hits


def audit(
    *,
    repo: pathlib.Path,
    heldout_dir: pathlib.Path,
    manifest: pathlib.Path,
    worktrees_dirs: list[pathlib.Path],
) -> dict:
    """Run the full audit; return a JSON-safe result dict (no secrets inside)."""
    tokens, rels = load_secrets(heldout_dir, manifest)
    rel_set = set(rels)
    try:
        heldout_resolved = heldout_dir.resolve()
        repo_resolved = repo.resolve()
        inside = heldout_resolved == repo_resolved or repo_resolved in heldout_resolved.parents
    except OSError:
        inside = False
    if inside:
        raise ValueError(f"HELDOUT_DIR {heldout_dir} is inside the repo {repo} — move it outside")

    manifest_abs = manifest.resolve()
    exclude_files = {manifest_abs}

    # 1. transcripts
    transcript_dir = repo / "ralph-logs"
    transcripts = sorted(transcript_dir.glob("*.jsonl")) if transcript_dir.is_dir() else []
    t_hits: list[str] = []
    for path in transcripts:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if scan_bytes(data, tokens):
            t_hits.append(path.name)
            if len(t_hits) >= MAX_HIT_PATHS:
                t_hits.append("… (more)")
                break

    # 2. git blobs
    b_scanned, b_excluded, b_hits = scan_git_blobs(repo, tokens, rel_set)

    # 3. worktrees
    w_scanned = 0
    w_hits: list[str] = []
    for wdir in worktrees_dirs:
        if not wdir.is_dir():
            continue
        scanned, hits = scan_working_tree(
            wdir, tokens, exclude_files=exclude_files, extra_skip_dirs={".git"}
        )
        w_scanned += scanned
        w_hits.extend(hits[: max(0, MAX_HIT_PATHS - len(w_hits))])

    # 4. project folder (repo working tree minus .git/skips/manifest).
    # NOTE: the post-reveal folder is NOT skipped in the walk — hits under
    # it are counted separately below as excluded, so a pre-reveal leak
    # there would still be visible in the exclusion count.
    p_scanned, p_hits = scan_working_tree(
        repo,
        tokens,
        exclude_files=exclude_files,
    )
    # NOTE: excluding the whole verifier/ tree would blind the audit to a
    # real leak there; instead exclude only the post-reveal folder below by
    # re-adding verifier hits that fall under revealed/ as excluded. The
    # walk above skipped nothing under verifier/, so filter here:
    p_hits_filtered: list[str] = []
    revealed_excluded = 0
    for h in p_hits:
        try:
            rel = pathlib.Path(h).resolve().relative_to(repo_resolved)
        except ValueError:
            p_hits_filtered.append(h)
            continue
        if rel.parts[:3] == REVEALED_DIR.parts[:3]:
            revealed_excluded += 1
        else:
            p_hits_filtered.append(h)

    ok = not (t_hits or b_hits or w_hits or p_hits_filtered)
    return {
        "ok": ok,
        "manifest_sha": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "sources": {
            "transcripts": {"hits": len(t_hits), "files": len(transcripts), "hit_paths": t_hits},
            "git-blobs": {"hits": len(b_hits), "blobs": b_scanned, "hit_shas": b_hits},
            "worktrees": {"hits": len(w_hits), "files": w_scanned, "hit_paths": w_hits},
            "project": {"hits": len(p_hits_filtered), "files": p_scanned, "hit_paths": p_hits_filtered},
        },
        "excluded": {
            "public_manifest_blobs": b_excluded,
            "public_manifest_files": 1,
            "post_reveal_files": revealed_excluded,
        },
    }


def default_worktrees_dirs(repo: pathlib.Path) -> list[pathlib.Path]:
    """Candidate ``.worktrees`` locations (repo-local or sibling of checkout)."""
    candidates = [repo / ".worktrees", repo.parent / ".worktrees"]
    seen: list[pathlib.Path] = []
    for cand in candidates:
        resolved = cand.resolve()
        if resolved not in [s.resolve() for s in seen]:
            seen.append(cand)
    return seen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Held-out leak audit (REQ-024 A1)")
    parser.add_argument("--heldout-dir", default=os.environ.get("HELDOUT_DIR", ""), help="private seed dir (default: $HELDOUT_DIR)")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="committed SHA256SUMS manifest")
    parser.add_argument("--repo", default=str(ROOT), help="project folder to scan (default: repo root)")
    parser.add_argument("--worktrees-dir", action="append", default=[], help="extra .worktrees dir to scan (repeatable)")
    parser.add_argument("--write", default="", help="write JSON result (counts only, never secrets) to this path")
    args = parser.parse_args(argv)

    if not args.heldout_dir:
        return _fail("HELDOUT_DIR not set (refusing to audit without private seeds)")
    heldout_dir = pathlib.Path(args.heldout_dir)
    manifest = pathlib.Path(args.manifest)
    repo = pathlib.Path(args.repo)
    if not heldout_dir.is_dir():
        return _fail(f"heldout dir not found: {heldout_dir}")
    if not manifest.is_file():
        return _fail(f"manifest not found: {manifest}")
    if shutil.which("git") is None:
        return _fail("git binary not found (needed to enumerate history blobs)")

    try:
        result = audit(
            repo=repo,
            heldout_dir=heldout_dir,
            manifest=manifest,
            worktrees_dirs=[pathlib.Path(d) for d in args.worktrees_dir] or default_worktrees_dirs(repo),
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        return _fail(str(exc))

    sources = result["sources"]
    print(f"transcripts: {sources['transcripts']['hits']} hits in {sources['transcripts']['files']} files")
    print(f"git-blobs: {sources['git-blobs']['hits']} hits in {sources['git-blobs']['blobs']} blobs")
    print(f"worktrees: {sources['worktrees']['hits']} hits in {sources['worktrees']['files']} files")
    print(
        f"project: {sources['project']['hits']} hits in {sources['project']['files']} files "
        f"(excluded: {result['excluded']['public_manifest_blobs']} public-manifest blobs/files, "
        f"{result['excluded']['post_reveal_files']} post-reveal files)"
    )
    if args.write:
        out = pathlib.Path(args.write)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(result)
        payload["generated_at"] = datetime.datetime.now(datetime.UTC).isoformat()
        out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    if not result["ok"]:
        print("FAIL: secret material found where the builder could see it", file=sys.stderr)
        return 1
    print("OK: no held-out seed material in transcripts, history, worktrees or project")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
