"""REQ-095 item 2: the keyed smoke skips without a key, fails loudly live.

Unkeyed-safe: the skip path (no FRONTIER_API_KEY -> one line, exit 0)
and the unreachable-control path (key set, nothing listening -> exit 1
with a FAIL line, never a traceback) both run in `make test`. The keyed
scene itself runs by hand (`make demo-smoke-live` with the key).
"""

from __future__ import annotations


def test_skip_without_key_prints_one_line_and_passes(
    monkeypatch: object, capsys: object
) -> None:
    """A missing key never fails a test or a gate (owner requirement)."""
    import bench.demo_smoke_live as smoke

    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)  # type: ignore[attr-defined]
    code = smoke.main([])
    assert code == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "SKIP" in out and "FRONTIER_API_KEY" in out
    assert out.strip().count("\n") == 0, "exactly one skip line"


def test_unreachable_control_fails_loudly_not_crash(
    monkeypatch: object, capsys: object
) -> None:
    """With a key but no stack, the smoke FAILs with a line, exit 1."""
    import bench.demo_smoke_live as smoke

    monkeypatch.setenv("FRONTIER_API_KEY", "test-key-never-sent")  # type: ignore[attr-defined]
    code = smoke.main(["--control-url", "http://127.0.0.1:1", "--seed", "0"])
    assert code == 1
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "FAIL" in out
