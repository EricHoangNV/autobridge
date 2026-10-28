"""Runtime mode: Docker vs local processes (REQ-017, plan §2.1).

AutoBridge normally runs under Docker Compose, where network isolation is
enforced by container attachment (``vendor-net`` only for the sandbox, no
verifier/admin route for the worker, isolated ``edge-net``).  REQ-017 adds a
Docker-free mode (``make local-up``) where every service runs as a local
process on the same host ports.

In local mode the engine executes specs in-process — i.e. with
:class:`LocalProcessTarget` semantics — even when a spec declares
``docker_sandbox``: there is no sandbox container, so nothing may pretend
network isolation exists.  :func:`active_target_kind` reports the effective
kind; :func:`resolve_target` builds it.  Services expose the mode in
``/readyz`` (``runtime_mode`` + ``network_isolation`` fields) and the UI
header shows it, so an operator can always tell the modes apart.

Trust boundary: this module never imports ``verifier`` or ``simulators``;
detecting Docker via ``docker info`` is a host probe, not a package import.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

#: Env var selecting the runtime mode (config, never code — DIRECTIVES §3):
#: ``local`` forces local processes, ``docker`` forces Compose assumptions,
#: anything else (or unset) auto-detects from Docker availability.
RUNTIME_MODE_ENV = "AUTOBRIDGE_RUNTIME"

LOCAL = "local"
DOCKER = "docker"


def docker_available(timeout_s: float = 3.0) -> bool:
    """Return True when a Docker daemon answers.

    ``DOCKER_HOST`` is honored (so ``DOCKER_HOST=unix:///nonexistent.sock``
    correctly reports unavailable, per the REQ-017 acceptance check).  Any
    failure — missing binary, refused socket, timeout — means unavailable,
    never an exception.
    """
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=timeout_s,
        )
        return proc.returncode == 0
    except Exception:
        return False


def runtime_mode() -> str:
    """Return ``"local"`` or ``"docker"`` for this process.

    Explicit ``AUTOBRIDGE_RUNTIME=local|docker`` wins; otherwise local when
    Docker is unavailable (a plain ``uv`` + Node machine) and docker when a
    daemon answers.
    """
    raw = os.environ.get(RUNTIME_MODE_ENV, "").strip().lower()
    if raw == LOCAL:
        return LOCAL
    if raw == DOCKER:
        return DOCKER
    return DOCKER if docker_available() else LOCAL


def network_isolation_enforced() -> bool:
    """Return True only when Docker networks enforce isolation.

    Local processes share the host loopback, so Docker network attachment
    (vendor-net / verifier-net / admin-net / edge-net separation) does not
    exist there — in-process allowlist checks still apply.
    """
    return runtime_mode() == DOCKER


def active_target_kind(declared_kind: str) -> str:
    """Return the effective target kind for *declared_kind* in this mode.

    In local mode a declared ``docker_sandbox`` runs as ``local_process``:
    there is no container to spawn, and reporting ``docker_sandbox`` would
    pretend sandbox isolation exists.  Every other kind passes through.
    """
    if runtime_mode() == LOCAL and declared_kind == "docker_sandbox":
        return "local_process"
    return declared_kind


def resolve_target(config: dict[str, Any]) -> Any:
    """Build a runtime target for *config*, honoring the current mode.

    Same input shape as :func:`create_target` (``{"kind": ...}``); in local
    mode ``docker_sandbox`` configs resolve to :class:`LocalProcessTarget`
    (timeout/memory preserved).  The substitution is data, not code: the
    stored spec is untouched, only the executed target changes.
    """
    from autobridge.runtime.targets import create_target

    kind = config.get("kind") if isinstance(config, dict) else None
    if runtime_mode() == LOCAL and kind == "docker_sandbox":
        mapped = dict(config)
        mapped["kind"] = "local_process"
        # LocalProcessTarget takes timeout_s + workdir only.
        mapped.pop("network_allowlist", None)
        mapped.pop("memory_mb", None)
        mapped.pop("image", None)
        return create_target(mapped)
    return create_target(config)
