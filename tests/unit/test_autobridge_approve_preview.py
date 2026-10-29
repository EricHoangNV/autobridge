"""Approve preview endpoint + approval on RepairRunOut (REQ-032 §4).

The repair page's "what happens when you click Approve" section is
generated from ``GET /repairs/{id}/approve-preview``, which must follow
the real approve path: the preview names the same candidate release,
from-release, dependents, scheduler interval and learn-on-approve outcome
as ``POST .../approve`` acts on. These tests pin that contract, plus the
``approval`` summary on the repairs list/detail used for the
fix-ready/approved/applied/rejected status.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

if TYPE_CHECKING:
    import pathlib

from autobridge.control.app import app
from autobridge.control.deps import get_db
from autobridge.control.models import (
    Base,
    Connector,
    DependencyRegistration,
    RepairRun,
    session_factory,
)
from autobridge.engine.releases import create_release


@pytest.fixture()
def setup(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("AUTOBRIDGE_SYNC_INTERVAL_S", "10")
    engine = create_engine(f"sqlite:///{tmp_path}/control.db")
    Base.metadata.create_all(engine)
    factory = session_factory(engine)

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
    yield c, factory
    app.dependency_overrides.clear()


def _spec() -> dict[str, Any]:
    return {
        "spec_version": 1,
        "connector": {"id": "c", "vendor": "Test", "runtime_target": "cloud"},
        "source": {
            "base_url": "https://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "rate_limit": {"max_rps": 5, "retry": {"statuses": [429, 503], "backoff": "exponential", "max_attempts": 3}},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "params": {},
                "pagination": {
                    "strategy": "cursor",
                    "cursor_param": "cursor",
                    "next_path": "$.meta.next_cursor",
                    "items_path": "$.data",
                    "stop_when": ["null_cursor"],
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
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {}},
    }


def _seed(setup: Any, **over: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Connector + active r1 + propose repair (+ candidate r2, + dependent)."""
    _, factory = setup
    decision = over.get("decision", "propose")
    with_candidate = over.get("with_candidate", True)
    with_dependent = over.get("with_dependent", True)
    with_active = over.get("with_active", True)
    vendor = over.get("vendor", "atlaspms")
    drift_class = over.get("drift_class", "PAGINATION_CHANGED")
    with factory() as s:
        conn = Connector(
            name="PreviewConn",
            base_url="https://example.com",
            credential_ref="TEST_TOKEN",
            target_entities=["Reservation"],
            runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.commit()
        r1 = create_release(s, connector_id=conn.id, spec=_spec())
        s.commit()
        if with_active:
            conn.active_release_id = r1.id
            s.commit()
        run = RepairRun(
            drift_event_ids=[],
            decision=decision,
            model_usage={"frontier_tokens": 0, "small_tokens": 0},
            vendor=vendor,
            drift_class=drift_class,
            fingerprint_delta={"pagination": {"strategy": {"old": "page", "new": "cursor"}}},
            candidate_patch=[{"op": "replace", "path": "/a", "value": "b"}],
            rule_id="page_to_cursor",
        )
        s.add(run)
        s.commit()
        candidate_id = None
        if with_candidate:
            r2 = create_release(
                s,
                connector_id=conn.id,
                spec=_spec(),
                parent_release_id=r1.id,
                provenance_repair_run_id=run.id,
            )
            s.commit()
            candidate_id = r2.id
        if with_dependent:
            s.add(
                DependencyRegistration(
                    consumer_id="downstream-dash",
                    connector_id=conn.id,
                    entity="Reservation",
                    policy="pause_on_release_change",
                )
            )
            s.commit()
        return {
            "connector_id": conn.id,
            "active_release_id": r1.id if with_active else None,
            "repair_id": run.id,
            "candidate_id": candidate_id,
        }


def test_preview_names_real_releases_and_dependents(setup: Any) -> None:  # type: ignore[no-untyped-def]
    client, _ = setup
    ids = _seed(setup)
    r = client.get(f"/repairs/{ids['repair_id']}/approve-preview")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approvable"] is True
    lines = body["approve_lines"]
    joined = "\n".join(lines)
    assert "Creates release r2 from r1" in joined
    assert "Activates r2." in joined
    assert "Pauses 1 dependent consumer until the next sync." in joined
    assert "in ~10 s" in joined and "runs on r2" in joined
    assert "kept as known for atlaspms + PAGINATION_CHANGED" in joined
    assert "Rollback to r1 stays available." in joined
    assert body["reject_lines"] == [
        "Discards the candidate release: no release is created or activated.",
        "The fix is not kept as learned knowledge.",
        "The drift stays open.",
    ]
    facts = body["facts"]
    assert facts["candidate_release_id"] == ids["candidate_id"]
    assert facts["candidate_version"] == 2
    assert facts["from_version"] == 1
    assert facts["dependent_pause_count"] == 1
    assert facts["sync_interval_s"] == 10
    assert facts["learnable"] is True


def test_preview_matches_real_approve(setup: Any) -> None:  # type: ignore[no-untyped-def]
    """Preview facts agree with what POST /approve then records."""
    client, _ = setup
    ids = _seed(setup)
    preview = client.get(f"/repairs/{ids['repair_id']}/approve-preview").json()
    # The UI sends connector_id (RepairActions), so from_release is recorded.
    r = client.post(f"/repairs/{ids['repair_id']}/approve", json={"connector_id": ids["connector_id"]})
    assert r.status_code == 200, r.text
    approval = r.json()
    assert approval["to_release_id"] == preview["facts"]["candidate_release_id"]
    assert approval["from_release_id"] == preview["facts"]["from_release_id"]
    # The repair now carries the approval summary for the list status.
    detail = client.get(f"/repairs/{ids['repair_id']}").json()
    assert detail["approval"] is not None
    assert detail["approval"]["id"] == approval["id"]
    assert detail["approval"]["actor"] == approval["actor"]
    listed = client.get("/repairs").json()
    row = next(x for x in listed if x["id"] == ids["repair_id"])
    assert row["approval"] is not None and row["approval"]["decision"] == "approve"


def test_preview_no_candidate_is_not_approvable(setup: Any) -> None:  # type: ignore[no-untyped-def]
    client, _ = setup
    ids = _seed(setup, with_candidate=False)
    body = client.get(f"/repairs/{ids['repair_id']}/approve-preview").json()
    assert body["approvable"] is False
    assert "No candidate release" in body["reason"]


def test_preview_non_propose_is_not_approvable(setup: Any) -> None:  # type: ignore[no-untyped-def]
    client, _ = setup
    ids = _seed(setup, decision="reject")
    body = client.get(f"/repairs/{ids['repair_id']}/approve-preview").json()
    assert body["approvable"] is False
    assert "reject" in body["reason"]


def test_preview_unknown_repair_404(setup: Any) -> None:  # type: ignore[no-untyped-def]
    client, _ = setup
    r = client.get("/repairs/does-not-exist/approve-preview")
    assert r.status_code == 404


def test_preview_first_release_and_no_dependents(setup: Any) -> None:  # type: ignore[no-untyped-def]
    client, _ = setup
    ids = _seed(setup, with_active=False, with_dependent=False)
    body = client.get(f"/repairs/{ids['repair_id']}/approve-preview").json()
    assert body["approvable"] is True
    joined = "\n".join(body["approve_lines"])
    assert "first release" in joined
    assert "No dependent consumers to pause." in joined
    assert "No earlier release to roll back to." in joined
