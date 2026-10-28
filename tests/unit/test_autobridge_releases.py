"""Releases: creation, approve/reject/rollback (P1.W4.5)."""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine

from autobridge.control.models import (
    Connector,
    RepairRun,
    init_db,
    session_factory,
    spec_signature,
)
from autobridge.engine.releases import (
    ReleaseError,
    approve_release,
    create_release,
    create_release_from_patch,
    next_version,
    reject_release,
    rollback_release,
)


def _session(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_engine(f"sqlite:///{tmp_path}/releases.db")
    init_db(engine)
    return session_factory(engine)


def _minimal_spec(connector_id: str = "testconn", vendor: str = "Test") -> dict[str, Any]:
    return {
        "spec_version": 1,
        "connector": {"id": connector_id, "vendor": vendor, "runtime_target": "cloud"},
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


def _make_connector(session, **over: Any) -> Connector:
    kw: dict[str, Any] = {
        "name": "TestConn",
        "base_url": "https://example.com",
        "credential_ref": "TEST_TOKEN",
        "target_entities": ["Reservation"],
        "runtime_target": {"kind": "local_process"},
    }
    kw.update(over)
    c = Connector(**kw)
    session.add(c)
    session.commit()
    return c


def _repair_run(session) -> RepairRun:
    rr = RepairRun(drift_event_ids=[], decision="propose", model_usage={})
    session.add(rr)
    session.commit()
    return rr


def test_next_version_empty_and_increment(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        assert next_version(s, conn.id) == 1
        spec = _minimal_spec()
        r1 = create_release(s, connector_id=conn.id, spec=spec)
        s.commit()
        assert r1.version == 1
        assert next_version(s, conn.id) == 2
        r2 = create_release(s, connector_id=conn.id, spec=spec)
        s.commit()
        assert r2.version == 2


def test_create_release_basic(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        spec = _minimal_spec()
        rel = create_release(s, connector_id=conn.id, spec=spec, verification_report_id="vr-1")
        s.commit()
        assert rel.version == 1
        assert rel.signature == spec_signature(rel.spec)
        assert rel.parent_release_id is None
        assert rel.verification_report_id == "vr-1"
        assert rel.spec["spec_version"] == 1


def test_create_release_with_parent_and_provenance(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        spec = _minimal_spec()
        r1 = create_release(s, connector_id=conn.id, spec=spec)
        s.commit()
        rr = _repair_run(s)
        r2 = create_release(
            s,
            connector_id=conn.id,
            spec=spec,
            parent_release_id=r1.id,
            provenance_repair_run_id=rr.id,
            verification_report_id="vr-2",
        )
        s.commit()
        assert r2.version == 2
        assert r2.parent_release_id == r1.id
        assert r2.provenance_repair_run_id == rr.id
        assert r2.verification_report_id == "vr-2"


def test_create_release_invalid_spec(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        bad = {"spec_version": 1, "connector": {"id": "bad id!", "vendor": "X"}}
        with pytest.raises(ReleaseError, match="spec validation failed"):
            create_release(s, connector_id=conn.id, spec=bad)


def test_create_release_missing_connector(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s, pytest.raises(ReleaseError, match="not found"):
        create_release(s, connector_id="nope", spec=_minimal_spec())


def test_create_release_parent_not_found(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        with pytest.raises(ReleaseError, match="not found"):
            create_release(s, connector_id=conn.id, spec=_minimal_spec(), parent_release_id="nope")


def test_create_release_parent_wrong_connector(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        c1 = _make_connector(s, name="c1")
        c2 = _make_connector(s, name="c2")
        r1 = create_release(s, connector_id=c1.id, spec=_minimal_spec())
        s.commit()
        with pytest.raises(ReleaseError, match="different connector"):
            create_release(s, connector_id=c2.id, spec=_minimal_spec(), parent_release_id=r1.id)


def test_create_release_from_patch_happy(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        base_spec = _minimal_spec()
        base = create_release(s, connector_id=conn.id, spec=base_spec)
        s.commit()
        rr = _repair_run(s)
        patch = [{"op": "replace", "path": "/source/rate_limit/max_rps", "value": 10}]
        new_rel = create_release_from_patch(
            s,
            connector_id=conn.id,
            base_release_id=base.id,
            patch=patch,
            provenance_repair_run_id=rr.id,
            verification_report_id="vr-patch",
        )
        s.commit()
        assert new_rel.version == 2
        assert new_rel.parent_release_id == base.id
        assert new_rel.provenance_repair_run_id == rr.id
        assert new_rel.verification_report_id == "vr-patch"
        assert new_rel.spec["source"]["rate_limit"]["max_rps"] == 10
        assert new_rel.signature != base.signature
        # base unchanged
        s.refresh(base)
        assert base.spec["source"]["rate_limit"]["max_rps"] == 5


def test_create_release_from_patch_empty_rejected(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        base = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        with pytest.raises(ReleaseError, match="non-empty"):
            create_release_from_patch(s, connector_id=conn.id, base_release_id=base.id, patch=[])


def test_create_release_from_patch_missing_connector(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        base = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        patch = [{"op": "replace", "path": "/source/rate_limit/max_rps", "value": 10}]
        with pytest.raises(ReleaseError, match="not found"):
            create_release_from_patch(s, connector_id="nope", base_release_id=base.id, patch=patch)


def test_create_release_from_patch_missing_base(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        patch = [{"op": "replace", "path": "/source/rate_limit/max_rps", "value": 10}]
        with pytest.raises(ReleaseError, match="not found"):
            create_release_from_patch(s, connector_id=conn.id, base_release_id="nope", patch=patch)


def test_create_release_from_patch_wrong_connector(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        c1 = _make_connector(s, name="c1")
        c2 = _make_connector(s, name="c2")
        base = create_release(s, connector_id=c1.id, spec=_minimal_spec())
        s.commit()
        patch = [{"op": "replace", "path": "/source/rate_limit/max_rps", "value": 10}]
        with pytest.raises(ReleaseError, match="different connector"):
            create_release_from_patch(s, connector_id=c2.id, base_release_id=base.id, patch=patch)


def test_create_release_from_patch_invalid_patch(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        base = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        # bad pointer
        bad_patch = [{"op": "replace", "path": "/no/such/path", "value": "x"}]
        with pytest.raises(ReleaseError, match="does not apply"):
            create_release_from_patch(s, connector_id=conn.id, base_release_id=base.id, patch=bad_patch)


def test_create_release_from_patch_invalid_spec_after_patch(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        base = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        # remove required field entity key -> invalid spec
        bad_patch = [{"op": "remove", "path": "/entities/Reservation/key"}]
        with pytest.raises(ReleaseError, match="does not apply|invalid spec"):
            create_release_from_patch(s, connector_id=conn.id, base_release_id=base.id, patch=bad_patch)


def test_create_release_from_patch_unknown_repair_run(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        base = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        patch = [{"op": "replace", "path": "/source/rate_limit/max_rps", "value": 10}]
        with pytest.raises(ReleaseError, match="RepairRun"):
            create_release_from_patch(
                s, connector_id=conn.id, base_release_id=base.id, patch=patch, provenance_repair_run_id="nope"
            )


def test_approve_release_activates(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        r1 = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        # activate r1 first
        conn.active_release_id = r1.id
        s.commit()
        rr = _repair_run(s)
        # AR-1-3: approval is bound to the repair run's candidate release.
        r2 = create_release(
            s,
            connector_id=conn.id,
            spec=_minimal_spec(),
            parent_release_id=r1.id,
            provenance_repair_run_id=rr.id,
        )
        s.commit()
        approval = approve_release(s, repair_run_id=rr.id, actor="alice", from_release_id=r1.id, to_release_id=r2.id)
        s.commit()
        assert approval.decision == "approve"
        assert approval.from_release_id == r1.id
        assert approval.to_release_id == r2.id
        s.refresh(conn)
        assert conn.active_release_id == r2.id


def test_approve_release_inferred_connector(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        r1 = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        rr = _repair_run(s)
        r2 = create_release(
            s,
            connector_id=conn.id,
            spec=_minimal_spec(),
            parent_release_id=r1.id,
            provenance_repair_run_id=rr.id,
        )
        s.commit()
        approval = approve_release(s, repair_run_id=rr.id, actor="bob", from_release_id=None, to_release_id=r2.id)
        s.commit()
        assert approval.from_release_id is None
        s.refresh(conn)
        assert conn.active_release_id == r2.id


def test_approve_release_mismatched_connector_id(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        c1 = _make_connector(s, name="c1")
        c2 = _make_connector(s, name="c2")
        r1 = create_release(s, connector_id=c1.id, spec=_minimal_spec())
        s.commit()
        rr = _repair_run(s)
        r2 = create_release(
            s,
            connector_id=c1.id,
            spec=_minimal_spec(),
            parent_release_id=r1.id,
            provenance_repair_run_id=rr.id,
        )
        s.commit()
        with pytest.raises(ReleaseError, match="does not match"):
            approve_release(
                s, repair_run_id=rr.id, actor="alice", from_release_id=r1.id, to_release_id=r2.id, connector_id=c2.id
            )


def test_approve_release_empty_actor(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        r1 = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        rr = _repair_run(s)
        with pytest.raises(ReleaseError, match="actor"):
            approve_release(s, repair_run_id=rr.id, actor="  ", from_release_id=None, to_release_id=r1.id)


def test_approve_release_missing_repair(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        r1 = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        with pytest.raises(ReleaseError, match="RepairRun"):
            approve_release(s, repair_run_id="nope", actor="alice", from_release_id=None, to_release_id=r1.id)


def test_approve_release_mismatched_from_to_connector(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        c1 = _make_connector(s, name="c1")
        c2 = _make_connector(s, name="c2")
        r1 = create_release(s, connector_id=c1.id, spec=_minimal_spec())
        s.commit()
        rr = _repair_run(s)
        r2 = create_release(
            s,
            connector_id=c2.id,
            spec=_minimal_spec(connector_id="other", vendor="Other"),
            provenance_repair_run_id=rr.id,
        )
        s.commit()
        with pytest.raises(ReleaseError, match="different connectors"):
            approve_release(s, repair_run_id=rr.id, actor="alice", from_release_id=r1.id, to_release_id=r2.id)


def test_approve_release_rejects_foreign_to_release(tmp_path) -> None:
    """AR-1-3: approval is bound to the repair run's candidate release."""
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        r1 = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        rr = _repair_run(s)
        other = _repair_run(s)
        # Candidate of a *different* repair run: must be rejected.
        r2 = create_release(
            s,
            connector_id=conn.id,
            spec=_minimal_spec(),
            parent_release_id=r1.id,
            provenance_repair_run_id=other.id,
        )
        s.commit()
        with pytest.raises(ReleaseError, match="not the candidate release"):
            approve_release(s, repair_run_id=rr.id, actor="alice", from_release_id=r1.id, to_release_id=r2.id)
        # A release with no repair provenance at all is equally rejected.
        r3 = create_release(s, connector_id=conn.id, spec=_minimal_spec(), parent_release_id=r1.id)
        s.commit()
        with pytest.raises(ReleaseError, match="not the candidate release"):
            approve_release(s, repair_run_id=rr.id, actor="alice", from_release_id=r1.id, to_release_id=r3.id)
        # Nothing was activated by the rejected attempts.
        s.refresh(conn)
        assert conn.active_release_id is None


def test_reject_release_does_not_activate(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        r1 = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        conn.active_release_id = r1.id
        s.commit()
        r2 = create_release(s, connector_id=conn.id, spec=_minimal_spec(), parent_release_id=r1.id)
        s.commit()
        rr = _repair_run(s)
        approval = reject_release(s, repair_run_id=rr.id, actor="alice", from_release_id=r1.id, to_release_id=r2.id)
        s.commit()
        assert approval.decision == "reject"
        s.refresh(conn)
        assert conn.active_release_id == r1.id


def test_reject_release_empty_actor(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        r1 = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        rr = _repair_run(s)
        with pytest.raises(ReleaseError, match="actor"):
            reject_release(s, repair_run_id=rr.id, actor="", from_release_id=None, to_release_id=r1.id)


def test_reject_release_missing_ids(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        r1 = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        rr = _repair_run(s)
        with pytest.raises(ReleaseError, match="not found"):
            reject_release(s, repair_run_id=rr.id, actor="alice", from_release_id="nope", to_release_id=r1.id)
        with pytest.raises(ReleaseError, match="not found"):
            reject_release(s, repair_run_id=rr.id, actor="alice", from_release_id=r1.id, to_release_id="nope")
        with pytest.raises(ReleaseError, match="not found"):
            reject_release(s, repair_run_id="nope", actor="alice", from_release_id=None, to_release_id=r1.id)


def test_rollback_happy_path(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        spec_v1 = _minimal_spec()
        r1 = create_release(s, connector_id=conn.id, spec=spec_v1)
        s.commit()
        conn.active_release_id = r1.id
        s.commit()
        # v2 diverges
        patch = [{"op": "replace", "path": "/source/rate_limit/max_rps", "value": 10}]
        # approve v2
        rr1 = _repair_run(s)
        r2 = create_release_from_patch(
            s, connector_id=conn.id, base_release_id=r1.id, patch=patch, provenance_repair_run_id=rr1.id
        )
        s.commit()
        approve_release(s, repair_run_id=rr1.id, actor="alice", from_release_id=r1.id, to_release_id=r2.id)
        s.commit()
        s.refresh(conn)
        assert conn.active_release_id == r2.id
        # rollback to v1
        rr2 = _repair_run(s)
        s.commit()
        s.refresh(conn)
        assert conn.active_release_id == r2.id
        # rollback to v1
        rr2 = _repair_run(s)
        new_rel, approval = rollback_release(s, connector_id=conn.id, target_release_id=r1.id, actor="alice", repair_run_id=rr2.id)
        s.commit()
        assert new_rel.version == 3
        assert new_rel.parent_release_id == r2.id
        assert new_rel.spec == r1.spec
        assert new_rel.signature == r1.signature
        assert approval.decision == "rollback"
        assert approval.from_release_id == r2.id
        assert approval.to_release_id == new_rel.id
        assert approval.rollback_of_release_id == r2.id
        s.refresh(conn)
        assert conn.active_release_id == new_rel.id
        # Verify r1 never mutated (stored spec is validated form, compare to itself)
        s.refresh(r1)
        assert r1.version == 1
        assert r1.spec["connector"]["id"] == spec_v1["connector"]["id"]
        assert r1.spec["spec_version"] == 1


def _fake_http(pages: list[dict[str, Any]]):  # type: ignore[no-untyped-def]
    idx = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        if idx["i"] >= len(pages):
            return httpx.Response(200, json={"data": [], "meta": {"next_cursor": None}})
        body = pages[idx["i"]]
        idx["i"] += 1
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):  # type: ignore[no-untyped-def]
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    from autobridge.runtime.http import HttpClient
    from autobridge.spec.models import RetryPolicy

    return HttpClient(
        "https://example.com",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=3),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )


def test_rollback_sync_output_bit_identical(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rollback restores prior spec and prior sync output is bit-identical."""
    monkeypatch.setenv("TEST_TOKEN", "tok")
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        spec_v1 = _minimal_spec()
        r1 = create_release(s, connector_id=conn.id, spec=spec_v1)
        s.commit()
        conn.active_release_id = r1.id
        s.commit()
        # v2 via patch
        patch = [{"op": "replace", "path": "/source/rate_limit/max_rps", "value": 10}]
        rr1 = _repair_run(s)
        r2 = create_release_from_patch(
            s, connector_id=conn.id, base_release_id=r1.id, patch=patch, provenance_repair_run_id=rr1.id
        )
        s.commit()
        approve_release(s, repair_run_id=rr1.id, actor="alice", from_release_id=r1.id, to_release_id=r2.id)
        s.commit()
        # rollback to v1
        rr2 = _repair_run(s)
        r3, _ = rollback_release(s, connector_id=conn.id, target_release_id=r1.id, actor="alice", repair_run_id=rr2.id)
        s.commit()
        assert r3.spec == r1.spec

        from autobridge.engine.sync_run import execute_sync

        pages_v1 = [{"data": [{"reservation_id": "R1"}, {"reservation_id": "R2"}], "meta": {"next_cursor": None}}]
        pages_r3 = copy.deepcopy(pages_v1)

        summary_v1 = execute_sync(r1.spec, _fake_http(pages_v1))
        summary_r3 = execute_sync(r3.spec, _fake_http(pages_r3))

        assert summary_v1["canonical_records"] == summary_r3["canonical_records"]
        assert summary_v1["records_observed"] == summary_r3["records_observed"]
        assert summary_v1["outcome"] == summary_r3["outcome"]
        # JSON round-trip identical
        import json

        assert json.dumps(summary_v1["canonical_records"], sort_keys=True, default=str) == json.dumps(
            summary_r3["canonical_records"], sort_keys=True, default=str
        )


def test_rollback_errors(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        r1 = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        rr = _repair_run(s)
        # no active -> error
        with pytest.raises(ReleaseError, match="no active release"):
            rollback_release(s, connector_id=conn.id, target_release_id=r1.id, actor="alice", repair_run_id=rr.id)
        # activate then missing target
        conn.active_release_id = r1.id
        s.commit()
        with pytest.raises(ReleaseError, match="not found"):
            rollback_release(s, connector_id=conn.id, target_release_id="nope", actor="alice", repair_run_id=rr.id)
        # empty actor
        with pytest.raises(ReleaseError, match="actor"):
            rollback_release(s, connector_id=conn.id, target_release_id=r1.id, actor=" ", repair_run_id=rr.id)
        # missing connector
        with pytest.raises(ReleaseError, match="not found"):
            rollback_release(s, connector_id="nope", target_release_id=r1.id, actor="alice", repair_run_id=rr.id)
        # missing repair run
        with pytest.raises(ReleaseError, match="not found"):
            rollback_release(s, connector_id=conn.id, target_release_id=r1.id, actor="alice", repair_run_id="nope")
        # target belongs different connector
        c2 = _make_connector(s, name="c2")
        r_other = create_release(s, connector_id=c2.id, spec=_minimal_spec(connector_id="other", vendor="Other"))
        s.commit()
        with pytest.raises(ReleaseError, match="different connector"):
            rollback_release(s, connector_id=conn.id, target_release_id=r_other.id, actor="alice", repair_run_id=rr.id)


def test_create_release_stores_verification_and_signature_stable(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        spec = _minimal_spec()
        r1 = create_release(s, connector_id=conn.id, spec=spec)
        s.commit()
        assert r1.signature == spec_signature(r1.spec)
        # second release with same spec content (different op) gets different version but same signature content
        r2 = create_release(s, connector_id=conn.id, spec=spec, parent_release_id=r1.id, verification_report_id="vr-x")
        s.commit()
        assert r2.signature == r1.signature
        assert r2.version == 2


def test_approve_and_reject_with_none_ids(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        _make_connector(s)
        rr = _repair_run(s)
        # reject with both None is allowed
        a = reject_release(s, repair_run_id=rr.id, actor="bob", from_release_id=None, to_release_id=None)
        s.commit()
        assert a.decision == "reject"
        assert a.from_release_id is None
