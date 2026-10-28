# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Edge command-queue relay (REQ-022 sub-step B).

A dumb, authenticated pipe between the cloud control plane and the
on-premise edge agent — the "local queue service in compose" from REQ-022 B:

* The agent (edge-net only) polls ``GET /v1/commands/next`` and reports
  through ``POST /v1/commands/{id}/result`` + ``POST /v1/postings``.
  It never accepts inbound connections from the cloud.
* The control plane (engine-net only) enqueues through ``POST /v1/enqueue``
  and drains through ``GET /v1/results``, ``GET /v1/postings``,
  ``GET /v1/agents`` (proxied as ``/edge/*`` on control; the UI and demo
  only ever talk to control).

Durability: SQLite (``EDGE_RELAY_DB``), so a relay restart loses nothing
queued. Delivery is at-least-once: a command polled but never resulted is
re-delivered after ``REDELIVER_AFTER_S``; the agent de-duplicates on its
side (idempotency keys) and results are first-write-wins.

Auth (client-credentials style, never logged): agent endpoints need
``EDGE_AGENT_TOKEN``, operator endpoints need ``AUTOBRIDGE_CONTROL_TOKEN``.
Either side is open when its token is unset (local dev, same semantics as
REQ-012); when set, the bearer is compared with ``hmac.compare_digest``.

Trust boundary: this module lives in the engine package
(``src/autobridge``) and never imports ``verifier`` or ``simulators``.
"""

from __future__ import annotations

import datetime
import hmac
import logging
import os
import sqlite3
import threading
import uuid
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from autobridge.health import healthz, readyz

log = logging.getLogger("autobridge.edge.relay")

#: Env var naming the agent-side bearer token (client credentials).
AGENT_TOKEN_ENV_VAR = "EDGE_AGENT_TOKEN"
#: Env var naming the operator-side bearer token (same value as control's).
CONTROL_TOKEN_ENV_VAR = "AUTOBRIDGE_CONTROL_TOKEN"
#: Env var for the relay SQLite file.
RELAY_DB_ENV_VAR = "EDGE_RELAY_DB"
#: Seconds after which a delivered-but-unresulted command is re-delivered.
REDELIVER_AFTER_S = 120.0

RESULT_STATUSES = ("sent", "ack_timeout", "expired", "link_down", "failed", "rejected")

bearer_scheme = HTTPBearer(auto_error=False)


def _utcnow() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def get_agent_token() -> str:
    return os.environ.get(AGENT_TOKEN_ENV_VAR, "")


def get_operator_token() -> str:
    return os.environ.get(CONTROL_TOKEN_ENV_VAR, "")


def _check_bearer(credentials: HTTPAuthorizationCredentials | None, expected: str, what: str) -> None:
    if not expected:
        return
    presented = credentials.credentials if credentials is not None else ""
    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing or invalid {what} token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_agent_token(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> None:
    """Guard agent endpoints (open when ``EDGE_AGENT_TOKEN`` is unset)."""
    _check_bearer(credentials, get_agent_token(), "agent")


def require_operator_token(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> None:
    """Guard operator endpoints (open when ``AUTOBRIDGE_CONTROL_TOKEN`` is unset)."""
    _check_bearer(credentials, get_operator_token(), "control")


def _db_path() -> str:
    return (os.environ.get(RELAY_DB_ENV_VAR, "") or "").strip() or "./edge_relay.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS commands (
    request_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT UNIQUE,
    expires_at TEXT,
    state TEXT NOT NULL DEFAULT 'queued',
    delivered_at TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_commands_agent_state ON commands(agent_id, state, created_at);
CREATE TABLE IF NOT EXISTS postings (
    idempotency_key TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    record_type TEXT NOT NULL,
    fields_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT,
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_postings_agent ON postings(agent_id, received_at);
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    link_up INTEGER NOT NULL DEFAULT 0,
    link_detail TEXT NOT NULL DEFAULT '',
    outbox_depth INTEGER NOT NULL DEFAULT 0,
    inbox_depth INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""


class _Store:
    """Thread-safe SQLite backing store (one connection + lock)."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript("PRAGMA journal_mode=WAL;")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def enqueue(
        self,
        agent_id: str,
        kind: str,
        params_json: str,
        idempotency_key: str | None,
        expires_at: str | None,
    ) -> tuple[str, bool]:

        with self._lock:
            if idempotency_key:
                row = self._conn.execute(
                    "SELECT request_id FROM commands WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if row is not None:
                    return str(row["request_id"]), True
            request_id = uuid.uuid4().hex
            try:
                self._conn.execute(
                    "INSERT INTO commands (request_id, agent_id, kind, params_json,"
                    " idempotency_key, expires_at, state, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)",
                    (request_id, agent_id, kind, params_json, idempotency_key, expires_at, _utcnow()),
                )
            except sqlite3.IntegrityError:  # lost a poll race on the key
                row = self._conn.execute(
                    "SELECT request_id FROM commands WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if row is None:  # pragma: no cover - defensive
                    raise
                return str(row["request_id"]), True
            self._conn.commit()
            return request_id, False

    def next_command(self, agent_id: str, redeliver_after_s: float = REDELIVER_AFTER_S) -> dict[str, Any] | None:
        with self._lock:
            cutoff = (
                datetime.datetime.now(datetime.UTC)
                - datetime.timedelta(seconds=redeliver_after_s)
            ).isoformat(timespec="seconds")
            row = self._conn.execute(
                "SELECT request_id, agent_id, kind, params_json, idempotency_key, expires_at"
                " FROM commands WHERE agent_id = ?"
                " AND (state = 'queued' OR (state = 'delivered' AND delivered_at < ?))"
                " ORDER BY created_at ASC LIMIT 1",
                (agent_id, cutoff),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE commands SET state = 'delivered', delivered_at = ? WHERE request_id = ?",
                (_utcnow(), row["request_id"]),
            )
            self._conn.commit()
            return {
                "request_id": row["request_id"],
                "agent_id": row["agent_id"],
                "kind": row["kind"],
                "params_json": row["params_json"],
                "idempotency_key": row["idempotency_key"],
                "expires_at": row["expires_at"],
            }

    def record_result(self, request_id: str, result_json: str) -> tuple[bool, dict[str, Any]]:
        """First write wins; returns (stored, existing-or-new result row)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT result_json FROM commands WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise KeyError(request_id)
            if row["result_json"] is not None:
                import json as _json

                return False, _json.loads(str(row["result_json"]))
            self._conn.execute(
                "UPDATE commands SET state = 'done', result_json = ? WHERE request_id = ?",
                (result_json, request_id),
            )
            self._conn.commit()
            import json as _json

            return True, _json.loads(result_json)

    def store_posting(
        self,
        agent_id: str,
        idempotency_key: str,
        record_type: str,
        fields_json: str,
        occurred_at: str | None,
    ) -> bool:
        """Insert a posting; False when the key was already present (duplicate)."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO postings"
                " (idempotency_key, agent_id, record_type, fields_json, occurred_at, received_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (idempotency_key, agent_id, record_type, fields_json, occurred_at, _utcnow()),
            )
            self._conn.commit()
            return cur.rowcount == 1

    def heartbeat(
        self,
        agent_id: str,
        link_up: bool,
        link_detail: str,
        outbox_depth: int,
        inbox_depth: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO agents"
                " (agent_id, link_up, link_detail, outbox_depth, inbox_depth, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(agent_id) DO UPDATE SET link_up = excluded.link_up,"
                " link_detail = excluded.link_detail, outbox_depth = excluded.outbox_depth,"
                " inbox_depth = excluded.inbox_depth, updated_at = excluded.updated_at",
                (agent_id, 1 if link_up else 0, link_detail, outbox_depth, inbox_depth, _utcnow()),
            )
            self._conn.commit()

    def results(self, agent_id: str | None) -> list[dict[str, Any]]:
        with self._lock:
            query = (
                "SELECT request_id, agent_id, kind, state, result_json, created_at"
                " FROM commands WHERE result_json IS NOT NULL"
            )
            args: list[Any] = []
            if agent_id:
                query += " AND agent_id = ?"
                args.append(agent_id)
            query += " ORDER BY created_at ASC"
            return [dict(r) for r in self._conn.execute(query, args).fetchall()]

    def postings(self, agent_id: str | None, since: str | None) -> list[dict[str, Any]]:
        with self._lock:
            query = (
                "SELECT idempotency_key, agent_id, record_type, fields_json,"
                " occurred_at, received_at FROM postings"
            )
            clauses: list[str] = []
            args: list[Any] = []
            if agent_id:
                clauses.append("agent_id = ?")
                args.append(agent_id)
            if since:
                clauses.append("received_at >= ?")
                args.append(since)
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY received_at ASC"
            return [dict(r) for r in self._conn.execute(query, args).fetchall()]

    def agents(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT agent_id, link_up, link_detail, outbox_depth, inbox_depth, updated_at"
                " FROM agents ORDER BY agent_id ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def ping(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False


_store: _Store | None = None
_store_lock = threading.Lock()


def get_store() -> _Store:
    """Process-wide relay store (created on first use; ``EDGE_RELAY_DB``)."""
    global _store
    with _store_lock:
        if _store is None:
            _store = _Store(_db_path())
        return _store


def reset_store_for_tests(path: str) -> _Store:
    """Point the process-wide store at a fresh file (tests only)."""
    global _store
    with _store_lock:
        if _store is not None:
            _store.close()
        _store = _Store(path)
        return _store


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class EnqueueRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    expires_at: str | None = Field(
        default=None, description="ISO-8601 UTC deadline; the agent never sends past it"
    )


class EnqueueResponse(BaseModel):
    request_id: str
    existed: bool = False


class CommandOut(BaseModel):
    request_id: str
    agent_id: str
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    expires_at: str | None = None


class ResultRequest(BaseModel):
    status: str = Field(description=f"one of {', '.join(RESULT_STATUSES)}")
    detail: str = Field(default="")


class ResultOut(BaseModel):
    request_id: str
    stored: bool
    status: str
    detail: str = ""


class PostingRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    record_type: str = Field(min_length=1)
    fields: dict[str, str] = Field(default_factory=dict)
    occurred_at: str | None = None


class PostingOut(BaseModel):
    duplicate: bool


class HeartbeatRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    link_up: bool = False
    link_detail: str = ""
    outbox_depth: int = 0
    inbox_depth: int = 0


class HeartbeatOut(BaseModel):
    recorded: bool = True


def create_app(store: _Store | None = None) -> FastAPI:
    """Build the relay app (a shared process-wide store by default)."""
    app = FastAPI(title="AutoBridge Edge Relay")

    def _store() -> _Store:
        return store if store is not None else get_store()

    @app.get("/healthz")
    def get_healthz() -> dict[str, str]:
        return healthz()  # type: ignore[return-value]

    @app.get("/readyz")
    def get_readyz() -> dict[str, object]:
        return readyz({"relay_store": _store().ping()})  # type: ignore[return-value]

    @app.post("/v1/enqueue", response_model=EnqueueResponse, dependencies=[Depends(require_operator_token)])
    def post_enqueue(body: EnqueueRequest) -> EnqueueResponse:
        import json as _json

        request_id, existed = _store().enqueue(
            body.agent_id, body.kind, _json.dumps(body.params),
            body.idempotency_key, body.expires_at,
        )
        log.info("enqueue agent=%s kind=%s request=%s existed=%s", body.agent_id, body.kind, request_id, existed)
        return EnqueueResponse(request_id=request_id, existed=existed)

    @app.get("/v1/commands/next", dependencies=[Depends(require_agent_token)])
    def get_next_command(agent_id: str = Query(min_length=1)) -> Any:
        import json as _json

        row = _store().next_command(agent_id)
        if row is None:
            from fastapi.responses import Response

            return Response(status_code=204)
        log.info("deliver agent=%s request=%s kind=%s", agent_id, row["request_id"], row["kind"])
        return CommandOut(
            request_id=row["request_id"],
            agent_id=row["agent_id"],
            kind=row["kind"],
            params=_json.loads(row["params_json"]),
            idempotency_key=row["idempotency_key"],
            expires_at=row["expires_at"],
        )

    @app.post("/v1/commands/{request_id}/result", response_model=ResultOut, dependencies=[Depends(require_agent_token)])
    def post_result(request_id: str, body: ResultRequest) -> ResultOut:
        import json as _json

        if body.status not in RESULT_STATUSES:
            raise HTTPException(status_code=422, detail=f"unknown result status {body.status!r}")
        try:
            stored, existing = _store().record_result(
                request_id, _json.dumps({"status": body.status, "detail": body.detail})
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown request_id") from None
        log.info("result request=%s status=%s stored=%s", request_id, existing["status"], stored)
        return ResultOut(request_id=request_id, stored=stored, status=existing["status"], detail=existing.get("detail", ""))

    @app.post("/v1/postings", response_model=PostingOut, dependencies=[Depends(require_agent_token)])
    def post_posting(body: PostingRequest) -> PostingOut:
        import json as _json

        fresh = _store().store_posting(
            body.agent_id, body.idempotency_key, body.record_type,
            _json.dumps(body.fields), body.occurred_at,
        )
        log.info("posting agent=%s key=%s fresh=%s", body.agent_id, body.idempotency_key, fresh)
        return PostingOut(duplicate=not fresh)

    @app.post("/v1/heartbeats", response_model=HeartbeatOut, dependencies=[Depends(require_agent_token)])
    def post_heartbeat(body: HeartbeatRequest) -> HeartbeatOut:
        _store().heartbeat(body.agent_id, body.link_up, body.link_detail, body.outbox_depth, body.inbox_depth)
        return HeartbeatOut()

    @app.get("/v1/results", dependencies=[Depends(require_operator_token)])
    def get_results(agent_id: str | None = None) -> dict[str, Any]:
        return {"results": _store().results(agent_id)}

    @app.get("/v1/postings", dependencies=[Depends(require_operator_token)])
    def get_postings(agent_id: str | None = None, since: str | None = None) -> dict[str, Any]:
        return {"postings": _store().postings(agent_id, since)}

    @app.get("/v1/agents", dependencies=[Depends(require_operator_token)])
    def get_agents() -> dict[str, Any]:
        return {"agents": _store().agents()}

    return app


app = create_app()
