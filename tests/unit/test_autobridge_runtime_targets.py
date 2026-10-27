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


def test_base_target_edge_reserved() -> None:
    with pytest.raises(TargetConfigError, match="edge.*reserved"):
        BaseTarget(kind="edge")


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
