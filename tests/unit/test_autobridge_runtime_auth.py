"""Tests for runtime auth strategies (P1.W3.2).

- Covers bearer, api_key_header, basic, oauth2_client_credentials.
- Credentials are read by credential_ref from env, never logged (DIRECTIVES §6).
- Secrets never appear in exception messages or headers beyond Authorization.
"""

from __future__ import annotations

import base64
import os
import time

import httpx
import pytest

from autobridge.runtime.auth import (
    AuthError,
    OAuth2Token,
    OAuth2TokenManager,
    TokenFetchError,
    build_auth_headers,
    get_auth_headers,
)
from autobridge.spec.models import AuthSpec

# ---------------------------------------------------------------------------
# bearer
# ---------------------------------------------------------------------------


def test_bearer_builds_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEARER_TOK", "s3cr3t")
    auth = AuthSpec(type="bearer", credential_ref="BEARER_TOK")
    headers = build_auth_headers(auth)
    assert headers == {"Authorization": "Bearer s3cr3t"}


def test_bearer_missing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_BEARER", raising=False)
    auth = AuthSpec(type="bearer", credential_ref="MISSING_BEARER")
    with pytest.raises(AuthError, match="Missing env var.*MISSING_BEARER") as exc:
        build_auth_headers(auth)
    # Secret never in message (we didn't set one, but ensure no secret leak)
    assert "s3cr3t" not in str(exc.value)


def test_bearer_secret_not_in_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    # Set secret, then test that missing case doesn't leak other secrets
    monkeypatch.setenv("SECRET_VAL", "super-secret-123")
    # Use a different ref that's missing; error should name ref, not value
    monkeypatch.delenv("MISSING_X", raising=False)
    auth = AuthSpec(type="bearer", credential_ref="MISSING_X")
    with pytest.raises(AuthError) as exc:
        build_auth_headers(auth)
    assert "super-secret-123" not in str(exc.value)
    assert "MISSING_X" in str(exc.value)


# ---------------------------------------------------------------------------
# api_key_header
# ---------------------------------------------------------------------------


def test_api_key_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY_VAL", "k-abc-123")
    auth = AuthSpec(type="api_key_header", credential_ref="API_KEY_VAL", header_name="X-Api-Key")
    headers = build_auth_headers(auth)
    assert headers == {"X-Api-Key": "k-abc-123"}


def test_api_key_header_missing_header_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY_VAL", "k")
    # Create valid then clear header_name to test runtime check
    auth = AuthSpec(type="api_key_header", credential_ref="API_KEY_VAL", header_name="X-K")
    auth.header_name = None  # type: ignore[assignment]
    with pytest.raises(AuthError, match="header_name"):
        build_auth_headers(auth)


def test_api_key_missing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_API_KEY", raising=False)
    auth = AuthSpec(type="api_key_header", credential_ref="MISSING_API_KEY", header_name="X-Key")
    with pytest.raises(AuthError, match="Missing env var"):
        build_auth_headers(auth)


# ---------------------------------------------------------------------------
# basic
# ---------------------------------------------------------------------------


def test_basic_builds_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASIC_PW", "p@ssw0rd")
    auth = AuthSpec(type="basic", credential_ref="BASIC_PW", username="alice")
    headers = build_auth_headers(auth)
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Basic ")
    decoded = base64.b64decode(headers["Authorization"].split(" ", 1)[1]).decode()
    assert decoded == "alice:p@ssw0rd"


def test_basic_missing_username(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASIC_PW", "pw")
    auth = AuthSpec(type="basic", credential_ref="BASIC_PW", username="bob")
    auth.username = None  # type: ignore[assignment]
    with pytest.raises(AuthError, match="username"):
        build_auth_headers(auth)


def test_basic_missing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_BASIC", raising=False)
    auth = AuthSpec(type="basic", credential_ref="MISSING_BASIC", username="bob")
    with pytest.raises(AuthError, match="Missing env var"):
        build_auth_headers(auth)


def test_basic_secret_not_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASIC_PW", "very-secret-pw")
    auth = AuthSpec(type="basic", credential_ref="BASIC_PW", username="bob")
    build_auth_headers(auth)
    # Header contains base64 of secret, but that's expected for transmission;
    # ensure error paths don't leak, and that we don't log secret elsewhere.
    # We check that AuthError for missing doesn't contain secret value.
    monkeypatch.delenv("MISSING2", raising=False)
    auth2 = AuthSpec(type="basic", credential_ref="MISSING2", username="bob")
    with pytest.raises(AuthError) as exc:
        build_auth_headers(auth2)
    assert "very-secret-pw" not in str(exc.value)


# ---------------------------------------------------------------------------
# unsupported
# ---------------------------------------------------------------------------


def test_unsupported_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANY", "tok")
    auth = AuthSpec(type="bearer", credential_ref="ANY")
    auth.type = "unknown"  # type: ignore[assignment]
    with pytest.raises(AuthError, match="Unsupported"):
        build_auth_headers(auth)  # type: ignore[arg-type]
    os.environ.pop("ANY", None)


# ---------------------------------------------------------------------------
# oauth2: build_auth_headers returns {} but validates secret
# ---------------------------------------------------------------------------


def test_oauth2_build_headers_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAUTH_SEC", "client-secret-xyz")
    auth = AuthSpec(
        type="oauth2_client_credentials",
        credential_ref="OAUTH_SEC",
        username="client123",
        token_url="https://auth.example.com/token",
        scopes=["read"],
    )
    headers = build_auth_headers(auth)
    assert headers == {}


def test_oauth2_build_headers_missing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_OAUTH2", raising=False)
    auth = AuthSpec(
        type="oauth2_client_credentials",
        credential_ref="MISSING_OAUTH2",
        username="cid",
        token_url="https://auth.example.com/token",
    )
    with pytest.raises(AuthError, match="Missing env var.*MISSING_OAUTH2"):
        build_auth_headers(auth)


# ---------------------------------------------------------------------------
# OAuth2TokenManager
# ---------------------------------------------------------------------------


def _make_oauth_auth(monkeypatch: pytest.MonkeyPatch, env_key: str = "OAUTH_SECRET") -> AuthSpec:
    monkeypatch.setenv(env_key, "my-secret")
    return AuthSpec(
        type="oauth2_client_credentials",
        credential_ref=env_key,
        username="my_client_id",
        token_url="https://auth.example.com/token",
        scopes=["read", "write"],
    )


def test_oauth2_manager_success(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _make_oauth_auth(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        assert request.url.path == "/token"
        # Check that request body contains expected fields but never log secret in test
        assert b"client_id" in request.content
        assert b"grant_type" in request.content
        return httpx.Response(200, json={"access_token": "tok123", "expires_in": 3600})

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    mgr = OAuth2TokenManager(auth, client_factory=factory)
    token = mgr.get_token()
    assert token == "tok123"
    assert mgr._token is not None
    assert mgr._token.access_token == "tok123"
    # Second call uses cache (no new HTTP)
    token2 = mgr.get_token()
    assert token2 == "tok123"


def test_oauth2_manager_fetch_token_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _make_oauth_auth(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"access_token": "direct-tok"})

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    mgr = OAuth2TokenManager(auth, client_factory=factory)
    tok = mgr.fetch_token()
    assert tok == "direct-tok"


def test_oauth2_manager_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _make_oauth_auth(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"access_token": "hdr-tok", "expires_in": 100})

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    mgr = OAuth2TokenManager(auth, client_factory=factory)
    headers = mgr.auth_header()
    assert headers == {"Authorization": "Bearer hdr-tok"}


def test_oauth2_manager_caches_without_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _make_oauth_auth(monkeypatch)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        calls.append(1)
        return httpx.Response(200, json={"access_token": "no-expiry-tok"})

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    mgr = OAuth2TokenManager(auth, client_factory=factory)
    t1 = mgr.get_token()
    t2 = mgr.get_token()
    assert t1 == t2 == "no-expiry-tok"
    assert len(calls) == 1  # Only one fetch


def test_oauth2_manager_expiry_and_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _make_oauth_auth(monkeypatch)
    token_calls = []

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        token_calls.append(request.url.path)
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": f"tok{len(token_calls)}", "expires_in": 1})
        return httpx.Response(200, json={"data": []})

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    mgr = OAuth2TokenManager(auth, client_factory=factory)
    t1 = mgr.get_token()
    # Fast-forward time beyond expiry + margin (10s)
    original = time.monotonic
    start = original()
    monkeypatch.setattr(time, "monotonic", lambda: start + 20)
    t2 = mgr.get_token()
    assert t1 != t2
    assert len([c for c in token_calls if c == "/token"]) == 2


def test_oauth2_manager_with_explicit_time_fn(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _make_oauth_auth(monkeypatch)
    fake_time = [1000.0]

    def time_fn():
        return fake_time[0]

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"access_token": "tok-timefn", "expires_in": 100})

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    mgr = OAuth2TokenManager(auth, client_factory=factory, time_fn=time_fn)
    t1 = mgr.get_token()
    assert t1 == "tok-timefn"
    # Still cached
    t2 = mgr.get_token()
    assert t2 == t1
    # Advance beyond expiry
    fake_time[0] += 200
    # Should fetch again (handler returns same token but call count increases)
    calls = []

    def handler2(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        calls.append(1)
        return httpx.Response(200, json={"access_token": "tok2", "expires_in": 100})

    transport2 = httpx.MockTransport(handler2)

    def factory2(**kwargs):
        return httpx.Client(transport=transport2, timeout=kwargs.get("timeout", 5))

    mgr.client_factory = factory2
    t3 = mgr.get_token()
    assert t3 == "tok2"
    assert len(calls) == 1


def test_oauth2_manager_clear_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _make_oauth_auth(monkeypatch)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        calls.append(1)
        return httpx.Response(200, json={"access_token": f"tok{len(calls)}", "expires_in": 3600})

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    mgr = OAuth2TokenManager(auth, client_factory=factory)
    t1 = mgr.get_token()
    mgr.clear_cache()
    t2 = mgr.get_token()
    assert t1 != t2
    assert len(calls) == 2


def test_oauth2_manager_missing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_OAUTH_MGR", raising=False)
    auth = AuthSpec(
        type="oauth2_client_credentials",
        credential_ref="MISSING_OAUTH_MGR",
        username="cid",
        token_url="https://auth.example.com/token",
    )

    def factory(**_kwargs):  # type: ignore[no-untyped-def]
        return httpx.Client()

    mgr = OAuth2TokenManager(auth, client_factory=factory)  # type: ignore[arg-type]
    with pytest.raises(AuthError, match="Missing env var.*MISSING_OAUTH_MGR"):
        mgr.fetch_token()


def test_oauth2_manager_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _make_oauth_auth(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(401, json={"error": "bad client"})

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    mgr = OAuth2TokenManager(auth, client_factory=factory)
    with pytest.raises(TokenFetchError, match="returned 401") as exc:
        mgr.fetch_token()
    assert exc.value.status_code == 401
    assert "my-secret" not in str(exc.value)


def test_oauth2_manager_not_json(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _make_oauth_auth(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, content=b"not json")

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    mgr = OAuth2TokenManager(auth, client_factory=factory)
    with pytest.raises(TokenFetchError, match="not JSON"):
        mgr.fetch_token()


def test_oauth2_manager_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _make_oauth_auth(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"no_token": "x"})

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    mgr = OAuth2TokenManager(auth, client_factory=factory)
    with pytest.raises(TokenFetchError, match="missing access_token"):
        mgr.fetch_token()


def test_oauth2_manager_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _make_oauth_auth(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        raise httpx.ConnectError("dns fail")

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    mgr = OAuth2TokenManager(auth, client_factory=factory)
    with pytest.raises(TokenFetchError, match="token fetch failed"):
        mgr.fetch_token()
    # Secret not in error
    try:
        mgr.fetch_token()
    except TokenFetchError as exc:
        assert "my-secret" not in str(exc)


def test_oauth2_manager_init_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANY", "tok")
    # Wrong type
    bad = AuthSpec(type="bearer", credential_ref="ANY")
    with pytest.raises(AuthError, match="requires oauth2"):
        OAuth2TokenManager(bad)  # type: ignore[arg-type]
    # Missing token_url — create valid then clear
    no_url = AuthSpec(
        type="oauth2_client_credentials", credential_ref="ANY", username="cid", token_url="https://a.com/token"
    )
    no_url.token_url = None  # type: ignore[assignment]
    with pytest.raises(AuthError, match="token_url"):
        OAuth2TokenManager(no_url)
    # Missing username — create valid then clear
    no_user = AuthSpec(
        type="oauth2_client_credentials", credential_ref="ANY", username="cid", token_url="https://a.com/token"
    )
    no_user.username = None  # type: ignore[assignment]
    with pytest.raises(AuthError, match="username"):
        OAuth2TokenManager(no_user)
    os.environ.pop("ANY", None)


def test_oauth2_manager_scope_included(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _make_oauth_auth(monkeypatch)

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        # Parse form body
        body = request.content.decode()
        # Should contain scope
        captured["body"] = body
        assert "scope=read+write" in body or "scope=read%20write" in body or "read" in body
        return httpx.Response(200, json={"access_token": "scoped-tok"})

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    mgr = OAuth2TokenManager(auth, client_factory=factory)
    tok = mgr.get_token()
    assert tok == "scoped-tok"
    assert "read" in captured["body"]


def test_oauth2_manager_expires_in_not_number(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _make_oauth_auth(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"access_token": "tok", "expires_in": "not-a-number"})

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    mgr = OAuth2TokenManager(auth, client_factory=factory)
    tok = mgr.get_token()
    assert tok == "tok"
    assert mgr._token is not None
    assert mgr._token.expires_at is None
    # Second call should use cache (no expiry)
    tok2 = mgr.get_token()
    assert tok2 == tok


# ---------------------------------------------------------------------------
# get_auth_headers helper
# ---------------------------------------------------------------------------


def test_get_auth_headers_none() -> None:
    assert get_auth_headers(None) == {}


def test_get_auth_headers_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEARER_TOK", "abc123")
    auth = AuthSpec(type="bearer", credential_ref="BEARER_TOK")
    assert get_auth_headers(auth) == {"Authorization": "Bearer abc123"}


def test_get_auth_headers_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K", "v")
    auth = AuthSpec(type="api_key_header", credential_ref="K", header_name="X-K")
    assert get_auth_headers(auth) == {"X-K": "v"}


def test_get_auth_headers_oauth2_with_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _make_oauth_auth(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"access_token": "mgr-tok", "expires_in": 3600})

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    mgr = OAuth2TokenManager(auth, client_factory=factory)
    headers = get_auth_headers(auth, oauth_manager=mgr)
    assert headers == {"Authorization": "Bearer mgr-tok"}


def test_get_auth_headers_oauth2_ephemeral(monkeypatch: pytest.MonkeyPatch) -> None:
    # When no manager passed, an ephemeral manager is created internally.
    # We can't easily mock its HTTP without patching, but we can ensure it
    # doesn't crash when env is set and token_url is mocked via httpx default?
    # Instead we test that it tries to fetch and fails with TokenFetchError if no server.
    # We'll just check that the function exists and handles bearer case without manager.
    monkeypatch.setenv("BEARER_TOK", "abc")
    auth_bearer = AuthSpec(type="bearer", credential_ref="BEARER_TOK")
    assert get_auth_headers(auth_bearer, oauth_manager=None) == {"Authorization": "Bearer abc"}


def test_get_auth_headers_oauth2_ephemeral_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch OAuth2TokenManager to avoid real HTTP
    auth = _make_oauth_auth(monkeypatch)

    # Mock the token endpoint via httpx MockTransport injected through default factory?
    # Instead, we patch the class's fetch to return a token, testing the fallback path.
    original_init = OAuth2TokenManager.__init__

    def patched_init(self, auth, **kwargs):  # type: ignore[no-untyped-def]
        # Use a factory that returns a mock token
        def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
            return httpx.Response(200, json={"access_token": "ephemeral-tok"})

        transport = httpx.MockTransport(handler)

        def factory(**kw):
            return httpx.Client(transport=transport, timeout=kw.get("timeout", 5))

        original_init(self, auth, client_factory=factory, **{k: v for k, v in kwargs.items() if k != "client_factory"})

    monkeypatch.setattr(OAuth2TokenManager, "__init__", patched_init)
    headers = get_auth_headers(auth)
    assert headers == {"Authorization": "Bearer ephemeral-tok"}


# ---------------------------------------------------------------------------
# Secrets never logged / never in API responses
# ---------------------------------------------------------------------------


def test_secrets_never_in_auth_error_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET_KEY", "top-secret-value-12345")
    # Try all types with missing env (different key) and ensure secret not leaked
    for ref_missing in ["MISSING_A", "MISSING_B"]:
        monkeypatch.delenv(ref_missing, raising=False)
        for auth_type, kwargs in [
            ("bearer", {}),
            ("api_key_header", {"header_name": "X-K"}),
            ("basic", {"username": "u"}),
        ]:
            auth = AuthSpec(type=auth_type, credential_ref=ref_missing, **kwargs)  # type: ignore[arg-type]
            with pytest.raises(AuthError) as exc:
                build_auth_headers(auth)
            assert "top-secret-value-12345" not in str(exc.value)
            assert ref_missing in str(exc.value)


def test_oauth2_secret_not_in_token_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAUTH_SECRET_LEAK", "leak-me-not-999")
    auth = AuthSpec(
        type="oauth2_client_credentials",
        credential_ref="OAUTH_SECRET_LEAK",
        username="cid",
        token_url="https://auth.example.com/token",
    )

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(500, json={"error": "server"})

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    mgr = OAuth2TokenManager(auth, client_factory=factory)
    with pytest.raises(TokenFetchError) as exc:
        mgr.fetch_token()
    assert "leak-me-not-999" not in str(exc.value)
    assert "leak-me-not-999" not in repr(exc.value)


def test_bearer_header_does_not_log_secret_in_exception():
    # Ensure that when we construct headers, the secret is only in the returned dict
    # and not in any log; we simulate by checking that the function's error path
    # doesn't include secret value (covered above) and that success path's header
    # is correctly formed.
    os.environ["CHECK_SECRET"] = "check-123"
    auth = AuthSpec(type="bearer", credential_ref="CHECK_SECRET")
    headers = build_auth_headers(auth)
    assert headers["Authorization"] == "Bearer check-123"
    # The secret is intentionally in the header for transmission, but should never
    # be logged. We verify that AuthError for a different ref doesn't contain it.
    os.environ.pop("MISSING_CHECK", None)
    auth2 = AuthSpec(type="bearer", credential_ref="MISSING_CHECK")
    with pytest.raises(AuthError) as exc:
        build_auth_headers(auth2)
    assert "check-123" not in str(exc.value)
    del os.environ["CHECK_SECRET"]


# ---------------------------------------------------------------------------
# OAuth2Token dataclass
# ---------------------------------------------------------------------------


def test_oauth2_token_dataclass() -> None:
    t = OAuth2Token(access_token="abc", expires_at=123.0)
    assert t.access_token == "abc"
    assert t.expires_at == 123.0
    t2 = OAuth2Token(access_token="xyz")
    assert t2.expires_at is None


# ---------------------------------------------------------------------------
# body_fields (spec v2, REQ-020.5a)
# ---------------------------------------------------------------------------


def _body_fields_auth() -> AuthSpec:
    return AuthSpec(
        type="body_fields",
        body_fields={"ClientToken": "VENDOR_CLIENT_TOKEN", "AccessToken": "VENDOR_ACCESS_TOKEN"},
    )


def test_body_fields_builds_nested_body(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.runtime.auth import build_auth_body

    monkeypatch.setenv("VENDOR_CLIENT_TOKEN", "ct-secret")
    monkeypatch.setenv("VENDOR_ACCESS_TOKEN", "at-secret")
    body = build_auth_body(_body_fields_auth())
    assert body == {"ClientToken": "ct-secret", "AccessToken": "at-secret"}


def test_body_fields_supports_dotted_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.runtime.auth import build_auth_body

    monkeypatch.setenv("VENDOR_CLIENT_TOKEN", "ct-secret")
    auth = AuthSpec(type="body_fields", body_fields={"Auth.ClientToken": "VENDOR_CLIENT_TOKEN"})
    assert build_auth_body(auth) == {"Auth": {"ClientToken": "ct-secret"}}


def test_body_fields_missing_secret_names_ref_not_value(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.runtime.auth import build_auth_body

    monkeypatch.delenv("VENDOR_CLIENT_TOKEN", raising=False)
    monkeypatch.setenv("VENDOR_ACCESS_TOKEN", "at-secret")
    with pytest.raises(AuthError) as exc:
        build_auth_body(_body_fields_auth())
    assert "VENDOR_CLIENT_TOKEN" in str(exc.value)
    assert "ct-secret" not in str(exc.value)


def test_body_fields_headers_stay_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VENDOR_CLIENT_TOKEN", "ct-secret")
    monkeypatch.setenv("VENDOR_ACCESS_TOKEN", "at-secret")
    assert build_auth_headers(_body_fields_auth()) == {}


def test_body_fields_wrong_type_rejected() -> None:
    from autobridge.runtime.auth import build_auth_body

    auth = AuthSpec(type="bearer", credential_ref="SOME_KEY")
    with pytest.raises(AuthError, match="body_fields"):
        build_auth_body(auth)


def test_auth_body_refs_lists_all_refs() -> None:
    from autobridge.runtime.auth import auth_body_refs

    assert sorted(auth_body_refs(_body_fields_auth())) == ["VENDOR_ACCESS_TOKEN", "VENDOR_CLIENT_TOKEN"]
    assert auth_body_refs(None) == []
    assert auth_body_refs(AuthSpec(type="bearer", credential_ref="SOME_KEY")) == ["SOME_KEY"]
