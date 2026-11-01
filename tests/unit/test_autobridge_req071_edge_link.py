"""REQ-071 — edge link recorded on the SyncRun; scheduler matches the live agent.

Covers, without any stack: ``parse_reconnects`` on heartbeat detail,
``RelayFeedReader.link_snapshot`` agent matching (id, name, first-agent
fallback like the PABX panel) and idempotency-key counts, the
``run_sync_handler`` edge wiring (snapshot merged into
``data_readiness["edge_link"]``, absent otherwise, never failing the
sync), and the scheduler link-event recorder matching the live agent
instead of ``link_up: false`` / ``agent_seen: false``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import create_engine

if TYPE_CHECKING:
    import pytest

from autobridge.control.models import (
    Connector,
    ConnectorRelease,
    PabxLinkEvent,
    SyncRun,
    init_db,
    session_factory,
    spec_signature,
)
from autobridge.discovery.bootstrap import build_draft_spec
from autobridge.runtime.feeds import RelayFeedReader, parse_reconnects

REPO_ROOT = Path(__file__).resolve().parents[2]
FIAS_MD = (REPO_ROOT / "simulators" / "docs" / "fias-pabx.md").read_text(encoding="utf-8")


def test_parse_reconnects() -> None:
    assert parse_reconnects("accepted=2 dropped=0") == 2
    assert parse_reconnects('accepted=11 dropped=1 layouts={"PS": ["RN"]}') == 11
    assert parse_reconnects("") == 0
    assert parse_reconnects(None) == 0
    assert parse_reconnects("up") == 0


class _FakeResp:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> Any:
        return self._payload


def _fake_client_class(agents: Any, postings: Any) -> Any:
    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def get(  # noqa: ARG002
            self, url: str, params: Any = None, headers: Any = None  # noqa: ARG002
        ) -> _FakeResp:
            if url.endswith("/v1/agents"):
                return _FakeResp(agents)
            if url.endswith("/v1/postings"):
                return _FakeResp(postings)
            raise AssertionError(f"unexpected url {url!r}")

    return _FakeClient


def _install_fake_relay(monkeypatch: pytest.MonkeyPatch, agents: Any, postings: Any) -> None:
    import httpx

    monkeypatch.setattr(httpx, "Client", _fake_client_class(agents, postings))


_AGENTS = {
    "agents": [
        {
            "agent_id": "grand-plaza-pabx",
            "link_up": True,
            "link_detail": "accepted=3 dropped=0",
            "outbox_depth": 0,
            "inbox_depth": 0,
        }
    ]
}
_POSTINGS = {
    "postings": [
        {"idempotency_key": "k-a", "record_type": "PS"},
        {"idempotency_key": "k-b", "record_type": "PS"},
        {"idempotency_key": "k-b", "record_type": "PS"},
    ]
}


def test_link_snapshot_matches_by_connector_name_not_agent_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_relay(monkeypatch, _AGENTS, _POSTINGS)
    reader = RelayFeedReader("http://relay:8000", "tok")
    snap = reader.link_snapshot(["some-connector-id", "PABX edge"])
    assert snap is not None
    assert snap["agent_id"] == "grand-plaza-pabx"
    assert snap["link_up"] is True
    assert snap["reconnects"] == 3
    assert snap["posting_keys_total"] == 3
    assert snap["posting_keys_unique"] == 2
    assert snap["posting_keys"] == ["k-a", "k-b"]
    assert snap["posting_keys_truncated"] is False


def test_link_snapshot_matches_by_connector_id(monkeypatch: pytest.MonkeyPatch) -> None:
    agents = {
        "agents": [
            {"agent_id": "cid-1", "link_up": False, "link_detail": "accepted=0 dropped=1"},
            {"agent_id": "other", "link_up": True, "link_detail": "accepted=9 dropped=0"},
        ]
    }
    _install_fake_relay(monkeypatch, agents, {"postings": []})
    reader = RelayFeedReader("http://relay:8000", "tok")
    snap = reader.link_snapshot(["cid-1", "PABX edge"])
    assert snap is not None
    assert snap["agent_id"] == "cid-1"
    assert snap["link_up"] is False
    assert snap["reconnects"] == 0


def test_link_snapshot_relay_down_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class _Down:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> _Down:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def get(self, *_args: Any, **_kwargs: Any) -> Any:
            raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "Client", _Down)
    reader = RelayFeedReader("http://relay:8000", "tok")
    assert reader.link_snapshot(["cid", "name"]) is None


def test_link_snapshot_unexpected_shape_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_relay(monkeypatch, {"agents": "nope"}, {"postings": []})
    reader = RelayFeedReader("http://relay:8000", "tok")
    assert reader.link_snapshot(["cid", "name"]) is None


def _fias_spec_dict() -> dict[str, Any]:
    result = build_draft_spec(
        FIAS_MD, source="simulators/docs/fias-pabx.md", connector_id="fiaspabx",
        spec_vocabulary="v3",
    )
    assert result.success, result.visible_errors
    return dict(result.draft_spec)


class _FakeEdgeFeed:
    """Scripted feed records plus a canned link snapshot (REQ-071)."""

    def __init__(self, by_record: dict[str, list[dict[str, Any]]], snapshot: Any) -> None:
        self.by_record = by_record
        self.snapshot = snapshot

    def read(self, feed: Any, _operation_name: str) -> list[dict[str, Any]]:
        rec = feed["record"] if isinstance(feed, dict) else feed.record
        return [dict(r) for r in self.by_record.get(rec, [])]

    def link_snapshot(self, candidates: list[str]) -> Any:
        assert candidates, "handler passes connector id + name candidates"
        return self.snapshot


def _seed_edge_connector(s: Any, spec_dict: dict[str, Any]) -> tuple[str, str]:
    conn = Connector(
        name="PABX edge",
        base_url="edge-relay://queue/fias-commands",
        credential_ref="EDGE_TOKEN",
        target_entities=["FolioCharge"],
        runtime_target={"kind": "edge"},
    )
    s.add(conn)
    s.commit()
    rel = ConnectorRelease(
        connector_id=conn.id,
        version=1,
        spec=spec_dict,
        signature=spec_signature(spec_dict),
    )
    s.add(rel)
    s.commit()
    return conn.id, rel.id


def test_run_sync_handler_records_edge_link(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.control.jobs import JobContext
    from autobridge.engine.sync_run import run_sync_handler

    snapshot = {
        "agent_id": "grand-plaza-pabx",
        "link_up": True,
        "reconnects": 3,
        "posting_keys_total": 2,
        "posting_keys_unique": 2,
        "posting_keys": ["k-a", "k-b"],
        "posting_keys_truncated": False,
    }
    import autobridge.runtime.feeds as feeds

    monkeypatch.setattr(
        feeds.RelayFeedReader, "from_env",
        classmethod(lambda _cls, _auth, _relay_url=None: _FakeEdgeFeed({"PS": [], "RE": []}, snapshot)),
    )
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    sessions = session_factory(engine)
    spec_dict = _fias_spec_dict()
    with sessions() as s:
        _, release_id = _seed_edge_connector(s, spec_dict)
        ctx = JobContext(
            job_id="job1", kind="sync", run_id=None,
            payload={"release_id": release_id}, attempt=1, session=s, worker_id="w1",
        )
        result = run_sync_handler(ctx)
        assert result["outcome"] == "Complete", result.get("unmet_criteria")
        s.commit()
        run = s.get(SyncRun, result["sync_run_id"])
        assert run is not None
        assert run.data_readiness["edge_link"] == snapshot
        assert run.data_readiness["freshness"] == "current"


def test_run_sync_handler_missing_snapshot_never_fails_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autobridge.runtime.feeds as feeds
    from autobridge.control.jobs import JobContext
    from autobridge.engine.sync_run import run_sync_handler

    monkeypatch.setattr(
        feeds.RelayFeedReader, "from_env",
        classmethod(lambda _cls, _auth, _relay_url=None: _FakeEdgeFeed({"PS": [], "RE": []}, None)),
    )
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    sessions = session_factory(engine)
    with sessions() as s:
        _, release_id = _seed_edge_connector(s, _fias_spec_dict())
        ctx = JobContext(
            job_id="job1", kind="sync", run_id=None,
            payload={"release_id": release_id}, attempt=1, session=s, worker_id="w1",
        )
        result = run_sync_handler(ctx)
        assert result["outcome"] == "Complete", result.get("unmet_criteria")
        s.commit()
        run = s.get(SyncRun, result["sync_run_id"])
        assert run is not None
        assert "edge_link" not in (run.data_readiness or {})


def test_run_sync_handler_non_edge_has_no_edge_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    from autobridge.control.jobs import JobContext
    from autobridge.engine.sync_run import run_sync_handler

    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    sessions = session_factory(engine)
    spec_dict = {
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
                "method": "GET", "path": "/reservations", "params": {},
                "pagination": {
                    "strategy": "cursor", "cursor_param": "cursor",
                    "next_path": "$.meta.next_cursor", "items_path": "$.data",
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
    import httpx as _httpx

    def _handler(request: _httpx.Request) -> _httpx.Response:  # noqa: ARG001
        return _httpx.Response(200, json={"data": [{"reservation_id": "R1"}], "meta": {"next_cursor": None}})

    def http_factory(_spec: dict) -> Any:
        from autobridge.runtime.http import HttpClient
        from autobridge.spec.models import RetryPolicy

        return HttpClient(
            "https://example.com",
            retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=3),
            client_factory=lambda **_kw: _httpx.Client(transport=_httpx.MockTransport(_handler)),
            sleep_fn=lambda _: None,
        )

    with sessions() as s:
        conn = Connector(
            name="Http", base_url="https://example.com", credential_ref="TEST_TOKEN",
            target_entities=["Reservation"], runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.commit()
        rel = ConnectorRelease(
            connector_id=conn.id, version=1, spec=spec_dict, signature=spec_signature(spec_dict),
        )
        s.add(rel)
        s.commit()
        ctx = JobContext(
            job_id="job1", kind="sync", run_id=None,
            payload={"release_id": rel.id}, attempt=1, session=s, worker_id="w1",
        )
        result = run_sync_handler(ctx, http_client_factory=http_factory)
        assert result["outcome"] == "Complete"
        s.commit()
        run = s.get(SyncRun, result["sync_run_id"])
        assert run is not None
        assert "edge_link" not in (run.data_readiness or {})


def test_scheduler_snapshot_matches_live_agent_not_connector_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autobridge.control.routes.edge as edge_routes
    from autobridge.control.scheduler import snapshot_edge_links

    monkeypatch.setattr(
        edge_routes, "_relay_get",
        lambda _path, _params: {"agents": [
            {
                "agent_id": "grand-plaza-pabx",
                "link_up": True,
                "link_detail": "accepted=3 dropped=0",
                "outbox_depth": 1,
                "inbox_depth": 0,
            }
        ]},
    )
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    sessions = session_factory(engine)
    with sessions() as s:
        conn = Connector(
            name="PABX edge",
            base_url="edge-relay://queue/fias-commands",
            credential_ref="EDGE_TOKEN",
            target_entities=["FolioCharge"],
            runtime_target={"kind": "edge"},
        )
        s.add(conn)
        s.commit()
        cid = conn.id
        assert snapshot_edge_links(s) == 1
        s.commit()
    with sessions() as s:
        rows = s.query(PabxLinkEvent).all()
        assert len(rows) == 1
        assert rows[0].connector_id == cid
        assert rows[0].payload["link_up"] is True
        assert rows[0].payload["agent_seen"] is True
