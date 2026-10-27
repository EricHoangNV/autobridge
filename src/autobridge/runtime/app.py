"""Runtime-sandbox service — health + interpreter wiring (W1, W3.6).

The sandbox interprets ConnectorSpecs (W3 interpreter + targets). It is the
only service allowed to reach the simulators' public API (vendor-net); it has
no route to the verifier or the simulator admin API (admin-net).  Network
isolation is enforced at two layers: Docker Compose network attachment
(``vendor-net`` only) and in-process allowlist checks in
:mod:`autobridge.runtime.targets` (W3.6).
"""

from __future__ import annotations

from fastapi import FastAPI

from autobridge.health import healthz, readyz

app = FastAPI(title="AutoBridge Runtime Sandbox")


@app.get("/healthz")
def get_healthz() -> dict[str, str]:
    return healthz()  # type: ignore[return-value]


def _check_runtime_target() -> bool:
    """Verify RuntimeTarget factory and network isolation are intact.

    Returns True when:
    - target factory can build LocalProcess and DockerSandbox,
    - DockerSandbox allowlist blocks verifier and admin paths,
    - Edge kind is correctly rejected.
    """
    try:
        from autobridge.runtime.targets import (
            DockerSandboxTarget,
            LocalProcessTarget,
            TargetConfigError,
        )

        # LocalProcess builds
        lp = LocalProcessTarget(timeout_s=10)
        assert lp.kind == "local_process"

        # DockerSandbox with vendor allowlist builds and blocks verifier
        ds = DockerSandboxTarget(
            network_allowlist=["sim-atlas", "sim-harbor"],
            timeout_s=60,
            memory_mb=512,
        )
        assert ds.kind == "docker_sandbox"
        # Negative network: must block verifier and admin
        if ds.can_reach("http://verifier:8000/verify"):
            return False
        if ds.can_reach("http://verifier.local:8000/verify"):
            return False
        if ds.can_reach("http://sim-atlas:8001/admin/scenario"):
            return False
        if ds.can_reach("http://sim-harbor:8001/admin/reset"):
            return False
        # Edge must be rejected
        try:
            DockerSandboxTarget(network_allowlist=["verifier"])  # blocked host
            return False
        except TargetConfigError:
            pass

        # Interpreter import check
        from autobridge.runtime.interpreter import Interpreter  # noqa: F401

        return True
    except Exception:
        return False


def _check_interpreter() -> bool:
    """Verify interpreter and transforms are importable."""
    try:
        from autobridge.runtime.checks import evaluate_entity_checks  # noqa: F401
        from autobridge.runtime.interpreter import Interpreter  # noqa: F401
        from autobridge.runtime.transforms import REGISTRY  # noqa: F401

        return True
    except Exception:
        return False


@app.get("/readyz")
def get_readyz() -> dict[str, object]:
    checks = {
        "runtime_target": _check_runtime_target(),
        "interpreter": _check_interpreter(),
    }
    return readyz(checks)  # type: ignore[return-value]
