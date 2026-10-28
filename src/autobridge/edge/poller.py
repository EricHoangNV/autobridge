"""Cloud loop for the edge agent (REQ-022 sub-step B).

Four loops, all outbound (the agent dials out; nothing dials in):

* poll — ``GET <relay>/v1/commands/next`` into the durable outbox;
* execute — oldest outbox command onto the FIAS link (one in flight,
  bounded wait), then ``POST <relay>/v1/commands/{id}/result``;
* forward — unforwarded inbox postings to ``POST <relay>/v1/postings``
  (the relay de-duplicates; the agent marks forwarded only after the
  POST succeeds);
* heartbeat — link + queue depths to ``POST <relay>/v1/heartbeats``.

Backoff toward the cloud: relay errors back off exponentially (capped)
without ever blocking the link — the PABX was already ACKed at the
durable-write boundary. Command expiry is enforced twice (at claim and
at the link slot): a stale command is reported ``expired`` and never
sent. ``link_down`` requeues for the next session up to
``EDGE_MAX_ATTEMPTS``; afterwards the command is reported ``failed``.

The agent token travels in exactly one place (the ``Authorization``
header, only when ``EDGE_AGENT_TOKEN`` is set) and is never logged —
only request ids, kinds and outcome names are.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Any

import httpx

from autobridge.edge.bundle import BundleError, render_command
from autobridge.edge.fias_host import _is_expired  # noqa: PLC2701 - same package, tested seam

if TYPE_CHECKING:
    from autobridge.edge.store import AgentStore

log = logging.getLogger("autobridge.edge.poller")


class _SharedClient:
    """Context manager around a shared test client (no close on exit)."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def __enter__(self) -> httpx.Client:
        return self._client

    def __exit__(self, *args: object) -> None:
        return None

AGENT_ID_ENV_VAR = "EDGE_AGENT_ID"
RELAY_URL_ENV_VAR = "EDGE_RELAY_URL"
AGENT_TOKEN_ENV_VAR = "EDGE_AGENT_TOKEN"
MAX_ATTEMPTS_ENV_VAR = "EDGE_MAX_ATTEMPTS"

DEFAULT_MAX_ATTEMPTS = 5
POLL_INTERVAL_S = 1.0
FORWARD_INTERVAL_S = 1.0
HEARTBEAT_INTERVAL_S = 10.0
BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 30.0


class RelayClient:
    """Minimal relay HTTP client (agent side; token never logged)."""

    def __init__(
        self,
        base_url: str,
        agent_token: str = "",
        timeout: float = 10.0,
        session: Any = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = agent_token
        self._timeout = timeout
        self._session = session  # sync session-like (TestClient in tests)
        self.last_contact_ok = False

    def _headers(self) -> dict[str, str]:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    def _client(self) -> Any:
        # Injected sessions (tests) are shared across calls and never
        # closed by us; plain httpx clients are per-call (pool hygiene).
        # trust_env=False: relay traffic is loopback/cluster-local and must
        # never consult system proxy settings (on macOS that touches the
        # Objective-C runtime from agent threads and poisons forked
        # children). Proxies are not used on this transport by design.
        if self._session is not None:
            return _SharedClient(self._session)
        return httpx.Client(base_url=self._base, headers=self._headers(),
                            timeout=self._timeout, trust_env=False)

    def next_command(self, agent_id: str) -> dict[str, Any] | None:
        try:
            with self._client() as client:
                resp = client.get("/v1/commands/next", params={"agent_id": agent_id})
        except httpx.HTTPError as exc:
            self.last_contact_ok = False
            log.warning("relay poll failed: %s", type(exc).__name__)
            return None
        self.last_contact_ok = resp.status_code in (200, 204)
        if resp.status_code == 204:
            return None
        if resp.status_code != 200:  # pragma: no cover - relay guards shape
            log.warning("relay poll status %s", resp.status_code)
            return None
        return resp.json()

    def report_result(self, request_id: str, status: str, detail: str = "") -> bool:
        try:
            with self._client() as client:
                resp = client.post(f"/v1/commands/{request_id}/result",
                                   json={"status": status, "detail": detail})
        except httpx.HTTPError as exc:
            self.last_contact_ok = False
            log.warning("relay result failed for %s: %s", request_id, type(exc).__name__)
            return False
        self.last_contact_ok = resp.status_code == 200
        return resp.status_code == 200

    def forward_posting(self, agent_id: str, key: str, record_type: str,
                        fields: dict[str, str], occurred_at: str | None) -> bool:
        try:
            with self._client() as client:
                resp = client.post("/v1/postings", json={
                    "agent_id": agent_id, "idempotency_key": key,
                    "record_type": record_type, "fields": fields, "occurred_at": occurred_at,
                })
        except httpx.HTTPError as exc:
            self.last_contact_ok = False
            log.warning("relay posting failed for %s: %s", key, type(exc).__name__)
            return False
        self.last_contact_ok = resp.status_code == 200
        return resp.status_code == 200

    def heartbeat(self, agent_id: str, link_up: bool, link_detail: str,
                  outbox_depth: int, inbox_depth: int) -> bool:
        try:
            with self._client() as client:
                resp = client.post("/v1/heartbeats", json={
                    "agent_id": agent_id, "link_up": link_up, "link_detail": link_detail,
                    "outbox_depth": outbox_depth, "inbox_depth": inbox_depth,
                })
        except httpx.HTTPError:
            self.last_contact_ok = False
            return False
        self.last_contact_ok = resp.status_code == 200
        return resp.status_code == 200


def _backoff(failures: int) -> float:
    return min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** min(failures, 5)))


class Agent:
    """Owns store + link + relay client; runs the four cloud loops."""

    def __init__(
        self,
        agent_id: str,
        store: AgentStore,
        relay: RelayClient,
        bundle: dict[str, Any],
        link: Any,  # FiasHost (duck-typed for tests)
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        command_timeout: float | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.store = store
        self.relay = relay
        self.bundle = bundle
        self.link = link
        self.max_attempts = max_attempts
        self.command_timeout = command_timeout
        self._halt = threading.Event()
        self._threads: list[threading.Thread] = []

    # -- single steps (unit-testable without threads) ------------------

    def poll_once(self) -> str:
        """Fetch one relay command into the outbox (``empty``/``stored``/``relay_down``)."""
        row = self.relay.next_command(self.agent_id)
        if row is None:
            return "empty" if self.relay.last_contact_ok else "relay_down"
        fresh = self.store.enqueue_command(
            row["request_id"], row["kind"], row.get("params") or {},
            row.get("idempotency_key"), row.get("expires_at"),
        )
        log.info("polled request=%s kind=%s fresh=%s", row["request_id"], row["kind"], fresh)
        return "stored"

    def execute_once(self) -> str:
        """Run the oldest queued command (``idle`` when none)."""
        cmd = self.store.next_command()
        if cmd is None:
            return "idle"
        request_id = cmd["request_id"]
        if _is_expired(cmd["expires_at"]):
            # Stale commands are reported expired and never sent (REQ-022 B).
            self.store.finish_command(request_id, "expired", "past expires_at; never sent")
            self.relay.report_result(request_id, "expired", "past expires_at; never sent")
            log.info("expired request=%s kind=%s", request_id, cmd["kind"])
            return "expired"
        if cmd["attempts"] > self.max_attempts:
            self.store.finish_command(request_id, "failed", f"{self.max_attempts} attempts; giving up")
            self.relay.report_result(request_id, "failed", f"{self.max_attempts} attempts; giving up")
            return "failed"
        try:
            record_type, fields, expect = render_command(self.bundle, cmd["kind"], cmd["params"])
        except BundleError as exc:
            self.store.finish_command(request_id, "rejected", str(exc))
            self.relay.report_result(request_id, "rejected", str(exc))
            log.info("rejected request=%s kind=%s: %s", request_id, cmd["kind"], exc)
            return "rejected"
        outcome, _answer = self.link.send_command(
            record_type, fields, expect,
            timeout=self.command_timeout, expires_at=cmd["expires_at"],
        )
        if outcome == "link_down":
            self.store.requeue_command(request_id)
            log.info("link_down request=%s kind=%s requeued", request_id, cmd["kind"])
            return "link_down"
        detail = "link ACK" if outcome == "sent" and expect == "ack" else f"answer {expect}"
        if outcome == "ack_timeout":
            detail = "bounded wait exceeded; link stays up"
        self.store.finish_command(request_id, outcome, detail)
        self.relay.report_result(request_id, outcome, detail)
        log.info("request=%s kind=%s outcome=%s", request_id, cmd["kind"], outcome)
        return outcome

    def forward_once(self) -> str:
        """Forward one unforwarded posting (``idle`` when none)."""
        row = self.store.next_unforwarded()
        if row is None:
            return "idle"
        ok = self.relay.forward_posting(
            self.agent_id, row["idempotency_key"], row["record_type"], row["fields"], row["occurred_at"]
        )
        if ok:
            self.store.mark_forwarded(row["idempotency_key"])
            return "forwarded"
        return "relay_down"

    def heartbeat_once(self) -> bool:
        try:
            state = self.link.link_state()
        except Exception:  # pragma: no cover - defensive
            state = {"link_up": False}
        depths = self.store.depths()
        detail = ""
        if isinstance(state, dict):
            detail = f"accepted={state.get('links_accepted', 0)} dropped={state.get('links_dropped', 0)}"
            link_up = bool(state.get("link_up", False))
        else:  # pragma: no cover - duck-typed fakes
            link_up = False
        return self.relay.heartbeat(self.agent_id, link_up, detail, depths["outbox"], depths["inbox"])

    # -- threads ---------------------------------------------------------

    def start(self) -> None:
        self._halt.clear()
        self._threads = [
            threading.Thread(name=f"edge-{name}", daemon=True, target=loop)
            for name, loop in (
                ("poll", self._poll_loop),
                ("exec", self._exec_loop),
                ("forward", self._forward_loop),
                ("heartbeat", self._heartbeat_loop),
            )
        ]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._halt.set()
        for thread in self._threads:
            thread.join(timeout=5.0)
        self._threads = []

    def _poll_loop(self) -> None:
        failures = 0
        while not self._halt.is_set():
            outcome = self.poll_once()
            if outcome == "relay_down":
                failures += 1
                self._halt.wait(_backoff(failures))
            else:
                failures = 0
                self._halt.wait(0 if outcome == "stored" else POLL_INTERVAL_S)

    def _exec_loop(self) -> None:
        idle_backoff = 0
        while not self._halt.is_set():
            outcome = self.execute_once()
            if outcome == "idle":
                idle_backoff = min(idle_backoff + 1, 5)
                self._halt.wait(POLL_INTERVAL_S if idle_backoff < 5 else 5.0)
            elif outcome == "link_down":
                idle_backoff = 0
                self._halt.wait(2.0)  # let the listener accept the redial
            else:
                idle_backoff = 0

    def _forward_loop(self) -> None:
        failures = 0
        while not self._halt.is_set():
            outcome = self.forward_once()
            if outcome == "relay_down":
                failures += 1
                self._halt.wait(_backoff(failures))
            else:
                failures = 0
                self._halt.wait(0 if outcome == "forwarded" else FORWARD_INTERVAL_S)

    def _heartbeat_loop(self) -> None:
        while not self._halt.is_set():
            try:
                self.heartbeat_once()
            except Exception:  # pragma: no cover - defensive
                log.exception("heartbeat failed")
            self._halt.wait(HEARTBEAT_INTERVAL_S)


def agent_id_from_env(default: str = "grand-plaza-pabx") -> str:
    import os

    return (os.environ.get(AGENT_ID_ENV_VAR, "") or "").strip() or default


def relay_url_from_env(default: str = "http://edge-relay:8000") -> str:
    import os

    return (os.environ.get(RELAY_URL_ENV_VAR, "") or "").strip() or default


def relay_client_from_env(**kwargs: Any) -> RelayClient:
    import os

    return RelayClient(
        relay_url_from_env(), agent_token=(os.environ.get(AGENT_TOKEN_ENV_VAR, "") or "").strip(), **kwargs
    )


def max_attempts_from_env() -> int:
    import os

    try:
        return max(1, int((os.environ.get(MAX_ATTEMPTS_ENV_VAR, "") or "").strip() or DEFAULT_MAX_ATTEMPTS))
    except ValueError:
        return DEFAULT_MAX_ATTEMPTS


def json_detail(value: Any) -> str:
    """Render a relay result row for logs (never includes tokens)."""
    return json.dumps(value, sort_keys=True)[:500]
