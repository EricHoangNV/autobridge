"""Unit tests for simulator launcher port overrides (REQ-017).

``make local-up`` maps every simulator onto compose's host ports via
``SIM_PUBLIC_PORT`` / ``SIM_ADMIN_PORT``; defaults keep container behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def test_ports_default_to_container_values(monkeypatch: pytest.MonkeyPatch) -> None:
    from simulators.run import _listen_ports

    monkeypatch.delenv("SIM_PUBLIC_PORT", raising=False)
    monkeypatch.delenv("SIM_ADMIN_PORT", raising=False)
    assert _listen_ports() == (8000, 8001)


def test_ports_honor_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    from simulators.run import _listen_ports

    monkeypatch.setenv("SIM_PUBLIC_PORT", "8105")
    monkeypatch.setenv("SIM_ADMIN_PORT", "8109")
    assert _listen_ports() == (8105, 8109)


def test_ports_ignore_empty_and_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    from simulators.run import _listen_ports

    monkeypatch.setenv("SIM_PUBLIC_PORT", "")
    monkeypatch.setenv("SIM_ADMIN_PORT", "not-a-port")
    assert _listen_ports() == (8000, 8001)


def test_ports_reject_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    from simulators.run import _listen_ports

    monkeypatch.setenv("SIM_PUBLIC_PORT", "99999")
    monkeypatch.setenv("SIM_ADMIN_PORT", "0")
    assert _listen_ports() == (8000, 8001)
