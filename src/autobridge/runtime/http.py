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

import os
import re
import time
from collections.abc import Callable  # noqa: TC003
from typing import TYPE_CHECKING, Any

import httpx

from autobridge.runtime.auth import (
    AuthError as _AuthError,
)
from autobridge.runtime.auth import (
    OAuth2Token as _OAuth2Token,
)
from autobridge.runtime.auth import (
    OAuth2TokenManager,
    TokenFetchError,
)
from autobridge.runtime.auth import (
    build_auth_headers as _build_auth_headers,
)

if TYPE_CHECKING:
    from autobridge.spec.models import AuthSpec, RetryPolicy

# Re-export for backward compat (tests import from http)
AuthError = _AuthError
OAuth2Token = _OAuth2Token
build_auth_headers = _build_auth_headers

# Placeholder pattern: ${UPPER_SNAKE}
_ENV_PLACEHOLDER_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")

_DEFAULT_BASE_DELAY_S = 0.05  # small so tests stay fast


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


# build_auth_headers and OAuth2Token are imported from auth (re-exported above)
# for backward compat; see autobridge.runtime.auth for implementation.


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
        self._oauth_manager: OAuth2TokenManager | None = None
        if auth is not None and auth.type == "oauth2_client_credentials":
            self._oauth_manager = OAuth2TokenManager(
                auth,
                timeout_s=timeout_s,
                client_factory=self.client_factory,
            )

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
        # Lazy init manager if not created (e.g. auth changed after init)
        if self._oauth_manager is None:
            self._oauth_manager = OAuth2TokenManager(
                self.auth, timeout_s=self.timeout_s, client_factory=self.client_factory
            )
        # Keep _oauth_token in sync for external inspection / legacy tests
        try:
            token = self._oauth_manager.get_token()
        except AuthError:
            raise
        except TokenFetchError as exc:
            # Translate to HttpError for backward compat (existing tests expect HttpError)
            if exc.status_code is not None:
                raise HttpError(str(exc), status_code=exc.status_code, response=exc.response) from exc
            raise HttpError(str(exc)) from exc
        # Sync legacy attribute
        self._oauth_token = self._oauth_manager._token
        return token

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
