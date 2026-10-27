"""Canary monitor + DependencyRegistration pause signal (P1.W4.6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine

from autobridge.control.models import (
    Connector,
    DependencyRegistration,
    EvidenceRecord,
    RepairRun,
    init_db,
    session_factory,
)
from autobridge.engine.monitor import (
    RELEASE_ACTIVATED_EVENT,
    ensure_demo_consumer,
    get_canary_config,
    get_dependency_status,
    handle_release_activated,
    is_consumer_paused,
    parse_canary_interval,
    register_consumer,
    resume_consumer,
    run_canary,
    should_run_canary,
)
from autobridge.engine.releases import (
    approve_release,
    create_release,
    rollback_release,
)


def _session(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_engine(f"sqlite:///{tmp_path}/monitor.db")
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
            },
            "list_folios": {
                "method": "GET",
                "path": "/folios",
                "params": {},
                "pagination": {
                    "strategy": "cursor",
                    "cursor_param": "cursor",
                    "next_path": "$.meta.next_cursor",
                    "items_path": "$.data",
                    "stop_when": ["null_cursor"],
                },
            },
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {"reservation_id": {"path": "$.reservation_id"}},
            },
            "FolioCharge": {
                "from": "list_folios",
                "key": "$.charge_id",
                "fields": {"charge_id": {"path": "$.charge_id"}},
            },
        },
        "capabilities": {"read": ["Reservation", "FolioCharge"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations", "max_pages": 1}, "fingerprint": {}},
    }


def _spec_with_enum() -> dict[str, Any]:
    spec = _minimal_spec()
    spec["entities"]["Reservation"]["fields"]["status"] = {
        "path": "$.status",
        "transform": [{"name": "enum_map", "args": {"map": {"ok": "confirmed"}, "on_unknown": "block"}}],
    }
    return spec


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


def _failing_http(exc: Exception):  # type: ignore[no-untyped-def]
    def factory(**kwargs):  # type: ignore[no-untyped-def]  # noqa: ARG001
        raise exc

    from autobridge.runtime.http import HttpClient
    from autobridge.spec.models import RetryPolicy

    class _FailingClient(HttpClient):  # type: ignore[no-untyped-def]
        def get(self, path, params=None):  # type: ignore[no-untyped-def]  # noqa: ARG002
            raise exc

    return _FailingClient(
        "https://example.com",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=3),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )


# ---------------------------------------------------------------------------
# Interval parsing
# ---------------------------------------------------------------------------


def test_parse_canary_interval_variants() -> None:
    assert parse_canary_interval("60s") == 60
    assert parse_canary_interval("5m") == 300
    assert parse_canary_interval("1h") == 3600
    assert parse_canary_interval("0s") == 0
    assert parse_canary_interval(" 60s ") == 60


def test_parse_canary_interval_invalid() -> None:
    with pytest.raises(ValueError, match="canary.every"):
        parse_canary_interval("hourly")
    with pytest.raises(ValueError, match="canary.every"):
        parse_canary_interval("5d")
    with pytest.raises(ValueError, match="canary.every"):
        parse_canary_interval("")
    with pytest.raises(ValueError, match="canary.every"):
        parse_canary_interval(60)  # type: ignore[arg-type]


def test_should_run_canary_no_last() -> None:
    now = datetime.now(UTC)
    assert should_run_canary(None, now, every="60s") is True


def test_should_run_canary_elapsed() -> None:
    now = datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)
    last = now - timedelta(seconds=70)
    assert should_run_canary(last, now, every="60s") is True
    last2 = now - timedelta(seconds=30)
    assert should_run_canary(last2, now, every="60s") is False
    # Exactly at boundary -> due
    last3 = now - timedelta(seconds=60)
    assert should_run_canary(last3, now, every="60s") is True
    # Naive treated as UTC
    last_naive = datetime(2026, 10, 27, 11, 58, 50)
    now_naive = datetime(2026, 10, 27, 12, 0, 0)
    assert should_run_canary(last_naive, now_naive, every="60s") is True
    # Default now (use explicit now to avoid system time drift)
    assert should_run_canary(now - timedelta(hours=1), now, every="60s") is True


def test_get_canary_config() -> None:
    spec = _minimal_spec()
    cfg = get_canary_config(spec)
    assert cfg["every"] == "60s"
    assert cfg["operation"] == "list_reservations"
    assert cfg["max_pages"] == 1
    # Custom max_pages
    spec["health"]["canary"]["max_pages"] = 3
    assert get_canary_config(spec)["max_pages"] == 3


def test_get_canary_config_missing() -> None:
    with pytest.raises(ValueError, match="health.canary"):
        get_canary_config({})
    with pytest.raises(ValueError, match="health.canary"):
        get_canary_config({"health": {}})
    with pytest.raises(ValueError, match="requires"):
        get_canary_config({"health": {"canary": {"every": "60s"}}})
    with pytest.raises(ValueError, match="requires"):
        get_canary_config({"health": {"canary": {"operation": "x"}}})


# ---------------------------------------------------------------------------
# run_canary
# ---------------------------------------------------------------------------


def test_run_canary_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    spec = _minimal_spec()
    pages = [{"data": [{"reservation_id": "R1"}, {"reservation_id": "R2"}], "meta": {"next_cursor": None}}]
    summary = run_canary(spec, _fake_http(pages))
    assert summary["outcome"] == "Complete"
    assert summary["operation"] == "list_reservations"
    assert summary["records_observed"]["Reservation"] == 2
    assert summary["fingerprint_id"]
    assert summary["data_readiness"]["freshness"] == "current"
    # Only canary operation results
    assert "list_reservations" in summary["operation_results"]
    # Non-canary op not executed (max efficiency)
    assert "list_folios" not in summary["operation_results"]
    # No secret in summary
    import json

    assert "tok" not in json.dumps(summary, default=str)


def test_run_canary_max_pages_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    spec = _minimal_spec()
    # Two pages available but canary capped to 1
    pages = [
        {"data": [{"reservation_id": "R1"}], "meta": {"next_cursor": "c1"}},
        {"data": [{"reservation_id": "R2"}], "meta": {"next_cursor": None}},
    ]
    summary = run_canary(spec, _fake_http(pages), max_pages_override=1)
    assert summary["operation_results"]["list_reservations"].pages_fetched == 1
    assert summary["records_observed"]["Reservation"] == 1


def test_run_canary_blocked_unknown_enum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    spec = _spec_with_enum()
    pages = [{"data": [{"reservation_id": "R1", "status": "H1"}], "meta": {"next_cursor": None}}]
    summary = run_canary(spec, _fake_http(pages))
    # H1 unknown for map ok->confirmed -> Blocked
    assert summary["outcome"] == "Blocked"
    assert any("unknown_enum" in c for c in summary["unmet_criteria"])


def test_run_canary_http_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    spec = _minimal_spec()
    exc = RuntimeError("network down")
    summary = run_canary(spec, _failing_http(exc))
    assert summary["outcome"] == "Blocked"
    assert "http_error" in summary["unmet_criteria"]
    assert summary["operation"] == "list_reservations"


def test_run_canary_empty_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    spec = _minimal_spec()
    pages = [{"data": [], "meta": {"next_cursor": None}}]
    summary = run_canary(spec, _fake_http(pages))
    # Empty still Complete (no invariant fails for this spec)
    assert summary["outcome"] in {"Complete", "Degraded"}
    assert summary["records_observed"]["Reservation"] == 0


def test_run_canary_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    spec = _minimal_spec()
    # Add checkpoint to operation
    spec["operations"]["list_reservations"]["checkpoint"] = {"field": "$.updated_at", "mode": "max"}
    spec["entities"]["Reservation"]["fields"]["updated_at"] = {"path": "$.updated_at"}
    pages = [
        {"data": [{"reservation_id": "R1", "updated_at": "2026-10-26T10:00:00Z"}], "meta": {"next_cursor": None}},
    ]
    summary = run_canary(spec, _fake_http(pages))
    assert summary["outcome"] == "Complete"


def test_run_canary_signals_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Canary summary can be fed to signals_from_canary."""
    monkeypatch.setenv("TEST_TOKEN", "tok")
    spec = _minimal_spec()
    pages = [{"data": [{"reservation_id": "R1"}], "meta": {"next_cursor": None}}]
    summary = run_canary(spec, _fake_http(pages))
    from autobridge.drift.signals import signals_from_canary

    sigs = signals_from_canary(summary, spec=spec)
    # Complete canary produces no signals
    assert sigs == []
    # Blocked canary produces canary_failed
    spec2 = _spec_with_enum()
    summary2 = run_canary(spec2, _fake_http([{"data": [{"reservation_id": "R1", "status": "H1"}], "meta": {"next_cursor": None}}]))
    sigs2 = signals_from_canary(summary2, spec=spec2)
    assert any(s.kind == "canary_failed" for s in sigs2)


# ---------------------------------------------------------------------------
# DependencyRegistration helpers
# ---------------------------------------------------------------------------


def test_register_consumer_idempotent(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        r1 = register_consumer(s, connector_id=conn.id, consumer_id="c1", entity="Reservation", policy="pause_on_release_change")
        s.commit()
        assert r1.paused is False
        r2 = register_consumer(s, connector_id=conn.id, consumer_id="c1", entity="Reservation", policy="pause_on_release_change")
        s.commit()
        assert r1.id == r2.id
        assert s.query(DependencyRegistration).count() == 1


def test_ensure_demo_consumer(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        demo = ensure_demo_consumer(s, conn.id)
        s.commit()
        assert demo.consumer_id == "demo-dashboard"
        assert demo.entity == "Reservation"
        assert demo.policy == "pause_on_release_change"
        assert demo.paused is False
        # Idempotent
        demo2 = ensure_demo_consumer(s, conn.id)
        s.commit()
        assert demo.id == demo2.id
        # Custom
        demo3 = ensure_demo_consumer(s, conn.id, consumer_id="other", entity="FolioCharge", policy="notify")
        s.commit()
        assert demo3.consumer_id == "other"
        assert s.query(DependencyRegistration).count() == 2


def test_is_paused_and_resume(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        assert is_consumer_paused(s, connector_id=conn.id, consumer_id="c1", entity="Reservation") is False
        register_consumer(s, connector_id=conn.id, consumer_id="c1", entity="Reservation", policy="pause_on_release_change")
        s.commit()
        assert is_consumer_paused(s, connector_id=conn.id, consumer_id="c1", entity="Reservation") is False
        # Pause via handle_release_activated
        rel = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        handle_release_activated(s, connector_id=conn.id, new_release_id=rel.id)
        s.commit()
        assert is_consumer_paused(s, connector_id=conn.id, consumer_id="c1", entity="Reservation") is True
        # Resume
        resumed = resume_consumer(s, connector_id=conn.id, consumer_id="c1", entity="Reservation")
        s.commit()
        assert resumed is not None
        assert resumed.paused is False
        assert is_consumer_paused(s, connector_id=conn.id, consumer_id="c1", entity="Reservation") is False
        # Resume missing returns None
        assert resume_consumer(s, connector_id=conn.id, consumer_id="nope", entity="Reservation") is None


def test_handle_release_activated_emits_and_pauses(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        reg_pause = register_consumer(s, connector_id=conn.id, consumer_id="pause-consumer", entity="Reservation", policy="pause_on_release_change")
        reg_notify = register_consumer(s, connector_id=conn.id, consumer_id="notify-consumer", entity="Reservation", policy="notify")
        s.commit()
        assert reg_pause.paused is False
        assert reg_notify.paused is False
        rel = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        out = handle_release_activated(s, connector_id=conn.id, new_release_id=rel.id, actor="alice")
        s.commit()
        assert out["event"] == RELEASE_ACTIVATED_EVENT
        assert "pause-consumer" in out["paused"]
        assert "notify-consumer" not in out["paused"]
        s.refresh(reg_pause)
        s.refresh(reg_notify)
        assert reg_pause.paused is True
        assert reg_notify.paused is False
        # EvidenceRecord emitted
        ev = s.get(EvidenceRecord, out["evidence_id"])
        assert ev is not None
        assert ev.kind == "decision"
        assert ev.payload["event"] == RELEASE_ACTIVATED_EVENT
        assert ev.payload["connector_id"] == conn.id
        assert ev.payload["release_id"] == rel.id
        assert ev.payload["actor"] == "alice"
        assert "pause-consumer" in ev.payload["paused_consumers"]
        assert "paused_consumers" in ev.payload
        # No secret value in payload
        assert "tok" not in str(ev.payload)


def test_handle_release_activated_no_consumers(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        rel = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        out = handle_release_activated(s, connector_id=conn.id, new_release_id=rel.id)
        s.commit()
        assert out["event"] == RELEASE_ACTIVATED_EVENT
        assert out["paused"] == []
        ev = s.get(EvidenceRecord, out["evidence_id"])
        assert ev is not None
        assert ev.payload["paused_consumers"] == []


def test_handle_release_activated_already_paused(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        reg = register_consumer(s, connector_id=conn.id, consumer_id="c1", entity="Reservation", policy="pause_on_release_change")
        s.commit()
        reg.paused = True
        s.commit()
        rel = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        out = handle_release_activated(s, connector_id=conn.id, new_release_id=rel.id)
        s.commit()
        # Still reports as affected (for trace visibility)
        assert "c1" in out["paused"]
        s.refresh(reg)
        assert reg.paused is True


def test_get_dependency_status(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        register_consumer(s, connector_id=conn.id, consumer_id="c1", entity="Reservation", policy="pause_on_release_change")
        register_consumer(s, connector_id=conn.id, consumer_id="c2", entity="FolioCharge", policy="notify")
        s.commit()
        status = get_dependency_status(s, conn.id)
        assert len(status) == 2
        by_id = {x["consumer_id"]: x for x in status}
        assert by_id["c1"]["policy"] == "pause_on_release_change"
        assert by_id["c1"]["paused"] is False
        assert by_id["c2"]["policy"] == "notify"
        assert s.query(DependencyRegistration).count() == 2
        assert get_dependency_status(s, "nonexistent") == []


# ---------------------------------------------------------------------------
# Activation integration: approve/rollback pause demo consumer
# ---------------------------------------------------------------------------


def test_approve_activates_and_pauses_demo_consumer(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        # Demo registers one dummy consumer
        demo = ensure_demo_consumer(s, conn.id)
        s.commit()
        assert demo.paused is False
        spec = _minimal_spec()
        r1 = create_release(s, connector_id=conn.id, spec=spec)
        s.commit()
        # Manually activate r1 first without pause? Use _activate? Instead approve flow:
        r2 = create_release(s, connector_id=conn.id, spec=spec, parent_release_id=r1.id)
        s.commit()
        rr = _repair_run(s)
        # No active yet, approve r2 -> should pause demo
        approval = approve_release(s, repair_run_id=rr.id, actor="alice", from_release_id=None, to_release_id=r2.id)
        s.commit()
        assert approval.decision == "approve"
        s.refresh(demo)
        assert demo.paused is True
        # Evidence emitted for release.activated
        evs = s.query(EvidenceRecord).all()
        activated = [e for e in evs if e.payload.get("event") == RELEASE_ACTIVATED_EVENT]
        assert len(activated) >= 1
        assert activated[0].payload["release_id"] == r2.id
        assert "demo-dashboard" in activated[0].payload["paused_consumers"]
        # Check no secret leak
        import json

        assert "tok" not in json.dumps([e.payload for e in activated], default=str)
        # Connector active flipped
        s.refresh(conn)
        assert conn.active_release_id == r2.id


def test_rollback_pauses_and_emits(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        demo = ensure_demo_consumer(s, conn.id)
        s.commit()
        spec = _minimal_spec()
        r1 = create_release(s, connector_id=conn.id, spec=spec)
        s.commit()
        conn.active_release_id = r1.id
        s.commit()
        # Create v2 and approve to activate (pauses)
        r2 = create_release(s, connector_id=conn.id, spec=spec, parent_release_id=r1.id)
        s.commit()
        rr1 = _repair_run(s)
        approve_release(s, repair_run_id=rr1.id, actor="alice", from_release_id=r1.id, to_release_id=r2.id)
        s.commit()
        s.refresh(demo)
        assert demo.paused is True
        # Resume so we can test rollback pauses again (or already paused remains)
        resume_consumer(s, connector_id=conn.id, consumer_id="demo-dashboard", entity="Reservation")
        s.commit()
        s.refresh(demo)
        assert demo.paused is False
        # Rollback to r1 should pause again
        rr2 = _repair_run(s)
        new_rel, approval = rollback_release(s, connector_id=conn.id, target_release_id=r1.id, actor="bob", repair_run_id=rr2.id)
        s.commit()
        assert approval.decision == "rollback"
        s.refresh(demo)
        assert demo.paused is True
        # Evidence for rollback activation
        evs = [e for e in s.query(EvidenceRecord).all() if e.payload.get("event") == RELEASE_ACTIVATED_EVENT and e.payload.get("release_id") == new_rel.id]
        assert len(evs) == 1
        assert evs[0].payload["actor"] == "bob"


def test_reject_does_not_pause(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        demo = ensure_demo_consumer(s, conn.id)
        s.commit()
        spec = _minimal_spec()
        r1 = create_release(s, connector_id=conn.id, spec=spec)
        s.commit()
        from autobridge.engine.releases import reject_release

        rr = _repair_run(s)
        reject_release(s, repair_run_id=rr.id, actor="alice", from_release_id=None, to_release_id=r1.id)
        s.commit()
        s.refresh(demo)
        assert demo.paused is False
        # No release.activated emitted on reject
        evs = [e for e in s.query(EvidenceRecord).all() if e.payload.get("event") == RELEASE_ACTIVATED_EVENT]
        assert len(evs) == 0


def test_notify_policy_never_pauses(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        reg = register_consumer(s, connector_id=conn.id, consumer_id="notify-only", entity="Reservation", policy="notify")
        s.commit()
        spec = _minimal_spec()
        r1 = create_release(s, connector_id=conn.id, spec=spec)
        s.commit()
        r2 = create_release(s, connector_id=conn.id, spec=spec, parent_release_id=r1.id)
        s.commit()
        rr = _repair_run(s)
        approve_release(s, repair_run_id=rr.id, actor="alice", from_release_id=r1.id, to_release_id=r2.id)
        s.commit()
        s.refresh(reg)
        assert reg.paused is False
        # Still emitted but paused empty
        evs = [e for e in s.query(EvidenceRecord).all() if e.payload.get("event") == RELEASE_ACTIVATED_EVENT]
        assert len(evs) == 1
        assert evs[0].payload["paused_consumers"] == []


def test_multiple_consumers_selective_pause(tmp_path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        r_pause = register_consumer(s, connector_id=conn.id, consumer_id="p1", entity="Reservation", policy="pause_on_release_change")
        r_pause2 = register_consumer(s, connector_id=conn.id, consumer_id="p2", entity="FolioCharge", policy="pause_on_release_change")
        r_notify = register_consumer(s, connector_id=conn.id, consumer_id="n1", entity="Reservation", policy="notify")
        s.commit()
        rel = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        out = handle_release_activated(s, connector_id=conn.id, new_release_id=rel.id)
        s.commit()
        assert set(out["paused"]) == {"p1", "p2"}
        for reg in (r_pause, r_pause2):
            s.refresh(reg)
            assert reg.paused is True
        s.refresh(r_notify)
        assert r_notify.paused is False


def test_run_canary_respects_spec_canary_definition(monkeypatch: pytest.MonkeyPatch) -> None:
    """Canary uses health.canary.operation and max_pages from spec."""
    monkeypatch.setenv("TEST_TOKEN", "tok")
    spec = _minimal_spec()
    spec["health"]["canary"]["operation"] = "list_folios"
    spec["health"]["canary"]["max_pages"] = 1
    # Only folio data present
    pages = [{"data": [{"charge_id": "C1"}], "meta": {"next_cursor": None}}]
    summary = run_canary(spec, _fake_http(pages))
    assert summary["operation"] == "list_folios"
    assert summary["records_observed"]["FolioCharge"] == 1
    assert "Reservation" not in summary["records_observed"] or summary["records_observed"]["Reservation"] == 0


def test_canary_interval_various_units() -> None:
    assert parse_canary_interval("1h") == 3600
    assert parse_canary_interval("10m") == 600
    assert parse_canary_interval("120s") == 120
    with pytest.raises(ValueError):
        parse_canary_interval("5")
    with pytest.raises(ValueError):
        parse_canary_interval("m5")


def test_should_run_canary_default_now() -> None:
    # Covers branch where now is None (line 56)
    past = datetime.now(UTC) - timedelta(seconds=120)
    assert should_run_canary(past, every="60s") is True
    recent = datetime.now(UTC) - timedelta(seconds=10)
    # May be flaky if clock crosses, but 10s < 60s so should be False
    assert should_run_canary(recent, every="60s") is False


def test_run_canary_no_loss_invariant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Covers compare_to extraction and invariant_failure path."""
    monkeypatch.setenv("TEST_TOKEN", "tok")
    spec = _minimal_spec()
    spec["entities"]["Reservation"]["checks"] = [{"kind": "no_loss", "compare_to": "$.meta.total_count"}]
    # Raw page claims 10 but only 1 record returned -> no_loss fails -> Degraded
    pages = [{"data": [{"reservation_id": "R1"}], "meta": {"total_count": 10, "next_cursor": None}}]
    summary = run_canary(spec, _fake_http(pages))
    assert summary["outcome"] == "Degraded"
    assert "no_loss" in summary["unmet_criteria"]


def test_run_canary_unknown_enum_without_field_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Covers blocked without field else branch via monkeypatch."""
    monkeypatch.setenv("TEST_TOKEN", "tok")
    spec = _minimal_spec()
    # monkeypatch mapping to return blocked without field
    # Patch via runtime.mapping module used inside run_canary
    from unittest.mock import patch

    def fake_map(raw_records, ent_spec, source_spec=None, entity_name=None):  # type: ignore[no-untyped-def] # noqa: ARG001
        # Return one blocked without field
        return [], [{"blocked": True}]  # no field key -> hits line 190-191

    with patch("autobridge.runtime.mapping.map_records_to_entities", side_effect=fake_map):
        # Need to patch the import path inside monitor (it imports at runtime)
        # run_canary imports inside function, so patching runtime.mapping works
        pages = [{"data": [{"reservation_id": "R1"}], "meta": {"next_cursor": None}}]
        summary = run_canary(spec, _fake_http(pages))
        assert summary["outcome"] == "Blocked"
        assert "unknown_enum" in summary["unmet_criteria"]
