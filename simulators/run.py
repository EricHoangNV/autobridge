"""Dual-listener launcher for simulators (W2.2).

Runs the public API on 8000 (vendor-net) and the admin API on 8001
(admin-net) in the same container. The two APIs share state via
``simulators.common.state`` (file-backed JSON for cross-thread sync).

Usage (Docker CMD):
  APP_MODULE=simulators.atlaspms.app:app python -m simulators.run
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


def main() -> None:
    public_module = os.getenv("APP_MODULE", "simulators.atlaspms.app:app")
    public_app = _load_app(public_module)

    # Admin app is always the same
    admin_app = _load_app("simulators.common.admin:admin_app")

    # Start admin in background thread
    def run_admin() -> None:
        uvicorn.run(admin_app, host="0.0.0.0", port=8001, log_level="info")

    t = threading.Thread(target=run_admin, daemon=True)
    t.start()

    # Run public in foreground (handles signals)
    uvicorn.run(public_app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
