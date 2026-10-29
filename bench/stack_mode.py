"""One shared local-vs-Compose stack-mode detector (REQ-037).

Three host-side entry points used to decide the mode by the mere presence
of ``.local/ACTIVE``: ``scripts/smoke.sh``, ``bench/demo.py`` and
``tests/e2e/conftest.py``. When the local processes died without
``make local-down`` the marker survives and every consumer silently took
the local branch — smoke skipped its network checks, the demo asserted
loopback addresses against a Compose stack, e2e took loopback URLs — all
reporting misleading results instead of "stale marker".

The marker is now *validated*: it counts only while the local stack it
records is actually alive — a recorded PID still runs, or the control
plane answers ``/readyz`` with ``runtime_mode: local``. A stale marker
falls through to the Compose branch exactly as if it were absent, plus a
one-per-process stderr warning naming the file. An explicit
``AUTOBRIDGE_RUNTIME=local`` still wins (operator assertion, unchanged).

Stdlib only: ``scripts/smoke.sh`` runs this file directly with the system
``python3`` (``python3 bench/stack_mode.py --mode``), so no package
imports here — only ``__main__`` below, and everything works both as
``python -m``/imported module and as a script path.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.request
from dataclasses import dataclass

#: Modes returned by :func:`detect_stack_mode`. ``"compose"`` means "not
#: a live local stack" — i.e. the Compose branch, or no stack at all.
LOCAL = "local"
COMPOSE = "compose"

#: Explicit operator override (unchanged semantics: wins over the marker).
RUNTIME_ENV_VAR = "AUTOBRIDGE_RUNTIME"

#: Control-plane URL honoured by the readyz probe (same default the demo
#: and the bench harness use; ``local-up`` exports the same loopback).
DEFAULT_CONTROL_URL = "http://localhost:8101"
CONTROL_URL_ENV_VAR = "CONTROL_URL"

#: ``/readyz`` answers in this many seconds on a live stack; the detector
#: must never stall a caller (smoke, demo, e2e collection).
PROBE_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class StackMode:
    """Validated stack mode (REQ-037).

    ``mode`` is ``"local"`` only for a live local stack; ``stale_marker``
    is True when ``.local/ACTIVE`` exists but the local stack it records
    is gone (callers then take the Compose branch, as without the file).
    ``detail`` names the evidence for logs and warnings.
    """

    mode: str
    stale_marker: bool
    detail: str


def repo_root() -> pathlib.Path:
    """Repo root (this file lives in ``<root>/bench/``)."""
    return pathlib.Path(__file__).resolve().parents[1]


def marker_path(root: pathlib.Path | str | None = None) -> pathlib.Path:
    """Path of the ``make local-up`` liveness marker."""
    base = pathlib.Path(root) if root is not None else repo_root()
    return base / ".local" / "ACTIVE"


def pids_dir(root: pathlib.Path | str | None = None) -> pathlib.Path:
    """Directory holding ``make local-up`` PID files."""
    base = pathlib.Path(root) if root is not None else repo_root()
    return base / ".local" / "pids"


def recorded_pids(root: pathlib.Path | str | None = None) -> list[int]:
    """PIDs recorded by ``make local-up`` (empty when none are readable)."""
    try:
        entries = sorted(pids_dir(root).glob("*.pid"))
    except OSError:
        return []
    pids: list[int] = []
    for entry in entries:
        try:
            text = entry.read_text(encoding="utf-8").strip().split()
        except OSError:
            continue
        if not text:
            continue
        try:
            pids.append(int(text[0]))
        except ValueError:
            continue
    return pids


def pid_alive(pid: int) -> bool:
    """True when a process with *pid* exists (``kill -0`` semantics)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # A process exists that we may not signal.
        return True
    except OSError:
        return False
    return True


def probe_runtime_mode(
    control_url: str | None = None, timeout_s: float = PROBE_TIMEOUT_S
) -> str | None:
    """Ask the control plane which stack answers (REQ-037 probe leg).

    Returns ``"local"``/``"docker"`` from the top-level ``runtime_mode``
    field of ``GET <control>/readyz`` (see ``autobridge.health``), else
    None when nothing answers or the body is unexpected. Never raises.
    """
    base = (control_url or os.environ.get(CONTROL_URL_ENV_VAR, "") or DEFAULT_CONTROL_URL).rstrip("/")
    try:
        with urllib.request.urlopen(base + "/readyz", timeout=timeout_s) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    mode = body.get("runtime_mode")
    return mode if mode in (LOCAL, "docker") else None


def detect_stack_mode(
    root: pathlib.Path | str | None = None,
    control_url: str | None = None,
    timeout_s: float = PROBE_TIMEOUT_S,
) -> StackMode:
    """Validate the local-stack marker and return the mode (REQ-037).

    * Explicit ``AUTOBRIDGE_RUNTIME=local`` → local (operator assertion).
    * No ``.local/ACTIVE`` → compose branch (unchanged).
    * Marker + any recorded PID alive → local (covers the startup window
      before ``/readyz`` answers).
    * Marker + readyz reporting ``runtime_mode: local`` → local (covers
      lost PID files while the stack still runs).
    * Marker + anything else → compose branch with ``stale_marker=True``
      (dead local stack, or a Compose stack answering instead).
    """
    base_root = root if root is not None else repo_root()
    if os.environ.get(RUNTIME_ENV_VAR, "").strip().lower() == LOCAL:
        return StackMode(LOCAL, False, f"{RUNTIME_ENV_VAR}=local")
    marker = marker_path(base_root)
    if not marker.is_file():
        return StackMode(COMPOSE, False, "no .local/ACTIVE marker")
    if any(pid_alive(pid) for pid in recorded_pids(base_root)):
        return StackMode(LOCAL, False, "local pids alive")
    probed = probe_runtime_mode(control_url, timeout_s)
    if probed == LOCAL:
        return StackMode(LOCAL, False, "control /readyz reports runtime_mode=local")
    if probed == "docker":
        detail = (
            f"stale {marker}: no local pids alive but control answers "
            "runtime_mode=docker (Compose stack up, local stack dead)"
        )
    else:
        detail = f"stale {marker}: no local pids alive and control unreachable"
    return StackMode(COMPOSE, True, detail)


#: Repo roots already warned about (one stderr line per process, so the
#: demo's repeated ``_stack_is_local()`` calls do not spam).
_warned_stale_roots: set[str] = set()


def is_local_stack(
    root: pathlib.Path | str | None = None,
    control_url: str | None = None,
    timeout_s: float = PROBE_TIMEOUT_S,
) -> bool:
    """True only for a live local stack (REQ-037).

    A stale ``.local/ACTIVE`` prints one warning naming the file and
    answers False — callers take the Compose branch exactly as if the
    marker were absent.
    """
    detection = detect_stack_mode(root, control_url, timeout_s)
    if detection.stale_marker:
        key = str(marker_path(root if root is not None else repo_root()))
        if key not in _warned_stale_roots:
            _warned_stale_roots.add(key)
            print(f"stack_mode: WARNING: {detection.detail}", file=sys.stderr)
    return detection.mode == LOCAL


def main(argv: list[str] | None = None) -> int:
    """CLI for ``scripts/smoke.sh``: print ``local`` or ``compose``."""
    parser = argparse.ArgumentParser(description="validated local-vs-Compose stack mode (REQ-037)")
    parser.add_argument("--mode", action="store_true", help="print the mode and exit 0")
    args = parser.parse_args(argv)
    if not args.mode:
        parser.print_usage(sys.stderr)
        return 2
    detection = detect_stack_mode()
    if detection.stale_marker:
        print(f"stack_mode: WARNING: {detection.detail}", file=sys.stderr)
    sys.stdout.write(detection.mode + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
