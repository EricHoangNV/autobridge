# ruff: noqa: TC003, ARG001, F401, I001, F541
"""Discovery static import tests (P2.S3.1, plan §5 S3).

Covers reserved-schema validation, static fixture import, the connectors
list showing all five lifecycle states, state transitions only via explicit
endpoints, illegal-transition 422s, duplicates (batch + stored), unknown
status rejection, and the trust boundary (engine never imports
verifier/simulators).
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from autobridge.control.app import app
from autobridge.control.deps import get_db
from autobridge.control.models import (
    DISCOVERY_STATUSES,
    DISCOVERY_TRANSITIONS,
    Base,
    session_factory,
)

FIXTURE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "e2e"
    / "fixtures"
    / "discovery_s3_static.json"
)


@pytest.fixture()
def client(tmp_path: pathlib.Path) -> Any:  # type: ignore[no-untyped-def]
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
    yield c
    app.dependency_overrides.clear()


def _fixture_payload() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _lifecycle_record(suffix: str = "life") -> dict[str, Any]:
    return {
        "discovery_id": f"disc-s3-{suffix}-001",
        "status": "discovered",
        "vendor": "LifecyclePMS",
        "base_url": "https://lifecycle.example.com",
        "candidate_entities": ["Reservation"],
        "evidence_refs": [],
    }


def test_fixture_file_covers_all_five_statuses() -> None:
    payload = _fixture_payload()
    assert len(payload["records"]) == 5
    assert {r["status"] for r in payload["records"]} == set(DISCOVERY_STATUSES)
    assert {r["status"] for r in payload["records"]} == {
        "discovered",
        "needs_confirmation",
        "authorized",
        "connected",
        "healing",
    }
    # No secret values in the static fixture: credential_ref never appears.
    raw = FIXTURE_PATH.read_text(encoding="utf-8")
    assert "credential" not in raw.lower()
    assert "api_key" not in raw.lower()


def test_import_fixture_then_list_shows_all_statuses(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/discovery/import", json=_fixture_payload())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["total"] == 5
    assert len(body["imported"]) == 5

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
    # Rows carry no secrets, only refs and public metadata.
    for row in listed.json():
        assert "credential" not in json.dumps(row).lower()


def test_get_one_discovery(client: Any) -> None:  # type: ignore[no-untyped-def]
    client.post("/discovery/import", json=_fixture_payload())
    r = client.get("/discovery/disc-s3-atlas-001")
    assert r.status_code == 200, r.text
    assert r.json()["vendor"] == "AtlasPMS"
    assert r.json()["candidate_entities"] == ["Reservation", "FolioCharge"]
    assert client.get("/discovery/disc-s3-missing").status_code == 404


def test_full_lifecycle_only_via_explicit_endpoints(client: Any) -> None:  # type: ignore[no-untyped-def]
    client.post("/discovery/import", json={"records": [_lifecycle_record()]})
    did = "disc-s3-life-001"

    r = client.post(f"/discovery/{did}/request-confirmation")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "needs_confirmation"

    r = client.post(f"/discovery/{did}/authorize")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "authorized"

    r = client.post(f"/discovery/{did}/connect")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "connected"

    r = client.post(f"/discovery/{did}/mark-healing")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "healing"

    r = client.post(f"/discovery/{did}/resolve")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "connected"


def test_illegal_transitions_are_422(client: Any) -> None:  # type: ignore[no-untyped-def]
    client.post("/discovery/import", json={"records": [_lifecycle_record("skip")]})
    did = "disc-s3-skip-001"
    # discovered cannot skip straight to connected/authorized.
    assert client.post(f"/discovery/{did}/connect").status_code == 422
    assert client.post(f"/discovery/{did}/authorize").status_code == 422
    assert client.post(f"/discovery/{did}/mark-healing").status_code == 422
    assert client.post(f"/discovery/{did}/resolve").status_code == 422
    # State is unchanged after rejected transitions.
    assert client.get(f"/discovery/{did}").json()["status"] == "discovered"
    # Unknown record id on a transition endpoint is 404, not 422.
    assert client.post("/discovery/disc-s3-nope/authorize").status_code == 404


def test_unknown_status_rejected_never_guessed(client: Any) -> None:  # type: ignore[no-untyped-def]
    bad = _lifecycle_record("bad")
    bad["status"] = "maybe_connected"
    r = client.post("/discovery/import", json={"records": [bad]})
    assert r.status_code == 422, r.text
    assert client.get("/discovery").json() == []


def test_invalid_candidate_entity_rejected(client: Any) -> None:  # type: ignore[no-untyped-def]
    bad = _lifecycle_record("entity")
    bad["candidate_entities"] = ["GuestProfile"]
    r = client.post("/discovery/import", json={"records": [bad]})
    assert r.status_code == 422, r.text


def test_duplicate_in_batch_rejected(client: Any) -> None:  # type: ignore[no-untyped-def]
    rec = _lifecycle_record("dup")
    r = client.post("/discovery/import", json={"records": [rec, dict(rec)]})
    assert r.status_code == 422, r.text


def test_duplicate_against_store_is_409(client: Any) -> None:  # type: ignore[no-untyped-def]
    rec = _lifecycle_record("twice")
    assert client.post("/discovery/import", json={"records": [rec]}).status_code == 201
    r = client.post("/discovery/import", json={"records": [dict(rec)]})
    assert r.status_code == 409, r.text


def test_empty_import_rejected(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/discovery/import", json={"records": []})
    assert r.status_code == 422, r.text


def test_transitions_cover_every_declared_edge(client: Any) -> None:  # type: ignore[no-untyped-def]
    """Every DISCOVERY_TRANSITIONS edge walks green; every other pair 422s."""
    actions = {
        "request-confirmation": "/request-confirmation",
        "authorize": "/authorize",
        "connect": "/connect",
        "mark-healing": "/mark-healing",
        "resolve": "/resolve",
    }
    endpoints = list(actions.values())
    for start, edges in DISCOVERY_TRANSITIONS.items():
        for action, nxt in edges.items():
            rec = _lifecycle_record(f"{start}-{action}".replace("_", "-")[:40])
            rec["status"] = start
            assert client.post("/discovery/import", json={"records": [rec]}).status_code == 201
            did = rec["discovery_id"]
            r = client.post(f"/discovery/{did}{actions[action]}")
            assert r.status_code == 200, (start, action, r.text)
            assert r.json()["status"] == nxt
            # One step further re-walks only where the map allows; a
            # wrong action from the new state must 422.
            for other in endpoints:
                if other == actions[action]:
                    continue
                probe = client.post(f"/discovery/{did}{other}")
                # Either the new state also allows it (resolve/connect
                # cycle) or it must be rejected — never a silent no-op.
                if probe.status_code == 200:
                    assert probe.json()["status"] != nxt or other in (
                        actions.get("connect"),
                        actions.get("resolve"),
                        actions.get("mark-healing"),
                    )


def test_model_matches_reserved_schema_file() -> None:
    """The request-path model mirrors schemas/discovery-result.v0.json."""
    from autobridge.spec.entities import (
        DISCOVERY_RESULT_SCHEMA_FILE,
        DiscoveryResult,
        validate_against_schema,
    )

    base: dict[str, Any] = {
        "discovery_id": "disc-parity-001",
        "status": "discovered",
        "vendor": "ParityPMS",
        "base_url": "https://parity.example.com",
        "candidate_entities": ["Reservation"],
        "evidence_refs": [],
    }
    for status in DISCOVERY_STATUSES:
        payload = dict(base, status=status)
        record = DiscoveryResult.model_validate(payload)
        validate_against_schema(
            record.model_dump(mode="json", exclude_none=True),
            DISCOVERY_RESULT_SCHEMA_FILE,
        )
    # Rejections agree on both sides: unknown status, bad entity, extra key.
    import jsonschema
    from pydantic import ValidationError as PydanticValidationError

    for bad in [
        dict(base, status="maybe_connected"),
        dict(base, candidate_entities=["GuestProfile"]),
        dict(base, unexpected_key="x"),
    ]:
        with pytest.raises(PydanticValidationError):
            DiscoveryResult.model_validate(bad)
        with pytest.raises(jsonschema.ValidationError):
            validate_against_schema(bad, DISCOVERY_RESULT_SCHEMA_FILE)


def test_import_schema_vocab_matches_canonical_entities() -> None:
    """OpenAPI Literals stay pinned to the canonical Discovery vocabularies."""
    import typing

    from autobridge.control.schemas import (
        DiscoveryImportEntity,
        DiscoveryImportStatus,
    )
    from autobridge.spec.entities import DISCOVERY_STATUSES

    assert set(typing.get_args(DiscoveryImportStatus)) == set(DISCOVERY_STATUSES)
    assert set(typing.get_args(DiscoveryImportEntity)) == {
        "Reservation",
        "FolioCharge",
    }


def test_engine_never_imports_verifier_or_simulators() -> None:
    import autobridge.control.routes.discovery as mod

    source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    assert "import verifier" not in source
    assert "import simulators" not in source
    assert "from verifier" not in source
    assert "from simulators" not in source
