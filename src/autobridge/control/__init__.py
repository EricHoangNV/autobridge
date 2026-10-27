"""Control plane package."""

from autobridge.health import healthz, readyz

__all__ = ["healthz", "readyz"]
