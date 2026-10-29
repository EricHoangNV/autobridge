"""REQ-030 — sync scheduler, learn-on-approve, connector state, persistence.

* Item 1: scheduler interval parsing; pass skips (no release, in-flight
  job, awaiting decision) and syncs idle connectors through the same path
  as POST /sync; a proposal is never approved by the scheduler.
* Item 2b: proposing caches nothing (repeat → L0 miss); approving learns
  (repeat → L0 hit with approval attribution); reject/rollback evict.
* Item 4: server-side working/degrading/error + activity + last_sync_at.
* Item 3: record-climb + pabx endpoints persist rows the UI reads;
  ModelCall mode/captured/outcome columns; drift resolution on the
  verifying sync.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pathlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from autobridge.control.app import app
from autobridge.control.deps import get_db
from autobridge.control.models import (
    Approval,
    Base,
    Connector,
    ConnectorRelease,
    DriftEvent,
    Job,
    PabxLinkEvent,
    RepairRun,
    SyncRun,
    session_factory,
    spec_signature,
)


@pytest.fixture()
def client(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Any:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://127.0.0.1:9")
    # The edge-relay proxy default (http://edge-relay:8000) would resolve a
    # hostname (DNS trip, hermetic-test failure, and in this sandbox a proxy
    # lookup): point link snapshots at refused loopback instead.
    monkeypatch.setenv("EDGE_RELAY_URL", "http://127.0.0.1:9")
    engine = create_engine(f"sqlite:///{tmp_path}/req030.db")
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


def _spec() -> dict[str, Any]:
    """Minimal valid spec syncing against a refused loopback port (Blocked)."""
    return {
        "spec_version": 1,
        "connector": {"id": "sched", "vendor": "schedvendor"},
        "source": {
            "base_url": "http://127.0.0.1:9",
            "auth": {"type": "bearer", "credential_ref": "SCHED_TOKEN"},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_stuff": {
                "method": "GET",
                "path": "/stuff",
                "params": {},
                "pagination": {
                    "strategy": "page",
                    "page_param": "page",
                    "limit_param": "limit",
                    "items_path": "$.data",
                    "stop_when": ["empty_page"],
                },
            }
        },
        "entities": {
            "Stuff": {
                "from": "list_stuff",
                "key": "$.id",
                "fields": {"id": {"path": "$.id"}},
            }
        },
        "capabilities": {"read": ["Stuff"]},
        "health": {"canary": {"every": "60s", "operation": "list_stuff"}},
    }


def _seed(client: Any, name: str = "Sched") -> tuple[str, str]:  # type: ignore[no-untyped-def]
    r = client.post(
        "/connectors",
        json={
            "name": name,
            "base_url": "http://127.0.0.1:9",
            "credential_ref": "SCHED_TOKEN",
            "target_entities": ["Stuff"],
            "runtime_target": {"kind": "local_process"},
        },
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    return cid, ""


# ---------------------------------------------------------------------------
# Item 1: interval parsing
# ---------------------------------------------------------------------------


def test_sync_interval_off_when_unset_or_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.config import sync_interval_seconds

    monkeypatch.delenv("AUTOBRIDGE_SYNC_INTERVAL_S", raising=False)
    assert sync_interval_seconds() is None
    monkeypatch.setenv("AUTOBRIDGE_SYNC_INTERVAL_S", "")
    assert sync_interval_seconds() is None
    monkeypatch.setenv("AUTOBRIDGE_SYNC_INTERVAL_S", "   ")
    assert sync_interval_seconds() is None
    monkeypatch.setenv("AUTOBRIDGE_SYNC_INTERVAL_S", "0")
    assert sync_interval_seconds() is None
    monkeypatch.setenv("AUTOBRIDGE_SYNC_INTERVAL_S", "-3")
    assert sync_interval_seconds() is None


def test_sync_interval_seconds_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.config import sync_interval_seconds

    monkeypatch.setenv("AUTOBRIDGE_SYNC_INTERVAL_S", "10")
    assert sync_interval_seconds() == 10.0
    monkeypatch.setenv("AUTOBRIDGE_SYNC_INTERVAL_S", "2.5")
    assert sync_interval_seconds() == 2.5
    monkeypatch.setenv("AUTOBRIDGE_SYNC_INTERVAL_S", "soon")
    with pytest.raises(ValueError, match="AUTOBRIDGE_SYNC_INTERVAL_S"):
        sync_interval_seconds()


# ---------------------------------------------------------------------------
# Item 1: scheduler pass (DB-level, same engine as the route)
# ---------------------------------------------------------------------------


def _factory_for(tmp_path: pathlib.Path) -> Any:  # type: ignore[no-untyped-def]
    # The client fixture already created the file DB; reopen it here so the
    # scheduler runs against the same rows the API wrote.
    engine = create_engine(f"sqlite:///{tmp_path}/req030.db")
    return session_factory(engine)


def _add_release(factory: Any, connector_id: str, spec: dict[str, Any], *, active: bool = True) -> str:  # type: ignore[no-untyped-def]
    with factory() as s:
        rel = ConnectorRelease(
            connector_id=connector_id, version=1, spec=spec, signature=spec_signature(spec)
        )
        s.add(rel)
        s.flush()
        if active:
            conn = s.get(Connector, connector_id)
            assert conn is not None
            conn.active_release_id = rel.id
        rid = rel.id
        s.commit()
        return rid


def test_scheduler_skips_connector_without_release(client: Any, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    cid, _ = _seed(client)
    from autobridge.control.scheduler import run_scheduler_pass

    factory = _factory_for(tmp_path)
    with factory() as s:
        out = run_scheduler_pass(s)
    assert out["connectors"] == 1
    assert out["results"][0]["status"] == "skipped"
    assert out["results"][0]["reason"] == "no active release"


def test_scheduler_syncs_idle_connector(client: Any, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    cid, _ = _seed(client)
    factory = _factory_for(tmp_path)
    _add_release(factory, cid, _spec())
    from autobridge.control.scheduler import run_scheduler_pass

    with factory() as s:
        out = run_scheduler_pass(s)
    assert out["synced"] == 1, out
    row = out["results"][0]
    assert row["status"] == "synced"
    assert row["outcome"] == "Blocked"  # refused loopback: no vendor response
    assert row["sync_run_id"]
    with factory() as s:
        assert s.query(SyncRun).count() == 1
        assert s.query(RepairRun).count() == 0  # Blocked connectivity opens no drift
        assert s.query(Approval).count() == 0  # the scheduler never approves


def test_scheduler_skips_while_sync_job_in_flight(client: Any, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    cid, _ = _seed(client)
    factory = _factory_for(tmp_path)
    rid = _add_release(factory, cid, _spec())
    with factory() as s:
        s.add(Job(kind="sync", state="queued", payload={"release_id": rid}))
        s.commit()
    from autobridge.control.scheduler import run_scheduler_pass

    with factory() as s:
        out = run_scheduler_pass(s)
    assert out["results"][0]["status"] == "skipped"
    assert "queued" in out["results"][0]["reason"]
    with factory() as s:
        assert s.query(SyncRun).count() == 0


def test_scheduler_skips_while_repair_awaits_and_resumes_after_approval(  # type: ignore[no-untyped-def]
    client: Any, tmp_path: pathlib.Path
) -> None:
    cid, _ = _seed(client)
    factory = _factory_for(tmp_path)
    _add_release(factory, cid, _spec())
    with factory() as s:
        drift = DriftEvent(connector_id=cid, drift_class="PAGINATION_CHANGE")
        s.add(drift)
        s.flush()
        run = RepairRun(
            drift_event_ids=[drift.id],
            ladder_level_reached=1,
            vendor="schedvendor",
            decision="propose",
            model_usage={"frontier_tokens": 0, "small_tokens": 0},
            ladder_trace=[{"level": 1, "action": "rule_applied", "reason": "r"}],
        )
        s.add(run)
        s.commit()
        run_id = run.id
    from autobridge.control.scheduler import run_scheduler_pass

    with factory() as s:
        out = run_scheduler_pass(s)
    assert out["results"][0]["status"] == "skipped"
    assert "awaiting decision" in out["results"][0]["reason"]
    # Operator rejects → decision recorded → the scheduler resumes.
    r = client.post(f"/repairs/{run_id}/reject", json={})
    assert r.status_code == 200, r.text
    with factory() as s:
        out = run_scheduler_pass(s)
    assert out["results"][0]["status"] == "synced", out


def test_scheduler_never_approves_and_isolates_errors(client: Any, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    good, _ = _seed(client, name="Good")
    factory = _factory_for(tmp_path)
    _add_release(factory, good, _spec())
    # Broken: active release id names a missing release → 404 per connector.
    with factory() as s:
        bad = Connector(
            name="Bad", base_url="http://127.0.0.1:9", credential_ref="SCHED_TOKEN",
            target_entities=["Stuff"], runtime_target={"kind": "local_process"},
            active_release_id="missing-release",
        )
        s.add(bad)
        s.commit()
    from autobridge.control.scheduler import run_scheduler_pass

    with factory() as s:
        out = run_scheduler_pass(s)
    by_status = {r["status"] for r in out["results"]}
    assert by_status == {"synced", "error"}, out
    with factory() as s:
        assert s.query(Approval).count() == 0


# ---------------------------------------------------------------------------
# Item 4: server-side connector state
# ---------------------------------------------------------------------------


def _state(factory: Any, connector_id: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    from autobridge.control.connector_state import connector_state

    with factory() as s:
        return connector_state(s, connector_id)


def test_state_never_synced_is_degrading(client: Any, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    cid, _ = _seed(client)
    st = _state(_factory_for(tmp_path), cid)
    assert st["state"] == "degrading"
    assert "first sync" in st["activity"]
    assert st["last_sync_at"] is None
    r = client.get(f"/connectors/{cid}")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "degrading"
    assert r.json()["last_sync_at"] is None


def test_state_working_after_complete_sync(client: Any, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    cid, _ = _seed(client)
    factory = _factory_for(tmp_path)
    rid = _add_release(factory, cid, _spec())
    with factory() as s:
        s.add(SyncRun(release_id=rid, outcome="Complete", unmet_criteria=[]))
        s.commit()
    st = _state(factory, cid)
    assert st["state"] == "working"
    assert "Complete" in st["activity"]
    assert st["last_sync_at"] is not None
    r = client.get("/connectors")
    assert r.status_code == 200, r.text
    rows = {c["id"]: c for c in r.json()}
    assert rows[cid]["state"] == "working"
    assert rows[cid]["last_sync_at"] is not None


def test_state_blocked_sync_is_error(client: Any, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    cid, _ = _seed(client)
    factory = _factory_for(tmp_path)
    rid = _add_release(factory, cid, _spec())
    with factory() as s:
        s.add(SyncRun(release_id=rid, outcome="Blocked", unmet_criteria=["http_error"]))
        s.commit()
    st = _state(factory, cid)
    assert st["state"] == "error"
    assert st["activity"] == "Blocked: http_error"


def test_state_fix_ready_versus_needs_decision(client: Any, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    cid, _ = _seed(client)
    factory = _factory_for(tmp_path)
    _add_release(factory, cid, _spec())
    with factory() as s:
        drift = DriftEvent(connector_id=cid, drift_class="PAGINATION_CHANGE")
        s.add(drift)
        s.flush()
        s.add(
            RepairRun(
                drift_event_ids=[drift.id], ladder_level_reached=1,
                decision="propose",
                model_usage={"frontier_tokens": 0, "small_tokens": 0},
                ladder_trace=[],
            )
        )
        s.commit()
    st = _state(factory, cid)
    assert st["state"] == "degrading"
    assert st["activity"] == "fix ready · verified · 0 model calls"
    # Same repair after a model-assisted rung → error.
    with factory() as s:
        run = s.query(RepairRun).one()
        run.model_usage = {"frontier_tokens": 100, "small_tokens": 0}
        s.commit()
    st = _state(factory, cid)
    assert st["state"] == "error"
    assert st["activity"] == "needs your decision"


def test_state_escalation_is_error(client: Any, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    cid, _ = _seed(client)
    factory = _factory_for(tmp_path)
    _add_release(factory, cid, _spec())
    with factory() as s:
        drift = DriftEvent(connector_id=cid, drift_class="SEMANTIC_VALUE_DRIFT")
        s.add(drift)
        s.flush()
        s.add(
            RepairRun(
                drift_event_ids=[drift.id], ladder_level_reached=4,
                decision="escalate",
                model_usage={"frontier_tokens": 10, "small_tokens": 5},
                ladder_trace=[],
                escalation_bundle={"question": "Which mapping is correct?"},
            )
        )
        s.commit()
    st = _state(factory, cid)
    assert st["state"] == "error"
    assert st["activity"] == "needs your decision"


def test_state_verifying_after_approval_then_working(client: Any, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    cid, _ = _seed(client)
    factory = _factory_for(tmp_path)
    rid = _add_release(factory, cid, _spec())
    from autobridge.engine.releases import approve_release

    with factory() as s:
        drift = DriftEvent(connector_id=cid, drift_class="PAGINATION_CHANGE")
        s.add(drift)
        s.flush()
        run = RepairRun(
            drift_event_ids=[drift.id], ladder_level_reached=1,
            decision="propose", rule_id="page_to_cursor",
            candidate_patch=[{"op": "replace", "path": "/a", "value": "b"}],
            model_usage={"frontier_tokens": 0, "small_tokens": 0},
            ladder_trace=[],
        )
        s.add(run)
        s.flush()
        candidate = ConnectorRelease(
            connector_id=cid, version=2, spec=_spec(),
            signature=spec_signature(_spec()),
            parent_release_id=rid, provenance_repair_run_id=run.id,
        )
        s.add(candidate)
        s.flush()
        approve_release(
            s, repair_run_id=run.id, actor="tester",
            from_release_id=rid, to_release_id=candidate.id, connector_id=cid,
        )
        s.commit()
        candidate_id = candidate.id
    # Approved, drift still open, no verifying sync yet → degrading.
    st = _state(factory, cid)
    assert st["state"] == "degrading"
    assert "verifying" in st["activity"]
    # A Complete sync on the approved release retires the drift → working.
    from autobridge.engine.detect_repair import detect_and_repair_after_sync

    baseline = {
        "outcome": "Complete", "unmet_criteria": [],
        "data_readiness": {"freshness": "current", "completeness": "complete"},
        "fingerprint_id": "x",
        "operation_results": {
            "list_stuff": {
                "records": [{"id": "1"}],
                "raw_pages": [{"success": True, "data": [{"id": "1"}]}],
            }
        },
        "records_observed": {"Stuff": 1},
        "invariant_results": [],
    }
    with factory() as s:
        out = detect_and_repair_after_sync(
            s, connector_id=cid, release_id=candidate_id, spec_dict=_spec(),
            summary=baseline, sync_run_id="sync-verify",
        )
        # The verifying sync's own row (run_sync_handler writes this in prod).
        s.add(SyncRun(release_id=candidate_id, outcome="Complete", unmet_criteria=[]))
        s.commit()
    assert out["drifts_resolved"], out
    st = _state(factory, cid)
    assert st["state"] == "working", st


# ---------------------------------------------------------------------------
# Scheduler loop: thread offload, overlap guard, never raises
# ---------------------------------------------------------------------------


def test_run_scheduler_pass_never_raises(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autobridge.control.app import _run_scheduler_pass

    # Hermetic relay (see the client fixture): link snapshots must not
    # resolve hostnames.
    monkeypatch.setenv("EDGE_RELAY_URL", "http://127.0.0.1:9")

    def _boom() -> object:
        raise RuntimeError("store down")

    _run_scheduler_pass(_boom)  # must not raise
    factory = session_factory(create_engine(f"sqlite:///{tmp_path}/loop.db"))
    Base.metadata.create_all(factory().bind)  # type: ignore[union-attr]
    _run_scheduler_pass(factory)  # empty pass commits cleanly


def test_scheduler_loop_ticks_off_thread_and_skips_overlap(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio as _asyncio
    import time as _time

    import autobridge.control.app as _app

    monkeypatch.setenv("EDGE_RELAY_URL", "http://127.0.0.1:9")
    factory = session_factory(create_engine(f"sqlite:///{tmp_path}/tick.db"))
    Base.metadata.create_all(factory().bind)  # type: ignore[union-attr]
    opened: list[str] = []

    def _fake_factory() -> object:
        opened.append("session")

        class _Ctx:
            def __enter__(self) -> object:
                _time.sleep(0.12)  # slower than the tick: overlap must skip
                return factory()

            def __exit__(self, *exc: object) -> bool:
                return False

        return _Ctx()

    monkeypatch.setattr(
        "autobridge.control.deps.get_session_factory", lambda: _fake_factory
    )
    import contextlib as _contextlib

    # Explicit loop: the thread-local one may be missing or closed after
    # unrelated async tests (order-dependent), which is not what this
    # test is about. wait_for cancels the loop on timeout: the clean
    # shutdown path.
    loop = _asyncio.new_event_loop()
    try:
        with _contextlib.suppress(TimeoutError):
            loop.run_until_complete(
                _asyncio.wait_for(_app._scheduler_loop(0.05), timeout=0.2)
            )
    finally:
        loop.close()
    assert opened, "the loop must tick at least once"
    assert len(opened) < 4, f"overlap guard must skip ticks while a pass runs: {len(opened)}"


def test_sqlite_engine_uses_wal_and_busy_timeout(tmp_path: pathlib.Path) -> None:
    """Scheduler + API share one SQLite file: WAL keeps passes from locking
    API writes out (readers never block writers; writers wait their turn)."""
    from autobridge.control.models import engine_from_url

    engine = engine_from_url(f"sqlite:///{tmp_path}/wal.db")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30000


# ---------------------------------------------------------------------------
# Branch coverage: busy reasons, human_ago, runner validation, fallbacks
# ---------------------------------------------------------------------------


def test_busy_reason_connector_not_found(tmp_path: pathlib.Path) -> None:
    from autobridge.control.scheduler import connector_busy_reason

    factory = session_factory(create_engine(f"sqlite:///{tmp_path}/b.db"))
    Base.metadata.create_all(factory().bind)  # type: ignore[union-attr]
    with factory() as s:
        assert connector_busy_reason(s, "missing") == "connector not found"


def test_busy_while_build_or_repair_job_in_flight(client: Any, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    cid, _ = _seed(client)
    factory = _factory_for(tmp_path)
    _add_release(factory, cid, _spec())
    with factory() as s:
        drift = DriftEvent(connector_id=cid, drift_class="PAGINATION_CHANGE")
        s.add(drift)
        s.flush()
        s.add(Job(kind="build", state="running", payload={"connector_id": cid}))
        s.add(Job(kind="repair", state="queued", payload={"drift_event_ids": [drift.id]}))
        s.commit()
    from autobridge.control.scheduler import connector_busy_reason, run_scheduler_pass

    with factory() as s:
        assert connector_busy_reason(s, cid) == "build running"
    with factory() as s:
        out = run_scheduler_pass(s)
    assert out["results"][0]["status"] == "skipped"


def test_human_ago_units() -> None:
    from datetime import UTC, datetime, timedelta

    from autobridge.control.connector_state import _parse_ts, human_ago

    assert human_ago(None) == "never"
    now = datetime.now(UTC)
    assert human_ago(now + timedelta(seconds=5), now=now) == "just now"
    assert human_ago(now - timedelta(seconds=4), now=now) == "4 s ago"
    assert human_ago(now - timedelta(minutes=3), now=now) == "3 m ago"
    assert human_ago(now - timedelta(hours=2), now=now) == "2 h ago"
    assert human_ago(now - timedelta(days=2), now=now) == "2 d ago"
    assert _parse_ts(None) is None
    assert _parse_ts("not-a-date") is None
    assert _parse_ts("2026-01-01T00:00:00") is not None  # naive → UTC


def test_state_unknown_connector_raises(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pytest as _pytest

    monkeypatch.setenv("EDGE_RELAY_URL", "http://127.0.0.1:9")
    from autobridge.control.connector_state import connector_state

    factory = session_factory(create_engine(f"sqlite:///{tmp_path}/u.db"))
    Base.metadata.create_all(factory().bind)  # type: ignore[union-attr]
    with factory() as s, _pytest.raises(KeyError):
        connector_state(s, "missing")


def test_state_repair_running_and_sync_running(client: Any, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    cid, _ = _seed(client)
    factory = _factory_for(tmp_path)
    rid = _add_release(factory, cid, _spec())
    with factory() as s:
        drift = DriftEvent(connector_id=cid, drift_class="PAGINATION_CHANGE")
        s.add(drift)
        s.flush()
        s.add(Job(kind="repair", state="running", payload={"drift_event_ids": [drift.id]}))
        s.commit()
    st = _state(factory, cid)
    assert st["state"] == "degrading"
    assert st["activity"].endswith("· repairing")
    # No drift, sync in flight → "sync running".
    with factory() as s:
        s.query(Job).delete()
        s.query(DriftEvent).delete()
        s.add(Job(kind="sync", state="queued", payload={"release_id": rid}))
        s.commit()
    st = _state(factory, cid)
    assert st["state"] == "degrading"
    assert st["activity"] == "sync running"


def test_state_degraded_outcome_is_error(client: Any, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    cid, _ = _seed(client)
    factory = _factory_for(tmp_path)
    rid = _add_release(factory, cid, _spec())
    with factory() as s:
        s.add(SyncRun(release_id=rid, outcome="Degraded", unmet_criteria=["money_invariant"]))
        s.commit()
    st = _state(factory, cid)
    assert st["state"] == "error"
    assert st["activity"] == "Degraded: money_invariant"


def test_state_non_dict_model_usage_counts_zero(client: Any, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    cid, _ = _seed(client)
    factory = _factory_for(tmp_path)
    _add_release(factory, cid, _spec())
    with factory() as s:
        drift = DriftEvent(connector_id=cid, drift_class="PAGINATION_CHANGE")
        s.add(drift)
        s.flush()
        run = RepairRun(
            drift_event_ids=[drift.id], ladder_level_reached=1,
            decision="propose", model_usage=["not-a-dict"],  # type: ignore[list-item]
            ladder_trace=[],
        )
        s.add(run)
        s.commit()
    st = _state(factory, cid)
    assert st["state"] == "degrading"
    assert st["activity"] == "fix ready · verified · 0 model calls"


def test_runner_validation_branches(client: Any, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    import pytest as _pytest

    from autobridge.control.models import BuildRun
    from autobridge.control.runner import SyncError, get_runner, sync_connector_now

    factory = _factory_for(tmp_path)
    # Unknown connector → 404.
    with factory() as s, _pytest.raises(SyncError) as exc:
        sync_connector_now(s, "missing")
    assert exc.value.status_code == 404
    # get_runner with no session falls back to the global factory.
    assert get_runner(None) is not None
    cid, _ = _seed(client, name="R1")
    _add_release(factory, cid, _spec())
    c2, _ = _seed(client, name="R2")
    other_rel = _add_release(factory, c2, _spec())
    with factory() as s:
        # Release of another connector → 422.
        with _pytest.raises(SyncError):
            sync_connector_now(s, cid, release_id=other_rel)
        # Gate-blocked provenance → 422.
        with factory() as inner:
            blocked_build = BuildRun(connector_id=c2, state="succeeded", result={"gate_blocked": "no verifier"})
            inner.add(blocked_build)
            inner.flush()
            blocked = ConnectorRelease(
                connector_id=c2, version=99, spec=_spec(), signature=spec_signature(_spec()),
                provenance_build_run_id=blocked_build.id,
            )
            inner.add(blocked)
            inner.flush()
            blocked_id = blocked.id
            inner.commit()
        with _pytest.raises(SyncError, match="blocked by the verifier gate"):
            sync_connector_now(s, c2, release_id=blocked_id)
        # Named but unapproved release → 422.
        pending = ConnectorRelease(
            connector_id=cid, version=99, spec=_spec(), signature=spec_signature(_spec())
        )
        s.add(pending)
        s.flush()
        with _pytest.raises(SyncError, match="not active or approved"):
            sync_connector_now(s, cid, release_id=pending.id)


def test_runner_registers_stubs_when_engine_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from sqlalchemy import create_engine as _ce
    from sqlalchemy.orm import sessionmaker as _sm

    from autobridge.control.jobs import JobRunner
    from autobridge.control.runner import _register_real_handlers

    factory = _sm(bind=_ce("sqlite:///:memory:"))
    runner = JobRunner(factory)
    for mod in (
        "autobridge.engine.build_run",
        "autobridge.engine.sync_run",
        "autobridge.repair.controller",
    ):
        monkeypatch.setitem(sys.modules, mod, None)
    _register_real_handlers(runner)  # import failures keep the stubs, never raise
    assert set(runner._handlers) == {"build", "sync", "repair"}


# ---------------------------------------------------------------------------
# Item 3: record-climb + pabx endpoints
# ---------------------------------------------------------------------------


def test_record_climb_persists_rows_the_ui_reads(client: Any) -> None:  # type: ignore[no-untyped-def]
    cid, _ = _seed(client, name="Harbor")
    body = {
        "connector_id": cid,
        "drift_class": "SEMANTIC_VALUE_DRIFT",
        "vendor": "harbor",
        "scenario": "gross_to_net",
        "hypotheses": [
            {"id": "gross_is_canonical", "family": "amount", "claim": "stay gross"},
            {"id": "net_is_canonical", "family": "amount", "claim": "track net"},
        ],
        "enumerator_yield_count": 2,
        "attempts": [
            {"level": 0, "action": "knowledge_miss", "reason": "L0 miss"},
            {"level": 1, "action": "no_rule_matched", "reason": "L1: no rule"},
            {"level": 2, "action": "ranked", "reason": "L2 below threshold"},
            {"level": 3, "action": "frontier_generate", "reason": "L3 invoked"},
        ],
        "model_usage": {"frontier_tokens": 2115, "small_tokens": 544, "calls": 2},
        "model_calls": [
            {
                "tier": "small", "provider": "Muse", "model": "muse-spark-1.3",
                "input_tokens": 300, "output_tokens": 244, "reasoning_tokens": 0,
                "purpose": "l2-rank-hypotheses", "mode": "replay",
                "captured_at": "2026-10-28", "outcome": "ranked",
            },
            {
                "tier": "frontier", "provider": "Muse", "model": "muse-spark-1.3",
                "input_tokens": 1200, "output_tokens": 915, "reasoning_tokens": 800,
                "purpose": "l3-generate-patch", "mode": "live",
                "captured_at": "", "outcome": "proposal",
            },
        ],
        "proposal": [{"op": "replace", "path": "/a", "value": "b"}],
        "verifier_verdict": {"verdict": "fail", "failed_checks": ["invalid_patch_trap"]},
        "escalation_question": "gross_to_net: which mapping is correct?",
    }
    r = client.post("/demo/record-climb", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["repair_run_id"]
    assert len(data["model_call_ids"]) == 2

    rep = client.get(f"/repairs/{data['repair_run_id']}")
    assert rep.status_code == 200, rep.text
    assert rep.json()["decision"] == "escalate"
    assert rep.json()["ladder_level_reached"] == 4
    assert [a["level"] for a in rep.json()["ladder_trace"]] == [0, 1, 2, 3, 4]
    assert rep.json()["hypothesis_ids"] == ["gross_is_canonical", "net_is_canonical"]
    assert rep.json()["escalation_bundle"]["question"].endswith("?")

    ledger = client.get(f"/repairs/{data['repair_run_id']}/ledger")
    assert ledger.status_code == 200, ledger.text
    calls = [c for a in ledger.json()["attempts"] for c in a["calls"]]
    assert len(calls) == 2
    by_tier = {c["tier"]: c for c in calls}
    assert by_tier["small"]["mode"] == "replay"
    assert by_tier["small"]["captured_at"] == "2026-10-28"
    assert by_tier["small"]["outcome"] == "ranked"
    assert by_tier["frontier"]["mode"] == "live"
    assert by_tier["frontier"]["outcome"] == "proposal"


def test_pabx_restart_and_events(client: Any) -> None:  # type: ignore[no-untyped-def]
    cid, _ = _seed(client, name="PABX edge")
    r = client.post(
        "/demo/pabx-restart",
        json={"connector_id": cid, "redelivered": 12, "lost": 0, "duplicated": 0, "unique_keys": 12},
    )
    assert r.status_code == 200, r.text
    assert r.json()["payload"] == {"redelivered": 12, "lost": 0, "duplicated": 0, "unique_keys": 12}
    r = client.get(f"/demo/pabx-events?connector_id={cid}")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1 and rows[0]["kind"] == "restart"
    r = client.post("/demo/pabx-restart", json={"connector_id": "missing", "redelivered": 1, "lost": 0, "duplicated": 0})
    assert r.status_code == 404


def test_scheduler_records_edge_link_snapshot(client: Any, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/connectors",
        json={
            "name": "Edge", "base_url": "edge-relay://queue/x",
            "credential_ref": "EDGE_TOKEN", "target_entities": ["FolioCharge"],
            "runtime_target": {"kind": "edge"},
        },
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    import autobridge.control.routes.edge as edge_routes  # noqa: PLC0415

    monkeypatch.setattr(
        edge_routes, "_relay_get",
        lambda _path, _params: {"agents": [{"agent_id": "Edge", "link_up": True, "link_detail": "ok", "outbox_depth": 3, "inbox_depth": 0}]},
    )
    from autobridge.control.scheduler import run_scheduler_pass, snapshot_edge_links

    factory = _factory_for(tmp_path)
    with factory() as s:
        assert snapshot_edge_links(s) == 1
        s.commit()
    with factory() as s:
        rows = s.query(PabxLinkEvent).all()
        assert len(rows) == 1
        assert rows[0].connector_id == cid
        assert rows[0].kind == "link"
        assert rows[0].payload["link_up"] is True
        assert rows[0].payload["outbox_depth"] == 3
    # Relay down → missed snapshot, pass still runs.
    def _down(_path: str, _params: object) -> object:
        raise RuntimeError("down")

    monkeypatch.setattr(edge_routes, "_relay_get", _down)
    with factory() as s:
        assert snapshot_edge_links(s) == 0
    with factory() as s:
        out = run_scheduler_pass(s)
    assert out["link_snapshots"] == 0
