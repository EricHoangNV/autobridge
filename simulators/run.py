"""Dual-listener launcher for simulators (W2.2).

Runs the public API on 8000 (vendor-net) and the admin API on 8001
(admin-net) in the same container. The two APIs share state via
``simulators.common.state`` (file-backed JSON for cross-thread sync).

Usage (Docker CMD):
  APP_MODULE=simulators.atlaspms.app:app python -m simulators.run

REQ-017 (Docker-free local mode): ``SIM_PUBLIC_PORT`` / ``SIM_ADMIN_PORT``
override the ports (``make local-up`` maps every simulator onto the same
host ports compose publishes); defaults keep container behavior unchanged.
"""

from __future__ import annotations

import importlib
import os
import threading

import uvicorn


def _load_app(module_path: str):  # type: ignore[no-untyped-def]
    mod_name, attr = module_path.split(":")
    mod = importlib.import_module(mod_name)
    return getattr(mod, attr)


def _listen_ports() -> tuple[int, int]:
    """Return ``(public_port, admin_port)`` from the environment.

    Falls back to the container defaults (8000/8001) on missing or invalid
    values — an empty ``SIM_PUBLIC_PORT=`` (REQ-014 style) never crashes
    the launcher.
    """
    return (_port_from_env("SIM_PUBLIC_PORT", 8000), _port_from_env("SIM_ADMIN_PORT", 8001))


def _port_from_env(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        port = int(raw)
    except ValueError:
        return default
    if 1 <= port <= 65535:
        return port
    return default


def main() -> None:
    public_module = os.getenv("APP_MODULE", "simulators.atlaspms.app:app")
    public_app = _load_app(public_module)

    # Admin app is the same, plus an optional simulator-specific router
    # (e.g. REQ-022 FIAS link knobs: ADMIN_EXTRA_ROUTER set to
    # "simulators.fias_pabx.admin:router"). Unset by default: behaviour of
    # every other simulator is unchanged.
    extra_router = None
    extra_ref = os.getenv("ADMIN_EXTRA_ROUTER", "").strip()
    if extra_ref:
        try:
            extra_router = _load_app(extra_ref)
        except (ImportError, AttributeError) as exc:
            raise SystemExit(f"ADMIN_EXTRA_ROUTER {extra_ref!r} failed to load: {exc}") from exc
    from simulators.common.admin import create_admin_app

    admin_app = create_admin_app(extra_router)

    public_port, admin_port = _listen_ports()

    # Start admin in background thread
    def run_admin() -> None:
        uvicorn.run(admin_app, host="0.0.0.0", port=admin_port, log_level="info")

    t = threading.Thread(target=run_admin, daemon=True)
    t.start()

    # Run public in foreground (handles signals)
    uvicorn.run(public_app, host="0.0.0.0", port=public_port, log_level="info")


if __name__ == "__main__":
    main()
