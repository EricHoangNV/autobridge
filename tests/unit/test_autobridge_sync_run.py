"""Tests for SyncRun outcome determination (P1.W3.3 / P1.W3.5)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import create_engine

from autobridge.control.models import (
    Connector,
    ConnectorRelease,
    init_db,
    session_factory,
    spec_signature,
)
from autobridge.engine.sync_run import (
    compute_data_readiness,
    determine_outcome,
    execute_sync,
)
from autobridge.runtime.interpreter import OperationResult


def test_determine_outcome_complete() -> None:
    results = {
        "op": OperationResult(operation="list_reservations", records=[{"id": 1}], pages_fetched=1, stop_reason="null_cursor")
    }
    outcome, unmet = determine_outcome(results)
    assert outcome == "Complete"
    assert unmet == []


def test_determine_outcome_repeated_cursor_degraded() -> None:
    results = {
        "op": OperationResult(
            operation="list_reservations",
            records=[{"id": 1}],
            pages_fetched=2,
            repeated_cursor=True,
            stop_reason="repeated_cursor",
        )
    }
    outcome, unmet = determine_outcome(results)
    assert outcome == "Degraded"
    assert "no_loss" in unmet


def test_determine_outcome_repeated_via_stop_reason() -> None:
    results = {
        "op": OperationResult(
            operation="list_reservations",
            records=[{"id": 1}],
            pages_fetched=2,
            repeated_cursor=False,
            stop_reason="repeated_cursor",
        )
    }
    outcome, unmet = determine_outcome(results)
    assert outcome == "Degraded"
    assert unmet == ["no_loss"]


def test_determine_outcome_invariant() -> None:
    results = {
        "op": OperationResult(operation="op", records=[], stop_reason="null_cursor"),
    }
    outcome, unmet = determine_outcome(results, invariant_failures=["money_invariant"])
    assert outcome == "Degraded"
    assert "money_invariant" in unmet


def test_determine_outcome_repeated_plus_invariant_no_dup() -> None:
    results = {
        "op": OperationResult(operation="op", records=[], repeated_cursor=True, stop_reason="repeated_cursor"),
    }
    outcome, unmet = determine_outcome(results, invariant_failures=["no_loss"])
    # no_loss should not duplicate
    assert unmet.count("no_loss") == 1
    assert outcome == "Degraded"


def test_compute_data_readiness() -> None:
    assert compute_data_readiness("Complete", {}) == {"freshness": "current", "completeness": "complete"}
    assert compute_data_readiness("Degraded", {}) == {"freshness": "current", "completeness": "partial"}
    assert compute_data_readiness("Blocked", {}) == {"freshness": "current", "completeness": "partial"}


def _fake_http(pages: list[dict]) -> httpx.MockTransport:
    idx = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        if idx["i"] >= len(pages):
            return httpx.Response(200, json={"data": [], "meta": {"next_cursor": None}})
        body = pages[idx["i"]]
        idx["i"] += 1
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    from autobridge.runtime.http import HttpClient
    from autobridge.spec.models import RetryPolicy

    return HttpClient(
        "https://example.com",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=3),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )


def _spec_dict(pagination: dict) -> dict:
    return {
        "spec_version": 1,
        "connector": {"id": "testconn", "vendor": "Test", "runtime_target": "cloud"},
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
                "pagination": pagination,
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


def test_execute_sync_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    spec_dict = _spec_dict(pagination)
    pages = [
        {"data": [{"reservation_id": "R1"}], "meta": {"next_cursor": None}},
    ]
    http = _fake_http(pages)
    summary = execute_sync(spec_dict, http)
    assert summary["outcome"] == "Complete"
    assert summary["unmet_criteria"] == []
    assert summary["data_readiness"]["completeness"] == "complete"
    assert summary["fingerprint_id"]
    assert summary["records_observed"] == {"Reservation": 1}


def test_execute_sync_degraded_repeated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["repeated_cursor"],
    }
    spec_dict = _spec_dict(pagination)
    pages = [
        {"data": [{"reservation_id": "R1"}], "meta": {"next_cursor": "abc"}},
        {"data": [{"reservation_id": "R2"}], "meta": {"next_cursor": "abc"}},
    ]
    http = _fake_http(pages)
    summary = execute_sync(spec_dict, http)
    assert summary["outcome"] == "Degraded"
    assert "no_loss" in summary["unmet_criteria"]
    assert summary["data_readiness"]["completeness"] == "partial"


def test_execute_sync_fingerprint_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    spec_dict = _spec_dict(pagination)
    pages1 = [{"data": [{"reservation_id": "R1"}], "meta": {"next_cursor": None}}]
    pages2 = [{"data": [{"reservation_id": "R1"}], "meta": {"next_cursor": None}}]
    summary1 = execute_sync(spec_dict, _fake_http(pages1))
    summary2 = execute_sync(spec_dict, _fake_http(pages2))
    assert summary1["fingerprint_id"] == summary2["fingerprint_id"]


def test_create_sync_run_row_and_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    sessions = session_factory(engine)

    # Create connector + release with repeated_cursor spec
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["repeated_cursor"],
    }
    spec_dict = _spec_dict(pagination)

    with sessions() as s:
        conn = Connector(
            name="Test",
            base_url="https://example.com",
            credential_ref="TEST_TOKEN",
            target_entities=["Reservation"],
            runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.commit()
        conn_id = conn.id
        rel = ConnectorRelease(
            connector_id=conn_id,
            version=1,
            spec=spec_dict,
            signature=spec_signature(spec_dict),
        )
        s.add(rel)
        s.commit()
        release_id = rel.id

    # Run via handler factory
    pages = [
        {"data": [{"reservation_id": "R1"}], "meta": {"next_cursor": "abc"}},
        {"data": [{"reservation_id": "R2"}], "meta": {"next_cursor": "abc"}},
    ]

    def http_factory(_spec: dict) -> httpx.Client:
        return _fake_http(pages)

    from autobridge.control.jobs import JobContext
    from autobridge.engine.sync_run import run_sync_handler

    with sessions() as s:
        # Simulate job context
        ctx = JobContext(
            job_id="job1",
            kind="sync",
            run_id=None,
            payload={"release_id": release_id},
            attempt=1,
            session=s,
            worker_id="w1",
        )
        result = run_sync_handler(ctx, http_client_factory=http_factory)
        assert result["outcome"] == "Degraded"
        assert "no_loss" in result["unmet_criteria"]
        assert result["fingerprint_id"]
        s.commit()
        # Verify row persisted
        from autobridge.control.models import SyncRun

        run = s.get(SyncRun, result["sync_run_id"])
        assert run is not None
        assert run.outcome == "Degraded"
        assert run.fingerprint_id == result["fingerprint_id"]


def test_run_sync_handler_missing_release() -> None:
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    sessions = session_factory(engine)
    from autobridge.control.jobs import JobContext
    from autobridge.engine.sync_run import run_sync_handler

    with sessions() as s:
        ctx = JobContext(job_id="j", kind="sync", run_id=None, payload={}, attempt=1, session=s, worker_id="w")
        with pytest.raises(ValueError, match="needs 'release_id'"):
            run_sync_handler(ctx)


def test_run_sync_handler_unknown_release() -> None:
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    sessions = session_factory(engine)
    from autobridge.control.jobs import JobContext
    from autobridge.engine.sync_run import run_sync_handler

    with sessions() as s:
        ctx = JobContext(job_id="j", kind="sync", run_id=None, payload={"release_id": "nope"}, attempt=1, session=s, worker_id="w")
        with pytest.raises(ValueError, match="not found"):
            run_sync_handler(ctx)
