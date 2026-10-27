"""HTTP execution with retry/backoff per ``rate_limit.retry`` (P1.W3.1).

- ``resolve_base_url`` expands ``${ENV}`` placeholders via :mod:`os.environ`.
- Auth headers are built from ``credential_ref`` (env key name only) — secret
  values are never logged (DIRECTIVES §6).
- Retry honours ``RetryPolicy`` (statuses / backoff / max_attempts) with
  exponential / linear / constant backoff; ``Retry-After`` overrides backoff
  when present and numeric.
- Every model call goes through :mod:`autobridge.llm.provider`; this module
  never touches it, so a reference sync keeps ``ModelCall`` count 0.

Trust boundary: no import of :mod:`verifier` or :mod:`simulators`.
"""

from __future__ import annotations

import base64
import os
import re
import time
from collections.abc import Callable  # noqa: TC003
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from autobridge.spec.models import AuthSpec, RetryPolicy

# Placeholder pattern: ${UPPER_SNAKE}
_ENV_PLACEHOLDER_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")

_DEFAULT_BASE_DELAY_S = 0.05  # small so tests stay fast


class AuthError(RuntimeError):
    """Missing credential or unsupported auth type."""


class HttpError(RuntimeError):
    """Non-retryable HTTP error or retry budget exhausted."""

    def __init__(self, message: str, *, status_code: int | None = None, response: Any | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def resolve_env_placeholders(text: str) -> str:
    """Expand ``${ENV}`` placeholders using :mod:`os.environ`.

    Raises ``KeyError`` when a referenced env var is missing — the caller
    surfaces it as a config error, never as a secret value.
    """

    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in os.environ:
            raise KeyError(f"Missing env var for placeholder ${{{key}}} in base_url")
        return os.environ[key]

    return _ENV_PLACEHOLDER_RE.sub(repl, text)


def resolve_base_url(base_url: str) -> str:
    """Resolve ``base_url`` placeholders and validate the resulting URL."""
    resolved = resolve_env_placeholders(base_url)
    if not resolved.startswith(("http://", "https://")):
        raise ValueError(f"base_url {resolved!r} must be http(s) after placeholder resolution")
    # Strip trailing slash for predictable joining.
    return resolved.rstrip("/")


def compute_backoff(attempt: int, backoff: str, base_delay: float = _DEFAULT_BASE_DELAY_S) -> float:
    """Delay before retry number *attempt* (1-indexed retry count).

    *attempt* is the index of the retry about to happen (1 = first retry
    after the initial failure). ``backoff`` is one of ``exponential``,
    ``linear`` or ``constant`` from :class:`autobridge.spec.models.RetryPolicy`.
    """
    if backoff == "exponential":
        return base_delay * (2 ** (attempt - 1))
    if backoff == "linear":
        return base_delay * attempt
    # constant
    return base_delay


def _retry_after_seconds(response: httpx.Response) -> float | None:
    header = response.headers.get("Retry-After") or response.headers.get("retry-after")
    if header is None:
        return None
    try:
        # Numeric seconds; date form is ignored (rare for simulators).
        return float(header.strip())
    except ValueError:
        return None


def build_auth_headers(auth: AuthSpec) -> dict[str, str]:
    """Build auth header(s) from *auth*.

    Reads the secret from ``credential_ref`` env key.  For ``bearer`` and
    ``api_key_header`` the secret is the token; for ``basic`` it is the
    password paired with ``auth.username``; for ``oauth2_client_credentials``
    the secret is the client secret (token exchange is handled by
    :class:`HttpClient` when needed).
    """
    secret = os.environ.get(auth.credential_ref)
    if not secret:
        raise AuthError(f"Missing env var {auth.credential_ref!r} for auth credential_ref")
    if auth.type == "bearer":
        return {"Authorization": f"Bearer {secret}"}
    if auth.type == "api_key_header":
        assert auth.header_name is not None
        return {auth.header_name: secret}
    if auth.type == "basic":
        assert auth.username is not None
        raw = f"{auth.username}:{secret}".encode()
        token = base64.b64encode(raw).decode()
        return {"Authorization": f"Basic {token}"}
    if auth.type == "oauth2_client_credentials":
        # Token will be fetched on demand; return placeholder so caller can
        # detect that an exchange is required.
        # We still validate env presence here.
        return {}
    raise AuthError(f"Unsupported auth type {auth.type!r}")


@dataclass(slots=True)
class OAuth2Token:
    access_token: str
    expires_at: float | None = None


# ---------------------------------------------------------------------------
# HttpClient
# ---------------------------------------------------------------------------


class HttpClient:
    """Retry-aware HTTP client for a single :class:`ConnectorSpec` source.

    One client per source (base_url + auth + rate_limit).  A fresh
    :class:`httpx.Client` is created per request so the caller can inject a
    mock transport in tests via *client_factory*.
    """

    def __init__(
        self,
        base_url: str,
        auth: AuthSpec | None = None,
        retry: RetryPolicy | None = None,
        *,
        timeout_s: float = 30.0,
        base_delay_s: float = _DEFAULT_BASE_DELAY_S,
        sleep_fn: Callable[[float], None] = time.sleep,
        client_factory: Callable[..., httpx.Client] | None = None,
    ) -> None:
        self.base_url = resolve_base_url(base_url)
        self.auth = auth
        self.retry = retry
        self.timeout_s = timeout_s
        self.base_delay_s = base_delay_s
        self.sleep_fn = sleep_fn
        self.client_factory = client_factory or httpx.Client
        self._oauth_token: OAuth2Token | None = None

    # -- URL helpers -------------------------------------------------------

    def url_for(self, path: str) -> str:
        """Join ``base_url`` and *path* (both slash-normalized)."""
        if not path.startswith("/"):
            raise ValueError(f"operation path {path!r} must start with '/'")
        return f"{self.base_url}{path}"

    # -- auth --------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        if self.auth is None:
            return {}
        if self.auth.type == "oauth2_client_credentials":
            token = self._ensure_oauth_token()
            return {"Authorization": f"Bearer {token}"}
        return build_auth_headers(self.auth)

    def _ensure_oauth_token(self) -> str:
        assert self.auth is not None and self.auth.type == "oauth2_client_credentials"
        if (
            self._oauth_token is not None
            and self._oauth_token.expires_at is not None
            and time.monotonic() < self._oauth_token.expires_at - 10
        ):
            return self._oauth_token.access_token
        # Fetch token — use a short-lived httpx client.
        secret = os.environ.get(self.auth.credential_ref)
        if not secret:
            raise AuthError(f"Missing env var {self.auth.credential_ref!r} for oauth2 client secret")
        assert self.auth.token_url is not None
        assert self.auth.username is not None  # client_id
        data = {
            "grant_type": "client_credentials",
            "client_id": self.auth.username,
            "client_secret": secret,
        }
        if self.auth.scopes:
            data["scope"] = " ".join(self.auth.scopes)
        url = self.auth.token_url
        # Token fetch itself respects retry if the token_url is retryable; for
        # simplicity fetch without retry (failures are surfaced as HttpError).
        try:
            with self.client_factory(timeout=self.timeout_s) as client:
                resp = client.post(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        except Exception as exc:
            raise HttpError(f"oauth2 token fetch failed: {exc}") from exc
        if resp.status_code != 200:
            raise HttpError(
                f"oauth2 token fetch returned {resp.status_code}",
                status_code=resp.status_code,
                response=resp.text,
            )
        try:
            body = resp.json()
        except Exception as exc:
            raise HttpError(f"oauth2 token response is not JSON: {exc}") from exc
        token = body.get("access_token")
        if not token:
            raise HttpError("oauth2 token response missing access_token")
        expires_in = body.get("expires_in")
        expires_at = None
        if isinstance(expires_in, (int, float)):
            expires_at = time.monotonic() + float(expires_in)
        self._oauth_token = OAuth2Token(access_token=str(token), expires_at=expires_at)
        return self._oauth_token.access_token

    # -- request -----------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Perform *method* request with retry/backoff.

        Retries only when ``response.status_code`` is in ``retry.statuses`` and
        ``attempt < retry.max_attempts``.  ``Retry-After`` header, when numeric,
        overrides the computed backoff.  Exhaustion raises :class:`HttpError`.
        Network errors (``httpx.RequestError``) are treated as retryable
        when a retry policy exists, otherwise they raise immediately.
        """
        url = self.url_for(path)
        merged_headers: dict[str, str] = {}
        # Auth headers first; explicit headers override them.
        merged_headers.update(self._auth_headers())
        if headers:
            merged_headers.update(headers)

        retry_statuses: set[int] = set(self.retry.statuses if self.retry else [])
        max_attempts = self.retry.max_attempts if self.retry else 1
        backoff = self.retry.backoff if self.retry else "exponential"

        last_exc: Exception | None = None
        last_response: httpx.Response | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                with self.client_factory(timeout=self.timeout_s) as client:
                    resp = client.request(method, url, params=params, headers=merged_headers, json=json_body)
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt >= max_attempts:
                    raise HttpError(f"request failed after {max_attempts} attempts: {exc}") from exc
                # Network error is retryable if policy exists; otherwise fail fast.
                if not retry_statuses:
                    raise HttpError(f"network error with no retry policy: {exc}") from exc
                delay = compute_backoff(attempt, backoff, self.base_delay_s)
                self.sleep_fn(delay)
                continue

            # Success path: 2xx
            if 200 <= resp.status_code < 300:
                return resp

            # Retryable status?
            if resp.status_code in retry_statuses and attempt < max_attempts:
                retry_after = _retry_after_seconds(resp)
                if retry_after is not None and retry_after >= 0:
                    delay = retry_after
                else:
                    delay = compute_backoff(attempt, backoff, self.base_delay_s)
                # Capture for diagnostics; do not log credential values.
                last_response = resp
                self.sleep_fn(delay)
                continue

            # Non-retryable or exhausted: raise with status.
            if resp.status_code in retry_statuses and attempt >= max_attempts:
                raise HttpError(
                    f"retry budget exhausted after {max_attempts} attempts (last status {resp.status_code})",
                    status_code=resp.status_code,
                    response=resp.text,
                )
            raise HttpError(
                f"HTTP {resp.status_code} for {method} {path}",
                status_code=resp.status_code,
                response=resp.text,
            )

        # Should not reach here; mypy guard.
        if last_exc is not None:
            raise HttpError(f"request failed: {last_exc}") from last_exc
        if last_response is not None:
            raise HttpError(
                f"retry exhausted (last status {last_response.status_code})",
                status_code=last_response.status_code,
            )
        raise HttpError("request failed with no response")

    def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """GET *path* with retry and return JSON body as dict.

        Raises :class:`HttpError` for non-JSON or error status.
        """
        resp = self.request("GET", path, params=params, headers=headers)
        try:
            data = resp.json()
        except Exception as exc:
            raise HttpError(f"response is not JSON: {exc}", status_code=resp.status_code) from exc
        if not isinstance(data, dict):
            raise HttpError(f"expected JSON object, got {type(data).__name__}", status_code=resp.status_code)
        return data
