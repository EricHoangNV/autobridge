"""REQ-077 item 1: beacon-eval.sh helpers (discovery, ralph guard, push opt-in).

The checkout guard, protocol discovery and push decision live in
scripts/beacon-lib.sh (sourced, never executed) so every branch is
exercised here in bash without a stack, a freeze or a real push.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import time

LIB = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "beacon-lib.sh"


def _bash(script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(
        ["bash", "-c", f"source '{LIB}'\n{script}"],
        capture_output=True, text=True, timeout=60, env=merged,
    )


def _beacon_tree(root: pathlib.Path, rounds: dict[str, bool], *, old: bool = False) -> None:
    """Build experiments/beacon/<round>/protocol.md dirs; True entries hold results."""
    base = root / "experiments" / "beacon"
    if old:
        base.mkdir(parents=True, exist_ok=True)
        (base / "protocol.md").write_text("old\n", encoding="utf-8")
    for name, finished in rounds.items():
        d = base / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "protocol.md").write_text(f"round {name}\n", encoding="utf-8")
        if finished:
            (d / "summary.json").write_text("{}\n", encoding="utf-8")


def test_discovery_prefers_old_single_path(tmp_path: pathlib.Path) -> None:
    _beacon_tree(tmp_path, {"9934166": False}, old=True)
    proc = _bash(f'beacon_unrun_protocol "{tmp_path}"')
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(tmp_path / "experiments" / "beacon" / "protocol.md")


def test_discovery_single_unrun_round(tmp_path: pathlib.Path) -> None:
    _beacon_tree(tmp_path, {"9934166": False})
    proc = _bash(f'beacon_unrun_protocol "{tmp_path}"')
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(tmp_path / "experiments" / "beacon" / "9934166" / "protocol.md")


def test_discovery_skips_finished_runs_and_rehearsals(tmp_path: pathlib.Path) -> None:
    _beacon_tree(tmp_path, {"9934165": True, "9934166": False})
    reh = tmp_path / "experiments" / "beacon" / "rehearsals" / "6593481"
    reh.mkdir(parents=True, exist_ok=True)
    (reh / "protocol.md").write_text("rehearsal\n", encoding="utf-8")
    proc = _bash(f'beacon_unrun_protocol "{tmp_path}"')
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("9934166/protocol.md")


def test_discovery_none_or_several_is_an_error(tmp_path: pathlib.Path) -> None:
    proc = _bash(f'beacon_unrun_protocol "{tmp_path}"')
    assert proc.returncode == 2
    _beacon_tree(tmp_path, {"111": False, "222": False})
    proc = _bash(f'beacon_unrun_protocol "{tmp_path}"')
    assert proc.returncode == 2


def _spawn_named(name: str, cwd: pathlib.Path) -> subprocess.Popen[str]:
    """A process whose command line contains *name*, running in *cwd*."""
    return subprocess.Popen(
        ["bash", "-c", f"exec -a {name} sleep 60"],
        cwd=str(cwd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def test_ralph_guard_ignores_loop_in_another_path(tmp_path: pathlib.Path) -> None:
    other = tmp_path / "other-project"
    other.mkdir()
    proc_loop = _spawn_named("ralph-afk.sh", other)
    try:
        time.sleep(0.3)
        mine = tmp_path / "checkout"
        mine.mkdir()
        proc = _bash(f'beacon_ralph_running_here "{mine}"; echo "rc=$?"')
        assert "rc=1" in proc.stdout, (proc.stdout, proc.stderr)
    finally:
        proc_loop.terminate()


def test_ralph_guard_matches_loop_in_this_checkout(tmp_path: pathlib.Path) -> None:
    mine = tmp_path / "checkout"
    mine.mkdir()
    proc_loop = _spawn_named("ralph-afk.sh", mine)
    try:
        time.sleep(0.3)
        proc = _bash(f'beacon_ralph_running_here "{mine}"; echo "rc=$?"')
        assert "rc=0" in proc.stdout, (proc.stdout, proc.stderr)
    finally:
        proc_loop.terminate()


def _stub_git(bin_dir: pathlib.Path, log: pathlib.Path) -> None:
    stub = bin_dir / "git"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "git $*" >> "{log}"\n'
        'case "$1 $2" in\n'
        '  "branch --show-current") echo "testbranch";;\n'
        '  "config branch.testbranch.remote") exit 1;;\n'
        '  "push "*) exit 0;;\n'
        "  *) exit 0;;\n"
        "esac\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)


def test_no_push_without_push_yes(tmp_path: pathlib.Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "git.log"
    _stub_git(bin_dir, log)
    proc = _bash('beacon_maybe_push "eval-freeze-1" "1"; echo "rc=$?"',
                 env={"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "PUSH": ""})
    assert "rc=0" in proc.stdout, (proc.stdout, proc.stderr)
    assert "PUSH!=yes" in proc.stdout
    assert "fork" in proc.stdout
    assert log.exists()
    assert "push" not in log.read_text(encoding="utf-8")


def test_push_yes_pushes_branch_and_tag(tmp_path: pathlib.Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "git.log"
    _stub_git(bin_dir, log)
    proc = _bash('beacon_maybe_push "eval-freeze-424242" "424242"; echo "rc=$?"',
                 env={"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                      "PUSH": "yes", "CONFIRM": "yes"})
    assert "rc=0" in proc.stdout, (proc.stdout, proc.stderr)
    lines = log.read_text(encoding="utf-8")
    assert "git push origin testbranch" in lines
    assert "git push origin tag eval-freeze-424242" in lines


def test_push_aborts_without_confirm(tmp_path: pathlib.Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "git.log"
    _stub_git(bin_dir, log)
    proc = _bash('printf "no\\n" | beacon_maybe_push "eval-freeze-1" "1"; echo "rc=$?"',
                 env={"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "PUSH": "yes"})
    assert "rc=3" in proc.stdout, (proc.stdout, proc.stderr)
    assert "push" not in (log.read_text(encoding="utf-8") if log.exists() else "")
