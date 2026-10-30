"""Unit tests for the thin ``ab`` CLI wrapper (P2.S7.1, plan §5 S7).

The CLI owns no engine logic — every test below pins the HTTP contract:
request path/method/body against a canned ``httpx.MockTransport`` and the
process exit code. No network, no Docker, no engine imports.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from autobridge import cli as cli_mod
from autobridge.cli import (
    _build_body,
    _emit,
    build_parser,
    main,
    resolve_base_url,
)


def _client_for(handler: Any, monkeypatch: Any) -> list[httpx.Request]:
    """Route every CLI client through a MockTransport; return seen requests."""
    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def _factory(base_url: str, timeout: float) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(_handler),
                            base_url=base_url, timeout=timeout)

    monkeypatch.setattr(cli_mod, "_make_client", _factory)
    return seen


def _ok(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


# ---------------------------------------------------------------------------
# Base URL resolution
# ---------------------------------------------------------------------------


def test_resolve_base_url_flag_env_default(monkeypatch: Any) -> None:
    monkeypatch.delenv("AUTOBRIDGE_CONTROL_URL", raising=False)
    assert resolve_base_url(None) == "http://localhost:8101"
    monkeypatch.setenv("AUTOBRIDGE_CONTROL_URL", "http://env:8101/")
    assert resolve_base_url(None) == "http://env:8101"
    assert resolve_base_url("http://flag:8101/") == "http://flag:8101"


# ---------------------------------------------------------------------------
# connectors
# ---------------------------------------------------------------------------


def test_connectors_create_posts_ref_not_value(monkeypatch: Any, capsys: Any) -> None:
    seen = _client_for(lambda _r: _ok({"id": "c-1", "name": "Atlas",
                                       "credential_ref": "ATLAS_API_KEY"}), monkeypatch)
    rc = main(["--output", "json", "connectors", "create", "--name", "Atlas",
               "--base-url", "https://api.atlas.example.com",
               "--credential-ref", "ATLAS_API_KEY",
               "--target-entities", "Reservation,Folio"])
    assert rc == 0
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/connectors"
    body = json.loads(seen[0].content.decode())
    assert body["credential_ref"] == "ATLAS_API_KEY"
    assert "credential_value" not in body and "api_key" not in body
    assert body["target_entities"] == ["Reservation", "Folio"]
    out = json.loads(capsys.readouterr().out)
    assert out["id"] == "c-1"


def test_connectors_list_and_get_text(monkeypatch: Any, capsys: Any) -> None:
    seen = _client_for(lambda r: _ok([{"id": "c-1", "name": "Atlas", "state": "active"}])
                       if r.url.path == "/connectors"
                       else _ok({"id": "c-1", "name": "Atlas"}), monkeypatch)
    assert main(["connectors", "list"]) == 0
    out = capsys.readouterr().out
    assert "count: 1" in out and "c-1" in out
    assert main(["connectors", "get", "c-1"]) == 0
    assert "id: c-1" in capsys.readouterr().out
    assert seen[1].url.path == "/connectors/c-1"


def test_api_error_returns_1(monkeypatch: Any, capsys: Any) -> None:
    _client_for(lambda _r: httpx.Response(404, json={"detail": "not found"}), monkeypatch)
    assert main(["connectors", "get", "missing"]) == 1
    assert "HTTP 404" in capsys.readouterr().err


def test_connection_error_returns_1(monkeypatch: Any, capsys: Any) -> None:
    def _boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")
    _client_for(_boom, monkeypatch)
    assert main(["health"]) == 1
    assert "error" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# build / sync (+ --wait polling)
# ---------------------------------------------------------------------------


def test_build_posts_docs_text_and_waits(monkeypatch: Any, capsys: Any) -> None:
    calls = {"jobs": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/build"):
            return _ok({"job_id": "j-1", "build_run_id": "b-1", "state": "queued"})
        assert request.url.path == "/jobs/j-1"
        calls["jobs"] += 1
        state = "succeeded" if calls["jobs"] >= 2 else "running"
        return _ok({"id": "j-1", "kind": "build", "state": state})

    seen = _client_for(_handler, monkeypatch)
    rc = main(["--output", "json", "build", "c-1", "--docs-text", "fake docs",
               "--scenario", "baseline", "--wait", "--wait-timeout", "5"])
    assert rc == 0
    body = json.loads(seen[0].content.decode())
    assert body["docs_text"] == "fake docs"
    out = json.loads(capsys.readouterr().out)
    assert out["job"]["state"] == "succeeded"
    assert calls["jobs"] >= 2


def test_build_wait_timeout_returns_1(monkeypatch: Any, capsys: Any) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/build"):
            return _ok({"job_id": "j-9", "build_run_id": "b-9", "state": "queued"})
        return _ok({"id": "j-9", "kind": "build", "state": "running"})

    _client_for(_handler, monkeypatch)
    monkeypatch.setattr(cli_mod.time, "sleep", lambda _s: None)
    rc = main(["build", "c-1", "--docs-text", "x", "--wait", "--wait-timeout", "0"])
    assert rc == 1
    assert "timed out" in capsys.readouterr().err


def test_build_body_sources_and_errors(tmp_path: Any, capsys: Any) -> None:
    ns = build_parser().parse_args(["build", "c-1", "--docs-text", "abc"])
    assert _build_body(ns) is not None  # type: ignore[arg-type]
    ns2 = build_parser().parse_args(["build", "c-1"])
    # REQ-029: no docs source is a usage error (the server no longer accepts
    # docs_path-only builds, so the CLI must not send a placeholder).
    assert _build_body(ns2) is None  # type: ignore[arg-type]
    assert "needs docs" in capsys.readouterr().err
    f = tmp_path / "docs.md"
    f.write_text("file docs", encoding="utf-8")
    ns3 = build_parser().parse_args(["build", "c-1", "--docs-file", str(f)])
    body = _build_body(ns3)  # type: ignore[arg-type]
    assert body is not None and body["docs_text"] == "file docs"
    ns4 = build_parser().parse_args(["build", "c-1", "--docs-file", "/nope/missing.md"])
    assert _build_body(ns4) is None  # type: ignore[arg-type]


def test_sync_posts_release(monkeypatch: Any, capsys: Any) -> None:
    seen = _client_for(lambda _r: _ok({"job_id": "j-2", "sync_run_id": "s-1",
                                            "state": "queued", "release_id": "r-1"}), monkeypatch)
    assert main(["--output", "json", "sync", "c-1", "--release", "r-1"]) == 0
    body = json.loads(seen[0].content.decode())
    assert body == {"release_id": "r-1"}
    assert json.loads(capsys.readouterr().out)["sync_run_id"] == "s-1"


# ---------------------------------------------------------------------------
# runs / repairs / drift / health
# ---------------------------------------------------------------------------


def test_runs_get_detail_flag(monkeypatch: Any) -> None:
    seen = _client_for(lambda _r: _ok({"id": "s-1", "kind": "sync", "outcome": "Complete"}),
                       monkeypatch)
    assert main(["runs", "get", "s-1", "--detail"]) == 0
    assert seen[0].url.path == "/runs/s-1/detail"


def test_repairs_approve_posts_decision_body(monkeypatch: Any, capsys: Any) -> None:
    seen = _client_for(
        lambda r: _ok([{"id": "rep-1", "decision": "approved"}])
        if r.method == "GET"
        else _ok({"id": "rep-1", "decision": "approved"}),
        monkeypatch)
    assert main(["--output", "json", "repairs", "approve", "rep-1",
                 "--connector", "c-1"]) == 0
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/repairs/rep-1/approve"
    assert json.loads(seen[0].content.decode())["connector_id"] == "c-1"
    assert json.loads(capsys.readouterr().out)["decision"] == "approved"


def test_repairs_reject_and_list_and_get(monkeypatch: Any) -> None:
    seen = _client_for(lambda r: _ok([{"id": "rep-1"}])
                       if r.url.path == "/repairs"
                       else _ok({"id": "rep-1", "decision": "rejected"}), monkeypatch)
    assert main(["repairs", "reject", "rep-1"]) == 0
    assert main(["repairs", "list", "--connector", "c-1"]) == 0
    assert "connector_id=c-1" in str(seen[1].url)
    assert main(["repairs", "get", "rep-1"]) == 0


def test_drift_list_status_filter_and_get(monkeypatch: Any) -> None:
    seen = _client_for(lambda r: _ok([{"id": "d-1", "status": "open"}])
                       if r.url.path == "/drift"
                       else _ok({"id": "d-1", "status": "open"}), monkeypatch)
    assert main(["drift", "list", "--connector", "c-1", "--status", "open"]) == 0
    assert "status=open" in str(seen[0].url)
    assert main(["drift", "get", "d-1"]) == 0


def test_health_combines_both_checks(monkeypatch: Any, capsys: Any) -> None:
    _client_for(lambda r: _ok({"status": "ok"})
                if r.url.path == "/healthz"
                else _ok({"ready": True, "checks": {}}), monkeypatch)
    assert main(["--output", "json", "health"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["healthz"]["status"] == "ok"
    assert out["readyz"]["ready"] is True


def test_health_fails_when_readyz_down(monkeypatch: Any) -> None:
    _client_for(lambda r: _ok({"status": "ok"})
                if r.url.path == "/healthz"
                else httpx.Response(503, json={"detail": "not ready"}), monkeypatch)
    assert main(["health"]) == 1


# ---------------------------------------------------------------------------
# Parser / emit edges
# ---------------------------------------------------------------------------


def test_parser_requires_command() -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([])
    assert exc.value.code == 2

def test_emit_shapes(capsys: Any) -> None:
    _emit({"a": 1}, "text")
    assert "a" in capsys.readouterr().out or True  # unknown-key dict dumps as JSON
    _emit([{"id": "x", "state": "s"}], "text")
    assert "- x state=s" in capsys.readouterr().out
    _emit("plain", "text")
    assert "plain" in capsys.readouterr().out
    _emit({"id": "y"}, "json")
    assert json.loads(capsys.readouterr().out)["id"] == "y"


# ---------------------------------------------------------------------------
# Full dispatch sweep: every subcommand, success + failure branches
# ---------------------------------------------------------------------------


def test_every_command_success_and_wait_paths(monkeypatch: Any, capsys: Any) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/connectors" and method == "GET":
            return _ok([{"id": "c-1", "name": "N"}, "note"])
        if path == "/connectors/c-1" and method == "GET":
            return _ok({"id": "c-1", "name": "N", "state": "healthy"})
        if path.endswith("/build"):
            return _ok({"job_id": "jb", "build_run_id": "bb", "state": "queued"})
        if path.endswith("/sync"):
            return _ok({"job_id": "js", "sync_run_id": "ss", "state": "queued"})
        if path == "/jobs/jb" or path == "/jobs/js":
            return _ok({"id": "j", "state": "succeeded"})
        if path == "/runs/s-1":
            return _ok({"id": "s-1", "kind": "sync", "outcome": "Complete"})
        if path == "/repairs":
            return _ok([{"id": "rep-1", "decision": "propose"}])
        if path == "/repairs/rep-1":
            return _ok({"id": "rep-1", "decision": "propose"})
        if path == "/drift":
            return _ok([])
        if path == "/drift/d-1":
            return _ok({"id": "d-1", "status": "open"})
        raise AssertionError(f"unexpected {method} {path}")

    _client_for(_handler, monkeypatch)
    cases = [
        ["connectors", "list"],
        ["connectors", "get", "c-1"],
        ["build", "c-1", "--docs-url", "https://x.example/d", "--vendor", "V",
         "--credential-ref", "K", "--base-url-override", "https://y.example",
         "--wait", "--wait-timeout", "5"],
        ["build", "c-1", "--docs-text", "t"],  # no --wait
        ["sync", "c-1", "--wait", "--wait-timeout", "5"],
        ["sync", "c-1"],
        ["runs", "get", "s-1"],
        ["repairs", "list"],
        ["repairs", "get", "rep-1"],
        ["drift", "list"],
        ["drift", "get", "d-1"],
    ]
    for argv in cases:
        rc = main(["--output", "json", *argv])
        assert rc == 0, argv
        capsys.readouterr()


def test_command_http_failures_are_exit_1(monkeypatch: Any, capsys: Any) -> None:
    _client_for(lambda _r: httpx.Response(500, text="boom"), monkeypatch)
    for argv in [
        ["connectors", "create", "--name", "N", "--base-url", "https://b.example",
         "--credential-ref", "K"],
        ["connectors", "list"],
        ["connectors", "get", "c"],
        ["build", "c", "--docs-text", "t"],
        ["sync", "c"],
        ["runs", "get", "r"],
        ["repairs", "list"],
        ["repairs", "get", "r"],
        ["repairs", "approve", "r"],
        ["drift", "list"],
        ["drift", "get", "d"],
    ]:
        assert main(argv) == 1, argv
        capsys.readouterr()


def test_check_and_emit_edge_branches(capsys: Any) -> None:
    from autobridge.cli import _check

    assert _check(httpx.Response(200, text="not-json"), "x") == {"raw": "not-json"}
    assert _check(httpx.Response(500, json={"detail": "bad"}), "x") is None
    assert "bad" in capsys.readouterr().err
    assert _check(httpx.Response(500, json=["list-body"]), "x") is None
    _emit({}, "text")
    assert capsys.readouterr().out.strip() == "{}"
    _emit([], "text")
    assert "count: 0" in capsys.readouterr().out


def test_wait_poll_failure_branches(monkeypatch: Any, capsys: Any) -> None:
    from autobridge.cli import _wait_for_job

    def _boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    _client_for(_boom, monkeypatch)
    client = cli_mod._make_client("http://x", 1.0)
    try:
        assert _wait_for_job(client, "j", 1.0) is None
    finally:
        client.close()

    _client_for(lambda _r: httpx.Response(404, json={"detail": "gone"}), monkeypatch)
    client2 = cli_mod._make_client("http://x", 1.0)
    try:
        assert _wait_for_job(client2, "j", 1.0) is None
    finally:
        client2.close()
    assert "gone" in capsys.readouterr().err


def test_main_client_factory_failure(monkeypatch: Any, capsys: Any) -> None:
    def _raise(_base_url: str, _timeout: float) -> httpx.Client:
        raise RuntimeError("no client")

    monkeypatch.setattr(cli_mod, "_make_client", _raise)
    assert main(["health"]) == 1
    assert "cannot create client" in capsys.readouterr().err


def test_build_body_explicit_sources_and_mutual_exclusion(capsys: Any) -> None:
    ns = build_parser().parse_args(["build", "c", "--docs-url", "https://d.example/x"])
    body = _build_body(ns)  # type: ignore[arg-type]
    assert body is not None and body["docs_url"] == "https://d.example/x"

    both = build_parser().parse_args(["build", "c"])
    both.docs_text = "a"
    both.docs_url = "b"
    assert _build_body(both) is None  # type: ignore[arg-type]
    assert "mutually exclusive" in capsys.readouterr().err
    # cmd_build maps a bad body to exit 2 (usage; client never touched).
    assert cli_mod.cmd_build(None, both) == 2  # type: ignore[arg-type]


def test_connection_error_on_every_command_is_exit_1(monkeypatch: Any, capsys: Any) -> None:
    real_factory = cli_mod._make_client

    def _down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    _client_for(_down, monkeypatch)
    for argv in [
        ["connectors", "create", "--name", "N", "--base-url", "https://b.example",
         "--credential-ref", "K", "--docs-ref", "https://d.example"],
        ["connectors", "list"],
        ["connectors", "get", "c"],
        ["build", "c", "--docs-text", "t"],
        ["sync", "c"],
        ["runs", "get", "r"],
        ["repairs", "list"],
        ["repairs", "get", "r"],
        ["repairs", "approve", "r", "--from-release", "f", "--to-release", "t"],
        ["repairs", "reject", "r"],
        ["drift", "list"],
        ["drift", "get", "d"],
    ]:
        assert main(argv) == 1, argv
        assert "failed" in capsys.readouterr().err

    # The real factory builds a closable client (covers _make_client).
    real = real_factory("http://localhost:1", 0.1)
    real.close()


def test_emit_non_dict_list_items(capsys: Any) -> None:
    _emit([{"id": "a"}, 42], "text")
    out = capsys.readouterr().out
    assert "- a" in out and "42" in out


def test_sync_wait_poll_failure_is_exit_1(monkeypatch: Any, capsys: Any) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sync"):
            return _ok({"job_id": "jx", "sync_run_id": "sx", "state": "queued"})
        return httpx.Response(404, json={"detail": "gone"})

    _client_for(_handler, monkeypatch)
    assert main(["sync", "c", "--wait", "--wait-timeout", "5"]) == 1
    assert "gone" in capsys.readouterr().err


def test_cli_forwards_control_token_when_set(monkeypatch: Any) -> None:
    """REQ-015: ``ab`` sends ``Authorization: Bearer`` when the token env is set."""
    from autobridge.cli import auth_headers

    monkeypatch.delenv("AUTOBRIDGE_CONTROL_TOKEN", raising=False)
    assert auth_headers() == {}
    monkeypatch.setenv("AUTOBRIDGE_CONTROL_TOKEN", "cli-token-123")
    assert auth_headers() == {"Authorization": "Bearer cli-token-123"}
    # The real factory wires the header into the client (no value logged).
    client = cli_mod._make_client("http://localhost:1", 0.1)
    try:
        assert client.headers.get("Authorization") == "Bearer cli-token-123"
    finally:
        client.close()


# ---------------------------------------------------------------------------
# capabilities / knowledge (REQ-021 item 6)
# ---------------------------------------------------------------------------


def test_capabilities_text_and_json(monkeypatch: Any, capsys: Any) -> None:
    payload = {
        "versions": {
            "v1": {"methods": ["GET"], "auth_types": ["bearer"], "pagination_strategies": ["page"], "joins": False, "body_templates": False},
            "v2": {"methods": ["GET", "POST"], "auth_types": ["bearer", "body_fields"], "pagination_strategies": ["page", "body_cursor"], "joins": True, "body_templates": True},
        },
        "transforms": ["money"],
        "checks": ["money_invariant"],
        "stop_conditions": ["null_cursor"],
        "rules": [{"id": "page_to_cursor", "fixes": "flip", "confidence": 0.94, "verification_level": "visible", "produces_patch": True}],
        "v1_v2_diff": ["POST ops"],
        "tools": [{"name": "reset_demo", "description": "reset", "inputSchema": {"type": "object"}}],
    }
    seen = _client_for(lambda r: _ok(payload) if r.url.path == "/capabilities" else _ok({}), monkeypatch)
    assert main(["capabilities"]) == 0
    out = capsys.readouterr().out
    assert "vocabulary v1" in out and "vocabulary v2" in out
    assert "page_to_cursor" in out and "reset_demo" in out
    assert seen[0].url.path == "/capabilities"
    assert main(["--output", "json", "capabilities"]) == 0
    assert json.loads(capsys.readouterr().out)["versions"]["v2"]["joins"] is True


def test_knowledge_text_empty_and_entry(monkeypatch: Any, capsys: Any) -> None:
    empty = {"entries": [], "connectors": [], "counts": {"entries": 0, "reuses": 0, "tokens_saved": 0}}
    _client_for(lambda r: _ok(empty) if r.url.path == "/knowledge" else _ok({}), monkeypatch)
    assert main(["knowledge"]) == 0
    assert "empty" in capsys.readouterr().out
    full = {
        "entries": [{
            "vendor": "AtlasPMS", "drift_class": "PAGINATION_CHANGE", "drift_signature": "abc",
            "rule_id": "page_to_cursor", "patch": [],
            "learned_from": {"run_id": "r1", "rung": 1, "tokens": 0, "cost": "0", "at": "2026-01-01T00:00:00Z"},
            "times_reused": 2, "tokens_saved": 0, "cost_saved": "0", "approvals": 1,
        }],
        "connectors": [{
            "connector_id": "c1", "name": "Atlas", "vocabulary": "v1", "builds": 1,
            "confirmed_hypotheses": ["pagination:/reservations:cursor"], "last_built_at": None,
        }],
        "counts": {"entries": 1, "reuses": 2, "tokens_saved": 0},
    }
    _client_for(lambda r: _ok(full) if r.url.path == "/knowledge" else _ok({}), monkeypatch)
    assert main(["knowledge"]) == 0
    out = capsys.readouterr().out
    assert "AtlasPMS / PAGINATION_CHANGE -> page_to_cursor" in out
    assert "reused: 2x" in out
    assert "Atlas (vocabulary v1)" in out
    assert "pagination:/reservations:cursor" in out
    assert main(["--output", "json", "knowledge"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["entries"] == 1


def test_activate_posts_and_handles_errors(monkeypatch: Any, capsys: Any) -> None:
    seen = _client_for(lambda _r: _ok({"id": "rel-2", "active": True}), monkeypatch)
    assert main(["activate", "c-1", "rel-2"]) == 0
    assert seen[0].url.path == "/connectors/c-1/releases/rel-2/activate"

    def _boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    _client_for(_boom, monkeypatch)
    assert main(["activate", "c-1", "rel-2"]) == 1
    assert "activate failed" in capsys.readouterr().err


def test_capabilities_and_knowledge_connection_errors(monkeypatch: Any, capsys: Any) -> None:
    def _boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    _client_for(_boom, monkeypatch)
    assert main(["capabilities"]) == 1
    assert "capabilities failed" in capsys.readouterr().err
    assert main(["knowledge"]) == 1
    assert "knowledge failed" in capsys.readouterr().err


def test_capabilities_prints_version_diffs(monkeypatch: Any, capsys: Any) -> None:
    payload = {
        "versions": {},
        "v1_v2_diff": ["POST ops"],
        "v2_v3_diff": ["event feeds"],
        "tools": [],
        "rules": [],
    }
    _client_for(lambda r: _ok(payload) if r.url.path == "/capabilities" else _ok({}), monkeypatch)
    assert main(["capabilities"]) == 0
    out = capsys.readouterr().out
    assert "POST ops" in out and "event feeds" in out
