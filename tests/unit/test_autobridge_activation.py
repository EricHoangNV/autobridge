# ruff: noqa: TC003, ARG001
"""REQ-013: build → activate → sync; sync never runs unapproved releases.

First build auto-activates; later build candidates need explicit operator
activation; repair candidates go through repair approval; gate-blocked and
rejected releases stay unrunnable (422).
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from autobridge.control.app import app
from autobridge.control.deps import get_db
from autobridge.control.models import Base, session_factory

DOCS = "# HarborPMS API\n\nBase URL: http://sim-harbor:8000\n\nGET /reservations\n\nGET /folios\n"
DOCS_V2 = "# HarborPMS API\n\nBase URL: http://sim-harbor:8000\n\nGET /reservations\n\nGET /folios\n\nGET /guests\n"


@pytest.fixture()
def store(tmp_path: pathlib.Path) -> Any:  # type: ignore[no-untyped-def]
    engine = create_engine(f"sqlite:///{tmp_path}/control.db")
    Base.metadata.create_all(engine)
    return {"engine": engine, "factory": session_factory(engine)}


@pytest.fixture()
def client(store: Any) -> Any:  # type: ignore[no-untyped-def]
    factory = store["factory"]

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
def session(store: Any) -> Any:  # type: ignore[no-untyped-def]
    s = store["factory"]()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def _create_connector(client: Any, **over: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    payload = {
        "name": over.get("name", "Harbor REQ013"),
        "base_url": over.get("base_url", "http://sim-harbor:8000"),
        "credential_ref": over.get("credential_ref", "HARBOR_API_KEY"),
        "target_entities": over.get("target_entities", ["Reservation"]),
        "runtime_target": {"kind": "local_process"},
    }
    r = client.post("/connectors", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _build(client: Any, cid: str, docs: str = DOCS) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    r = client.post(f"/connectors/{cid}/build", json={"docs_text": docs})
    assert r.status_code == 200, r.text
    return r.json()


def _releases(client: Any, cid: str) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    return client.get(f"/releases?connector_id={cid}").json()


def _seed_repair_candidate(session: Any, connector_id: str, base_release_id: str) -> tuple[str, str, str]:  # type: ignore[no-untyped-def]
    """Seed drift + propose repair + candidate release; return (drift, repair, candidate)."""
    from autobridge.control.models import ConnectorRelease, DriftEvent, RepairRun

    base = session.get(ConnectorRelease, base_release_id)
    assert base is not None
    drift = DriftEvent(connector_id=connector_id, drift_class="offset_to_cursor", severity="medium", evidence_ids=[], status="open")
    session.add(drift)
    session.flush()
    rep = RepairRun(drift_event_ids=[drift.id], ladder_level_reached=1, rule_id="offset_to_cursor", decision="propose", model_usage={"frontier_tokens": 0})
    session.add(rep)
    session.flush()
    cand = ConnectorRelease(
        connector_id=connector_id,
        version=99,
        spec=dict(base.spec),
        parent_release_id=base.id,
        signature="1" * 64,
        provenance_repair_run_id=rep.id,
    )
    session.add(cand)
    session.flush()
    session.commit()
    return drift.id, rep.id, cand.id


# ---------------------------------------------------------------------------
# First-build auto-activation
# ---------------------------------------------------------------------------


def test_first_build_auto_activates_and_sync_runs(client: Any) -> None:  # type: ignore[no-untyped-def]
    c = _create_connector(client)
    _build(client, c["id"])
    detail = client.get(f"/connectors/{c['id']}").json()
    rels = detail["releases"]
    assert len(rels) == 1
    assert detail["active_release_id"] == rels[0]["id"]
    r = client.post(f"/connectors/{c['id']}/sync", json={})
    assert r.status_code == 200, r.text
    assert r.json()["release_id"] == rels[0]["id"]


def test_second_build_stays_candidate_until_activated(client: Any) -> None:  # type: ignore[no-untyped-def]
    c = _create_connector(client)
    _build(client, c["id"], DOCS)
    v1 = _releases(client, c["id"])[-1]["id"]
    _build(client, c["id"], DOCS_V2)
    rels = _releases(client, c["id"])
    assert len(rels) == 2
    v2 = [r for r in rels if r["id"] != v1][0]["id"]
    detail = client.get(f"/connectors/{c['id']}").json()
    assert detail["active_release_id"] == v1
    # Unapproved candidate cannot sync, even when named explicitly.
    r = client.post(f"/connectors/{c['id']}/sync", json={"release_id": v2})
    assert r.status_code == 422, r.text
    assert "not active or approved" in r.text
    # Explicit activation makes it runnable.
    a = client.post(f"/connectors/{c['id']}/releases/{v2}/activate")
    assert a.status_code == 200, a.text
    assert a.json()["active_release_id"] == v2
    r2 = client.post(f"/connectors/{c['id']}/sync", json={"release_id": v2})
    assert r2.status_code == 200, r2.text
    # Activating the already-active release is idempotent.
    a2 = client.post(f"/connectors/{c['id']}/releases/{v2}/activate")
    assert a2.status_code == 200, a2.text


# ---------------------------------------------------------------------------
# Activation refusals
# ---------------------------------------------------------------------------


def test_activate_unknown_release_is_404(client: Any) -> None:  # type: ignore[no-untyped-def]
    c = _create_connector(client)
    r = client.post(f"/connectors/{c['id']}/releases/does-not-exist/activate")
    assert r.status_code == 404


def test_activate_foreign_release_is_422(client: Any) -> None:  # type: ignore[no-untyped-def]
    c1 = _create_connector(client, name="C1")
    c2 = _create_connector(client, name="C2")
    _build(client, c1["id"])
    rel = _releases(client, c1["id"])[0]["id"]
    r = client.post(f"/connectors/{c2['id']}/releases/{rel}/activate")
    assert r.status_code == 422, r.text
    assert "different connector" in r.text


def test_activate_repair_candidate_is_422(client: Any, session: Any) -> None:  # type: ignore[no-untyped-def]
    c = _create_connector(client)
    _build(client, c["id"])
    v1 = _releases(client, c["id"])[0]["id"]
    _, _, cand = _seed_repair_candidate(session, c["id"], v1)
    r = client.post(f"/connectors/{c['id']}/releases/{cand}/activate")
    assert r.status_code == 422, r.text
    assert "repair approval" in r.text


def test_activate_rejected_candidate_is_422(client: Any, session: Any) -> None:  # type: ignore[no-untyped-def]
    c = _create_connector(client)
    _build(client, c["id"])
    v1 = _releases(client, c["id"])[0]["id"]
    _, rep, cand = _seed_repair_candidate(session, c["id"], v1)
    rej = client.post(f"/repairs/{rep}/reject", json={"to_release_id": cand})
    assert rej.status_code == 200, rej.text
    r = client.post(f"/connectors/{c['id']}/releases/{cand}/activate")
    assert r.status_code == 422, r.text
    assert "rejected" in r.text


def test_activate_gate_blocked_provenance_is_422(client: Any, session: Any) -> None:  # type: ignore[no-untyped-def]
    from autobridge.control.models import BuildRun, ConnectorRelease

    c = _create_connector(client)
    _build(client, c["id"])
    v1 = _releases(client, c["id"])[0]["id"]
    base = session.get(ConnectorRelease, v1)
    assert base is not None
    brun = BuildRun(
        connector_id=c["id"],
        state="succeeded",
        inputs={},
        result={"success": False, "verified": False, "gate_blocked": "verdict fail: field_missing"},
    )
    session.add(brun)
    session.flush()
    blocked = ConnectorRelease(
        connector_id=c["id"],
        version=42,
        spec=dict(base.spec),
        parent_release_id=v1,
        signature="2" * 64,
        provenance_build_run_id=brun.id,
    )
    session.add(blocked)
    session.flush()
    session.commit()
    a = client.post(f"/connectors/{c['id']}/releases/{blocked.id}/activate")
    assert a.status_code == 422, a.text
    assert "verifier gate" in a.text
    s = client.post(f"/connectors/{c['id']}/sync", json={"release_id": blocked.id})
    assert s.status_code == 422, s.text
    assert "verifier gate" in s.text


# ---------------------------------------------------------------------------
# Sync gate: active or approved only
# ---------------------------------------------------------------------------


def test_sync_approved_but_inactive_release_runs(client: Any, session: Any) -> None:  # type: ignore[no-untyped-def]
    c = _create_connector(client)
    _build(client, c["id"])
    v1 = _releases(client, c["id"])[0]["id"]
    _, rep, cand = _seed_repair_candidate(session, c["id"], v1)
    ap = client.post(f"/repairs/{rep}/approve", json={"connector_id": c["id"], "from_release_id": v1})
    assert ap.status_code == 200, ap.text
    # Repair approval activated the candidate; flip back to v1 explicitly.
    back = client.post(f"/connectors/{c['id']}/releases/{v1}/activate")
    assert back.status_code == 200, back.text
    assert back.json()["active_release_id"] == v1
    # The approved-but-inactive repair release still syncs.
    r = client.post(f"/connectors/{c['id']}/sync", json={"release_id": cand})
    assert r.status_code == 200, r.text
    assert r.json()["release_id"] == cand


def test_sync_rejected_release_is_422(client: Any, session: Any) -> None:  # type: ignore[no-untyped-def]
    c = _create_connector(client)
    _build(client, c["id"])
    v1 = _releases(client, c["id"])[0]["id"]
    _, rep, cand = _seed_repair_candidate(session, c["id"], v1)
    rej = client.post(f"/repairs/{rep}/reject", json={"to_release_id": cand})
    assert rej.status_code == 200, rej.text
    r = client.post(f"/connectors/{c['id']}/sync", json={"release_id": cand})
    assert r.status_code == 422, r.text
    assert "not active or approved" in r.text
