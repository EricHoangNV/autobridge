"""Durable agent store (REQ-022 sub-step B).

SQLite outbox + inbox surviving agent restarts:

* ``outbox`` — commands polled from the relay, awaiting execution on the
  FIAS link and a result report back. ``claimed`` rows (sent to the link,
  answer not yet in) return to ``queued`` on startup: a killed agent
  retries on the new link instead of losing the command — and the relay
  result is first-write-wins, so a retry can never double-report.
* ``inbox`` — PABX-originated records durably written *before* the link
  ACK goes out, awaiting forward to the cloud. A restart re-forwards;
  the relay de-duplicates on the idempotency key, so nothing lands twice.

Idempotency keys: commands carry the relay's ``request_id`` (enforced
UNIQUE); postings use ``P# + room + time`` for ``PS`` (REQ-022 D names
this key) and ``record + fields`` hash otherwise.

``PRAGMA journal_mode=WAL`` + ``busy_timeout`` so the link thread, the
executor and the forwarder share one file without Schannel locks.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
import threading
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    request_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT UNIQUE,
    expires_at TEXT,
    state TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbox_state ON outbox(state, created_at);
CREATE TABLE IF NOT EXISTS inbox (
    idempotency_key TEXT PRIMARY KEY,
    record_type TEXT NOT NULL,
    fields_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT,
    forwarded INTEGER NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inbox_unforwarded ON inbox(forwarded, received_at);
"""


def _utcnow() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def posting_key(record_type: str, fields: dict[str, str]) -> str:
    """Idempotency key for a PABX-originated record.

    ``PS`` postings key on ``P# + room + time`` (the D-scenario contract:
    a resent posting with the same P# lands once); anything else keys on
    the full record content.
    """
    if record_type == "PS":
        return f"PS:{fields.get('P#', '')}:{fields.get('RN', '')}:{fields.get('DA', '')}:{fields.get('TI', '')}"
    return f"{record_type}:{json.dumps(fields, sort_keys=True)}"


class AgentStore:
    """Thread-safe durable outbox/inbox (one SQLite file)."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript("PRAGMA journal_mode=WAL;")
            self._conn.executescript("PRAGMA busy_timeout=10000;")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            # Crash recovery: commands claimed but never resulted go back
            # to queued (the relay result is first-write-wins, so a retry
            # after restart can never double-report).
            self._conn.execute("UPDATE outbox SET state = 'queued', updated_at = ? WHERE state = 'claimed'", (_utcnow(),))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- outbox ----------------------------------------------------------

    def enqueue_command(
        self,
        request_id: str,
        kind: str,
        params: dict[str, Any],
        idempotency_key: str | None,
        expires_at: str | None,
    ) -> bool:
        """Insert a polled command; False when already present (re-delivery)."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO outbox"
                " (request_id, kind, params_json, idempotency_key, expires_at, state, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
                (request_id, kind, json.dumps(params), idempotency_key, expires_at, _utcnow(), _utcnow()),
            )
            self._conn.commit()
            return cur.rowcount == 1

    def next_command(self) -> dict[str, Any] | None:
        """Claim the oldest queued command (``claimed`` until resulted)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT request_id, kind, params_json, idempotency_key, expires_at, attempts"
                " FROM outbox WHERE state = 'queued' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE outbox SET state = 'claimed', attempts = attempts + 1, updated_at = ?"
                " WHERE request_id = ?",
                (_utcnow(), row["request_id"]),
            )
            self._conn.commit()
            return {
                "request_id": row["request_id"],
                "kind": row["kind"],
                "params": json.loads(row["params_json"]),
                "idempotency_key": row["idempotency_key"],
                "expires_at": row["expires_at"],
                "attempts": int(row["attempts"]) + 1,
            }

    def finish_command(self, request_id: str, status: str, detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE outbox SET state = 'done', result_json = ?, updated_at = ? WHERE request_id = ?",
                (json.dumps({"status": status, "detail": detail}), _utcnow(), request_id),
            )
            self._conn.commit()

    def requeue_command(self, request_id: str) -> None:
        """Back to queued (link down mid-command; retried on the new link)."""
        with self._lock:
            self._conn.execute(
                "UPDATE outbox SET state = 'queued', updated_at = ? WHERE request_id = ?",
                (_utcnow(), request_id),
            )
            self._conn.commit()

    def command_result(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT result_json FROM outbox WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None or row["result_json"] is None:
                return None
            return json.loads(str(row["result_json"]))

    # -- inbox -----------------------------------------------------------

    def store_posting(
        self, record_type: str, fields: dict[str, str], occurred_at: str | None = None
    ) -> tuple[str, bool]:
        """Durably store a PABX record *before* its ACK goes out.

        Returns ``(idempotency_key, fresh)``; ``fresh`` is False for a
        resent duplicate (same P# resent until PA — §3.1).
        """
        key = posting_key(record_type, fields)
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO inbox"
                " (idempotency_key, record_type, fields_json, occurred_at, received_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (key, record_type, json.dumps(fields), occurred_at, _utcnow()),
            )
            self._conn.commit()
            return key, cur.rowcount == 1

    def next_unforwarded(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT idempotency_key, record_type, fields_json, occurred_at"
                " FROM inbox WHERE forwarded = 0 ORDER BY received_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            return {
                "idempotency_key": row["idempotency_key"],
                "record_type": row["record_type"],
                "fields": json.loads(row["fields_json"]),
                "occurred_at": row["occurred_at"],
            }

    def mark_forwarded(self, idempotency_key: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE inbox SET forwarded = 1 WHERE idempotency_key = ?", (idempotency_key,))
            self._conn.commit()

    # -- introspection ----------------------------------------------------

    def depths(self) -> dict[str, int]:
        with self._lock:
            outbox = self._conn.execute(
                "SELECT COUNT(*) AS n FROM outbox WHERE state IN ('queued', 'claimed')"
            ).fetchone()
            inbox = self._conn.execute("SELECT COUNT(*) AS n FROM inbox WHERE forwarded = 0").fetchone()
            return {"outbox": int(outbox["n"]), "inbox": int(inbox["n"])}

    def ping(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False
