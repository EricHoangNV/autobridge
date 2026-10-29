"""REQ-031 owner checks: scheduler pause + reference-release registration.

* The sync scheduler skips whole passes while a fresh pause is held
  (reset/seed/heal), with owner scoping so a nested reset cannot resume a
  seed's pause; stale pauses expire instead of wedging the scheduler.
* ``POST /demo/reset`` waits for in-flight sync/build/repair jobs (409
  naming the stuck jobs on timeout) and resumes its own pause at both
  exits.
* ``POST /connectors/{id}/reference-release`` registers a pre-built spec
  (verifier-gated, secret values rejected, build row + first-build
  activation recorded) so Harbor seeds from its reference release.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from autobridge.control.app import app
from autobridge.control.deps import get_db
from autobridge.control.models import (
    Base,
    BuildRun,
    ConnectorRelease,
    Job,
    SchedulerState,
    SyncRun,
    spec_signature,
)

if TYPE_CHECKING:
    import pathlib


@pytest.fixture()
def client(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Any:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("EDGE_RELAY_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("VERIFIER_URL", raising=False)
    monkeypatch.delenv("CONTROL_URL", raising=False)
    monkeypatch.delenv("AUTOBRIDGE_REQUIRE_VERIFIER_GATE", raising=False)
    engine = create_engine(f"sqlite:///{tmp_path}/req031.db")
    Base.metadata.create_all(engine)
    from autobridge.control.models import session_factory as _session_factory

    factory = _session_factory(engine)

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


@pytest.fixture()
def factory_for(tmp_path: pathlib.Path) -> Any:  # type: ignore[no-untyped-def]
    from autobridge.control.models import session_factory as _session_factory

    engine = create_engine(f"sqlite:///{tmp_path}/req031.db")
    Base.metadata.create_all(engine)
    return _session_factory(engine)


def _seed_connector(c: TestClient, name: str = "Harbor") -> str:
    r = c.post(
        "/connectors",
        json={
            "name": name,
            "vendor_docs_ref": "simulators/docs/harborpms.md",
            "base_url": "http://127.0.0.1:9",
            "credential_ref": "HARBOR_API_KEY",
            "target_entities": ["Reservation"],
        },
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def _ref_spec() -> dict[str, Any]:
    return {
        "spec_version": 1,
        "connector": {"id": "harborpms", "vendor": "harborpms"},
        "source": {
            "base_url": "${HARBOR_BASE_URL}",
            "auth": {"type": "bearer", "credential_ref": "HARBOR_API_KEY"},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "params": {},
                "pagination": {
                    "strategy": "page",
                    "page_param": "pageNumber",
                    "limit_param": "pageSize",
                    "items_path": "$.data",
                    "stop_when": ["empty_page"],
                },
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {"reservation_id": {"path": "$.reservation_id"}},
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}},
    }


# ---------------------------------------------------------------------------
# Scheduler pause primitives
# ---------------------------------------------------------------------------


def test_pause_resume_owner_scoped(client: TestClient, factory_for: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/demo/scheduler/pause", json={"owner": "demo-seed", "reason": "seeding"})
    assert r.status_code == 200, r.text
    assert r.json()["paused"] is True
    # A nested reset (owner demo-reset) must not steal or clear the seed's pause.
    r = client.post("/demo/scheduler/resume", json={"owner": "demo-reset"})
    assert r.status_code == 200, r.text
    assert r.json()["resumed"] is False
    assert r.json()["owner"] == "demo-seed"
    with factory_for() as s:
        row = s.get(SchedulerState, "scheduler")
        assert row is not None and row.paused is True
    r = client.post("/demo/scheduler/resume", json={"owner": "demo-seed"})
    assert r.json()["resumed"] is True
    with factory_for() as s:
        row = s.get(SchedulerState, "scheduler")
        assert row is not None and row.paused is False


def test_stale_pause_expires(client: TestClient, factory_for: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    import autobridge.control.models as _models
    from autobridge.control import scheduler as _sched

    monkeypatch.setattr(_models, "SCHEDULER_PAUSE_STALE_AFTER_S", -1.0)
    client.post("/demo/scheduler/pause", json={"owner": "demo-seed"})
    with factory_for() as s:
        status = _sched.scheduler_pause_status(s)
        assert status["paused"] is False, "an expired pause must read as cleared"
        assert status.get("stale") is True
    monkeypatch.undo()
    with factory_for() as s:
        status = _sched.scheduler_pause_status(s)
        assert status["paused"] is True, "the pause itself is untouched by the read"


def test_scheduler_pass_skips_while_paused(client: TestClient, factory_for: Any) -> None:  # type: ignore[no-untyped-def]
    from autobridge.control.scheduler import run_scheduler_pass

    cid = _seed_connector(client)
    with factory_for() as s:
        s.add(
            ConnectorRelease(
                connector_id=cid, version=1, spec=_ref_spec(),
                signature=spec_signature(_ref_spec()),
            )
        )
        s.commit()
    client.post("/demo/scheduler/pause", json={"owner": "demo-seed"})
    with factory_for() as s:
        out = run_scheduler_pass(s)
    assert out.get("paused") is True
    assert out["synced"] == 0
    with factory_for() as s:
        assert s.query(SyncRun).count() == 0, "paused pass syncs nothing"
    client.post("/demo/scheduler/resume", json={"owner": "demo-seed"})


def test_wait_for_quiescent_timeout_names_jobs(factory_for: Any) -> None:  # type: ignore[no-untyped-def]
    import pytest as _pytest

    from autobridge.control.scheduler import wait_for_quiescent

    with factory_for() as s:
        s.add(Job(kind="sync", state="running", payload={"release_id": "r1"}, worker_id="w1"))
        s.commit()
    with factory_for() as s, _pytest.raises(TimeoutError, match="r1"):
        wait_for_quiescent(s, timeout_s=0.1, poll_s=0.05)
    with factory_for() as s:
        s.query(Job).delete()
        s.commit()
    with factory_for() as s:
        assert wait_for_quiescent(s, timeout_s=1)["quiescent"] is True


def test_wait_ignores_queued_only_jobs(factory_for: Any) -> None:  # type: ignore[no-untyped-def]
    """Queued rows are demo state the reset wipes; only running blocks."""
    from autobridge.control.scheduler import wait_for_quiescent

    with factory_for() as s:
        s.add(Job(kind="sync", state="queued", payload={"release_id": "r1"}))
        s.commit()
    with factory_for() as s:
        assert wait_for_quiescent(s, timeout_s=1)["quiescent"] is True


def test_reset_waits_then_409_on_stuck_job(
    client: TestClient, factory_for: Any, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("AUTOBRIDGE_RESET_WAIT_S", "0.2")
    with factory_for() as s:
        s.add(Job(kind="build", state="running", payload={"connector_id": "c1"}, worker_id="w9"))
        s.commit()
    r = client.post("/demo/reset", json={})
    assert r.status_code == 409, r.text
    assert "build" in r.json()["detail"]
    # The reset's own pause is resumed even on the refusal path.
    with factory_for() as s:
        row = s.get(SchedulerState, "scheduler")
        assert row is None or row.paused is False


def test_reset_resumes_own_pause(client: TestClient, factory_for: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/demo/reset", json={})
    assert r.status_code == 200, r.text
    with factory_for() as s:
        row = s.get(SchedulerState, "scheduler")
        assert row is None or row.paused is False


# ---------------------------------------------------------------------------
# Reference-release registration
# ---------------------------------------------------------------------------


def test_reference_release_registers_and_activates(client: TestClient, factory_for: Any) -> None:  # type: ignore[no-untyped-def]
    cid = _seed_connector(client)
    r = client.post(
        f"/connectors/{cid}/reference-release",
        json={"spec": _ref_spec(), "docs_source": "reference:harborpms"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connector_id"] == cid
    assert body["version"] == 1
    assert body["activated"] is True
    assert body["active_release_id"] == body["release_id"]
    with factory_for() as s:
        builds = s.query(BuildRun).filter_by(connector_id=cid).all()
        assert len(builds) == 1
        assert builds[0].result["success"] is True
        assert builds[0].inputs["spec_vocabulary"] == "v1"
        assert builds[0].inputs["docs_source"] == "reference:harborpms"
        assert builds[0].draft_spec["connector"]["vendor"] == "harborpms"
    # Knowledge page lists harborpms under v1 after registration.
    k = client.get("/knowledge")
    assert k.status_code == 200, k.text
    conns = {c["name"]: c for c in k.json().get("connectors", [])}
    assert conns["Harbor"]["vocabulary"] == "v1"


def test_reference_release_unknown_connector_404(client: TestClient) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/connectors/missing/reference-release", json={"spec": _ref_spec()})
    assert r.status_code == 404


def test_reference_release_rejects_invalid_spec(client: TestClient, factory_for: Any) -> None:  # type: ignore[no-untyped-def]
    cid = _seed_connector(client)
    bad = _ref_spec()
    bad["operations"] = {"broken": {"method": "GET"}}  # no path: schema-invalid
    r = client.post(f"/connectors/{cid}/reference-release", json={"spec": bad})
    assert r.status_code == 422, r.text
    with factory_for() as s:
        assert s.query(ConnectorRelease).count() == 0, "invalid spec persists nothing"


def test_reference_release_rejects_secret_values(client: TestClient) -> None:  # type: ignore[no-untyped-def]
    cid = _seed_connector(client)
    evil = _ref_spec()
    evil["source"]["auth"] = {"type": "bearer", "credential_ref": "HARBOR_API_KEY", "api_key": "live-value"}
    r = client.post(f"/connectors/{cid}/reference-release", json={"spec": evil})
    assert r.status_code == 422, r.text
    assert "credential_ref" in r.json()["detail"]


def test_reference_release_gate_blocked_persists_nothing(
    client: TestClient, factory_for: Any, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("AUTOBRIDGE_REQUIRE_VERIFIER_GATE", "1")
    cid = _seed_connector(client)
    r = client.post(f"/connectors/{cid}/reference-release", json={"spec": _ref_spec()})
    assert r.status_code == 422, r.text
    assert "verifier gate" in r.json()["detail"]
    with factory_for() as s:
        assert s.query(ConnectorRelease).count() == 0
        assert s.query(BuildRun).count() == 0, "gate-blocked registration persists nothing"
