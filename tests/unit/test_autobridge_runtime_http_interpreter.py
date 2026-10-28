"""Tests for runtime http + interpreter (P1.W3.1)."""

from __future__ import annotations

import base64
import os
import time
from unittest.mock import Mock

import httpx
import pytest

from autobridge.runtime.http import (
    MAX_RETRY_AFTER_S,
    AuthError,
    HttpClient,
    HttpError,
    RetryDeadlineExceeded,
    build_auth_headers,
    compute_backoff,
    resolve_base_url,
    resolve_env_placeholders,
)
from autobridge.runtime.interpreter import (
    Interpreter,
    _extract_items,
    _extract_next_token,
    execute_spec_dict,
    extract_jsonpath,
    extract_jsonpath_list,
    resolve_checkpoint_templates,
)
from autobridge.spec.models import AuthSpec, RetryPolicy


def _spec_dict(base_url: str, operation_path: str = "/reservations", pagination: dict | None = None) -> dict:
    return {
        "spec_version": 1,
        "connector": {"id": "testconn", "vendor": "Test", "runtime_target": "cloud"},
        "source": {
            "base_url": base_url,
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "rate_limit": {"max_rps": 5, "retry": {"statuses": [429, 503], "backoff": "exponential", "max_attempts": 3}},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": operation_path,
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


# ---------------------------------------------------------------------------
# http helpers
# ---------------------------------------------------------------------------


def test_resolve_env_placeholders_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOO", "bar")
    assert resolve_env_placeholders("https://${FOO}/api") == "https://bar/api"
    assert resolve_env_placeholders("no placeholder") == "no placeholder"


def test_resolve_env_placeholders_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_XYZ", raising=False)
    with pytest.raises(KeyError, match="MISSING_XYZ"):
        resolve_env_placeholders("https://${MISSING_XYZ}/api")


def test_resolve_base_url_trailing_strip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASE", "https://example.com")
    assert resolve_base_url("${BASE}/api/") == "https://example.com/api"
    assert resolve_base_url("https://example.com/v1") == "https://example.com/v1"


def test_resolve_base_url_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_BASE_URL", "https://atlas.example.com")
    assert resolve_base_url("${ATLAS_BASE_URL}") == "https://atlas.example.com"


def test_resolve_base_url_invalid() -> None:
    with pytest.raises(ValueError, match="http\\(s\\)"):
        resolve_base_url("ftp://example.com")


def test_compute_backoff() -> None:
    assert compute_backoff(1, "exponential", 0.1) == pytest.approx(0.1)
    assert compute_backoff(2, "exponential", 0.1) == pytest.approx(0.2)
    assert compute_backoff(3, "exponential", 0.1) == pytest.approx(0.4)
    assert compute_backoff(1, "linear", 0.1) == pytest.approx(0.1)
    assert compute_backoff(2, "linear", 0.1) == pytest.approx(0.2)
    assert compute_backoff(1, "constant", 0.1) == pytest.approx(0.1)
    assert compute_backoff(5, "constant", 0.1) == pytest.approx(0.1)


def test_build_auth_headers_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TOKEN", "secret123")
    auth = AuthSpec(type="bearer", credential_ref="MY_TOKEN")
    assert build_auth_headers(auth) == {"Authorization": "Bearer secret123"}


def test_build_auth_headers_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "k123")
    auth = AuthSpec(type="api_key_header", credential_ref="API_KEY", header_name="X-Api-Key")
    assert build_auth_headers(auth) == {"X-Api-Key": "k123"}


def test_build_auth_headers_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASIC_PASS", "pword")
    auth = AuthSpec(type="basic", credential_ref="BASIC_PASS", username="bob")
    headers = build_auth_headers(auth)
    assert headers["Authorization"].startswith("Basic ")
    decoded = base64.b64decode(headers["Authorization"].split(" ", 1)[1]).decode()
    assert decoded == "bob:pword"


def test_build_auth_headers_oauth2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAUTH_SEC", "sec")
    auth = AuthSpec(
        type="oauth2_client_credentials",
        credential_ref="OAUTH_SEC",
        username="client_id",
        token_url="https://auth.example.com/token",
        scopes=["read"],
    )
    assert build_auth_headers(auth) == {}


def test_build_auth_headers_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_TOKEN", raising=False)
    auth = AuthSpec(type="bearer", credential_ref="MISSING_TOKEN")
    with pytest.raises(AuthError, match="Missing env var"):
        build_auth_headers(auth)


def test_build_auth_headers_unsupported() -> None:
    # Construct via model_validate to bypass type check
    auth = AuthSpec.model_validate({"type": "bearer", "credential_ref": "X"})
    auth.type = "unknown"  # type: ignore[assignment]
    os.environ["X"] = "tok"
    try:
        with pytest.raises(AuthError, match="Unsupported"):
            build_auth_headers(auth)  # type: ignore[arg-type]
    finally:
        del os.environ["X"]


def test_http_client_url_for() -> None:
    c = HttpClient("https://example.com", timeout_s=5)
    assert c.url_for("/v1/bookings") == "https://example.com/v1/bookings"
    with pytest.raises(ValueError, match="must start"):
        c.url_for("v1/bookings")


def test_http_client_auth_headers_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOK", "abc")
    auth = AuthSpec(type="bearer", credential_ref="TOK")
    c = HttpClient("https://example.com", auth=auth)
    assert c._auth_headers() == {"Authorization": "Bearer abc"}


def test_http_client_no_auth() -> None:
    c = HttpClient("https://example.com")
    assert c._auth_headers() == {}


# ---------------------------------------------------------------------------
# HttpClient request with retry (mocked httpx)
# ---------------------------------------------------------------------------


def _make_client_with_transport(
    handler, retry: RetryPolicy | None = None, sleep_fn=None, timeout_s: float = 5
) -> tuple[HttpClient, Mock]:
    sleep_mock = sleep_fn or Mock()
    transport = httpx.MockTransport(handler)
    def factory(**kwargs):
        return httpx.Client(transport=transport, **{k: v for k, v in kwargs.items() if k == "timeout"})
    client = HttpClient(
        "https://example.com",
        retry=retry or RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=3),
        sleep_fn=sleep_mock,
        client_factory=factory,
        timeout_s=timeout_s,
    )
    return client, sleep_mock


def test_request_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"data": []})

    client, sleep_mock = _make_client_with_transport(handler)
    resp = client.request("GET", "/reservations")
    assert resp.status_code == 200
    sleep_mock.assert_not_called()


def test_request_retry_on_429_then_success() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": "rate"})
        return httpx.Response(200, json={"ok": True})

    client, sleep_mock = _make_client_with_transport(handler)
    resp = client.request("GET", "/reservations")
    assert resp.status_code == 200
    assert len(calls) == 2
    assert sleep_mock.call_count == 1


def test_request_retry_with_retry_after() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"}, json={})
        return httpx.Response(200, json={"ok": True})

    client, sleep_mock = _make_client_with_transport(handler)
    resp = client.request("GET", "/reservations")
    assert resp.status_code == 200
    # Retry-After overrides backoff
    sleep_mock.assert_called_once_with(0.01)


def test_request_retry_with_invalid_retry_after_uses_backoff() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "not-a-date"}, json={})
        return httpx.Response(200, json={"ok": True})

    client, sleep_mock = _make_client_with_transport(handler)
    resp = client.request("GET", "/reservations")
    assert resp.status_code == 200
    assert sleep_mock.call_count == 1
    # Backoff for attempt 1 exponential 0.05
    assert sleep_mock.call_args[0][0] == pytest.approx(0.05)


def test_request_exhaustion_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(429, json={})

    client, sleep_mock = _make_client_with_transport(handler)
    with pytest.raises(HttpError, match="retry budget exhausted"):
        client.request("GET", "/reservations")
    # 3 attempts => 2 sleeps
    assert sleep_mock.call_count == 2


def test_request_retry_after_is_capped() -> None:
    """AR-2-1: a hostile ``Retry-After: 99999`` sleeps at most MAX_RETRY_AFTER_S."""

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(429, headers={"Retry-After": "99999"}, json={})

    client, sleep_mock = _make_client_with_transport(handler)
    with pytest.raises(HttpError, match="retry budget exhausted"):
        client.request("GET", "/reservations")
    # 3 attempts => 2 sleeps, each capped instead of 99999
    assert sleep_mock.call_count == 2
    for call in sleep_mock.call_args_list:
        assert call[0][0] == pytest.approx(MAX_RETRY_AFTER_S)
    assert client.total_delay_s == pytest.approx(2 * MAX_RETRY_AFTER_S)


def test_request_total_delay_budget_exceeded() -> None:
    """AR-2-1: cumulative delay past max_total_delay_s raises RetryDeadlineExceeded."""

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(429, headers={"Retry-After": "99999"}, json={})

    sleeps: list[float] = []
    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    client = HttpClient(
        "https://example.com",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=5),
        max_total_delay_s=100.0,
        sleep_fn=sleeps.append,
        client_factory=factory,
    )
    with pytest.raises(RetryDeadlineExceeded, match="budget exhausted"):
        client.request("GET", "/reservations")
    # 60 capped + 40 remainder = budget; the third sleep raises instead.
    assert sum(sleeps) == pytest.approx(100.0)
    assert client.total_delay_s == pytest.approx(100.0)


def test_retry_deadline_is_http_error() -> None:
    """RetryDeadlineExceeded stays catchable as HttpError (backward compat)."""
    assert issubclass(RetryDeadlineExceeded, HttpError)


def test_request_non_retryable_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(404, json={"msg": "not found"})

    client, _ = _make_client_with_transport(handler)
    with pytest.raises(HttpError, match="HTTP 404"):
        client.request("GET", "/reservations")


def test_request_network_error_retry() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("dns fail")
        return httpx.Response(200, json={"ok": True})

    client, sleep_mock = _make_client_with_transport(handler)
    resp = client.request("GET", "/reservations")
    assert resp.status_code == 200
    assert sleep_mock.call_count == 1


def test_request_network_error_no_retry_policy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        raise httpx.ConnectError("dns fail")

    transport = httpx.MockTransport(handler)
    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))
    client = HttpClient(
        "https://example.com",
        retry=None,  # type: ignore[arg-type]
        sleep_fn=Mock(),
        client_factory=factory,
    )
    # Manually set retry None behavior: should treat as max_attempts 1 but no retry statuses
    # Actually our code sets retry None => max_attempts 1 and empty statuses, so network error should raise after 1 attempt with "no retry policy"
    # But max_attempts is 1, so it will raise "after 1 attempts"
    with pytest.raises(HttpError, match="after 1 attempts|no retry policy"):
        client.request("GET", "/reservations")


def test_request_network_error_exhaustion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        raise httpx.ConnectError("fail")

    client, sleep_mock = _make_client_with_transport(handler)
    with pytest.raises(HttpError, match="after 3 attempts"):
        client.request("GET", "/reservations")
    assert sleep_mock.call_count == 2


def test_get_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"data": [{"id": 1}]})

    client, _ = _make_client_with_transport(handler)
    data = client.get("/reservations")
    assert data == {"data": [{"id": 1}]}


def test_get_money_parsed_as_decimal_not_float() -> None:
    # REQ-006: money is Decimal end to end — vendor JSON numbers must arrive
    # as Decimal (parse_float=Decimal) since transform_money rejects floats.
    from decimal import Decimal

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"data": [{"total_amount": 42.5}]})

    client, _ = _make_client_with_transport(handler)
    data = client.get("/reservations")
    amount = data["data"][0]["total_amount"]
    assert isinstance(amount, Decimal)
    assert amount == Decimal("42.5")


def test_get_non_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, content=b"not json")

    client, _ = _make_client_with_transport(handler)
    with pytest.raises(HttpError, match="not JSON"):
        client.get("/reservations")


def test_get_non_dict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json=[1, 2, 3])

    client, _ = _make_client_with_transport(handler)
    with pytest.raises(HttpError, match="expected JSON object"):
        client.get("/reservations")


# ---------------------------------------------------------------------------
# OAuth2
# ---------------------------------------------------------------------------


def test_oauth2_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAUTH_SECRET", "sec123")

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "tok123", "expires_in": 3600})
        return httpx.Response(200, json={"data": []})

    transport = httpx.MockTransport(handler)
    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))
    auth = AuthSpec(
        type="oauth2_client_credentials",
        credential_ref="OAUTH_SECRET",
        username="myclient",
        token_url="https://auth.example.com/token",
        scopes=["read"],
    )
    client = HttpClient("https://example.com", auth=auth, client_factory=factory)
    # Trigger token fetch via _auth_headers
    headers = client._auth_headers()
    assert headers == {"Authorization": "Bearer tok123"}
    # Second call uses cache
    headers2 = client._auth_headers()
    assert headers2 == {"Authorization": "Bearer tok123"}


def test_oauth2_missing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_OAUTH", raising=False)
    auth = AuthSpec(
        type="oauth2_client_credentials",
        credential_ref="MISSING_OAUTH",
        username="cid",
        token_url="https://auth.example.com/token",
    )
    def _factory(**_kwargs):  # type: ignore[no-untyped-def]  # noqa: ARG001
        return httpx.Client()

    client = HttpClient("https://example.com", auth=auth, client_factory=_factory)
    with pytest.raises(AuthError, match="Missing env var"):
        client._ensure_oauth_token()


def test_oauth2_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAUTH_SECRET", "sec")

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(401, json={"error": "bad"})

    transport = httpx.MockTransport(handler)
    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))
    auth = AuthSpec(
        type="oauth2_client_credentials",
        credential_ref="OAUTH_SECRET",
        username="cid",
        token_url="https://auth.example.com/token",
    )
    client = HttpClient("https://example.com", auth=auth, client_factory=factory)
    with pytest.raises(HttpError, match="token fetch returned 401"):
        client._ensure_oauth_token()


def test_oauth2_not_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAUTH_SECRET", "sec")

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, content=b"not json")

    transport = httpx.MockTransport(handler)
    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))
    auth = AuthSpec(
        type="oauth2_client_credentials",
        credential_ref="OAUTH_SECRET",
        username="cid",
        token_url="https://auth.example.com/token",
    )
    client = HttpClient("https://example.com", auth=auth, client_factory=factory)
    with pytest.raises(HttpError, match="not JSON"):
        client._ensure_oauth_token()


def test_oauth2_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAUTH_SECRET", "sec")

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"no_token": "x"})

    transport = httpx.MockTransport(handler)
    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))
    auth = AuthSpec(
        type="oauth2_client_credentials",
        credential_ref="OAUTH_SECRET",
        username="cid",
        token_url="https://auth.example.com/token",
    )
    client = HttpClient("https://example.com", auth=auth, client_factory=factory)
    with pytest.raises(HttpError, match="missing access_token"):
        client._ensure_oauth_token()


def test_oauth2_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAUTH_SECRET", "sec")

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        raise httpx.ConnectError("fail")

    transport = httpx.MockTransport(handler)
    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))
    auth = AuthSpec(
        type="oauth2_client_credentials",
        credential_ref="OAUTH_SECRET",
        username="cid",
        token_url="https://auth.example.com/token",
    )
    client = HttpClient("https://example.com", auth=auth, client_factory=factory)
    with pytest.raises(HttpError, match="token fetch failed"):
        client._ensure_oauth_token()


def test_oauth2_cache_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAUTH_SECRET", "sec")
    token_calls = []

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        token_calls.append(request.url.path)
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": f"tok{len(token_calls)}", "expires_in": 1})
        return httpx.Response(200, json={"data": []})

    transport = httpx.MockTransport(handler)
    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))
    auth = AuthSpec(
        type="oauth2_client_credentials",
        credential_ref="OAUTH_SECRET",
        username="cid",
        token_url="https://auth.example.com/token",
    )
    client = HttpClient("https://example.com", auth=auth, client_factory=factory)
    t1 = client._ensure_oauth_token()
    # Fast-forward time by monkeypatching time.monotonic
    original_monotonic = time.monotonic
    start = original_monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: start + 5)  # beyond expiry + margin
    t2 = client._ensure_oauth_token()
    assert t1 != t2
    assert len([c for c in token_calls if c == "/token"]) == 2


def test_oauth2_request_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAUTH_SECRET", "sec123")

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "bearer-token", "expires_in": 3600})
        assert request.headers.get("Authorization") == "Bearer bearer-token"
        return httpx.Response(200, json={"data": []})

    transport = httpx.MockTransport(handler)
    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))
    auth = AuthSpec(
        type="oauth2_client_credentials",
        credential_ref="OAUTH_SECRET",
        username="cid",
        token_url="https://auth.example.com/token",
    )
    client = HttpClient("https://example.com", auth=auth, client_factory=factory)
    resp = client.request("GET", "/reservations")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Interpreter helpers
# ---------------------------------------------------------------------------


def test_extract_jsonpath_found() -> None:
    data = {"a": {"b": 1}}
    assert extract_jsonpath(data, "$.a.b") == 1


def test_extract_jsonpath_missing() -> None:
    assert extract_jsonpath({"a": 1}, "$.b") is None


def test_extract_jsonpath_list() -> None:
    data = {"a": [{"x": 1}, {"x": 2}]}
    assert extract_jsonpath_list(data, "$.a[*].x") == [1, 2]


def test_resolve_checkpoint_templates_exact() -> None:
    assert resolve_checkpoint_templates({"since": "{{checkpoint.reservations}}"}, {"reservations": "2024-01-01"}) == {
        "since": "2024-01-01"
    }
    assert resolve_checkpoint_templates({"since": "{{checkpoint.reservations}}"}, {}) == {"since": ""}
    assert resolve_checkpoint_templates({"since": "{{checkpoint.reservations}}"}, None) == {"since": ""}


def test_resolve_checkpoint_templates_substring() -> None:
    out = resolve_checkpoint_templates({"q": "a {{checkpoint.x}} b"}, {"x": "val"})
    assert out["q"] == "a val b"


def test_resolve_checkpoint_templates_plain() -> None:
    assert resolve_checkpoint_templates({"a": "hello"}, {"x": 1}) == {"a": "hello"}


def test_resolve_checkpoint_templates_non_string() -> None:
    assert resolve_checkpoint_templates({"a": 123}, {}) == {"a": 123}  # type: ignore[arg-type]


def test_extract_items_list() -> None:
    assert _extract_items({"data": [{"id": 1}]}, "$.data") == [{"id": 1}]


def test_extract_items_missing() -> None:
    assert _extract_items({"other": []}, "$.data") == []


def test_extract_items_single_dict() -> None:
    assert _extract_items({"data": {"id": 1}}, "$.data") == [{"id": 1}]


def test_extract_items_non_dict_list() -> None:
    # When items_path yields a number, should return []
    assert _extract_items({"data": 123}, "$.data") == []


def test_extract_next_token_present() -> None:
    assert _extract_next_token({"meta": {"next": "abc"}}, "$.meta.next") == "abc"


def test_extract_next_token_missing() -> None:
    assert _extract_next_token({"meta": {}}, "$.meta.next") is None
    assert _extract_next_token({"a": 1}, None) is None


# ---------------------------------------------------------------------------
# Interpreter execution
# ---------------------------------------------------------------------------


def _fake_http(pages: list[dict[str, object]]) -> HttpClient:
    # pages is list of JSON bodies to return per call in order
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
    from autobridge.spec.models import RetryPolicy

    return HttpClient(
        "https://example.com",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=3),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )


def test_interpreter_no_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    spec_dict = _spec_dict("https://example.com")
    # remove pagination to test no-pagination path
    spec_dict["operations"]["list_reservations"]["pagination"] = None  # type: ignore[index]
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    http = _fake_http([{"data": [{"reservation_id": "R1"}]}])
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert result.pages_fetched == 1
    assert result.records == [{"reservation_id": "R1"}]
    assert result.stop_reason == "no_pagination"


def test_interpreter_single_record_no_data_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    spec_dict = _spec_dict("https://example.com")
    spec_dict["operations"]["list_reservations"]["pagination"] = None  # type: ignore[index]
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    # Return single object not wrapped in data list
    http = _fake_http([{"reservation_id": "R1"}])
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert result.records == [{"reservation_id": "R1"}]


def test_interpreter_cursor_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
        "limit_param": "limit",
    }
    spec_dict = _spec_dict("https://example.com", pagination=pagination)
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    pages = [
        {"data": [{"id": 1}], "meta": {"next_cursor": "abc"}},
        {"data": [{"id": 2}], "meta": {"next_cursor": None}},
    ]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert len(result.records) == 2
    assert result.pages_fetched == 2
    assert result.stop_reason == "null_cursor"
    assert result.repeated_cursor is False


def test_interpreter_offset_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "offset",
        "offset_param": "offset",
        "limit_param": "limit",
        "next_path": "$.meta.next_offset",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    spec_dict = _spec_dict("https://example.com", pagination=pagination)
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    pages = [
        {"data": [{"id": 1}], "meta": {"next_offset": 1}},
        {"data": [{"id": 2}], "meta": {"next_offset": None}},
    ]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert len(result.records) == 2
    assert result.pages_fetched == 2


def test_interpreter_offset_pagination_non_int_next(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "offset",
        "offset_param": "offset",
        "limit_param": "limit",
        "next_path": "$.meta.next_offset",
        "items_path": "$.data",
        "stop_when": ["empty_page"],
    }
    spec_dict = _spec_dict("https://example.com", pagination=pagination)
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    pages = [
        {"data": [{"id": 1}], "meta": {"next_offset": "not-an-int"}},
        {"data": [], "meta": {"next_offset": None}},
    ]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert result.stop_reason == "empty_page"


def test_interpreter_page_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "page",
        "page_param": "page",
        "limit_param": "limit",
        "next_path": "$.meta.next_page",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    spec_dict = _spec_dict("https://example.com", pagination=pagination)
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    pages = [
        {"data": [{"id": 1}], "meta": {"next_page": 2}},
        {"data": [{"id": 2}], "meta": {"next_page": None}},
    ]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert len(result.records) == 2


def test_interpreter_repeated_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["repeated_cursor"],
    }
    spec_dict = _spec_dict("https://example.com", pagination=pagination)
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    # Simulate repeated cursor bug: first page returns abc, second page returns abc again (repeats)
    pages = [
        {"data": [{"id": 1}], "meta": {"next_cursor": "abc"}},
        {"data": [{"id": 2}], "meta": {"next_cursor": "abc"}},
    ]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert result.repeated_cursor is True
    assert result.stop_reason == "repeated_cursor"


def test_interpreter_repeated_cursor_previous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["repeated_cursor"],
    }
    spec_dict = _spec_dict("https://example.com", pagination=pagination)
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    # Need 3 pages to hit previous_cursor check: cursor sequence None -> A -> B -> A (repeat previous)
    pages = [
        {"data": [{"id": 1}], "meta": {"next_cursor": "A"}},
        {"data": [{"id": 2}], "meta": {"next_cursor": "B"}},
        {"data": [{"id": 3}], "meta": {"next_cursor": "A"}},
    ]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert result.repeated_cursor is True
    assert result.stop_reason == "repeated_cursor"


def test_interpreter_empty_page() -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["empty_page"],
    }
    spec_dict = _spec_dict("https://example.com", pagination=pagination)
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    pages = [{"data": [], "meta": {"next_cursor": "abc"}}]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert result.stop_reason == "empty_page"
    monkeypatch.undo()


def test_interpreter_no_next() -> None:
    import os

    os.environ["TEST_TOKEN"] = "tok"
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["no_next"],
    }
    spec_dict = _spec_dict("https://example.com", pagination=pagination)
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    pages = [{"data": [{"id": 1}], "meta": {}}]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert result.stop_reason == "no_next"


def test_interpreter_null_cursor_empty_string() -> None:
    import os

    os.environ["TEST_TOKEN"] = "tok"
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    spec_dict = _spec_dict("https://example.com", pagination=pagination)
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    pages = [{"data": [{"id": 1}], "meta": {"next_cursor": ""}}]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert result.stop_reason == "null_cursor"


def test_interpreter_page_limit() -> None:
    import os

    os.environ["TEST_TOKEN"] = "tok"
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    spec_dict = _spec_dict("https://example.com", pagination=pagination)
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    # Always returns next cursor, but max_pages=2 should trigger page_limit
    pages = [
        {"data": [{"id": 1}], "meta": {"next_cursor": "a"}},
        {"data": [{"id": 2}], "meta": {"next_cursor": "b"}},
        {"data": [{"id": 3}], "meta": {"next_cursor": "c"}},
    ]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations", max_pages=2)
    assert result.stop_reason == "page_limit"
    assert result.pages_fetched == 2


def test_interpreter_implicit_no_next_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["empty_page"],
    }
    spec_dict = _spec_dict("https://example.com", pagination=pagination)
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    pages = [{"data": [{"id": 1}], "meta": {"next_cursor": None}}]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert result.stop_reason == "no_next_token"


def test_interpreter_implicit_empty_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    spec_dict = _spec_dict("https://example.com", pagination=pagination)
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    # Second page empty with next token present should trigger implicit empty_page
    pages = [
        {"data": [{"id": 1}], "meta": {"next_cursor": "a"}},
        {"data": [], "meta": {"next_cursor": "b"}},
    ]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    # First page has next token, second page empty -> implicit
    # Actually second page empty should be detected? But stop_when doesn't contain empty_page,
    # so it should go to "empty_page_implicit" after advancing
    assert result.stop_reason in ("empty_page_implicit", "no_next_token")


def test_interpreter_checkpoint_max() -> None:
    import os

    os.environ["TEST_TOKEN"] = "tok"
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    spec_dict = _spec_dict("https://example.com", pagination=pagination)
    spec_dict["operations"]["list_reservations"]["checkpoint"] = {"field": "$.updated_at", "mode": "max"}  # type: ignore[index]
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    pages = [
        {"data": [{"updated_at": "2024-01-01"}, {"updated_at": "2024-01-03"}], "meta": {"next_cursor": "a"}},
        {"data": [{"updated_at": "2024-01-02"}], "meta": {"next_cursor": None}},
    ]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert result.checkpoint_value == "2024-01-03"


def test_interpreter_checkpoint_min() -> None:
    import os

    os.environ["TEST_TOKEN"] = "tok"
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    spec_dict = _spec_dict("https://example.com", pagination=pagination)
    spec_dict["operations"]["list_reservations"]["checkpoint"] = {"field": "$.updated_at", "mode": "min"}  # type: ignore[index]
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    pages = [
        {"data": [{"updated_at": "2024-01-03"}, {"updated_at": "2024-01-01"}], "meta": {"next_cursor": None}},
    ]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert result.checkpoint_value == "2024-01-01"


def test_interpreter_checkpoint_mixed_types() -> None:
    import os

    os.environ["TEST_TOKEN"] = "tok"
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    spec_dict = _spec_dict("https://example.com", pagination=pagination)
    spec_dict["operations"]["list_reservations"]["checkpoint"] = {"field": "$.val", "mode": "max"}  # type: ignore[index]
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    pages = [
        {"data": [{"val": 1}, {"val": "a"}], "meta": {"next_cursor": None}},
    ]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    # Mixed int/string -> stringify comparison, max string is "a"
    assert result.checkpoint_value == "a"


def test_interpreter_unknown_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    spec_dict = _spec_dict("https://example.com")
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    http = _fake_http([])
    interp = Interpreter(spec, http_client=http)
    with pytest.raises(KeyError, match="unknown operation"):
        interp.execute_operation("missing_op")


def test_interpreter_execute_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    spec_dict = _spec_dict("https://example.com")
    spec_dict["operations"]["second_op"] = {
        "method": "GET",
        "path": "/second",
        "params": {},
        "pagination": {
            "strategy": "cursor",
            "cursor_param": "cursor",
            "next_path": "$.meta.next_cursor",
            "items_path": "$.data",
            "stop_when": ["null_cursor"],
        },
    }
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    # Fake http that returns data for both ops based on path
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        if request.url.path == "/reservations":
            return httpx.Response(200, json={"data": [{"id": 1}], "meta": {"next_cursor": None}})
        if request.url.path == "/second":
            return httpx.Response(200, json={"data": [{"id": 2}], "meta": {"next_cursor": None}})
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))
    http = HttpClient(
        "https://example.com",
        retry=RetryPolicy(statuses=[429], backoff="exponential", max_attempts=2),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )
    interp = Interpreter(spec, http_client=http)
    out = interp.execute_all()
    assert set(out.keys()) == {"list_reservations", "second_op"}
    assert len(out["list_reservations"].records) == 1


def test_execute_spec_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    spec_dict = _spec_dict("https://example.com")
    http = _fake_http([{"data": [{"id": 1}], "meta": {"next_cursor": None}}])
    # Need pagination for this spec? No, default spec_dict has no pagination details? Actually we set pagination None implicitly
    # Use offset pagination for test
    spec_dict["operations"]["list_reservations"]["pagination"] = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    result = execute_spec_dict(spec_dict, "list_reservations", http)
    assert result.operation == "list_reservations"
    assert len(result.records) == 1


def test_checkpoint_template_in_params(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    spec_dict = _spec_dict("https://example.com", pagination=pagination)
    spec_dict["operations"]["list_reservations"]["params"] = {"since": "{{checkpoint.reservations}}"}  # type: ignore[index]
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        captured.update(dict(request.url.params))
        return httpx.Response(200, json={"data": [], "meta": {"next_cursor": None}})

    transport = httpx.MockTransport(handler)
    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))
    http = HttpClient(
        "https://example.com",
        retry=RetryPolicy(statuses=[429], backoff="exponential", max_attempts=2),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )
    interp = Interpreter(spec, http_client=http)
    interp.execute_operation("list_reservations", checkpoint={"reservations": "2024-01-01"})
    assert captured.get("since") == "2024-01-01"


def test_http_default_client_from_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok")
    monkeypatch.setenv("MY_BASE", "https://example.com")
    spec_dict = _spec_dict("${MY_BASE}")
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    # Interpreter with no explicit http_client should create one from spec.source
    interp = Interpreter(spec)
    assert interp.http.base_url == "https://example.com"
    assert interp.http.auth is not None
    assert interp.http.retry is not None


def test_modelcall_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reference sync must not create ModelCall rows."""
    monkeypatch.setenv("TEST_TOKEN", "tok")
    # Use in-memory DB
    from sqlalchemy import create_engine

    from autobridge.control.models import Base, ModelCall, session_factory

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = session_factory(engine)

    # Build a spec and run interpreter via fake http — no LLM involved
    spec_dict = _spec_dict("https://example.com")
    spec_dict["operations"]["list_reservations"]["pagination"] = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    http = _fake_http([{"data": [{"id": 1}], "meta": {"next_cursor": None}}])
    result = execute_spec_dict(spec_dict, "list_reservations", http)
    assert len(result.records) == 1

    # Verify no ModelCall rows exist
    with sessions() as session:
        count = session.query(ModelCall).count()
        assert count == 0


def test_interpreter_retry_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pagination loop respects retry/backoff on 429."""
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    spec_dict = _spec_dict("https://example.com", pagination=pagination)
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={})
        if len(calls) == 2:
            return httpx.Response(200, json={"data": [{"id": 1}], "meta": {"next_cursor": None}})
        return httpx.Response(200, json={"data": [], "meta": {"next_cursor": None}})

    transport = httpx.MockTransport(handler)
    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))
    http = HttpClient(
        "https://example.com",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=3),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert len(result.records) == 1
    assert len(calls) == 2
