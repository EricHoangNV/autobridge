"""REQ-020 item 4 — Meridian "before": the spec-v1-only engine refuses safely.

With the simulator and docs in place and the engine unchanged, a build from
Meridian's docs must stop cleanly: no success, no draft spec (so no release
is persisted), a named reason, escalation to L4, and zero model calls — never
a half-working connector with invented GET operations.

The in-process test pins this for the suite; the live test additionally
exercises the real sim-meridian HTTP surface when the stack is up.
"""

from __future__ import annotations

import json
import pathlib
import urllib.request

from autobridge.discovery.bootstrap import build_draft_spec

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MERIDIAN_YAML_PATH = REPO_ROOT / "simulators" / "docs" / "meridian-openapi.yaml"
MERIDIAN_YAML = MERIDIAN_YAML_PATH.read_text(encoding="utf-8")

AUTH = {"ClientToken": "meridian-client-token", "AccessToken": "meridian-access-token", "Client": "e2e/1.0"}


def _meridian_build():  # type: ignore[no-untyped-def]
    return build_draft_spec(MERIDIAN_YAML, source="simulators/docs/meridian-openapi.yaml", connector_id="meridianpms")


def test_meridian_before_refuses_in_process() -> None:
    """Spec-v1-only engine: Meridian docs refuse with a named reason, 0 calls."""
    result = _meridian_build()
    assert not result.success
    assert result.draft_spec is None
    assert result.degradation_reason is not None
    assert "POST" in result.degradation_reason
    assert "ConnectorSpec v1" in result.degradation_reason
    assert result.model_usage.get("calls", 0) == 0
    assert result.model_usage.get("frontier_tokens", 0) == 0
    assert any(t.get("stage") == "vocabulary_guard" for t in result.trace)


def _live_post(path: str, body: dict) -> dict:
    from tests.e2e.conftest import service_url

    req = urllib.request.Request(
        service_url("sim-meridian", path),
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 200, path
        return json.loads(resp.read().decode())


def test_meridian_before_live_surface_when_stack_up(e2e_stack: str) -> None:
    """Live sim-meridian answers the documented contract; the build still refuses."""
    assert e2e_stack
    from tests.e2e.conftest import service_url

    with urllib.request.urlopen(service_url("sim-meridian", "/healthz"), timeout=10) as resp:
        assert resp.status == 200
    page = _live_post(
        "/api/connector/v1/reservations/getAll/2023-06-06",
        {**AUTH, "Limitation": {"Count": 5}},
    )
    assert len(page["Reservations"]) == 5
    assert page["Cursor"] == page["Reservations"][-1]["Id"]
    # Same docs, fetched live, refuse the same way through the v1 engine.
    with urllib.request.urlopen(service_url("sim-meridian", "/developers/source"), timeout=10):
        pass
    result = _meridian_build()
    assert not result.success
    assert result.draft_spec is None
