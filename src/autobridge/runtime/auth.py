"""Auth strategies for ConnectorSpec (P1.W3.2, plan §2.4).

- Five types: bearer, api_key_header, basic, oauth2_client_credentials,
  body_fields (spec v2: secrets placed into named JSON body fields).
- Credentials are read by ``credential_ref`` (or each ``body_fields`` ref)
  from the environment only — the value is never stored in a spec, release,
  log, prompt or API response (DIRECTIVES §6).
- No function in this module logs a secret value; error messages name only
  refs (env keys), never values.

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
    """Build auth header(s) for non-OAuth2 header types from *auth*.

    Reads the secret via ``credential_ref`` from the environment. For
    ``oauth2_client_credentials`` this returns an empty dict — callers
    must obtain a bearer token via :class:`OAuth2TokenManager` and add
    ``Authorization: Bearer <token>`` themselves. For ``body_fields``
    (spec v2) this returns an empty dict — secrets go into the JSON body
    via :func:`build_auth_body`, never into headers.

    Raises :class:`AuthError` when the env var is missing or the type is
    unsupported. No secret value is logged or included in the message.
    """
    if auth.type == "oauth2_client_credentials":
        # Validate that credential exists without exposing it; actual token
        # fetch is handled by OAuth2TokenManager.
        if auth.credential_ref is None:
            raise AuthError("oauth2_client_credentials auth requires credential_ref")
        _get_secret(auth.credential_ref)
        return {}

    if auth.type == "body_fields":
        # Body-placed credentials never become headers; validate refs exist.
        build_auth_body(auth)
        return {}

    if auth.credential_ref is None:
        raise AuthError(f"{auth.type} auth requires credential_ref")
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


def _set_dotted(target: dict[str, Any], path: str, value: Any) -> None:
    """Set *value* at dotted *path* inside *target* (creating intermediates)."""
    parts = path.split(".")
    node = target
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def build_auth_body(auth: AuthSpec) -> dict[str, Any]:
    """Build JSON-body auth fields for ``body_fields`` auth (spec v2).

    Returns ``{body field path: secret value}`` as a nested dict, reading
    each secret via its ``credential_ref`` from the environment. Error
    messages name only refs (env keys), never values. Callers must not log
    the returned values; they are redacted like any other secret.
    """
    if auth.type != "body_fields":
        raise AuthError(f"build_auth_body requires body_fields auth, got {auth.type!r}")
    if not auth.body_fields:
        raise AuthError("body_fields auth requires body_fields mapping")
    out: dict[str, Any] = {}
    for field_path, ref in auth.body_fields.items():
        _set_dotted(out, field_path, _get_secret(ref))
    return out


def auth_body_refs(auth: AuthSpec | None) -> list[str]:
    """Return every credential ref *auth* needs (for redaction checks).

    Header types need ``[credential_ref]``; ``body_fields`` needs every
    value of its mapping; ``None`` needs nothing.
    """
    if auth is None:
        return []
    if auth.type == "body_fields":
        return list((auth.body_fields or {}).values())
    return [auth.credential_ref] if auth.credential_ref is not None else []


def spec_auth_refs(auth: dict[str, Any] | None) -> list[str]:
    """Return every credential ref in a plain spec-dict ``auth`` mapping.

    Dict-level counterpart of :func:`auth_body_refs` for callers that hold
    the spec as data (releases, Connector rows, evidence) rather than a
    validated :class:`AuthSpec`: ``body_fields`` yields every value of its
    mapping, header types yield ``credential_ref``, anything else yields
    nothing. Never raises — unknown shapes simply need nothing.
    """
    if not isinstance(auth, dict):
        return []
    if auth.get("type") == "body_fields":
        body_fields = auth.get("body_fields")
        if isinstance(body_fields, dict):
            return [ref for ref in body_fields.values() if isinstance(ref, str) and ref]
        return []
    ref = auth.get("credential_ref")
    return [ref] if isinstance(ref, str) and ref else []


def primary_credential_ref(auth: dict[str, Any] | None, fallback: str = "CONNECTOR_API_KEY") -> str:
    """One representative credential ref for a spec-dict ``auth`` mapping.

    Single-ref auth returns its ``credential_ref``; ``body_fields`` auth
    returns the first ref in sorted field order (deterministic); anything
    else returns *fallback*. Used where a single ref must be stored (e.g.
    the Connector row) while the full ref list stays on the spec — the
    read-time redaction sweep covers every ref by env pattern.
    """
    refs = spec_auth_refs(auth)
    if not refs:
        return fallback
    if isinstance(auth, dict) and auth.get("type") == "body_fields":
        body_fields = auth.get("body_fields")
        if isinstance(body_fields, dict) and body_fields:
            first_field = sorted(str(k) for k in body_fields)
            first_ref = body_fields[first_field[0]]
            if isinstance(first_ref, str) and first_ref:
                return first_ref
    return refs[0]


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
        if self.auth.credential_ref is None:
            raise AuthError("oauth2_client_credentials auth requires credential_ref")
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
    - ``oauth2_client_credentials`` → requires *oauth_manager* (or creates a      short-lived one with default settings). Prefer passing a cached manager
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
