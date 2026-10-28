"""Runtime targets — real + fakes (P1.W1.9, P1.W3.6, P2.S2, plan §2.1, §2.4).

The v1 engine executes :class:`ConnectorSpec` operations inside a
:class:`RuntimeTarget`.  Real targets are:

* :class:`LocalProcessTarget` — in-process, no network isolation.  Used for
  local runs and unit tests; it can reach any host with a valid URL.
* :class:`DockerSandboxTarget` — container with network allowlist, timeout
  and memory limits.  It cannot reach the verifier or simulator admin
  (negative network test, W3.6).  Isolation is enforced at two layers:
  Docker Compose network attachment (vendor-net only, no verifier-net/admin-net)
  and in-process allowlist checks (host + ``/admin`` path guard).
* :class:`EdgeTarget` — on-premise agent target (REQ-022 B) on the isolated
  ``edge-net``.  It executes only signed releases: the agent verifies the
  release signature before running anything (verify centrally, execute
  locally — HMAC-signed with ``EDGE_RELEASE_SECRET`` when configured,
  integrity checksum otherwise, AR-2-4).  HTTP egress defaults to deny
  (empty allowlist): the agent speaks FIAS over TCP and polls the
  ``edge-relay`` queue instead of proxying vendor HTTP.

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

import hashlib
import hmac
import json
import os
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
    """Raised for invalid target configuration."""


# ---------------------------------------------------------------------------
# Constants — network isolation (W3.6)
# ---------------------------------------------------------------------------

# Hosts that must never appear in a DockerSandbox allowlist.  The runtime-
# sandbox container has no Docker network route to verifier-net or admin-net
# (see compose.yaml), but we also enforce it at the application layer so
# a misconfigured spec cannot silently widen the sandbox.
BLOCKED_HOSTS: frozenset[str] = frozenset(
    {
        "verifier",
        "control",
        "bench",
        # Admin hosts are addressed by the same simulator hostname but on the
        # admin port/path; the path guard below blocks /admin regardless of host.
    }
)

# Paths that are always blocked for DockerSandbox (simulator admin API).
BLOCKED_PATH_PREFIXES: tuple[str, ...] = ("/admin",)

# Resource limits (plan §2.4 runtime-target.json + W3.6).
MIN_MEMORY_MB = 64
MAX_MEMORY_MB = 8192
MIN_TIMEOUT_S = 1
MAX_TIMEOUT_S = 3600
DEFAULT_TIMEOUT_S = 60
DEFAULT_MEMORY_MB = 512


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


def path_from_url(url: str) -> str:
    """Extract path from *url* (empty string if unparseable)."""
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.path or ""
    except Exception:
        return ""


def _is_blocked_path(url: str) -> bool:
    """Return True if *url* path is in the blocked admin prefix set."""
    path = path_from_url(url)
    return any(path == prefix or path.startswith(prefix + "/") for prefix in BLOCKED_PATH_PREFIXES)


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


def validate_allowlist(allowlist: list[str]) -> None:
    """Reject allowlists that contain blocked hosts.

    Raises :class:`TargetConfigError` when a blocked host is present so a
    misconfigured spec fails fast at creation time rather than at first fetch.
    """
    for entry in allowlist:
        e_host = entry.lower().strip().split(":")[0].split("/")[0]
        if not e_host:
            continue
        # Check exact blocked host or subdomain of blocked host
        for blocked in BLOCKED_HOSTS:
            if e_host == blocked or e_host == f"{blocked}.local" or e_host.endswith(f".{blocked}"):
                raise TargetConfigError(
                    f"network_allowlist must not contain blocked host {entry!r} "
                    f"(verifier/admin isolation, W3.6)"
                )
            # Also check if allowlist entry is the blocked host itself
            if e_host == blocked:
                raise TargetConfigError(
                    f"network_allowlist must not contain blocked host {entry!r}"
                )


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
        if self.timeout_s < MIN_TIMEOUT_S or self.timeout_s > MAX_TIMEOUT_S:
            raise TargetConfigError(f"timeout_s {self.timeout_s} must be {MIN_TIMEOUT_S}..{MAX_TIMEOUT_S}")
        if self.memory_mb < MIN_MEMORY_MB:
            raise TargetConfigError(f"memory_mb {self.memory_mb} must be >= {MIN_MEMORY_MB}")
        if self.memory_mb > MAX_MEMORY_MB:
            raise TargetConfigError(f"memory_mb {self.memory_mb} must be <= {MAX_MEMORY_MB}")
        # docker_sandbox + edge allowlists must not contain blocked hosts
        if self.kind in ("docker_sandbox", "edge") and self.network_allowlist:
            validate_allowlist(self.network_allowlist)

    def can_reach(self, url: str) -> bool:
        """Return True if *url* host is allowed."""
        # Admin path is always blocked for isolated targets, even if host matches
        if self.kind in ("docker_sandbox", "edge") and _is_blocked_path(url):
            return False
        host = host_from_url(url)
        if not host:
            return False
        # LocalProcess allows all; isolated targets check the allowlist
        if self.kind == "local_process":
            return True
        return _host_allowed(host, self.network_allowlist)

    def check_network(self, url: str) -> None:
        """Raise :class:`NetworkBlockedError` if *url* is not allowed."""
        if not self.can_reach(url):
            host = host_from_url(url)
            # Include path in host hint for admin blocks
            if _is_blocked_path(url):
                raise NetworkBlockedError(host or url, self.network_allowlist)
            raise NetworkBlockedError(host or url, self.network_allowlist)

    def resource_limits(self) -> dict[str, Any]:
        """Return resource limits for container creation (memory, timeout)."""
        return {
            "memory_mb": self.memory_mb,
            "timeout_s": self.timeout_s,
            "image": self.image,
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_target(config: dict[str, Any]) -> BaseTarget:
    """Create a :class:`BaseTarget` from a runtime_target config dict.

    The dict is the ``connector.runtime_target`` value or the
    ``runtime-target.json`` payload validated against its JSON Schema.
    ``kind`` is required; ``edge`` is rejected; missing fields get defaults.
    """
    if not isinstance(config, dict):
        raise TargetConfigError(f"runtime_target must be a mapping, got {type(config).__name__}")
    kind = config.get("kind")
    if not kind:
        raise TargetConfigError("runtime_target missing required field 'kind'")
    if kind == "local_process":
        return LocalProcessTarget(
            timeout_s=int(config.get("timeout_s", DEFAULT_TIMEOUT_S)),
            workdir=config.get("workdir"),
        )
    if kind == "docker_sandbox":
        return DockerSandboxTarget(
            network_allowlist=list(config.get("network_allowlist", [])),
            timeout_s=int(config.get("timeout_s", DEFAULT_TIMEOUT_S)),
            memory_mb=int(config.get("memory_mb", DEFAULT_MEMORY_MB)),
            image=config.get("image"),
        )
    if kind == "edge":
        return EdgeTarget(
            endpoint=config.get("endpoint"),
            network_allowlist=list(config.get("network_allowlist", [])),
            timeout_s=int(config.get("timeout_s", DEFAULT_TIMEOUT_S)),
            memory_mb=int(config.get("memory_mb", DEFAULT_MEMORY_MB)),
        )
    raise TargetConfigError(f"unknown RuntimeTarget kind {kind!r}")


def target_from_connector_spec(spec_dict: dict[str, Any]) -> BaseTarget | None:
    """Extract and build a target from a ConnectorSpec dict.

    Looks for ``connector.runtime_target`` (ConnectorMeta) and falls back to
    ``runtime_target`` top-level key.  Returns None when no target is declared
    so callers can default to LocalProcess.
    """
    # Try ConnectorMeta path
    connector_meta = spec_dict.get("connector")
    if isinstance(connector_meta, dict) and "runtime_target" in connector_meta:
        raw = connector_meta["runtime_target"]
        if isinstance(raw, dict):
            return create_target(raw)
        if isinstance(raw, str):
            # Legacy string form: "cloud" / "local" / "sandbox"
            mapping = {
                "cloud": {"kind": "docker_sandbox"},
                "local": {"kind": "local_process"},
                "sandbox": {"kind": "docker_sandbox"},
            }
            if raw in mapping:
                return create_target(mapping[raw])
            if raw == "edge":
                return create_target({"kind": "edge"})
    # Top-level fallback
    if "runtime_target" in spec_dict and isinstance(spec_dict["runtime_target"], dict):
        return create_target(spec_dict["runtime_target"])
    return None


# ---------------------------------------------------------------------------
# Real targets
# ---------------------------------------------------------------------------


class LocalProcessTarget(BaseTarget):
    """In-process execution for tests and local runs.

    No network isolation; respects ``timeout_s`` via the underlying
    :class:`autobridge.runtime.http.HttpClient` timeout.  ``memory_mb`` is
    accepted but not enforced (in-process has no cgroup).
    """

    def __init__(
        self,
        *,
        timeout_s: int = 60,
        workdir: str | None = None,
        memory_mb: int = 512,
        image: str | None = None,
    ) -> None:
        super().__init__(
            kind="local_process",
            timeout_s=timeout_s,
            memory_mb=memory_mb,
            workdir=workdir,
            image=image,
        )

    def execute(
        self,
        spec: dict[str, Any] | None = None,
        operation: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Execute via :class:`Interpreter` when spec provided, else stub ok."""
        if (
            spec is not None
            and operation is not None
            and isinstance(spec, dict)
            and "source" in spec
            and "spec_version" in spec
        ):
            try:
                from autobridge.runtime.http import HttpClient
                from autobridge.spec.models import load_spec_from_dict

                spec_obj = load_spec_from_dict(spec)
                http_client = HttpClient(
                    base_url=spec_obj.source.base_url,
                    auth=spec_obj.source.auth,
                    retry=spec_obj.source.rate_limit.retry,
                    timeout_s=float(self.timeout_s),
                )
                # Enforce network allowlist on the base_url
                http_client_base = http_client.base_url
                # For LocalProcess we allow all, so no check needed
                _ = http_client_base
                from autobridge.runtime.interpreter import Interpreter

                interp = Interpreter(spec_obj, http_client=http_client)
                result = interp.execute_operation(operation, max_pages=100)
                return ExecutionResult(
                    status="ok",
                    records=result.records,
                    output={"pages_fetched": result.pages_fetched, "stop_reason": result.stop_reason},
                    elapsed_ms=0.0,
                )
            except Exception as exc:
                return ExecutionResult(status="error", error=str(exc))
        _ = (spec, operation, params)
        return ExecutionResult(status="ok", records=[], output={})

    def health_check(self) -> dict[str, str]:
        return {"status": "ok", "kind": self.kind}


class DockerSandboxTarget(BaseTarget):
    """Isolated container with allowlist, timeout and resource limits.

    In production this target spawns a container from ``image`` with
    ``memory_mb`` memory limit, ``timeout_s`` wall-clock, and egress
    restricted to ``network_allowlist`` (Docker network attachment to
    vendor-net only).  The in-process implementation enforces the same
    allowlist + admin-path guard so unit tests can assert the negative
    network property without Docker.
    """

    def __init__(
        self,
        *,
        network_allowlist: list[str] | None = None,
        timeout_s: int = 60,
        memory_mb: int = 512,
        image: str | None = None,
        workdir: str | None = None,
    ) -> None:
        super().__init__(
            kind="docker_sandbox",
            network_allowlist=list(network_allowlist or []),
            timeout_s=timeout_s,
            memory_mb=memory_mb,
            image=image,
            workdir=workdir,
        )

    def execute(
        self,
        spec: dict[str, Any] | None = None,
        operation: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Execute with network allowlist enforcement.

        When *spec* and *operation* are provided, validates that the spec's
        ``source.base_url`` is in the allowlist before delegating to the
        interpreter.  Otherwise returns stub ok (backward compat for W1 tests).
        """
        if (
            spec is not None
            and operation is not None
            and isinstance(spec, dict)
            and "source" in spec
            and "spec_version" in spec
        ):
            try:
                from autobridge.runtime.http import HttpClient
                from autobridge.spec.models import load_spec_from_dict

                spec_obj = load_spec_from_dict(spec)
                # Network check on base_url before any HTTP
                base_url = spec_obj.source.base_url
                # Resolve env placeholders if present (best-effort; missing env -> skip check)
                try:
                    from autobridge.runtime.http import resolve_base_url

                    resolved = resolve_base_url(base_url)
                    self.check_network(resolved)
                except (KeyError, ValueError):
                    # If env placeholder missing or URL invalid, let later code raise
                    pass

                http_client = HttpClient(
                    base_url=base_url,
                    auth=spec_obj.source.auth,
                    retry=spec_obj.source.rate_limit.retry,
                    timeout_s=float(self.timeout_s),
                )
                from autobridge.runtime.interpreter import Interpreter

                interp = Interpreter(spec_obj, http_client=http_client)
                result = interp.execute_operation(operation, max_pages=100)
                return ExecutionResult(
                    status="ok",
                    records=result.records,
                    output={"pages_fetched": result.pages_fetched, "stop_reason": result.stop_reason},
                    elapsed_ms=0.0,
                )
            except NetworkBlockedError:
                raise
            except Exception as exc:
                return ExecutionResult(status="error", error=str(exc))
        _ = (spec, operation, params)
        return ExecutionResult(status="ok", records=[], output={})

    def fetch(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Simulated fetch that enforces allowlist + admin path guard."""
        self.check_network(url)
        _ = params
        return {"url": url, "data": []}

    def health_check(self) -> dict[str, str]:
        return {"status": "ok", "kind": self.kind}

    def docker_run_args(self) -> dict[str, Any]:
        """Return Docker run arguments for container creation.

        Used by the worker when spawning the sandbox container.  Contains
        memory limit, timeout, image, and network (vendor-net only).
        """
        return {
            "image": self.image or "autobridge-runtime-sandbox",
            "mem_limit": f"{self.memory_mb}m",
            "network": "vendor-net",
            "timeout_s": self.timeout_s,
            "network_allowlist": list(self.network_allowlist),
        }


class EdgeTarget(BaseTarget):
    """On-premise agent target on an isolated network (REQ-022 B).

    Executes only signed releases: :meth:`execute_signed_release` verifies
    the release signature before running anything (HMAC when
    ``EDGE_RELEASE_SECRET`` is set — AR-2-4).  Egress
    is restricted to ``network_allowlist`` (default: deny — the agent
    speaks FIAS over TCP and polls the ``edge-relay`` queue instead of
    proxying vendor HTTP), enforced in-process exactly like
    :class:`DockerSandboxTarget` plus the Compose attachment
    (``edge-net`` only — no vendor-net / verifier-net / admin-net route).
    """

    endpoint: str | None

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        network_allowlist: list[str] | None = None,
        timeout_s: int = 60,
        memory_mb: int = 512,
        workdir: str | None = None,
    ) -> None:
        super().__init__(
            kind="edge",
            network_allowlist=list(network_allowlist if network_allowlist is not None else []),
            timeout_s=timeout_s,
            memory_mb=memory_mb,
            workdir=workdir,
        )
        # Keep endpoint accessible as an attribute (BaseTarget has no field).
        object.__setattr__(self, "endpoint", endpoint)

    def execute_signed_release(
        self,
        spec: dict[str, Any],
        signature: str | None,
        operation: str,
        params: dict[str, Any] | None = None,
        client_factory: Any | None = None,
    ) -> ExecutionResult:
        """Verify *signature*, enforce the allowlist, then interpret.

        Raises :class:`TargetConfigError` for unsigned/tampered releases and
        :class:`NetworkBlockedError` when the spec's ``source.base_url`` is
        outside the allowlist.  When ``EDGE_RELEASE_SECRET`` is set only
        HMAC signatures minted with the secret are accepted (AR-2-4).
        """
        _ = params  # reserved: the interpreter derives params from the spec operation
        verify_release_signature(spec, signature)
        try:
            from autobridge.runtime.http import HttpClient, resolve_base_url
            from autobridge.spec.models import load_spec_from_dict

            spec_obj = load_spec_from_dict(spec)
            try:
                resolved = resolve_base_url(spec_obj.source.base_url)
                self.check_network(resolved)
            except (KeyError, ValueError):
                pass
            http_client = HttpClient(
                base_url=spec_obj.source.base_url,
                auth=spec_obj.source.auth,
                retry=spec_obj.source.rate_limit.retry,
                timeout_s=float(self.timeout_s),
                client_factory=client_factory,
            )
            from autobridge.runtime.interpreter import Interpreter

            interp = Interpreter(spec_obj, http_client=http_client)
            result = interp.execute_operation(operation, max_pages=100)
            return ExecutionResult(
                status="ok",
                records=result.records,
                output={"pages_fetched": result.pages_fetched, "stop_reason": result.stop_reason},
                elapsed_ms=0.0,
            )
        except (NetworkBlockedError, TargetConfigError):
            raise
        except Exception as exc:
            return ExecutionResult(status="error", error=str(exc))

    def execute(
        self,
        spec: dict[str, Any] | None = None,
        operation: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Unsigned direct execution is refused: use signed releases.

        The edge agent never runs an unverified spec; callers must go
        through :meth:`execute_signed_release` (REQ-022 B removed the old
        inbound ``/execute`` HTTP port — the agent polls its queue outbound).
        """
        _ = (spec, operation, params)
        return ExecutionResult(
            status="blocked",
            error="edge target executes only signed releases (signature required)",
        )

    def fetch(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Simulated fetch that enforces allowlist + admin path guard."""
        self.check_network(url)
        _ = params
        return {"url": url, "data": []}

    def health_check(self) -> dict[str, str]:
        return {"status": "ok", "kind": self.kind}

    def docker_run_args(self) -> dict[str, Any]:
        """Return Docker run arguments (edge worker attaches to edge-net only)."""
        return {
            "image": self.image or "autobridge-edge-worker",
            "mem_limit": f"{self.memory_mb}m",
            "network": "edge-net",
            "timeout_s": self.timeout_s,
            "network_allowlist": list(self.network_allowlist),
            "endpoint": self.endpoint,
        }


# ---------------------------------------------------------------------------
# Release signatures — verify centrally, execute locally (P2.S2)
# ---------------------------------------------------------------------------

#: Env var carrying the HMAC key for release signatures (AR-2-4). Same
#: variable as ``autobridge.control.models.EDGE_RELEASE_SECRET_ENV``; kept
#: as a literal here so this module never imports control (the signer and
#: the verifier must read the same variable — covered by the agreement
#: tests in ``tests/unit/test_autobridge_edge_target.py``).
_EDGE_RELEASE_SECRET_ENV = "EDGE_RELEASE_SECRET"


def _signing_key(secret: str | None = None) -> bytes | None:
    """HMAC key bytes, or ``None`` when no key is configured (legacy mode)."""
    key = secret if secret is not None else os.environ.get(_EDGE_RELEASE_SECRET_ENV)
    if not key:
        return None
    return key.encode("utf-8")


def release_signature(spec: dict[str, Any], *, secret: str | None = None) -> str:
    """HMAC-SHA256 over the canonical JSON of a ConnectorSpec document.

    Identical canonicalization to
    :func:`autobridge.control.models.spec_signature` (kept local to avoid a
    control→runtime import); ``ConnectorRelease.signature`` stores this value
    and any spec drift changes it.  The edge worker refuses to execute when
    the presented signature differs.

    When ``EDGE_RELEASE_SECRET`` (or *secret*) is set the digest is keyed,
    so only the secret holder can mint an accepted signature (AR-2-4).
    Without a key this is the legacy unkeyed sha256: an integrity checksum
    only, not an authorization decision. V1 specs hash byte-identically to
    before spec v2 existed (v2-only fields at defaults are omitted).
    """
    from autobridge.spec.models import signature_canonical_data  # noqa: PLC0415 - keep runtime import-light

    payload = json.dumps(
        signature_canonical_data(spec), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    key = _signing_key(secret)
    if key is None:
        return hashlib.sha256(payload).hexdigest()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def verify_release_signature(
    spec: dict[str, Any], signature: str | None, *, secret: str | None = None
) -> None:
    """Raise :class:`TargetConfigError` unless *signature* matches *spec*.

    Missing, empty or mismatched signatures are all rejected: the edge
    worker executes only signed releases.  Comparison is constant-time;
    when a signing key is configured the expected value is the HMAC, so a
    self-computed unkeyed hash over a foreign spec is rejected (AR-2-4).
    """
    if not signature:
        raise TargetConfigError("edge release is unsigned: signature is required")
    expected = release_signature(spec, secret=secret)
    if not hmac.compare_digest(signature, expected):
        keyed = _signing_key(secret) is not None
        raise TargetConfigError(
            "edge release signature mismatch: refusing to execute (tampered or stale release)"
            if not keyed
            else "edge release HMAC signature mismatch: refusing to execute "
            "(forged, tampered or stale release)"
        )


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


class FakeEdgeTarget(FakeRuntimeTarget):
    """Fake edge worker: enforces allowlist + requires a valid signature.

    ``responses`` maps operation name → canned response, as in
    :class:`FakeRuntimeTarget`.  :meth:`execute_signed_release` verifies the
    signature first so unit tests can assert the signed-only property
    without Docker.
    """

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        network_allowlist: list[str] | None = None,
        timeout_s: int = 60,
        memory_mb: int = 512,
        responses: dict[str, Any] | None = None,
        fail_on: dict[str, Exception] | None = None,
        delay_s: float = 0.0,
    ) -> None:
        super().__init__(
            kind="edge",
            network_allowlist=network_allowlist if network_allowlist is not None else [],
            timeout_s=timeout_s,
            memory_mb=memory_mb,
            responses=responses,
            fail_on=fail_on,
            delay_s=delay_s,
        )
        object.__setattr__(self, "endpoint", endpoint)

    def execute_signed_release(
        self,
        spec: dict[str, Any],
        signature: str | None,
        operation: str,
        params: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Verify signature + allowlist, then return the canned response."""
        verify_release_signature(spec, signature)
        base_url = spec.get("source", {}).get("base_url", "") if isinstance(spec, dict) else ""
        if base_url:
            try:
                from autobridge.runtime.http import resolve_base_url

                self.check_network(resolve_base_url(base_url))
            except (KeyError, ValueError):
                pass
        self.calls.append(
            {"method": "execute_signed_release", "operation": operation, "params": params}
        )
        canned = self._lookup(operation, params)
        if isinstance(canned, ExecutionResult):
            return canned
        if isinstance(canned, dict):
            return ExecutionResult(status="ok", records=[], output=canned)
        if isinstance(canned, list):
            return ExecutionResult(status="ok", records=canned)
        return ExecutionResult(status="ok", records=[], output={})


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
        # Local process can reach anything with a valid host (admin path not blocked)
        return bool(host_from_url(url))

