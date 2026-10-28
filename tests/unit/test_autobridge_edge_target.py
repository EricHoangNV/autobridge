"""REQ-022 B — on-premise agent target: signed-only execution, deny-by-default.

Covers :class:`EdgeTarget`, the release-signature helpers, ``FakeEdgeTarget``
and the edge agent app (health/readyz, no inbound execution port).  Live
container-network isolation is asserted in ``tests/e2e/test_s2_edge_target.py``
and ``scripts/smoke.sh``; these unit tests stay green without Docker.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from pathlib import Path

from autobridge.edge.app import app as edge_app
from autobridge.runtime.targets import (
    EdgeTarget,
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


def _edge_spec(base_url: str = "http://edge-relay:8000") -> dict[str, Any]:
    return {
        "spec_version": 1,
        "connector": {"id": "edge-http", "vendor": "EdgeRelay", "runtime_target": "edge"},
        "source": {
            "base_url": base_url,
            "auth": {"type": "bearer", "credential_ref": "TEST_API_KEY"},
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


def test_edge_target_defaults_to_deny_allowlist() -> None:
    t = EdgeTarget()
    assert t.kind == "edge"
    assert t.network_allowlist == []
    assert not t.can_reach("http://edge-relay:8000/v1/agents")
    assert not t.can_reach("http://verifier:8000/verify")
    assert not t.can_reach("http://sim-atlas:8000/reservations")


def test_edge_target_explicit_allowlist_reaches_relay() -> None:
    t = EdgeTarget(network_allowlist=["edge-relay"])
    assert t.can_reach("http://edge-relay:8000/v1/agents")
    assert not t.can_reach("http://verifier:8000/verify")
    assert not t.can_reach("http://edge-relay:8001/admin/reset")


def test_edge_target_rejects_blocked_allowlist() -> None:
    with pytest.raises(TargetConfigError):
        EdgeTarget(network_allowlist=["verifier"])


def test_edge_target_unsigned_execute_is_blocked() -> None:
    t = EdgeTarget()
    res = t.execute(spec=_edge_spec(), operation="list_reservations")
    assert res.status == "blocked"


def test_edge_target_fetch_enforces_allowlist() -> None:
    t = EdgeTarget(network_allowlist=["edge-relay"])
    assert t.fetch("http://edge-relay:8000/v1/agents")["url"].endswith("/v1/agents")
    with pytest.raises(NetworkBlockedError):
        t.fetch("http://sim-atlas:8000/healthz")


def test_edge_target_docker_run_args_edge_net() -> None:
    args = EdgeTarget().docker_run_args()
    assert args["network"] == "edge-net"
    assert args["network_allowlist"] == []


def test_edge_target_health_check() -> None:
    assert EdgeTarget().health_check() == {"status": "ok", "kind": "edge"}


def test_create_target_and_spec_mapping_edge() -> None:
    t = create_target({"kind": "edge", "endpoint": "http://edge-worker:8000"})
    assert isinstance(t, EdgeTarget)
    assert t.network_allowlist == []
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
    monkeypatch.setenv("TEST_API_KEY", "test-key")
    spec = _edge_spec()
    sig = release_signature(spec)
    t = EdgeTarget(network_allowlist=["edge-relay"])
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
    t = EdgeTarget(network_allowlist=["edge-relay"])
    with pytest.raises(TargetConfigError, match="mismatch"):
        t.execute_signed_release(spec, "bogus", "list_reservations")
    with pytest.raises(TargetConfigError, match="unsigned"):
        t.execute_signed_release(spec, None, "list_reservations")


def test_edge_signed_release_blocks_disallowed_host() -> None:
    spec = _edge_spec("http://sim-atlas:8000")
    sig = release_signature(spec)
    with pytest.raises(NetworkBlockedError):
        EdgeTarget(network_allowlist=["edge-relay"]).execute_signed_release(spec, sig, "list_reservations")


# ---------------------------------------------------------------------------
# FakeEdgeTarget
# ---------------------------------------------------------------------------


def test_fake_edge_signed_ok_and_rejected() -> None:
    spec = _edge_spec()
    sig = release_signature(spec)
    fake = FakeEdgeTarget(
        network_allowlist=["edge-relay"],
        responses={"list_reservations": [{"reservation_id": "EDGE-1"}]},
    )
    res = fake.execute_signed_release(spec, sig, "list_reservations")
    assert res.status == "ok"
    assert res.records == [{"reservation_id": "EDGE-1"}]
    with pytest.raises(TargetConfigError):
        fake.execute_signed_release(spec, "wrong", "list_reservations")


# ---------------------------------------------------------------------------
# Edge agent app (outbound-only: health/readyz, no inbound execution port)
# ---------------------------------------------------------------------------


def test_edge_app_healthz_readyz_unbuilt_is_degraded() -> None:
    """Without lifespan startup the state is unbuilt: ready degraded, never forced ok."""
    from autobridge.edge import app as edge_mod

    edge_mod.reset()
    client = TestClient(edge_app)
    assert client.get("/healthz").json() == {"status": "ok"}
    body = client.get("/readyz").json()
    assert body["status"] == "degraded"
    assert body["checks"]["store"] == "fail"
    assert body["checks"]["bundle"] == "fail"


def test_edge_app_has_no_inbound_execute_port() -> None:
    """Outbound only (REQ-022 B): the old POST /execute is gone entirely."""
    client = TestClient(edge_app)
    assert client.post("/execute", json={"spec": {}, "operation": "x"}).status_code == 404


def test_edge_app_readyz_ok_after_startup(tmp_path: Path, monkeypatch: Any) -> None:
    """Built agent with no PABX peer: ready ok, link reported down in info."""
    from autobridge.edge import app as edge_mod

    monkeypatch.setenv("EDGE_AGENT_DISABLE_LINK", "1")
    monkeypatch.setenv("EDGE_AGENT_DISABLE_CLOUD", "1")
    monkeypatch.setenv("EDGE_STORE_PATH", str(tmp_path / "agent.db"))
    monkeypatch.delenv("EDGE_BUNDLE_SIGNATURE", raising=False)
    edge_mod.reset()
    edge_mod.startup()
    try:
        client = TestClient(edge_app)
        body = client.get("/readyz").json()
        assert body["status"] == "ok", body
        assert body["checks"] == {"store": "ok", "bundle": "ok", "link_thread": "ok"}
        assert body["link"]["link_up"] is False
        assert body["queue"] == {"outbox": 0, "inbox": 0}
    finally:
        edge_mod.shutdown()


def test_edge_app_readyz_degraded_on_bad_bundle_signature(tmp_path: Path, monkeypatch: Any) -> None:
    """A pinned signature that does not match refuses the bundle (fail closed)."""
    from autobridge.edge import app as edge_mod

    monkeypatch.setenv("EDGE_AGENT_DISABLE_LINK", "1")
    monkeypatch.setenv("EDGE_AGENT_DISABLE_CLOUD", "1")
    monkeypatch.setenv("EDGE_STORE_PATH", str(tmp_path / "agent.db"))
    monkeypatch.setenv("EDGE_BUNDLE_SIGNATURE", "bogus")
    edge_mod.reset()
    edge_mod.startup()
    try:
        body = TestClient(edge_app).get("/readyz").json()
        assert body["status"] == "degraded"
        assert body["checks"]["bundle"] == "fail"
    finally:
        edge_mod.shutdown()
        monkeypatch.delenv("EDGE_BUNDLE_SIGNATURE", raising=False)


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
    fake = FakeEdgeTarget(
        network_allowlist=["edge-relay"],
        responses={"list_reservations": [{"reservation_id": "EDGE-1"}]},
    )
    res = fake.execute_signed_release(spec, release_signature(spec), "list_reservations")
    assert res.status == "ok"
    with pytest.raises(TargetConfigError, match="HMAC.*mismatch"):
        fake.execute_signed_release(spec, legacy, "list_reservations")
