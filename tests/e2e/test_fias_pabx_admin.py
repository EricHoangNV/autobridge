"""REQ-022 sub-step A — FIAS PABX simulator on the live stack.

Stack-level checks for ``sim-fias-pabx`` (edge-net only): the public HTTP
surface (health, readyz, document viewer), the admin fault API, and the
trust-boundary negatives (the cloud control plane cannot reach the
on-premise exchange; the edge agent's host side is the only peer).

The FIAS TCP link itself is exercised without Docker in
``tests/unit/test_simulators_fias_pabx.py`` against an in-process host;
the edge agent that speaks this protocol lands in sub-step B with its own
live link test. Trust boundary: this file lives in tests/e2e and may
import the engine + simulators for verification, but the engine itself
never imports verifier/simulators.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import urllib.request
from typing import Any

import pytest

from tests.e2e.conftest import (
    admin_headers,
    admin_url,
    is_local_mode,
    service_url,
    stack_unreachable,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _admin_get(path: str) -> tuple[int, dict[str, Any] | None]:
    req = urllib.request.Request(admin_url("sim-fias-pabx", path), headers=dict(admin_headers()))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except Exception:
        return 0, None


def _admin_post(path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
    data = json.dumps(payload).encode()
    headers = dict(admin_headers())
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        admin_url("sim-fias-pabx", path), data=data, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except Exception:
        return 0, None


# ---------------------------------------------------------------------------
# In-process (no stack needed)
# ---------------------------------------------------------------------------


def test_demo_target_map_knows_fias() -> None:
    """REQ-022 A: POST /demo/inject-fault and /demo/reset accept the FIAS target."""
    from autobridge.control.routes.demo import (  # noqa: PLC0415
        TARGET_MAP,
        _admin_url_for_target,
        _resolve_target,
    )

    assert _resolve_target("fias") == "sim-fias-pabx"
    assert _resolve_target("sim-fias-pabx") == "sim-fias-pabx"
    assert TARGET_MAP["fiaspms"] == "sim-fias-pabx"
    assert _admin_url_for_target("sim-fias-pabx").endswith(":8119")
    with pytest.raises(Exception, match="unknown demo target"):
        _resolve_target("sim-nope")


# ---------------------------------------------------------------------------
# Live stack
# ---------------------------------------------------------------------------


def test_fias_public_surface_live(e2e_stack: str) -> None:
    """Public HTTP: healthz/readyz green, viewer + source served."""
    assert e2e_stack
    import urllib.request as _u

    with _u.urlopen(service_url("sim-fias-pabx", "/healthz"), timeout=10) as resp:
        assert resp.status == 200
    with _u.urlopen(service_url("sim-fias-pabx", "/readyz"), timeout=10) as resp:
        body = json.loads(resp.read())
        assert body["status"] == "ok"
        assert body["checks"]["fias_link"]  # reported, never a readiness failure (REQ-022 A)
    with _u.urlopen(service_url("sim-fias-pabx", "/developers"), timeout=10) as resp:
        html = resp.read().decode()
        assert "Grand Plaza" in html
        assert 'fetch("/developers/source"' in html
    with _u.urlopen(service_url("sim-fias-pabx", "/developers/source"), timeout=10) as resp:
        assert resp.read().decode().startswith("# Grand Plaza")


def test_fias_admin_state_and_faults_live(e2e_stack: str) -> None:
    """Admin API: link snapshot, layout fault, reset to baseline."""
    assert e2e_stack
    status, state = _admin_get("/admin/fias/state")
    assert status == 200, state
    assert state is not None and len(state["rooms"]) == 6
    assert state["ps_layout"] == "baseline"

    status, _ = _admin_post("/admin/fias/faults", {"ps_layout": "layout_v2"})
    assert status == 200
    status, state = _admin_get("/admin/fias/state")
    assert status == 200 and state is not None
    assert state["ps_layout"] == "layout_v2"
    assert state["ps_field_list"] == "P#RNTIDASOTADUDDCX"

    status, state = _admin_post("/admin/fias/reset", {})
    assert status == 200 and state is not None
    assert state["ps_layout"] == "baseline"


def _compose_exec(service: str, url: str) -> bool:
    """Return True when *service* can GET *url* from inside its container."""
    try:
        proc = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                service,
                "python",
                "-c",
                f"import urllib.request; print(urllib.request.urlopen({url!r}, timeout=5).status)",
            ],
            capture_output=True,
            timeout=30,
            cwd=str(REPO_ROOT),
        )
    except Exception:
        return False
    return proc.returncode == 0


def test_fias_negative_control_cannot_reach_pabx_live(e2e_stack: str) -> None:
    """Cloud control plane has no route to the on-premise exchange (edge-net)."""
    assert e2e_stack
    if is_local_mode():
        pytest.skip("local mode: no Docker network isolation to probe (REQ-017)")
    if shutil.which("docker") is None:
        stack_unreachable("docker not available — cannot probe container networks")
    assert _compose_exec("edge-worker", "http://sim-fias-pabx:8000/healthz"), (
        "edge-worker must reach sim-fias-pabx on edge-net"
    )
    assert not _compose_exec("control", "http://sim-fias-pabx:8000/healthz"), (
        "control plane must NOT reach sim-fias-pabx (isolated edge-net)"
    )
    assert not _compose_exec("runtime-sandbox", "http://sim-fias-pabx:8000/healthz"), (
        "cloud sandbox must NOT reach sim-fias-pabx"
    )
    assert not _compose_exec("edge-worker", "http://sim-fias-pabx:8001/healthz"), (
        "edge side must NOT reach the simulator admin API"
    )
