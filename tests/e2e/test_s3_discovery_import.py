"""P2.S3.1 — DiscoveryResult static import (plan §5 S3).

Passing criteria (plan §5 S3):
  * Reserved schema + ``POST /discovery/import``: every record validates
    against ``schemas/discovery-result.v0.json``; unknown statuses are
    rejected, never guessed.
  * Connectors list shows ``discovered / needs_confirmation / authorized /
    connected / healing`` from a static fixture.
  * State transitions only via explicit endpoints.
  * Own e2e file: ``tests/e2e/test_s3_discovery_import.py``.

Fake transports (control TestClient) keep the suite green without Docker;
live tests use the ``e2e_stack`` fixture and skip when the stack is down.
Trust boundary: this file lives in tests/e2e and talks to the control plane
over HTTP live; the engine itself never imports verifier/simulators.
"""

from __future__ import annotations

import json
import pathlib
import uuid
from contextlib import contextmanager
from typing import Any

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "tests" / "e2e" / "fixtures" / "discovery_s3_static.json"

CONTROL_URL = "http://localhost:8101"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@contextmanager
def _fake_client(tmp_path: pathlib.Path) -> Any:  # type: ignore[no-untyped-def]
    from autobridge.control.app import app
    from autobridge.control.deps import get_db
    from autobridge.control.models import Base, session_factory

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
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Fake: reserved schema + static fixture import + lifecycle
# ---------------------------------------------------------------------------


def test_s3_fixture_validates_against_reserved_schema() -> None:
    """The static fixture validates record-by-record against the v0 schema."""
    from autobridge.spec.entities import (
        DISCOVERY_RESULT_SCHEMA_FILE,
        DiscoveryResult,
        validate_against_schema,
    )

    payload = _fixture()
    assert len(payload["records"]) == 5
    for raw in payload["records"]:
        record = DiscoveryResult.model_validate(raw)
        validate_against_schema(
            record.model_dump(mode="json", exclude_none=True),
            DISCOVERY_RESULT_SCHEMA_FILE,
        )
    assert {r["status"] for r in payload["records"]} == {
        "discovered",
        "needs_confirmation",
        "authorized",
        "connected",
        "healing",
    }


def test_s3_import_and_list_fake(tmp_path: pathlib.Path) -> None:
    with _fake_client(tmp_path) as client:
        r = client.post("/discovery/import", json=_fixture())
        assert r.status_code == 201, r.text
        assert r.json()["total"] == 5
        listed = client.get("/discovery")
        assert listed.status_code == 200, listed.text
        by_id = {row["discovery_id"]: row["status"] for row in listed.json()}
        assert by_id == {
            "disc-s3-atlas-001": "discovered",
            "disc-s3-harbor-001": "needs_confirmation",
            "disc-s3-legacy-001": "authorized",
            "disc-s3-edge-001": "connected",
            "disc-s3-heal-001": "healing",
        }



def test_s3_lifecycle_only_via_explicit_endpoints_fake(tmp_path: pathlib.Path) -> None:
    with _fake_client(tmp_path) as client:
        rec = {
            "discovery_id": "disc-s3-e2e-life-001",
            "status": "discovered",
            "vendor": "LifecyclePMS",
            "base_url": "https://lifecycle.example.com",
            "candidate_entities": ["Reservation"],
            "evidence_refs": [],
        }
        assert client.post("/discovery/import", json={"records": [rec]}).status_code == 201
        did = rec["discovery_id"]
        # Skipping a step is rejected: discovered cannot connect directly.
        assert client.post(f"/discovery/{did}/connect").status_code == 422
        for action, nxt in [
            ("request-confirmation", "needs_confirmation"),
            ("authorize", "authorized"),
            ("connect", "connected"),
            ("mark-healing", "healing"),
            ("resolve", "connected"),
        ]:
            r = client.post(f"/discovery/{did}/{action}")
            assert r.status_code == 200, (action, r.text)
            assert r.json()["status"] == nxt
        # Unknown status is rejected, never guessed.
        bad = dict(rec, discovery_id="disc-s3-e2e-bad-001", status="maybe")
        assert client.post("/discovery/import", json={"records": [bad]}).status_code == 422



# ---------------------------------------------------------------------------
# Live: static fixture import + lifecycle against the Compose stack
# ---------------------------------------------------------------------------


def _live_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def test_s3_import_and_list_live(e2e_stack: str) -> None:
    """Live control plane imports the static fixture and lists all 5 states."""
    assert e2e_stack
    payload = _fixture()
    # Unique ids per run: the live DB persists across tests in one session.
    suffix = uuid.uuid4().hex[:8]
    for rec in payload["records"]:
        rec["discovery_id"] = f"{rec['discovery_id']}-{suffix}"
    try:
        r = httpx.post(f"{CONTROL_URL}/discovery/import", json=payload, timeout=15.0)
    except httpx.ConnectError as exc:
        from tests.e2e.conftest import stack_unreachable  # noqa: PLC0415

        stack_unreachable(f"control plane unreachable live: {exc}")
    assert r.status_code == 201, r.text
    assert r.json()["total"] == 5
    listed = httpx.get(f"{CONTROL_URL}/discovery", timeout=15.0)
    assert listed.status_code == 200, listed.text
    by_id = {row["discovery_id"]: row["status"] for row in listed.json()}
    for rec in payload["records"]:
        assert by_id[rec["discovery_id"]] == rec["status"]


def test_s3_lifecycle_live(e2e_stack: str) -> None:
    """Live: full lifecycle walks only through explicit endpoints."""
    assert e2e_stack
    did = _live_id("disc-s3-live-life")
    rec = {
        "discovery_id": did,
        "status": "discovered",
        "vendor": "LivePMS",
        "base_url": "https://live.example.com",
        "candidate_entities": ["FolioCharge"],
        "evidence_refs": [],
    }
    try:
        created = httpx.post(
            f"{CONTROL_URL}/discovery/import", json={"records": [rec]}, timeout=15.0
        )
    except httpx.ConnectError as exc:
        from tests.e2e.conftest import stack_unreachable  # noqa: PLC0415

        stack_unreachable(f"control plane unreachable live: {exc}")
    assert created.status_code == 201, created.text
    # Illegal skip is rejected live too.
    bad = httpx.post(f"{CONTROL_URL}/discovery/{did}/connect", timeout=15.0)
    assert bad.status_code == 422, bad.text
    for action, nxt in [
        ("request-confirmation", "needs_confirmation"),
        ("authorize", "authorized"),
        ("connect", "connected"),
        ("mark-healing", "healing"),
        ("resolve", "connected"),
    ]:
        r = httpx.post(f"{CONTROL_URL}/discovery/{did}/{action}", timeout=15.0)
        assert r.status_code == 200, (action, r.text)
        assert r.json()["status"] == nxt
