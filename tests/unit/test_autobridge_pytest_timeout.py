"""REQ-026: per-test timeout so a hanging test fails fast with a stack trace.

The unit suite defaults to 30 s per test (``timeout`` ini) with the
``thread`` method (every thread's stack is dumped on timeout, on macOS and
Linux); every ``tests/e2e`` item gets 180 s via
``tests/e2e/conftest.py::pytest_collection_modifyitems`` because stack
startup in fixtures must fit inside the limit. No test may raise above
these without a one-line comment giving the measured reason.
"""

from __future__ import annotations

import subprocess
import sys
import time
import tomllib
from pathlib import Path

UNIT_TIMEOUT_SECONDS = 30
E2E_TIMEOUT_SECONDS = 180


def _ini_options() -> dict:
    root = Path(__file__).resolve().parents[2]
    with open(root / "pyproject.toml", "rb") as handle:
        data = tomllib.load(handle)
    return data["tool"]["pytest"]["ini_options"]


def test_unit_timeout_config_is_30s_thread_method() -> None:
    """Pin the unit default: 30 s, thread method (stack dump, macOS-safe)."""
    options = _ini_options()
    assert options.get("timeout") == UNIT_TIMEOUT_SECONDS
    assert options.get("timeout_method") == "thread"


def test_hanging_test_fails_fast_with_timeout_and_stack(tmp_path: Path) -> None:
    """A deliberately sleeping test fails at the limit with a stack trace.

    Runs a real pytest in a subprocess with a 2 s override (not 30 s) so
    this test stays fast; the mechanism under test is identical. Fails if
    the hang is not cut short, if no ``Timeout`` is reported, or if the
    hung frame's stack is not dumped.
    """
    target = tmp_path / "test_hang.py"
    target.write_text("import time\n\n\ndef test_hang():\n    time.sleep(30)\n")
    started = time.monotonic()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(target),
            "-q",
            "-p",
            "no:randomly",
            "-p",
            "no:cacheprovider",
            "-o",
            "timeout=2",
            "-o",
            "timeout_method=thread",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
    )
    elapsed = time.monotonic() - started
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "Timeout" in combined
    assert "time.sleep" in combined  # the hung frame's stack is dumped
    assert elapsed < 20  # failed fast instead of waiting out the 30 s sleep


class _FakeItem:
    """Minimal stand-in for a pytest Item (fixturenames + add_marker)."""

    def __init__(self, fixturenames: tuple) -> None:
        self.fixturenames = fixturenames
        self.marks: list = []

    def add_marker(self, mark) -> None:  # noqa: ANN001 - duck-typed pytest Mark
        self.marks.append(mark)


def test_e2e_items_get_180s_timeout() -> None:
    """Every collected e2e item carries the 180 s timeout mark (REQ-026)."""
    from tests.e2e import conftest as e2e_conftest

    live = _FakeItem(("e2e_stack", "tmp_path"))
    fake_transport = _FakeItem(("tmp_path",))
    e2e_conftest.pytest_collection_modifyitems([live, fake_transport])  # type: ignore[list-item]
    for item in (live, fake_transport):
        timeouts = [m for m in item.marks if getattr(m, "name", None) == "timeout"]
        assert len(timeouts) == 1
        assert timeouts[0].args == (E2E_TIMEOUT_SECONDS,)
    # The existing live/inprocess auto-marks still apply alongside it.
    assert {m.name for m in live.marks} >= {"live", "timeout"}
    assert {m.name for m in fake_transport.marks} >= {"inprocess", "timeout"}
