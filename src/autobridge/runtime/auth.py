"""Auth strategies for ConnectorSpec (P1.W3.2, plan §2.4).

- Four types: bearer, api_key_header, basic, oauth2_client_credentials.
- Credentials are read by ``credential_ref`` from the environment only — the
  value is never stored in a spec, release, log, prompt or API response
  (DIRECTIVES §6).
- No function in this module logs a secret value; error messages name only
  ``credential_ref`` (the env key), never the value.

Trust boundary: no import of :mod:`verifier` or :mod:`simulators`.
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

    from autobridge.spec.models import AuthSpec


class AuthError(RuntimeError):
    """Missing credential, bad config or unsupported auth type."""


class TokenFetchError(RuntimeError):
    """OAuth2 token endpoint returned an error or non-JSON."""

    def __init__(self, message: str, *, status_code: int | None = None, response: Any | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = response


@dataclass(slots=True)
class OAuth2Token:
    """Cached OAuth2 access token with optional expiry."""

    access_token: str
    expires_at: float | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_secret(credential_ref: str) -> str:
    """Read secret value for *credential_ref* from env.

    Raises :class:`AuthError` naming only the ref (never the value) when
    the env var is missing or empty. Callers must not log the return value.
    """
    value = os.environ.get(credential_ref)
    if not value:
        raise AuthError(f"Missing env var {credential_ref!r} for auth credential_ref")
    return value


def build_auth_headers(auth: AuthSpec) -> dict[str, str]:
    """Build auth header(s) for non-OAuth2 types from *auth*.

    Reads the secret via ``credential_ref`` from the environment. For
    ``oauth2_client_credentials`` this returns an empty dict — callers
    must obtain a bearer token via :class:`OAuth2TokenManager` and add
    ``Authorization: Bearer <token>`` themselves.

    Raises :class:`AuthError` when the env var is missing or the type is
    unsupported. No secret value is logged or included in the message.
    """
    if auth.type == "oauth2_client_credentials":
        # Validate that credential exists without exposing it; actual token
        # fetch is handled by OAuth2TokenManager.
        _get_secret(auth.credential_ref)
        return {}

    secret = _get_secret(auth.credential_ref)

    if auth.type == "bearer":
        return {"Authorization": f"Bearer {secret}"}

    if auth.type == "api_key_header":
        if not auth.header_name:
            raise AuthError("api_key_header auth requires header_name")
        return {auth.header_name: secret}

    if auth.type == "basic":
        if not auth.username:
            raise AuthError("basic auth requires username (password in credential_ref)")
        raw = f"{auth.username}:{secret}".encode()
        token = base64.b64encode(raw).decode()
        return {"Authorization": f"Basic {token}"}

    raise AuthError(f"Unsupported auth type {auth.type!r}")


# ---------------------------------------------------------------------------
# OAuth2 client-credentials flow
# ---------------------------------------------------------------------------


class OAuth2TokenManager:
    """Manages OAuth2 client-credentials token fetch + cache.

    - Reads client secret via ``credential_ref`` from env (never logged).
    - POSTs ``grant_type=client_credentials`` with ``client_id`` (from
      ``auth.username``), ``client_secret`` and optional ``scope`` to
      ``auth.token_url``.
    - Caches the token until ``expires_in`` (minus 10s safety margin).
    - ``client_factory`` and ``time_fn`` are injectable for tests.

    The manager never logs secret values; errors name only URLs/statuses
    and ``credential_ref``.
    """

    def __init__(
        self,
        auth: AuthSpec,
        *,
        timeout_s: float = 30.0,
        client_factory: Callable[..., httpx.Client] | None = None,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        if auth.type != "oauth2_client_credentials":
            raise AuthError(f"OAuth2TokenManager requires oauth2_client_credentials, got {auth.type!r}")
        if not auth.token_url:
            raise AuthError("oauth2_client_credentials auth requires token_url")
        if not auth.username:
            raise AuthError("oauth2_client_credentials auth requires username (client_id)")
        self.auth = auth
        self.timeout_s = timeout_s
        self.client_factory = client_factory or httpx.Client
        self.time_fn = time_fn
        self._token: OAuth2Token | None = None

    def _now(self) -> float:
        return self.time_fn() if self.time_fn is not None else time.monotonic()

    def _is_cached_valid(self) -> bool:
        tok = self._token
        if tok is None or tok.expires_at is None:
            # If no expiry, token is valid until it fails; we cache it.
            return tok is not None and tok.expires_at is None
        # 10s safety margin before expiry
        return self._now() < tok.expires_at - 10

    def get_token(self) -> str:
        """Return a valid access token, fetching if needed."""
        if self._is_cached_valid():
            assert self._token is not None
            return self._token.access_token
        return self.fetch_token()

    def fetch_token(self) -> str:
        """Force-fetch a new token from the token endpoint."""
        secret = _get_secret(self.auth.credential_ref)
        assert self.auth.token_url is not None
        assert self.auth.username is not None

        data: dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": self.auth.username,
            "client_secret": secret,
        }
        if self.auth.scopes:
            data["scope"] = " ".join(self.auth.scopes)

        url = self.auth.token_url

        try:
            with self.client_factory(timeout=self.timeout_s) as client:
                resp = client.post(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        except Exception as exc:
            # Never include secret in message
            raise TokenFetchError(f"oauth2 token fetch failed: {exc}") from exc

        if resp.status_code != 200:
            raise TokenFetchError(
                f"oauth2 token fetch returned {resp.status_code}",
                status_code=resp.status_code,
                response=resp.text,
            )

        try:
            body = resp.json()
        except Exception as exc:
            raise TokenFetchError(f"oauth2 token response is not JSON: {exc}") from exc

        token = body.get("access_token")
        if not token:
            raise TokenFetchError("oauth2 token response missing access_token")

        expires_in = body.get("expires_in")
        expires_at = None
        if isinstance(expires_in, (int, float)):
            expires_at = self._now() + float(expires_in)

        self._token = OAuth2Token(access_token=str(token), expires_at=expires_at)
        return self._token.access_token

    def auth_header(self) -> dict[str, str]:
        """Convenience: ``Authorization: Bearer <token>`` header dict."""
        token = self.get_token()
        return {"Authorization": f"Bearer {token}"}

    def clear_cache(self) -> None:
        """Clear cached token (e.g. on 401)."""
        self._token = None


# ---------------------------------------------------------------------------
# Convenience helper for HttpClient: single call to get headers for any type
# ---------------------------------------------------------------------------


def get_auth_headers(
    auth: AuthSpec | None,
    *,
    oauth_manager: OAuth2TokenManager | None = None,
) -> dict[str, str]:
    """Return auth headers for *auth*.

    - ``None`` → ``{}``
    - non-OAuth types → :func:`build_auth_headers`
    - ``oauth2_client_credentials`` → requires *oauth_manager* (or creates a
      short-lived one with default settings). Prefer passing a cached manager
      for token reuse.

    This helper never logs secret values.
    """
    if auth is None:
        return {}
    if auth.type != "oauth2_client_credentials":
        return build_auth_headers(auth)
    # OAuth2 path
    if oauth_manager is not None:
        return oauth_manager.auth_header()
    # Fallback: create ephemeral manager (no cache across calls)
    mgr = OAuth2TokenManager(auth)
    return mgr.auth_header()
