"""AR-2-2: build route must not fetch internal URLs or read local files."""

from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from autobridge.control.app import app
from autobridge.control.deps import get_db
from autobridge.control.jobs import JobRunner
from autobridge.control.models import Base, BuildRun, Connector
from autobridge.control.routes.builds import _docs_url_resolves_internal, validate_docs_url
from autobridge.engine.build_run import run_build_handler


@pytest.fixture(autouse=True)
def _no_live_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """The route layer never does real DNS in these tests (unit hermeticity).

    ``validate_docs_url`` resolves public hosts in a worker thread; on macOS
    that initialises the system resolver off the main thread and poisons
    every later forked child (REQ-022 DNS-hermetic fix). The denylist and
    literal-IP checks under test do not need the network, so resolve every
    host as public here; ``_docs_url_resolves_internal`` itself is covered
    with a directly-mocked ``socket.getaddrinfo`` below.
    """
    from autobridge.control.routes import builds

    monkeypatch.setattr(builds, "_docs_url_resolves_internal", lambda _host, _port: False)


def _client_and_factory(tmp_path: pathlib.Path) -> tuple[TestClient, object, str]:
    engine = create_engine(f"sqlite:///{tmp_path}/docs_guard.db")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

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
    with factory() as s:
        conn = Connector(
            name="GuardTest",
            base_url="http://example.com",
            credential_ref="GUARD_API_KEY",
            target_entities=[],
            runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.commit()
        cid = conn.id
    return TestClient(app), factory, cid


def teardown_function() -> None:
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "url",
    [
        "http://verifier:8000/healthz",
        "http://verifier:8000/verify",
        "http://control:8000/healthz",
        "http://sim-harbor:8000/docs",
        "http://localhost:8104/healthz",
        "http://127.0.0.1:8104/healthz",
        "http://10.0.0.5/docs.md",
        "http://192.168.1.10/docs.md",
        "http://[::1]/docs.md",
        "http://host.docker.internal/docs.md",
    ],
)
def test_docs_url_internal_targets_rejected(tmp_path: pathlib.Path, url: str) -> None:
    client, _factory, cid = _client_and_factory(tmp_path)
    r = client.post(f"/connectors/{cid}/build", json={"docs_url": url})
    assert r.status_code == 422, (url, r.text)
    assert "not allowed" in r.text or "scheme" in r.text


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://example.com/docs.md", "gopher://example.com/x"]
)
def test_docs_url_non_http_scheme_rejected_without_fetch(tmp_path: pathlib.Path, url: str) -> None:
    from unittest.mock import patch

    client, _factory, cid = _client_and_factory(tmp_path)
    with patch("httpx.Client", side_effect=AssertionError("must not fetch")):
        r = client.post(f"/connectors/{cid}/build", json={"docs_url": url})
    assert r.status_code == 422, (url, r.text)
    assert "scheme" in r.text


def test_validate_docs_url_unit() -> None:
    import fastapi.exceptions

    for bad in (
        "http://verifier:8000/healthz",
        "http://sim-atlas:8000/x",
        "file:///etc/hosts",
        "http://169.254.169.254/",
    ):
        with pytest.raises(fastapi.exceptions.HTTPException) as exc:
            validate_docs_url(bad)
        assert exc.value.status_code == 422
    # Public https host passes validation (fetch itself is mocked elsewhere).
    validate_docs_url("https://example.com/docs.md")


def test_docs_url_resolves_internal_classifies_ips(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolver check without the network: classify mocked ``getaddrinfo`` results.

    Keeps coverage of the fail-open DNS probe after the route-level mock
    above removed its only real-network caller in this file.
    """
    import socket as socket_mod

    def _infos(*ips: str) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [(socket_mod.AF_INET, socket_mod.SOCK_STREAM, 6, "", (ip, 80)) for ip in ips]

    monkeypatch.setattr(socket_mod, "getaddrinfo", lambda *_args: _infos("93.184.216.34"))
    assert _docs_url_resolves_internal("example.com", 443) is False
    monkeypatch.setattr(socket_mod, "getaddrinfo", lambda *_args: _infos("10.0.0.5"))
    assert _docs_url_resolves_internal("example.com", 80) is True
    monkeypatch.setattr(socket_mod, "getaddrinfo", lambda *_args: _infos("not-an-ip"))
    assert _docs_url_resolves_internal("example.com", 80) is True

    def _offline(*_args: object) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        raise socket_mod.gaierror("offline hermetic test")

    monkeypatch.setattr(socket_mod, "getaddrinfo", _offline)
    assert _docs_url_resolves_internal("example.com", 80) is False


def test_fetch_error_redacts_userinfo(tmp_path: pathlib.Path) -> None:
    from unittest.mock import patch

    from autobridge.control.routes.builds import _redact_url

    assert _redact_url("http://user:s3cret@example.com/docs.md") == "http://example.com/docs.md"
    assert _redact_url("https://example.com/docs.md") == "https://example.com/docs.md"
    client, _factory, cid = _client_and_factory(tmp_path)
    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.side_effect = RuntimeError("boom")
        r = client.post(f"/connectors/{cid}/build", json={"docs_url": "http://user:s3cret@example.com/docs.md"})
    assert r.status_code == 422, r.text
    assert "s3cret" not in r.text


def test_docs_url_public_host_still_fetched(tmp_path: pathlib.Path) -> None:
    from unittest.mock import Mock, patch

    client, _factory, cid = _client_and_factory(tmp_path)
    mock_resp = Mock()
    mock_resp.text = "# Vendor API\nBase URL: http://example.com\nGET /things\n"
    mock_resp.raise_for_status = Mock()
    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.return_value = mock_resp
        r = client.post(f"/connectors/{cid}/build", json={"docs_url": "http://example.com/docs.md"})
    assert r.status_code == 200, r.text


def test_docs_path_is_identifier_server_never_reads_file(tmp_path: pathlib.Path) -> None:
    marker = "MARKER-STRING-should-never-appear-in-spec-xyz"
    docs_file = tmp_path / "secret-docs.md"
    docs_file.write_text(f"# Secret vendor docs\n{marker}\nGET /things\n")
    client, factory, cid = _client_and_factory(tmp_path)
    r = client.post(f"/connectors/{cid}/build", json={"docs_path": str(docs_file)})
    assert r.status_code == 200, r.text
    with factory() as s:  # type: ignore[operator]
        br = s.query(BuildRun).order_by(BuildRun.created_at.desc()).first()
        assert br is not None
        assert marker not in json.dumps(br.draft_spec or {})
        assert marker not in json.dumps(br.inputs or {})
        assert (br.inputs or {}).get("docs_text") == f"docs from {docs_file}"


def test_engine_handler_never_reads_docs_path_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path}/engine_guard.db")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        conn = Connector(
            name="GuardEng",
            base_url="http://example.com",
            credential_ref="GUARD_API_KEY",
            target_entities=[],
            runtime_target={},
        )
        s.add(conn)
        s.commit()
        cid = conn.id
    docs_file = tmp_path / "eng-docs.md"
    docs_file.write_text("# Real content that must be ignored\nGET /things\n")

    def _boom(*args: object, **kwargs: object) -> str:  # noqa: ARG001
        raise AssertionError("server must not read local files")

    monkeypatch.setattr(pathlib.Path, "read_text", _boom)
    runner = JobRunner(factory, handlers={"build": run_build_handler})
    runner.submit_build(cid, inputs={"docs_path": str(docs_file)})
    runner.run_until_empty()
    with factory() as s:
        br = s.query(BuildRun).first()
        assert br is not None
        assert br.state == "succeeded", br.result
    engine.dispose()
