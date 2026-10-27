"""Tests for RuntimeTarget fakes (P1.W1.9, plan §9)."""

from __future__ import annotations

import pytest

from autobridge.runtime.targets import (
    BaseTarget,
    DockerSandboxTarget,
    ExecutionResult,
    FakeDockerSandbox,
    FakeLocalProcess,
    FakeRuntimeTarget,
    LocalProcessTarget,
    NetworkBlockedError,
    TargetConfigError,
    TargetTimeoutError,
    _host_allowed,
    host_from_url,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_host_from_url_ok() -> None:
    assert host_from_url("https://atlas.example.com/v1/bookings") == "atlas.example.com"
    assert host_from_url("http://api.foo.com:8000/path?a=1") == "api.foo.com"
    assert host_from_url("https://ATLAS.EXAMPLE.COM/path") == "atlas.example.com"
    assert host_from_url("not a url") == ""
    assert host_from_url("") == ""


def test_host_allowed_exact() -> None:
    assert _host_allowed("atlas.example.com", ["atlas.example.com"])
    assert not _host_allowed("verifier", ["atlas.example.com"])


def test_host_allowed_subdomain() -> None:
    assert _host_allowed("api.atlas.example.com", ["atlas.example.com"])
    assert not _host_allowed("atlas.example.com.evil.com", ["atlas.example.com"])


def test_host_allowed_empty_allowlist() -> None:
    assert not _host_allowed("atlas.example.com", [])
    assert not _host_allowed("atlas.example.com", [""])


def test_host_allowed_port_stripping() -> None:
    assert _host_allowed("atlas.example.com", ["atlas.example.com:8000"])


def test_host_allowed_case_insensitive() -> None:
    assert _host_allowed("ATLAS.EXAMPLE.COM", ["atlas.example.com"])


# ---------------------------------------------------------------------------
# ExecutionResult
# ---------------------------------------------------------------------------


def test_execution_result_is_ok() -> None:
    assert ExecutionResult(status="ok").is_ok
    assert not ExecutionResult(status="error").is_ok
    assert not ExecutionResult(status="timeout").is_ok
    assert not ExecutionResult(status="blocked").is_ok


# ---------------------------------------------------------------------------
# BaseTarget validation
# ---------------------------------------------------------------------------


def test_base_target_validation_timeout_low() -> None:
    with pytest.raises(TargetConfigError, match="timeout_s"):
        BaseTarget(kind="local_process", timeout_s=0)
    with pytest.raises(TargetConfigError, match="timeout_s"):
        BaseTarget(kind="docker_sandbox", timeout_s=5000)


def test_base_target_validation_memory() -> None:
    with pytest.raises(TargetConfigError, match="memory_mb"):
        BaseTarget(kind="docker_sandbox", timeout_s=10, memory_mb=10)


def test_base_target_edge_builds_with_allowlist() -> None:
    t = BaseTarget(kind="edge", timeout_s=10, network_allowlist=["sim-edgepos"])
    assert t.kind == "edge"
    assert t.can_reach("http://sim-edgepos:8000/healthz")
    assert not t.can_reach("http://verifier:8000/verify")


def test_base_target_can_reach_local_allows_all() -> None:
    t = BaseTarget(kind="local_process", timeout_s=10, network_allowlist=[])
    assert t.can_reach("https://any.example.com/path")
    assert t.can_reach("http://verifier:8000/verify")
    assert not t.can_reach("not-a-url")


def test_base_target_can_reach_docker_allowlist() -> None:
    t = BaseTarget(kind="docker_sandbox", timeout_s=10, network_allowlist=["atlas.example.com"])
    assert t.can_reach("https://atlas.example.com/api")
    assert not t.can_reach("https://verifier:8000/verify")
    assert not t.can_reach("https://other.com/api")


def test_base_target_check_network_raise() -> None:
    t = BaseTarget(kind="docker_sandbox", timeout_s=10, network_allowlist=["atlas.example.com"])
    t.check_network("https://atlas.example.com/x")  # no raise
    with pytest.raises(NetworkBlockedError) as ei:
        t.check_network("https://verifier:8000/verify")
    assert ei.value.host == "verifier"
    assert ei.value.allowlist == ["atlas.example.com"]


def test_base_target_check_network_invalid_url() -> None:
    t = BaseTarget(kind="docker_sandbox", timeout_s=10, network_allowlist=["atlas.example.com"])
    with pytest.raises(NetworkBlockedError):
        t.check_network("not-a-url")


# ---------------------------------------------------------------------------
# LocalProcessTarget
# ---------------------------------------------------------------------------


def test_local_process_target_execute() -> None:
    t = LocalProcessTarget(timeout_s=5)
    assert t.kind == "local_process"
    res = t.execute()
    assert res.is_ok
    assert res.records == []


def test_local_process_health() -> None:
    t = LocalProcessTarget()
    assert t.health_check() == {"status": "ok", "kind": "local_process"}


def test_local_process_invalid_timeout() -> None:
    with pytest.raises(TargetConfigError):
        LocalProcessTarget(timeout_s=0)


# ---------------------------------------------------------------------------
# DockerSandboxTarget
# ---------------------------------------------------------------------------


def test_docker_sandbox_execute() -> None:
    t = DockerSandboxTarget(network_allowlist=["atlas.example.com"])
    assert t.kind == "docker_sandbox"
    res = t.execute(spec={"foo": 1}, operation="list_reservations")
    assert res.is_ok


def test_docker_sandbox_fetch_ok_and_blocked() -> None:
    t = DockerSandboxTarget(network_allowlist=["atlas.example.com"])
    out = t.fetch("https://atlas.example.com/api/bookings")
    assert out["url"] == "https://atlas.example.com/api/bookings"
    with pytest.raises(NetworkBlockedError):
        t.fetch("http://verifier:8000/verify")


def test_docker_sandbox_fetch_with_params() -> None:
    t = DockerSandboxTarget(network_allowlist=["atlas.example.com"])
    out = t.fetch("https://atlas.example.com/api", params={"since": "2026-01-01"})
    assert out["url"] == "https://atlas.example.com/api"


def test_docker_sandbox_health() -> None:
    t = DockerSandboxTarget()
    assert t.health_check()["status"] == "ok"


def test_docker_sandbox_invalid_memory() -> None:
    with pytest.raises(TargetConfigError):
        DockerSandboxTarget(memory_mb=10)


# ---------------------------------------------------------------------------
# FakeRuntimeTarget
# ---------------------------------------------------------------------------


def test_fake_fetch_canned_dict() -> None:
    fake = FakeRuntimeTarget(
        network_allowlist=["atlas.example.com"],
        responses={"bookings": {"data": [{"id": 1}]}},
    )
    out = fake.fetch("https://atlas.example.com/bookings")
    assert out["data"] == [{"id": 1}]
    assert fake.calls[0]["method"] == "fetch"


def test_fake_fetch_canned_list() -> None:
    fake = FakeRuntimeTarget(
        network_allowlist=["atlas.example.com"],
        responses={"bookings": [1, 2, 3]},
    )
    out = fake.fetch("https://atlas.example.com/bookings")
    assert out["data"] == [1, 2, 3]


def test_fake_fetch_callable() -> None:
    def responder(url: str, params: dict | None) -> dict[str, object]:
        return {"url": url, "echo": params}

    fake = FakeRuntimeTarget(
        network_allowlist=["atlas.example.com"],
        responses={"atlas.example.com": responder},
    )
    out = fake.fetch("https://atlas.example.com/x", params={"a": 1})
    assert out["echo"] == {"a": 1}  # type: ignore[comparison-overlap]


def test_fake_fetch_exception() -> None:
    fake = FakeRuntimeTarget(
        network_allowlist=["atlas.example.com"],
        responses={"atlas.example.com": RuntimeError("boom")},
    )
    with pytest.raises(RuntimeError, match="boom"):
        fake.fetch("https://atlas.example.com/x")


def test_fake_fetch_default() -> None:
    fake = FakeRuntimeTarget(network_allowlist=["atlas.example.com"])
    out = fake.fetch("https://atlas.example.com/unknown")
    assert out["url"] == "https://atlas.example.com/unknown"


def test_fake_fetch_blocked() -> None:
    fake = FakeRuntimeTarget(network_allowlist=["atlas.example.com"])
    with pytest.raises(NetworkBlockedError):
        fake.fetch("https://verifier:8000/verify")


def test_fake_fetch_fail_on() -> None:
    fake = FakeRuntimeTarget(
        network_allowlist=["atlas.example.com"],
        fail_on={"bookings": RuntimeError("injected")},
    )
    with pytest.raises(RuntimeError, match="injected"):
        fake.fetch("https://atlas.example.com/bookings")


def test_fake_fetch_delay_timeout() -> None:
    fake = FakeRuntimeTarget(
        network_allowlist=["atlas.example.com"],
        timeout_s=1,
        delay_s=5,
    )
    with pytest.raises(TargetTimeoutError):
        fake.fetch("https://atlas.example.com/x")


def test_fake_fetch_delay_ok() -> None:
    fake = FakeRuntimeTarget(
        network_allowlist=["atlas.example.com"],
        timeout_s=5,
        delay_s=0.01,
    )
    out = fake.fetch("https://atlas.example.com/x")
    assert out["url"] == "https://atlas.example.com/x"


def test_fake_execute_default() -> None:
    fake = FakeRuntimeTarget(network_allowlist=["atlas.example.com"])
    res = fake.execute(operation="list_reservations")
    assert res.is_ok
    assert res.elapsed_ms >= 0


def test_fake_execute_canned_execution_result() -> None:
    canned = ExecutionResult(status="ok", records=[{"id": 1}], output={"x": 1})
    fake = FakeRuntimeTarget(responses={"list_reservations": canned})
    res = fake.execute(operation="list_reservations")
    assert res.records == [{"id": 1}]


def test_fake_execute_canned_dict() -> None:
    fake = FakeRuntimeTarget(responses={"my_op": {"result": "hi"}})
    res = fake.execute(operation="my_op")
    assert res.is_ok
    assert res.output == {"result": "hi"}


def test_fake_execute_canned_list() -> None:
    fake = FakeRuntimeTarget(responses={"my_op": [{"id": 1}, {"id": 2}]})
    res = fake.execute(operation="my_op")
    assert len(res.records) == 2


def test_fake_execute_fail_on() -> None:
    fake = FakeRuntimeTarget(fail_on={"bad_op": RuntimeError("fail")})
    with pytest.raises(RuntimeError, match="fail"):
        fake.execute(operation="bad_op")


def test_fake_execute_timeout() -> None:
    fake = FakeRuntimeTarget(timeout_s=1, delay_s=5)
    res = fake.execute(operation="op")
    assert res.status == "timeout"
    assert "exceeded timeout" in (res.error or "")


def test_fake_execute_substring_match() -> None:
    fake = FakeRuntimeTarget(responses={"bookings": {"data": [1]}})
    res = fake.execute(operation="https://host/bookings")
    assert res.output == {"data": [1]}


def test_fake_execute_callable() -> None:
    def fn(key: str, params: dict | None) -> dict[str, object]:
        return {"key": key, "params": params or {}}

    fake = FakeRuntimeTarget(responses={"op": fn})
    res = fake.execute(operation="op", params={"a": 1})
    assert res.output is not None
    assert res.output["key"] == "op"  # type: ignore[index]


def test_fake_health_and_reset() -> None:
    fake = FakeRuntimeTarget(network_allowlist=["atlas.example.com"])
    assert fake.health_check()["status"] == "ok"
    assert len(fake.calls) == 1
    fake.fetch("https://atlas.example.com/x")
    assert len(fake.calls) == 2
    fake.reset()
    assert fake.calls == []
    assert fake.last_call is None


def test_fake_last_call() -> None:
    fake = FakeRuntimeTarget()
    assert fake.last_call is None
    fake.execute(operation="op1")
    assert fake.last_call is not None
    assert fake.last_call["operation"] == "op1"


# ---------------------------------------------------------------------------
# FakeDockerSandbox vs FakeLocalProcess
# ---------------------------------------------------------------------------


def test_fake_docker_sandbox_blocked() -> None:
    fake = FakeDockerSandbox(network_allowlist=["atlas.example.com"])
    assert fake.kind == "docker_sandbox"
    with pytest.raises(NetworkBlockedError):
        fake.fetch("https://verifier:8000/verify")
    out = fake.fetch("https://atlas.example.com/ok")
    assert "atlas.example.com" in out["url"]


def test_fake_docker_sandbox_execute_timeout() -> None:
    fake = FakeDockerSandbox(timeout_s=1, delay_s=10)
    res = fake.execute(operation="op")
    assert res.status == "timeout"


def test_fake_local_process_allows_all() -> None:
    fake = FakeLocalProcess()
    assert fake.kind == "local_process"
    # Allows any valid host
    assert fake.can_reach("https://verifier:8000/verify")
    assert fake.can_reach("https://atlas.example.com/x")
    assert not fake.can_reach("not-a-url")
    # Fetch should not block on allowlist
    out = fake.fetch("https://verifier:8000/verify")
    assert "verifier" in out["url"]


def test_fake_local_process_execute() -> None:
    fake = FakeLocalProcess(responses={"op": {"ok": True}})
    res = fake.execute(operation="op")
    assert res.output == {"ok": True}


def test_fake_local_process_invalid_timeout() -> None:
    with pytest.raises(TargetConfigError):
        FakeLocalProcess(timeout_s=0)


def test_fake_docker_invalid_memory() -> None:
    with pytest.raises(TargetConfigError):
        FakeDockerSandbox(memory_mb=1)


# ---------------------------------------------------------------------------
# W3.6 — RuntimeTarget with network allowlist, timeout, resource limits,
# Edge reserved, and negative network test (plan §2.1, §2.4)
# ---------------------------------------------------------------------------


def test_docker_sandbox_blocks_verifier_host() -> None:
    """DockerSandbox with vendor allowlist must not reach verifier (W3.6 negative test)."""
    from autobridge.runtime.targets import DockerSandboxTarget

    target = DockerSandboxTarget(network_allowlist=["sim-atlas", "sim-harbor"])
    # Public vendor hosts are reachable
    assert target.can_reach("http://sim-atlas:8000/healthz")
    assert target.can_reach("http://sim-harbor:8000/v1/reservations")
    # Verifier must be blocked — host not in allowlist
    assert not target.can_reach("http://verifier:8000/healthz")
    assert not target.can_reach("http://verifier:8000/verify")
    with pytest.raises(NetworkBlockedError) as ei:
        target.fetch("http://verifier:8000/verify")
    assert ei.value.host == "verifier"
    # Also via check_network
    with pytest.raises(NetworkBlockedError):
        target.check_network("http://verifier:8000/healthz")


def test_docker_sandbox_blocks_simulator_admin() -> None:
    """DockerSandbox must not reach simulator admin API (W3.6, /admin guard)."""
    from autobridge.runtime.targets import DockerSandboxTarget

    target = DockerSandboxTarget(network_allowlist=["sim-atlas", "sim-harbor"])
    # Admin paths are blocked even though host matches allowlist (path guard)
    assert not target.can_reach("http://sim-atlas:8001/admin/scenario")
    assert not target.can_reach("http://sim-atlas:8001/admin/reset")
    assert not target.can_reach("http://sim-harbor:8001/admin/scenario")
    assert not target.can_reach("http://sim-harbor:8000/admin/scenario")
    with pytest.raises(NetworkBlockedError):
        target.fetch("http://sim-atlas:8001/admin/scenario")
    with pytest.raises(NetworkBlockedError):
        target.check_network("http://sim-harbor:8001/admin/reset")


def test_docker_sandbox_rejects_blocked_host_in_allowlist() -> None:
    """Allowlist must not contain verifier or other blocked hosts (fail-fast)."""
    from autobridge.runtime.targets import DockerSandboxTarget, TargetConfigError

    with pytest.raises(TargetConfigError, match="blocked host"):
        DockerSandboxTarget(network_allowlist=["verifier"])
    with pytest.raises(TargetConfigError, match="blocked host"):
        DockerSandboxTarget(network_allowlist=["verifier:8000"])
    with pytest.raises(TargetConfigError, match="blocked host"):
        DockerSandboxTarget(network_allowlist=["sim-atlas", "verifier"])


def test_docker_sandbox_resource_limits() -> None:
    """DockerSandbox enforces memory and timeout bounds (W3.6 resource limits)."""
    from autobridge.runtime.targets import DockerSandboxTarget, TargetConfigError

    # Valid resource limits
    t = DockerSandboxTarget(network_allowlist=["a.example.com"], memory_mb=1024, timeout_s=30)
    assert t.memory_mb == 1024
    assert t.timeout_s == 30
    limits = t.resource_limits()
    assert limits["memory_mb"] == 1024
    assert limits["timeout_s"] == 30
    # Docker run args contain resource limits
    args = t.docker_run_args()
    assert args["mem_limit"] == "1024m"
    assert args["timeout_s"] == 30
    assert args["network"] == "vendor-net"

    # Memory too low
    with pytest.raises(TargetConfigError, match="memory_mb"):
        DockerSandboxTarget(memory_mb=10)
    # Memory too high
    with pytest.raises(TargetConfigError, match="memory_mb"):
        DockerSandboxTarget(memory_mb=9000)
    # Timeout too low
    with pytest.raises(TargetConfigError, match="timeout_s"):
        DockerSandboxTarget(timeout_s=0)
    # Timeout too high
    with pytest.raises(TargetConfigError, match="timeout_s"):
        DockerSandboxTarget(timeout_s=9999)


def test_local_process_resource_limits_and_timeout() -> None:
    """LocalProcess validates timeout and carries resource limits."""
    from autobridge.runtime.targets import LocalProcessTarget, TargetConfigError

    t = LocalProcessTarget(timeout_s=120, memory_mb=256)
    assert t.timeout_s == 120
    assert t.resource_limits()["memory_mb"] == 256
    with pytest.raises(TargetConfigError, match="timeout_s"):
        LocalProcessTarget(timeout_s=0)


def test_edge_target_via_factory() -> None:
    """Edge kind builds via the factory with the edge-only default allowlist (P2.S2)."""
    from autobridge.runtime.targets import EdgeTarget, create_target

    t = create_target({"kind": "edge"})
    assert isinstance(t, EdgeTarget)
    assert t.kind == "edge"
    assert t.can_reach("http://sim-edgepos:8000/healthz")
    t2 = create_target({"kind": "edge", "endpoint": "http://edge.local"})
    assert isinstance(t2, EdgeTarget)


def test_create_target_factory() -> None:
    """Factory creates correct target type from config dict."""
    from autobridge.runtime.targets import DockerSandboxTarget, LocalProcessTarget, create_target

    lp = create_target({"kind": "local_process", "timeout_s": 15})
    assert isinstance(lp, LocalProcessTarget)
    assert lp.timeout_s == 15

    ds = create_target(
        {"kind": "docker_sandbox", "network_allowlist": ["sim-atlas"], "memory_mb": 256, "timeout_s": 45}
    )
    assert isinstance(ds, DockerSandboxTarget)
    assert ds.memory_mb == 256
    assert ds.network_allowlist == ["sim-atlas"]

    # Defaults
    ds2 = create_target({"kind": "docker_sandbox"})
    assert ds2.timeout_s == 60
    assert ds2.memory_mb == 512


def test_target_from_connector_spec() -> None:
    """target_from_connector_spec extracts runtime_target from spec dict."""
    from autobridge.runtime.targets import (
        DockerSandboxTarget,
        LocalProcessTarget,
        target_from_connector_spec,
    )

    # No target -> None (caller defaults to LocalProcess)
    assert target_from_connector_spec({"connector": {"id": "x"}}) is None
    assert target_from_connector_spec({}) is None

    # Top-level runtime_target
    t = target_from_connector_spec({"runtime_target": {"kind": "local_process", "timeout_s": 20}})
    assert isinstance(t, LocalProcessTarget)
    assert t.timeout_s == 20

    # ConnectorMeta runtime_target
    t2 = target_from_connector_spec(
        {"connector": {"runtime_target": {"kind": "docker_sandbox", "network_allowlist": ["sim-atlas"]}}}
    )
    assert isinstance(t2, DockerSandboxTarget)

    # Legacy string form
    t3 = target_from_connector_spec({"connector": {"runtime_target": "local"}})
    assert isinstance(t3, LocalProcessTarget)
    t4 = target_from_connector_spec({"connector": {"runtime_target": "sandbox"}})
    assert isinstance(t4, DockerSandboxTarget)


def test_local_process_can_reach_verifier_and_admin() -> None:
    """LocalProcess has no isolation — can reach verifier and admin (vs DockerSandbox)."""
    from autobridge.runtime.targets import DockerSandboxTarget, LocalProcessTarget

    lp = LocalProcessTarget()
    assert lp.can_reach("http://verifier:8000/healthz")
    assert lp.can_reach("http://sim-atlas:8001/admin/scenario")

    ds = DockerSandboxTarget(network_allowlist=["sim-atlas"])
    assert not ds.can_reach("http://verifier:8000/healthz")
    assert not ds.can_reach("http://sim-atlas:8001/admin/scenario")
    # But vendor public API is reachable
    assert ds.can_reach("http://sim-atlas:8000/healthz")


def test_blocked_path_helper() -> None:
    """_is_blocked_path correctly identifies admin prefixes."""
    from autobridge.runtime.targets import _is_blocked_path

    assert _is_blocked_path("http://host/admin/scenario")
    assert _is_blocked_path("http://host/admin")
    assert _is_blocked_path("http://host/admin/reset")
    assert not _is_blocked_path("http://host/api/reservations")
    assert not _is_blocked_path("http://host/healthz")
    assert not _is_blocked_path("http://host/v1/admin_notes")


def test_validate_allowlist_helper() -> None:
    """validate_allowlist rejects blocked hosts."""
    from autobridge.runtime.targets import TargetConfigError, validate_allowlist

    validate_allowlist(["sim-atlas", "atlas.example.com"])  # ok
    validate_allowlist([])  # ok — empty is allowed (blocks all by default)
    with pytest.raises(TargetConfigError, match="blocked host"):
        validate_allowlist(["verifier"])
    with pytest.raises(TargetConfigError, match="blocked host"):
        validate_allowlist(["VERIFIER"])  # case insensitive
