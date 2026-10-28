"""P2.S2.1 — Simulated Edge target: signed-only execution on an isolated net.

Covers :class:`EdgeTarget`, the release-signature helpers, ``FakeEdgeTarget``
and the edge worker app (rejection paths + wired happy path).  Live
container-network isolation is asserted in ``tests/e2e/test_s2_edge_target.py``
and ``scripts/smoke.sh``; these unit tests stay green without Docker.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from autobridge.edge.app import app as edge_app
from autobridge.runtime.targets import (
    EDGE_POS_HOST,
    EdgeTarget,
    ExecutionResult,
    FakeEdgeTarget,
    NetworkBlockedError,
    TargetConfigError,
    create_target,
    release_signature,
    target_from_connector_spec,
    verify_release_signature,
)


@pytest.fixture(autouse=True)
def _no_release_secret(monkeypatch: Any) -> None:
    """Hermetic default: legacy unkeyed mode unless a test sets the key."""
    monkeypatch.delenv("EDGE_RELEASE_SECRET", raising=False)


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
# Signature helpers
# ---------------------------------------------------------------------------


def test_release_signature_matches_control_spec_signature() -> None:
    from autobridge.control.models import spec_signature

    spec = _edge_spec()
    assert release_signature(spec) == spec_signature(spec)


def test_verify_release_signature_ok_missing_mismatch() -> None:
    spec = _edge_spec()
    sig = release_signature(spec)
    verify_release_signature(spec, sig)  # no raise
    with pytest.raises(TargetConfigError, match="unsigned"):
        verify_release_signature(spec, None)
    with pytest.raises(TargetConfigError, match="unsigned"):
        verify_release_signature(spec, "")
    with pytest.raises(TargetConfigError, match="mismatch"):
        verify_release_signature(spec, "tampered")
    tampered = dict(spec)
    tampered["source"] = {"base_url": "http://evil:8000"}
    with pytest.raises(TargetConfigError, match="mismatch"):
        verify_release_signature(tampered, sig)


# ---------------------------------------------------------------------------
# EdgeTarget network isolation
# ---------------------------------------------------------------------------


def test_edge_target_defaults_to_edgepos_allowlist() -> None:
    t = EdgeTarget()
    assert t.kind == "edge"
    assert t.network_allowlist == [EDGE_POS_HOST]
    assert t.can_reach("http://sim-edgepos:8000/reservations")
    assert not t.can_reach("http://verifier:8000/verify")
    assert not t.can_reach("http://sim-atlas:8000/reservations")
    assert not t.can_reach("http://sim-edgepos:8001/admin/reset")


def test_edge_target_rejects_blocked_allowlist() -> None:
    with pytest.raises(TargetConfigError):
        EdgeTarget(network_allowlist=["verifier"])


def test_edge_target_unsigned_execute_is_blocked() -> None:
    t = EdgeTarget()
    res = t.execute(spec=_edge_spec(), operation="list_reservations")
    assert res.status == "blocked"


def test_edge_target_fetch_enforces_allowlist() -> None:
    t = EdgeTarget()
    assert t.fetch("http://sim-edgepos:8000/healthz")["url"].endswith("/healthz")
    with pytest.raises(NetworkBlockedError):
        t.fetch("http://sim-atlas:8000/healthz")


def test_edge_target_docker_run_args_edge_net() -> None:
    args = EdgeTarget().docker_run_args()
    assert args["network"] == "edge-net"
    assert args["network_allowlist"] == [EDGE_POS_HOST]


def test_edge_target_health_check() -> None:
    assert EdgeTarget().health_check() == {"status": "ok", "kind": "edge"}


def test_create_target_and_spec_mapping_edge() -> None:
    t = create_target({"kind": "edge", "endpoint": "http://edge-worker:8000"})
    assert isinstance(t, EdgeTarget)
    mapped = target_from_connector_spec({"connector": {"runtime_target": "edge"}})
    assert isinstance(mapped, EdgeTarget)


# ---------------------------------------------------------------------------
# Signed execution (deterministic via MockTransport)
# ---------------------------------------------------------------------------


def _mock_factory(payload: dict[str, Any]):  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))

    def factory(**kwargs: Any) -> httpx.Client:
        kwargs.pop("transport", None)
        return httpx.Client(transport=transport, **{k: v for k, v in kwargs.items() if k == "timeout"})

    return factory


def test_edge_signed_release_happy_path(monkeypatch: Any) -> None:
    monkeypatch.setenv("EDGEPOS_API_KEY", "test-key")
    spec = _edge_spec()
    sig = release_signature(spec)
    t = EdgeTarget()
    res = t.execute_signed_release(
        spec,
        sig,
        "list_reservations",
        client_factory=_mock_factory(
            {"data": [{"reservation_id": "EDGE-1001"}, {"reservation_id": "EDGE-1002"}]}
        ),
    )
    assert res.status == "ok"
    assert len(res.records) == 2


def test_edge_signed_release_rejects_tampered_and_unsigned() -> None:
    spec = _edge_spec()
    t = EdgeTarget()
    with pytest.raises(TargetConfigError, match="mismatch"):
        t.execute_signed_release(spec, "bogus", "list_reservations")
    with pytest.raises(TargetConfigError, match="unsigned"):
        t.execute_signed_release(spec, None, "list_reservations")


def test_edge_signed_release_blocks_disallowed_host() -> None:
    spec = _edge_spec("http://sim-atlas:8000")
    sig = release_signature(spec)
    with pytest.raises(NetworkBlockedError):
        EdgeTarget().execute_signed_release(spec, sig, "list_reservations")


# ---------------------------------------------------------------------------
# FakeEdgeTarget
# ---------------------------------------------------------------------------


def test_fake_edge_signed_ok_and_rejected() -> None:
    spec = _edge_spec()
    sig = release_signature(spec)
    fake = FakeEdgeTarget(responses={"list_reservations": [{"reservation_id": "EDGE-1"}]})
    res = fake.execute_signed_release(spec, sig, "list_reservations")
    assert res.status == "ok"
    assert res.records == [{"reservation_id": "EDGE-1"}]
    with pytest.raises(TargetConfigError):
        fake.execute_signed_release(spec, "wrong", "list_reservations")


# ---------------------------------------------------------------------------
# Edge worker app (rejection paths + wired happy path)
# ---------------------------------------------------------------------------


def test_edge_app_healthz_readyz() -> None:
    client = TestClient(edge_app)
    assert client.get("/healthz").json() == {"status": "ok"}
    body = client.get("/readyz").json()
    assert body["status"] == "ok"
    assert body["checks"]["edge_target"] == "ok"


def test_edge_app_rejects_unsigned_and_tampered() -> None:
    client = TestClient(edge_app)
    spec = _edge_spec()
    r = client.post("/execute", json={"spec": spec, "operation": "list_reservations"})
    assert r.status_code == 403
    r = client.post(
        "/execute",
        json={"spec": spec, "signature": "bogus", "operation": "list_reservations"},
    )
    assert r.status_code == 403


def test_edge_app_rejects_disallowed_host() -> None:
    client = TestClient(edge_app)
    spec = _edge_spec("http://sim-atlas:8000")
    r = client.post(
        "/execute",
        json={"spec": spec, "signature": release_signature(spec), "operation": "list_reservations"},
    )
    assert r.status_code == 403


def test_edge_app_readyz_degraded_on_wide_allowlist(monkeypatch: Any) -> None:
    monkeypatch.setenv("EDGE_ALLOWLIST", "sim-edgepos,verifier")
    client = TestClient(edge_app)
    body = client.get("/readyz").json()
    assert body["status"] == "degraded"
    assert body["checks"]["edge_target"] == "fail"


def test_edge_app_readyz_degraded_on_worker_error(monkeypatch: Any) -> None:
    from autobridge.edge import app as edge_mod

    def boom() -> Any:
        raise RuntimeError("no worker")

    monkeypatch.setattr(edge_mod, "_worker", boom)
    client = TestClient(edge_app)
    assert client.get("/readyz").json()["status"] == "degraded"


def test_edge_app_invalid_spec_maps_to_502() -> None:
    client = TestClient(edge_app)
    spec: dict[str, Any] = {"spec_version": 1, "source": {"base_url": "http://sim-edgepos:8000"}}
    r = client.post(
        "/execute",
        json={"spec": spec, "signature": release_signature(spec), "operation": "list_reservations"},
    )
    assert r.status_code == 502


def test_edge_app_happy_path_wired(monkeypatch: Any) -> None:
    from autobridge.edge import app as edge_mod

    def fake_exec(self: Any, spec: Any, signature: Any, operation: Any, params: Any = None) -> ExecutionResult:  # noqa: ARG001
        return ExecutionResult(status="ok", records=[{"reservation_id": "EDGE-1"}], output={"pages_fetched": 1})

    monkeypatch.setattr(edge_mod.EdgeTarget, "execute_signed_release", fake_exec)
    client = TestClient(edge_app)
    spec = _edge_spec()
    r = client.post(
        "/execute",
        json={"spec": spec, "signature": release_signature(spec), "operation": "list_reservations"},
    )
    assert r.status_code == 200
    assert r.json()["records"] == [{"reservation_id": "EDGE-1"}]


# ---------------------------------------------------------------------------
# AR-2-4 — HMAC-keyed release signatures
# ---------------------------------------------------------------------------


def test_keyed_signature_agrees_across_signer_and_verifier(monkeypatch: Any) -> None:
    """Signer (control models) and verifier (runtime targets) derive the same
    keyed digest from one env var; the keyed value differs from legacy."""
    from autobridge.control.models import spec_signature

    monkeypatch.setenv("EDGE_RELEASE_SECRET", "test-key-1")
    spec = _edge_spec()
    assert release_signature(spec) == spec_signature(spec)
    assert len(release_signature(spec)) == 64  # still hex sha256
    monkeypatch.delenv("EDGE_RELEASE_SECRET")
    legacy = release_signature(spec)
    assert legacy == spec_signature(spec)
    assert legacy != release_signature(spec, secret="test-key-1")


def test_keyed_signature_rejects_self_computed_legacy_hash(monkeypatch: Any) -> None:
    """AR-2-4 core: with a key configured, a self-computed unkeyed hash over
    a foreign spec is rejected — the signature is unforgeable, not just a
    checksum anyone can recompute."""
    spec = _edge_spec()
    legacy = release_signature(spec)  # env unset here: unkeyed sha256
    monkeypatch.setenv("EDGE_RELEASE_SECRET", "test-key-1")
    keyed = release_signature(spec)
    assert keyed != legacy
    verify_release_signature(spec, keyed)  # no raise
    with pytest.raises(TargetConfigError, match="HMAC.*mismatch"):
        verify_release_signature(spec, legacy)


def test_keyed_signature_rejects_tampered_wrong_secret_unsigned(monkeypatch: Any) -> None:
    monkeypatch.setenv("EDGE_RELEASE_SECRET", "test-key-1")
    spec = _edge_spec()
    sig = release_signature(spec)
    tampered = dict(spec)
    tampered["source"] = {"base_url": "http://evil:8000"}
    with pytest.raises(TargetConfigError, match="HMAC.*mismatch"):
        verify_release_signature(tampered, sig)
    foreign = release_signature(spec, secret="someone-elses-key")
    with pytest.raises(TargetConfigError, match="HMAC.*mismatch"):
        verify_release_signature(spec, foreign)
    with pytest.raises(TargetConfigError, match="unsigned"):
        verify_release_signature(spec, None)
    with pytest.raises(TargetConfigError, match="unsigned"):
        verify_release_signature(spec, "")


def test_keyed_signature_explicit_secret_overrides_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("EDGE_RELEASE_SECRET", "env-key")
    spec = _edge_spec()
    explicit = release_signature(spec, secret="explicit-key")
    assert explicit != release_signature(spec)  # env key gives a different digest
    verify_release_signature(spec, explicit, secret="explicit-key")  # no raise
    with pytest.raises(TargetConfigError, match="HMAC.*mismatch"):
        verify_release_signature(spec, explicit)  # env key does not accept it


def test_keyed_fake_edge_accepts_hmac_rejects_legacy(monkeypatch: Any) -> None:
    spec = _edge_spec()
    legacy = release_signature(spec)
    monkeypatch.setenv("EDGE_RELEASE_SECRET", "test-key-1")
    fake = FakeEdgeTarget(responses={"list_reservations": [{"reservation_id": "EDGE-1"}]})
    res = fake.execute_signed_release(spec, release_signature(spec), "list_reservations")
    assert res.status == "ok"
    with pytest.raises(TargetConfigError, match="HMAC.*mismatch"):
        fake.execute_signed_release(spec, legacy, "list_reservations")
