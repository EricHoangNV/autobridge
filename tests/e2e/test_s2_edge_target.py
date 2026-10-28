"""P2.S2.1 — Simulated Edge target (plan §5 S2).

Passing criteria (plan §5 S2):
  * ``RuntimeTarget.Edge``: a toy edge worker on an isolated Docker network
    runs a verified, signed release against a sim POS reachable only there;
    verify centrally, execute locally.
  * Control plane cannot reach the sim POS (negative test).
  * Edge worker executes only signed releases.
  * Own e2e file: ``tests/e2e/test_s2_edge_target.py``.

Fake transports keep the suite green without Docker; live tests use the
``e2e_stack`` fixture and skip when the stack is down. Trust boundary: this
file lives in tests/e2e and may import the engine + simulators for
verification, but the engine itself never imports verifier/simulators.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Any

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from autobridge.control.models import spec_signature
from autobridge.runtime.targets import (
    EDGE_POS_HOST,
    DockerSandboxTarget,
    EdgeTarget,
    FakeEdgeTarget,
    NetworkBlockedError,
    TargetConfigError,
    create_target,
    release_signature,
    verify_release_signature,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _edge_spec(base_url: str = "http://sim-edgepos:8000") -> dict[str, Any]:
    return {
        "spec_version": 1,
        "connector": {"id": "edgepos", "vendor": "EdgePOS", "runtime_target": "edge"},
        "source": {
            "base_url": base_url,
            "auth": {"type": "bearer", "credential_ref": "EDGEPOS_API_KEY"},
            "rate_limit": {
                "max_rps": 5,
                "retry": {"statuses": [429, 503], "backoff": "exponential", "max_attempts": 3},
            },
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "params": {},
                "pagination": {
                    "strategy": "offset",
                    "limit_param": "limit",
                    "offset_param": "offset",
                    "items_path": "$.data",
                    "stop_when": ["empty_page"],
                },
            },
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {"reservation_id": {"path": "$.reservation_id"}},
            },
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {
            "canary": {"every": "60s", "operation": "list_reservations", "max_pages": 1},
            "fingerprint": {},
        },
    }


# ---------------------------------------------------------------------------
# Signed releases: verify centrally, execute locally
# ---------------------------------------------------------------------------


def test_edge_signature_matches_release_signature() -> None:
    """The worker's check is the same hash ``ConnectorRelease.signature`` stores."""
    spec = _edge_spec()
    assert release_signature(spec) == spec_signature(spec)


def test_edge_worker_executes_only_signed_releases() -> None:
    """Unsigned and tampered releases are refused before any network work."""
    spec = _edge_spec()
    sig = release_signature(spec)
    fake = FakeEdgeTarget(responses={"list_reservations": [{"reservation_id": "EDGE-1001"}]})
    assert fake.execute_signed_release(spec, sig, "list_reservations").is_ok
    with pytest.raises(TargetConfigError):
        fake.execute_signed_release(spec, None, "list_reservations")
    with pytest.raises(TargetConfigError):
        fake.execute_signed_release(spec, "tampered", "list_reservations")
    tampered = dict(spec, source={"base_url": "http://evil:8000"})
    with pytest.raises(TargetConfigError):
        fake.execute_signed_release(tampered, sig, "list_reservations")
    assert EdgeTarget().execute().status == "blocked"


def test_edge_allowlist_isolation_in_process() -> None:
    """In-process: the edge worker reaches only the edge-only sim POS."""
    edge = create_target({"kind": "edge"})
    assert edge.can_reach(f"http://{EDGE_POS_HOST}:8000/reservations")
    for url in (
        "http://verifier:8000/verify",
        "http://control:8000/connectors",
        "http://sim-atlas:8000/reservations",
        "http://sim-harbor:8000/reservations",
        "http://sim-legacystay:8000/reservations",
        f"http://{EDGE_POS_HOST}:8001/admin/reset",
    ):
        assert not edge.can_reach(url), url
    # The cloud sandbox never allowlists the edge POS either.
    cloud = DockerSandboxTarget(network_allowlist=["sim-atlas", "sim-harbor"])
    with pytest.raises(NetworkBlockedError):
        cloud.check_network(f"http://{EDGE_POS_HOST}:8000/reservations")


def test_compose_isolates_edge_network() -> None:
    """Static negative test: compose attaches sim-edgepos only to edge-net."""
    compose = yaml.safe_load((REPO_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert services["sim-edgepos"]["networks"] == ["edge-net"]
    assert services["edge-worker"]["networks"] == ["edge-net"]
    assert "edge-net" not in services["control"]["networks"]
    assert "edge-net" not in services["runtime-sandbox"]["networks"]
    assert "edge-net" not in services["ui"]["networks"]
    assert "edge-net" in compose["networks"]


def test_edge_worker_app_signed_flow() -> None:
    """Worker app: signed executes, unsigned/tampered/off-allowlist refused."""
    from autobridge.edge.app import app as edge_app
    from autobridge.runtime.targets import ExecutionResult

    client = TestClient(edge_app)
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json()["status"] == "ok"
    spec = _edge_spec()
    assert client.post("/execute", json={"spec": spec, "operation": "list_reservations"}).status_code == 403
    assert (
        client.post(
            "/execute",
            json={"spec": spec, "signature": "bad", "operation": "list_reservations"},
        ).status_code
        == 403
    )
    off = _edge_spec("http://sim-atlas:8000")
    assert (
        client.post(
            "/execute",
            json={"spec": off, "signature": release_signature(off), "operation": "list_reservations"},
        ).status_code
        == 403
    )
    # Wired happy path (interpreter stubbed; live run covers the real one).
    from autobridge.edge import app as edge_mod

    def fake_exec(self: Any, spec: Any, signature: Any, operation: Any, params: Any = None) -> ExecutionResult:  # noqa: ARG001
        verify_release_signature(spec, signature)
        return ExecutionResult(status="ok", records=[{"reservation_id": "EDGE-1"}])

    orig = edge_mod.EdgeTarget.execute_signed_release
    edge_mod.EdgeTarget.execute_signed_release = fake_exec  # type: ignore[method-assign]
    try:
        r = client.post(
            "/execute",
            json={"spec": spec, "signature": release_signature(spec), "operation": "list_reservations"},
        )
    finally:
        edge_mod.EdgeTarget.execute_signed_release = orig  # type: ignore[method-assign]
    assert r.status_code == 200
    assert r.json()["records"] == [{"reservation_id": "EDGE-1"}]


def test_edgepos_sim_shape() -> None:
    """Sim POS serves canonical money strings + property-local business dates."""
    from simulators.edgepos.app import app as edgepos_app

    client = TestClient(edgepos_app)
    body = client.get("/reservations").json()
    assert body["total"] == 2
    first = body["data"][0]
    assert first["total_amount"] == f"{float(first['total_amount']):.2f}"
    assert first["updated_at"].endswith("+00:00")
    folios = client.get("/folios").json()
    assert folios["total"] == 1
    assert len(folios["data"][0]["business_date"]) == 10


def test_edge_signed_spec_syncs_complete_fake(monkeypatch: Any) -> None:
    """Signed release executes end to end against a fake edge transport."""
    import httpx as _httpx

    monkeypatch.setenv("EDGEPOS_API_KEY", "test-key")
    payload = {"data": [{"reservation_id": "EDGE-1001"}, {"reservation_id": "EDGE-1002"}]}
    transport = _httpx.MockTransport(lambda _request: _httpx.Response(200, json=payload))

    def factory(**kwargs: Any) -> _httpx.Client:
        kwargs.pop("transport", None)
        return _httpx.Client(transport=transport, **{k: v for k, v in kwargs.items() if k == "timeout"})

    spec = _edge_spec()
    res = EdgeTarget().execute_signed_release(spec, release_signature(spec), "list_reservations", client_factory=factory)
    assert res.status == "ok"
    assert [r["reservation_id"] for r in res.records] == ["EDGE-1001", "EDGE-1002"]


# ---------------------------------------------------------------------------
# Live: container-network isolation + signed execution on the stack
# ---------------------------------------------------------------------------


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


def test_edge_negative_control_cannot_reach_edgepos_live(e2e_stack: str) -> None:
    """Control plane cannot reach the sim POS; the edge worker can."""
    assert e2e_stack
    import shutil

    from tests.e2e.conftest import is_local_mode  # noqa: PLC0415

    if is_local_mode():
        pytest.skip("local mode: no Docker network isolation to probe (REQ-017)")
    if shutil.which("docker") is None:
        from tests.e2e.conftest import stack_unreachable  # noqa: PLC0415

        stack_unreachable("docker not available — cannot probe container networks")
    assert _compose_exec("edge-worker", "http://sim-edgepos:8000/healthz"), (
        "edge-worker must reach sim-edgepos on edge-net"
    )
    assert not _compose_exec("control", "http://sim-edgepos:8000/healthz"), (
        "control plane must NOT reach sim-edgepos (isolated edge-net)"
    )
    assert not _compose_exec("runtime-sandbox", "http://sim-edgepos:8000/healthz"), (
        "cloud sandbox must NOT reach sim-edgepos"
    )


def test_edge_execute_signed_release_live(e2e_stack: str) -> None:
    """Signed release executes on the live edge worker; tampered is refused."""
    assert e2e_stack
    from tests.e2e.conftest import edge_pos_url  # noqa: PLC0415

    pos_url = edge_pos_url()
    spec = _edge_spec(pos_url)
    sig = release_signature(spec)
    try:
        r = httpx.post(
            "http://localhost:8114/execute",
            json={"spec": spec, "signature": sig, "operation": "list_reservations"},
            timeout=30.0,
        )
    except httpx.ConnectError as exc:
        from tests.e2e.conftest import stack_unreachable  # noqa: PLC0415

        stack_unreachable(f"edge-worker unreachable live: {exc}")
    assert r.status_code == 200, r.text
    records = r.json()["records"]
    assert [rec["reservation_id"] for rec in records] == ["EDGE-1001", "EDGE-1002"]
    bad = httpx.post(
        "http://localhost:8114/execute",
        json={"spec": spec, "signature": "tampered", "operation": "list_reservations"},
        timeout=30.0,
    )
    assert bad.status_code == 403
    # Control plane accepts edge connectors (kind=edge shipped in P2.S2.1).
    os.environ.pop("EDGEPOS_API_KEY", None)
    from tests.e2e.conftest import control_headers  # noqa: PLC0415

    created = httpx.post(
        "http://localhost:8101/connectors",
        json={
            "name": "EdgePOS-live",
            "base_url": pos_url,
            "credential_ref": "EDGEPOS_API_KEY",
            "target_entities": ["Reservation"],
            "runtime_target": {"kind": "edge", "network_allowlist": ["sim-edgepos"]},
        },
        headers=control_headers(),
        timeout=15.0,
    )
    assert created.status_code == 201, created.text
