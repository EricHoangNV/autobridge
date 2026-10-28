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
    assert compute_data_readiness("Blocked", {}) == {"freshness": "stale", "completeness": "incomplete"}
    # Degraded with zero records -> stale
    assert compute_data_readiness("Degraded", {}, {"Reservation": 0}) == {"freshness": "stale", "completeness": "partial"}
    assert compute_data_readiness("Degraded", {}, {"Reservation": 5}) == {"freshness": "current", "completeness": "partial"}
    # Unknown outcome fallback
    assert compute_data_readiness("Unknown", {}) == {"freshness": "current", "completeness": "partial"}


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


def test_fingerprint_includes_pagination_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    from autobridge.engine.sync_run import _fingerprint_id
    from autobridge.runtime.interpreter import OperationResult

    spec = _spec_dict(
        {
            "strategy": "cursor",
            "cursor_param": "cursor",
            "next_path": "$.meta.next_cursor",
            "items_path": "$.data",
            "stop_when": ["null_cursor"],
        }
    )
    spec2 = _spec_dict(
        {
            "strategy": "offset",
            "offset_param": "offset",
            "limit_param": "limit",
            "next_path": "$.meta.next_cursor",
            "items_path": "$.data",
            "stop_when": ["empty_page"],
        }
    )
    results = {
        "list_reservations": OperationResult(
            operation="list_reservations", records=[{"reservation_id": "R1"}], pages_fetched=1, stop_reason="null_cursor"
        )
    }
    fp1 = _fingerprint_id(spec, results, {"Reservation": [{"reservation_id": "R1"}]})
    fp2 = _fingerprint_id(spec2, results, {"Reservation": [{"reservation_id": "R1"}]})
    assert fp1 != fp2


def test_fingerprint_keys_types_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    from autobridge.engine.sync_run import _fingerprint_id
    from autobridge.runtime.interpreter import OperationResult

    base_spec = _spec_dict(
        {
            "strategy": "cursor",
            "cursor_param": "cursor",
            "next_path": "$.meta.next_cursor",
            "items_path": "$.data",
            "stop_when": ["null_cursor"],
        }
    )
    # Disable keys and types via health.fingerprint
    spec_no_keys = dict(base_spec)
    spec_no_keys["health"] = {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {"keys": False, "types": False, "pagination_shape": True}}
    results = {
        "list_reservations": OperationResult(operation="list_reservations", records=[{"reservation_id": "R1"}], pages_fetched=1, stop_reason="null_cursor")
    }
    # Different keys should not affect fingerprint when disabled
    fp_a = _fingerprint_id(spec_no_keys, results, {"Reservation": [{"reservation_id": "R1"}]})
    fp_b = _fingerprint_id(spec_no_keys, results, {"Reservation": [{"reservation_id": "R99"}]})
    assert fp_a == fp_b
    # But enabled should differ
    fp_c = _fingerprint_id(base_spec, results, {"Reservation": [{"reservation_id": "R1"}]})
    fp_d = _fingerprint_id(base_spec, results, {"Reservation": [{"reservation_id": "R99"}]})
    assert fp_c != fp_d


def test_fingerprint_pages_different_changes_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    from autobridge.engine.sync_run import _fingerprint_id
    from autobridge.runtime.interpreter import OperationResult

    spec = _spec_dict(
        {
            "strategy": "cursor",
            "cursor_param": "cursor",
            "next_path": "$.meta.next_cursor",
            "items_path": "$.data",
            "stop_when": ["null_cursor"],
        }
    )
    results1 = {"list_reservations": OperationResult(operation="list_reservations", records=[{"reservation_id": "R1"}], pages_fetched=1, stop_reason="null_cursor")}
    results2 = {"list_reservations": OperationResult(operation="list_reservations", records=[{"reservation_id": "R1"}, {"reservation_id": "R2"}], pages_fetched=2, stop_reason="null_cursor")}
    fp1 = _fingerprint_id(spec, results1, {"Reservation": [{"reservation_id": "R1"}]})
    fp2 = _fingerprint_id(spec, results2, {"Reservation": [{"reservation_id": "R1"}, {"reservation_id": "R2"}]})
    assert fp1 != fp2


def test_fingerprint_with_decimal_and_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    from decimal import Decimal

    from autobridge.engine.sync_run import _fingerprint_id
    from autobridge.runtime.interpreter import OperationResult

    spec = _spec_dict(
        {
            "strategy": "cursor",
            "cursor_param": "cursor",
            "next_path": "$.meta.next_cursor",
            "items_path": "$.data",
            "stop_when": ["null_cursor"],
        }
    )
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    # Decimal field type should be captured
    fp = _fingerprint_id(spec, results, {"Reservation": [{"reservation_id": "R1", "total_amount": Decimal("10.00")}]})
    assert len(fp) == 16
    # Empty records
    fp2 = _fingerprint_id(spec, results, {"Reservation": []})
    assert len(fp2) == 16


def test_fingerprint_no_pagination_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    from autobridge.engine.sync_run import _fingerprint_id
    from autobridge.runtime.interpreter import OperationResult

    spec = _spec_dict(
        {
            "strategy": "cursor",
            "cursor_param": "cursor",
            "next_path": "$.meta.next_cursor",
            "items_path": "$.data",
            "stop_when": ["null_cursor"],
        }
    )
    spec["health"] = {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {"keys": True, "types": True, "pagination_shape": False}}
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[{"reservation_id": "R1"}], pages_fetched=1, stop_reason="null_cursor")}
    fp = _fingerprint_id(spec, results, {"Reservation": [{"reservation_id": "R1"}]})
    assert len(fp) == 16


def test_execute_sync_blocked_unknown_enum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    spec_dict = _spec_dict(pagination)
    # Add enum field that will block on unknown value
    spec_dict["entities"]["Reservation"]["fields"]["status"] = {
        "path": "$.status",
        "transform": [{"name": "enum_map", "args": {"map": {"CONFIRMED": "confirmed"}, "on_unknown": "block"}}],
    }
    pages = [{"data": [{"reservation_id": "R1", "status": "UNKNOWN_X"}], "meta": {"next_cursor": None}}]
    http = _fake_http(pages)
    from autobridge.engine.sync_run import execute_sync

    summary = execute_sync(spec_dict, http)
    assert summary["outcome"] == "Blocked"
    assert "unknown_enum" in summary["unmet_criteria"]
    assert summary["data_readiness"]["freshness"] == "stale"
    assert summary["data_readiness"]["completeness"] == "incomplete"
    assert summary["fingerprint_id"]
    assert len(summary["fingerprint_id"]) == 16


def test_execute_sync_data_readiness_complete_and_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    from autobridge.engine.sync_run import execute_sync

    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    spec_dict = _spec_dict(pagination)
    pages = [{"data": [{"reservation_id": "R1"}], "meta": {"next_cursor": None}}]
    summary = execute_sync(spec_dict, _fake_http(pages))
    assert summary["outcome"] == "Complete"
    assert summary["data_readiness"] == {"freshness": "current", "completeness": "complete"}
    # Degraded via invariant (no_loss)
    spec_dict2 = _spec_dict(pagination)
    spec_dict2["entities"]["Reservation"]["checks"] = [{"kind": "no_loss", "compare_to": "$.meta.total_count"}]
    pages2 = [{"data": [{"reservation_id": "R1"}], "meta": {"total_count": 5, "next_cursor": None}}]
    summary2 = execute_sync(spec_dict2, _fake_http(pages2))
    assert summary2["outcome"] == "Degraded"
    assert summary2["data_readiness"] == {"freshness": "current", "completeness": "partial"}


def test_fingerprint_config_defaults() -> None:
    from autobridge.engine.sync_run import _fingerprint_config

    assert _fingerprint_config({}) == {"keys": True, "types": True, "pagination_shape": True}
    assert _fingerprint_config({"health": {"fingerprint": {"keys": False}}}) == {"keys": False, "types": True, "pagination_shape": True}
    assert _fingerprint_config({"health": {"fingerprint": "bad"}}) == {"keys": True, "types": True, "pagination_shape": True}


def _hostile_http(budget_s: float = 10.0) -> object:
    """HttpClient whose vendor answers every page with a huge Retry-After (AR-2-1)."""

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(429, headers={"Retry-After": "99999"}, json={})

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    from autobridge.runtime.http import HttpClient
    from autobridge.spec.models import RetryPolicy

    return HttpClient(
        "https://example.com",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=3),
        max_total_delay_s=budget_s,
        sleep_fn=lambda _: None,
        client_factory=factory,
    )


def test_execute_sync_deadline_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """AR-2-1: a vendor stalling via Retry-After ends the run Blocked with a named criterion."""
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    summary = execute_sync(_spec_dict(pagination), _hostile_http())
    assert summary["outcome"] == "Blocked"
    assert summary["unmet_criteria"] == ["sync_deadline_exceeded"]
    assert summary["data_readiness"] == {"freshness": "stale", "completeness": "incomplete"}


def test_run_sync_handler_wires_delay_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """AR-2-1: the production sync path builds its client with the delay budget."""
    monkeypatch.setenv("TEST_TOKEN", "tok")
    from autobridge.engine.sync_run import DEFAULT_SYNC_MAX_TOTAL_DELAY_S

    assert DEFAULT_SYNC_MAX_TOTAL_DELAY_S > 0

    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    sessions = session_factory(engine)
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
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

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [], "meta": {"next_cursor": None}})  # noqa: ARG005
    )

    def default_factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    captured: dict = {}
    from autobridge.runtime import http as http_module

    real_client = http_module.HttpClient

    class SpyClient(real_client):  # type: ignore[valid-type, misc]
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("client_factory", default_factory)
            kwargs.setdefault("sleep_fn", lambda _: None)
            captured.update(kwargs)
            super().__init__(*args, **kwargs)

    # run_sync_handler does `from autobridge.runtime.http import HttpClient`
    # at call time, so this patch intercepts the default-client build.
    monkeypatch.setattr(http_module, "HttpClient", SpyClient)

    from autobridge.control.jobs import JobContext
    from autobridge.engine.sync_run import run_sync_handler

    with sessions() as s:
        ctx = JobContext(
            job_id="job-budget",
            kind="sync",
            run_id=None,
            payload={"release_id": release_id},
            attempt=1,
            session=s,
            worker_id="w1",
        )
        result = run_sync_handler(ctx)
        assert captured.get("max_total_delay_s") == DEFAULT_SYNC_MAX_TOTAL_DELAY_S
        assert result["outcome"] == "Complete"
