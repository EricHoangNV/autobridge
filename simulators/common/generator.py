"""Seeded generator stub (W1) — full implementation in W2."""

from __future__ import annotations

import hashlib
import random


def generate_batch(seed: int, count: int = 10) -> list[dict[str, object]]:
    """Deterministic fake record generator for testing."""
    rng = random.Random(seed)
    out: list[dict[str, object]] = []
    for i in range(count):
        # Use rng for determinism; hash ensures byte-identical with same seed
        rid = f"res_{hashlib.sha256(f'{seed}-{i}'.encode()).hexdigest()[:8]}"
        out.append(
            {
                "id": rid,
                "index": i,
                "value": rng.randint(1, 1000),
                "seed": seed,
            }
        )
    return out


def healthz() -> dict[str, str]:
    """Liveness."""
    return {"status": "ok"}


def readyz() -> dict[str, object]:
    """Readiness."""
    return {"status": "ok", "checks": {"default": "ok"}}
