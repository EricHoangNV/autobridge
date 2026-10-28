"""Smoke tests for simulators package."""

from __future__ import annotations

import importlib


def test_simulators_version() -> None:
    import simulators

    assert simulators.__version__ == "0.1.0"


def test_generate_batch_count() -> None:
    from simulators.common.generator import generate_batch

    batch = generate_batch(seed=42, count=5)
    assert len(batch) == 5
    for i, rec in enumerate(batch):
        assert rec["index"] == i
        assert rec["seed"] == 42
        assert "id" in rec
        assert "value" in rec


def test_generate_batch_deterministic() -> None:
    from simulators.common.generator import generate_batch

    a = generate_batch(seed=123, count=10)
    b = generate_batch(seed=123, count=10)
    assert a == b


def test_generate_batch_different_seed() -> None:
    from simulators.common.generator import generate_batch

    a = generate_batch(seed=1, count=5)
    b = generate_batch(seed=2, count=5)
    assert a != b


def test_generate_batch_default_count() -> None:
    from simulators.common.generator import generate_batch

    batch = generate_batch(seed=99)
    assert len(batch) == 10


def test_common_healthz() -> None:
    from simulators.common.generator import healthz

    assert healthz()["status"] == "ok"


def test_common_readyz() -> None:
    from simulators.common.generator import readyz

    result = readyz()
    assert result["status"] == "ok"
    assert result["checks"]["default"] == "ok"  # type: ignore[index]


def test_common_init_exports() -> None:
    from simulators.common import generate_batch

    assert callable(generate_batch)


def test_simulator_imports() -> None:
    for mod in [
        "simulators",
        "simulators.common",
        "simulators.common.generator",
        "simulators.atlaspms",
        "simulators.harborpms",
        "simulators.legacystay",
        "simulators.meridianpms",
    ]:
        importlib.import_module(mod)


def test_simulator_sub_health() -> None:
    from simulators.atlaspms import healthz as atlas_h
    from simulators.atlaspms import readyz as atlas_r
    from simulators.harborpms import healthz as harbor_h
    from simulators.legacystay import healthz as legacy_h
    from simulators.meridianpms import healthz as meridian_h

    assert atlas_h()["status"] == "ok"
    assert atlas_r()["status"] == "ok"  # type: ignore[union-attr]
    assert harbor_h()["status"] == "ok"
    assert legacy_h()["status"] == "ok"
    assert meridian_h()["status"] == "ok"
