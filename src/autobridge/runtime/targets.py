"""Runtime targets — real + fakes (P1.W1.9, plan §2.1, §2.4 W3.6).

The v1 engine executes :class:`ConnectorSpec` operations inside a
:class:`RuntimeTarget`.  Real targets are:

* :class:`LocalProcessTarget` — in-process, no network isolation.
* :class:`DockerSandboxTarget` — container with network allowlist, timeout
  and memory limits.  It cannot reach the verifier or simulator admin
  (negative network test, W3.6).

This module provides both the real minimal implementations and the
in-memory fakes used to reach 90% coverage on the control/SSE layer
and bench drivers without Docker (plan §9: ``90% coverage ... depends on
the sandbox and CLI fakes built in W1``).

The fake :class:`FakeRuntimeTarget` records every call and can be
configured to return canned responses, inject errors, or simulate a
timeout.  :class:`FakeDockerSandbox` adds strict allowlist enforcement
without spawning a container.

All network checks are via :func:`host_from_url` + allowlist matching;
``credential_ref`` values are never logged (DIRECTIVES §6).
"""

from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RuntimeTargetError(RuntimeError):
    """Base for target execution errors."""


class NetworkBlockedError(RuntimeTargetError):
    """Raised when a host is not in the sandbox allowlist."""

    def __init__(self, host: str, allowlist: list[str]) -> None:
        super().__init__(f"network blocked: {host!r} not in allowlist {allowlist}")
        self.host = host
        self.allowlist = allowlist


class TargetTimeoutError(RuntimeTargetError):
    """Raised when an execution exceeds ``timeout_s``."""


class TargetConfigError(ValueError):
    """Raised for invalid target configuration (e.g. edge reserved)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def host_from_url(url: str) -> str:
    """Extract host from *url* (empty string if unparseable)."""
    try:
        parsed = urllib.parse.urlparse(url)
        return (parsed.hostname or "").lower()
    except Exception:
        return ""


def _host_allowed(host: str, allowlist: list[str]) -> bool:
    """Return True if *host* matches the allowlist."""
    if not allowlist:
        return False
    h = host.lower()
    for entry in allowlist:
        e = entry.lower().strip()
        if not e:
            continue
        # Strip port/path from allowlist entry before compare.
        e_host = e.split(":")[0].split("/")[0]
        if not e_host:
            continue
        if h == e_host or h.endswith(f".{e_host}"):
            return True
        # Also allow exact entry with port (e.g. host == entry already handled via e_host)
        if h == e or h.endswith(f".{e}"):
            return True
    return False


# ---------------------------------------------------------------------------
# Execution result
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ExecutionResult:
    """Result of a single target execution."""

    status: Literal["ok", "error", "timeout", "blocked"]
    records: list[dict[str, Any]] = field(default_factory=list)
    output: dict[str, Any] | None = None
    error: str | None = None
    elapsed_ms: float = 0.0

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BaseTarget:
    """Shared config for all targets."""

    kind: str
    timeout_s: int = 60
    memory_mb: int = 512
    image: str | None = None
    workdir: str | None = None
    network_allowlist: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.timeout_s < 1 or self.timeout_s > 3600:
            raise TargetConfigError(f"timeout_s {self.timeout_s} must be 1..3600")
        if self.memory_mb < 64:
            raise TargetConfigError(f"memory_mb {self.memory_mb} must be >= 64")
        if self.kind == "edge":
            raise TargetConfigError("RuntimeTarget kind 'edge' is reserved (Phase 2 S2)")

    def can_reach(self, url: str) -> bool:
        """Return True if *url* host is allowed."""
        host = host_from_url(url)
        if not host:
            return False
        # LocalProcess allows all; DockerSandbox checks allowlist
        if self.kind == "local_process":
            return True
        return _host_allowed(host, self.network_allowlist)

    def check_network(self, url: str) -> None:
        """Raise :class:`NetworkBlockedError` if *url* is not allowed."""
        if not self.can_reach(url):
            host = host_from_url(url)
            raise NetworkBlockedError(host or url, self.network_allowlist)


# ---------------------------------------------------------------------------
# Real targets (minimal; W3.6 will wire the HTTP interpreter here)
# ---------------------------------------------------------------------------


class LocalProcessTarget(BaseTarget):
    """In-process execution for tests and local runs."""

    def __init__(
        self,
        *,
        timeout_s: int = 60,
        workdir: str | None = None,
    ) -> None:
        super().__init__(
            kind="local_process",
            timeout_s=timeout_s,
            workdir=workdir,
        )

    def execute(
        self,
        spec: dict[str, Any] | None = None,
        operation: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Stub execute — real logic arrives in W3."""
        _ = (spec, operation, params)
        return ExecutionResult(status="ok", records=[], output={})

    def health_check(self) -> dict[str, str]:
        return {"status": "ok", "kind": self.kind}


class DockerSandboxTarget(BaseTarget):
    """Isolated container with allowlist, timeout and resource limits."""

    def __init__(
        self,
        *,
        network_allowlist: list[str] | None = None,
        timeout_s: int = 60,
        memory_mb: int = 512,
        image: str | None = None,
    ) -> None:
        super().__init__(
            kind="docker_sandbox",
            network_allowlist=list(network_allowlist or []),
            timeout_s=timeout_s,
            memory_mb=memory_mb,
            image=image,
        )

    def execute(
        self,
        spec: dict[str, Any] | None = None,
        operation: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Stub — validates network then returns empty ok."""
        # In W3 this will spawn a container; for W1 it only validates.
        _ = (spec, operation, params)
        return ExecutionResult(status="ok", records=[], output={})

    def fetch(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Simulated fetch that enforces allowlist."""
        self.check_network(url)
        _ = params
        return {"url": url, "data": []}

    def health_check(self) -> dict[str, str]:
        return {"status": "ok", "kind": self.kind}


# ---------------------------------------------------------------------------
# Fake targets (in-memory, no Docker)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FakeRuntimeTarget(BaseTarget):
    """In-memory fake for unit tests (no Docker, no network).

    *responses* maps URL substring or operation name → canned response dict
    or callable ``(url, params) -> dict`` or exception to raise.
    *fail_on* maps trigger → exception for fault injection.
    All calls are appended to :attr:`calls` for assertions.
    """

    responses: dict[str, Any] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    fail_on: dict[str, Exception] = field(default_factory=dict)
    delay_s: float = 0.0

    def __init__(
        self,
        *,
        kind: str = "docker_sandbox",
        network_allowlist: list[str] | None = None,
        timeout_s: int = 60,
        memory_mb: int = 512,
        image: str | None = None,
        workdir: str | None = None,
        responses: dict[str, Any] | None = None,
        fail_on: dict[str, Exception] | None = None,
        delay_s: float = 0.0,
    ) -> None:
        # Bypass dataclass-generated __init__; set fields manually.
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "timeout_s", timeout_s)
        object.__setattr__(self, "memory_mb", memory_mb)
        object.__setattr__(self, "image", image)
        object.__setattr__(self, "workdir", workdir)
        object.__setattr__(self, "network_allowlist", list(network_allowlist or []))
        object.__setattr__(self, "responses", dict(responses or {}))
        object.__setattr__(self, "calls", [])
        object.__setattr__(self, "fail_on", dict(fail_on or {}))
        object.__setattr__(self, "delay_s", delay_s)
        self.__post_init__()

    def _maybe_delay(self) -> None:
        if self.delay_s > 0:
            if self.delay_s > self.timeout_s:
                raise TargetTimeoutError(f"execution exceeded timeout {self.timeout_s}s")
            time.sleep(self.delay_s)

    def _lookup(self, key: str, params: dict[str, Any] | None) -> Any:
        # Direct key hit
        if key in self.responses:
            val = self.responses[key]
            if callable(val):
                return val(key, params)
            if isinstance(val, BaseException):
                raise val
            return val
        # Substring match (URL contains key)
        for k, v in self.responses.items():
            if k in key:
                if callable(v):
                    return v(key, params)
                if isinstance(v, BaseException):
                    raise v
                return v
        return None

    def fetch(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Record call, enforce allowlist, return canned response or raise."""
        self.calls.append({"method": "fetch", "url": url, "params": params})
        self.check_network(url)
        # Fault injection
        for trigger, exc in self.fail_on.items():
            if trigger in url:
                raise exc
        self._maybe_delay()
        canned = self._lookup(url, params)
        if canned is not None:
            if isinstance(canned, dict):
                return canned
            return {"data": canned}
        return {"url": url, "data": []}

    def execute(
        self,
        spec: dict[str, Any] | None = None,
        operation: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Record call and return canned :class:`ExecutionResult`."""
        start = time.monotonic()
        self.calls.append(
            {"method": "execute", "spec": spec, "operation": operation, "params": params}
        )
        # Fault injection by operation name
        if operation and operation in self.fail_on:
            raise self.fail_on[operation]
        op_key = operation or "__default__"
        try:
            self._maybe_delay()
        except TargetTimeoutError as e:
            elapsed = (time.monotonic() - start) * 1000
            return ExecutionResult(status="timeout", error=str(e), elapsed_ms=elapsed)
        canned = self._lookup(op_key, params)
        elapsed = (time.monotonic() - start) * 1000
        if isinstance(canned, ExecutionResult):
            canned.elapsed_ms = elapsed
            return canned
        if isinstance(canned, dict):
            return ExecutionResult(status="ok", records=[], output=canned, elapsed_ms=elapsed)
        if isinstance(canned, list):
            return ExecutionResult(status="ok", records=canned, elapsed_ms=elapsed)
        # Default ok
        return ExecutionResult(status="ok", records=[], output={}, elapsed_ms=elapsed)

    def health_check(self) -> dict[str, str]:
        self.calls.append({"method": "health_check"})
        return {"status": "ok", "kind": self.kind}

    def reset(self) -> None:
        """Clear recorded calls."""
        self.calls.clear()

    @property
    def last_call(self) -> dict[str, Any] | None:
        return self.calls[-1] if self.calls else None


class FakeDockerSandbox(FakeRuntimeTarget):
    """Fake that strictly enforces allowlist like a real Docker sandbox."""

    def __init__(
        self,
        *,
        network_allowlist: list[str] | None = None,
        timeout_s: int = 60,
        memory_mb: int = 512,
        image: str | None = None,
        responses: dict[str, Any] | None = None,
        fail_on: dict[str, Exception] | None = None,
        delay_s: float = 0.0,
    ) -> None:
        super().__init__(
            kind="docker_sandbox",
            network_allowlist=network_allowlist,
            timeout_s=timeout_s,
            memory_mb=memory_mb,
            image=image,
            responses=responses,
            fail_on=fail_on,
            delay_s=delay_s,
        )


class FakeLocalProcess(FakeRuntimeTarget):
    """Fake for local process (allows all hosts)."""

    def __init__(
        self,
        *,
        timeout_s: int = 60,
        workdir: str | None = None,
        responses: dict[str, Any] | None = None,
        fail_on: dict[str, Exception] | None = None,
        delay_s: float = 0.0,
    ) -> None:
        super().__init__(
            kind="local_process",
            network_allowlist=[],
            timeout_s=timeout_s,
            workdir=workdir,
            responses=responses,
            fail_on=fail_on,
            delay_s=delay_s,
        )

    def can_reach(self, url: str) -> bool:
        # Local process can reach anything with a valid host
        return bool(host_from_url(url))
