#!/usr/bin/env python3
"""Held-out isolation evidence (REQ-024 A2, CI-free check script).

Prints the checkable facts behind the held-out protocol — no secrets,
no network, no Docker:

1. where the repo checkout lives (sandbox scoping is shown by
   ``sbx ls`` on the owner's host; this script prints the workspace
   column's source of truth, the git top-level);
2. whether ``HELDOUT_DIR`` is set and that it resolves outside the repo
   (exit 2 when it points inside — misconfiguration);
3. static evidence that the bench reads ``HELDOUT_DIR`` only for
   heldout runs (the ``HELDOUT_DIR_ENV`` use sites in the harness);
4. the freeze commit (``ff372083``) with its local date, and the sha256
   of the frozen ``SHA256SUMS`` manifest (GitHub push time is checked on
   the owner's host via ``gh`` — the command is printed).

Usage::

    python3 scripts/heldout_isolation_check.py
    HELDOUT_DIR=~/autobridge-heldout python3 scripts/heldout_isolation_check.py
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "verifier" / "hidden_cases" / "heldout" / "SHA256SUMS"
FREEZE_COMMIT = "ff372083"


def _git(*args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def main() -> int:
    print("== heldout isolation evidence (REQ-024 A2) ==")
    top = _git("rev-parse", "--show-toplevel")
    print(f"1. repo checkout: {top or ROOT} (sandboxes mount only the project folder — verify with `sbx ls` on the host)")

    heldout = os.environ.get("HELDOUT_DIR", "").strip()
    if not heldout:
        print("2. HELDOUT_DIR: not set here (owner-only; the bench refuses heldout runs without it)")
    else:
        try:
            inside = pathlib.Path(heldout).resolve() == ROOT.resolve() or ROOT.resolve() in pathlib.Path(
                heldout
            ).resolve().parents
        except OSError:
            inside = False
        where = "INSIDE the repo (misconfigured)" if inside else "outside the repo (good)"
        print(f"2. HELDOUT_DIR={heldout}: {where}")
        if inside:
            print("FAIL: move HELDOUT_DIR outside the checkout so seeds can never be committed", file=sys.stderr)
            return 2

    print("3. bench reads HELDOUT_DIR only for heldout runs (static evidence):")
    harness = ROOT / "src" / "autobridge" / "bench" / "harness.py"
    try:
        text = harness.read_text(encoding="utf-8")
    except OSError:
        text = ""
    shown = 0
    for i, line in enumerate(text.splitlines(), start=1):
        if shown < 8 and ("HELDOUT_DIR_ENV" in line or "dev runs skip" in line.lower()):
            print(f"   harness.py:{i}: {line.strip()[:120]}")
            shown += 1
    print("   dev runs skip the gate; heldout/all runs verify + load private seeds (see check_heldout_sha/load_heldout_seeds)")

    freeze = _git("log", "-1", "--format=%H %ad", "--date=iso", FREEZE_COMMIT)
    if freeze:
        print(f"4. freeze commit {FREEZE_COMMIT}: present locally ({freeze}); push time: check GitHub (`gh api repos/{{owner}}/{{repo}}/commits/{FREEZE_COMMIT} --jq .commit.committer.date` after pushing)")
    else:
        print(f"4. freeze commit {FREEZE_COMMIT}: NOT in this clone (shallow or old checkout) — fetch origin first")
    if MANIFEST.is_file():
        print(f"   frozen manifest: {MANIFEST.relative_to(ROOT)} sha256={hashlib.sha256(MANIFEST.read_bytes()).hexdigest()}")
    else:
        print("   frozen manifest: MISSING (heldout not frozen in this checkout)")
    print("== end (no secrets printed, nothing read from HELDOUT_DIR) ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
