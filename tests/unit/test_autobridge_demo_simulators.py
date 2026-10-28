"""Simulators index endpoint (REQ-022 E).

``GET /demo/simulators`` renders from the single registry
(``config/demo_targets.yaml``): display metadata, live up/down probed
server-side by the control plane, the active fault scenario if any, and
the connectors that use each simulator.
"""

from __future__ import annotations

import pathlib  # noqa: TC003
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from autobridge.control.app import app
from autobridge.control.demo_targets import get_demo_targets
from autobridge.control.deps import get_db
from autobridge.control.models import Base, Connector


@pytest.fixture()
def client(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Any:  # type: ignore[no-untyped-def]
    # Hermetic: admin probes go to refused loopback (literal IP, no DNS),
    # so every simulator reports down unless a test fakes httpx.
    monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://127.0.0.1:9")
    engine = create_engine(f"sqlite:///{tmp_path}/simulators.db")
    Base.metadata.create_all(engine)
    from autobridge.control.models import session_factory as _factory

    factory = _factory(engine)

    def override_db():  # type: ignore[no-untyped-def]
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_db
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


def test_simulators_come_from_registry(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/demo/simulators")
    assert r.status_code == 200
    rows = r.json()["simulators"]
    reg = get_demo_targets()
    assert [row["id"] for row in rows] == [t.id for t in reg.targets]
    for row, target in zip(rows, reg.targets, strict=True):
        assert row["display_name"] == (target.display_name or target.id)
        assert row["api_style"] == target.api_style
        assert row["public_port"] == target.public_port
        assert row["docs_source"] == target.docs_source
        assert row["developers_url"] == f"http://localhost:{target.public_port}/developers"
        # Refused loopback: down, no fault, no connectors on an empty DB.
        assert row["live"] is False
        assert row["active_scenario"] is None
        assert row["connectors"] == []


def test_simulators_match_connectors_by_docs_and_port(client: Any, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    from autobridge.control.models import session_factory  # noqa: PLC0415

    # Reach into the override factory via a fresh engine is overkill: the
    # TestClient app override is set, so write through a direct engine on the
    # same file the fixture created.
    engine = create_engine(f"sqlite:///{tmp_path}/simulators.db")
    factory = session_factory(engine)
    with factory() as s:
        s.add(
            Connector(
                name="Atlas ref",
                base_url="http://localhost:8105",
                credential_ref="ATLAS_API_KEY",
                vendor_docs_ref="simulators/docs/atlas-openapi.yaml",
                target_entities=["Reservation"],
                runtime_target={"kind": "local_process"},
            )
        )
        s.add(
            Connector(
                name="PABX edge",
                base_url="edge-relay://queue/fias-commands",
                credential_ref="EDGE_TOKEN",
                vendor_docs_ref="simulators/docs/fias-pabx.md",
                target_entities=["FolioCharge"],
                runtime_target={"kind": "edge"},
            )
        )
        s.commit()
    rows = {row["id"]: row for row in client.get("/demo/simulators").json()["simulators"]}
    assert [c["name"] for c in rows["sim-atlas"]["connectors"]] == ["Atlas ref"]
    assert [c["name"] for c in rows["sim-fias-pabx"]["connectors"]] == ["PABX edge"]
    assert rows["sim-harbor"]["connectors"] == []


class _FakeResp:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeClient:
    """Stand-in for httpx.Client: scenario per admin base URL."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        self.calls: list[str] = []

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *args: Any) -> bool:  # noqa: ARG002
        return False

    def get(self, url: str, headers: Any = None) -> _FakeResp:  # noqa: ARG002
        self.calls.append(url)
        if "8119" in url:
            return _FakeResp(200, {"scenario_id": "fias_record_layout_change", "seed": 42})
        if "8110" in url:
            return _FakeResp(200, {"scenario_id": "baseline", "seed": 0})
        return _FakeResp(403, {"detail": "admin token required"})


def test_simulators_live_fault_and_baseline(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    # Drop the refused-loopback override so per-target admin ports are used.
    monkeypatch.delenv("SIMULATOR_ADMIN_URL")
    with patch("autobridge.control.routes.demo.httpx.Client", _FakeClient):
        rows = {row["id"]: row for row in client.get("/demo/simulators").json()["simulators"]}
    fias = rows["sim-fias-pabx"]
    assert fias["live"] is True
    assert fias["active_scenario"] == "fias_record_layout_change"
    harbor = rows["sim-harbor"]
    assert harbor["live"] is True
    assert harbor["active_scenario"] is None
    assert rows["sim-atlas"]["live"] is False


def test_simulators_malformed_payload_counts_down(client: Any) -> None:  # type: ignore[no-untyped-def]
    class _BadClient(_FakeClient):
        def get(self, url: str, headers: Any = None) -> _FakeResp:  # noqa: ARG002
            return _FakeResp(200, ["not", "a", "mapping"])

    with patch("autobridge.control.routes.demo.httpx.Client", _BadClient):
        rows = client.get("/demo/simulators").json()["simulators"]
    assert all(row["live"] is False for row in rows)
