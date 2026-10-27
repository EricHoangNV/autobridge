"""SSE streaming from stored EvidenceRecords (P1.W7.2, plan §2.11).

Streams stored ``EvidenceRecord`` rows as Server-Sent Events. The dashboard
renders stored records, never narrated text — ``/runs/{id}/events`` is the
SSE view over the evidence table.

Redaction: every payload is passed through ``evidence.redact.redact_payload``
before it is serialized to the wire. Secret values are collected from:

* ``Connector.credential_ref`` rows (their env values), and
* any env var that looks like a credential (``^[A-Z][A-Z0-9_]{2,}$`` and
  length >=6, plus the traditional ``*_API_KEY|*_TOKEN|*_SECRET`` suffixes).

This is defense-in-depth: ``EvidenceStore.append`` already redacts at write
time, but read-time redaction catches any legacy or externally inserted row
and any secret that was added to the environment after the row was written.

Trust boundaries: this module never imports ``verifier`` or ``simulators``.
"""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from autobridge.control.models import EvidenceRecord
from autobridge.evidence.redact import redact_payload

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_CRED_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
_SKIP_ENV = frozenset(
    {
        "PATH",
        "HOME",
        "PWD",
        "OLDPWD",
        "SHELL",
        "USER",
        "LOGNAME",
        "LANG",
        "TERM",
        "DISPLAY",
        "DATABASE_URL",
        "VERIFIER_URL",
        "SIMULATOR_ADMIN_URL",
    }
)


def collect_secrets(db: Session | None = None) -> list[str]:
    """Collect secret values that must not appear in an SSE payload.

    Values are gathered from ``Connector.credential_ref`` (via *db*) and from
    env vars matching the credential pattern. Only non-empty values >=6 chars
    are returned so redaction does not mask everything.
    """
    refs: list[str] = []
    if db is not None:
        try:
            from autobridge.control.models import Connector  # noqa: PLC0415 (avoid circular at import)

            rows = db.execute(select(Connector.credential_ref)).scalars().all()
            refs.extend(rows)
        except Exception:
            pass

    # Env-wide credential-like keys (defense-in-depth)
    for k, v in os.environ.items():
        if k in _SKIP_ENV:
            continue
        if not v or len(v) < 6:
            continue
        if _CRED_RE.match(k) and k not in refs:
            refs.append(k)
    for k in os.environ:
        if (k.endswith("_API_KEY") or k.endswith("_TOKEN") or k.endswith("_SECRET")) and k not in refs:
            refs.append(k)

    values: list[str] = []
    for ref in refs:
        val = os.environ.get(ref)
        if val:
            values.append(val)
    return values


def evidence_to_sse_data(
    rec: EvidenceRecord,
    secrets: list[str] | None = None,
) -> dict[str, Any]:
    """Serialize one evidence row to the JSON object sent as an SSE ``data``.

    ``payload`` is redacted against *secrets* (if provided). ``created_at``
    is rendered as ISO-8601 for wire stability.
    """
    payload = rec.payload
    if secrets:
        payload = redact_payload(payload, secrets)
    return {
        "id": rec.id,
        "kind": rec.kind,
        "payload": payload,
        "parent_ids": rec.parent_ids,
        "run_id": rec.run_id,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
    }


def get_evidence_for_run(db: Session, run_id: str) -> list[EvidenceRecord]:
    """Stored EvidenceRecords for *run_id*, oldest first.

    Matches both exact ``run_id`` and the ``<prefix>:<run_id>`` form used by
    ``EvidenceStore`` (e.g. ``build_runs:<build_run_id>``). Filtering is done
    in Python to avoid dialect-specific JSON operators and to keep the query
    portable (SQLite and Postgres).
    """
    rows = db.execute(select(EvidenceRecord).order_by(EvidenceRecord.created_at)).scalars().all()
    return [r for r in rows if r.run_id == run_id or r.run_id.endswith(f":{run_id}")]


def records_to_sse_payloads(
    records: list[EvidenceRecord],
    secrets: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Map a list of records to their redacted SSE data dicts."""
    return [evidence_to_sse_data(r, secrets) for r in records]


async def sse_event_generator(
    records: list[EvidenceRecord],
    secrets: list[str],
    request: Any,
):
    """Async SSE generator for ``EventSourceResponse``.

    Yields one ``{"event": kind, "data": json}`` per record, then a terminal
    ``{"event": "done", "data": {"run_id": ..., "count": ...}}``. Respects
    client disconnect via ``request.is_disconnected()``.
    """
    for rec in records:
        if await request.is_disconnected():
            break
        data = evidence_to_sse_data(rec, secrets)
        yield {"event": rec.kind, "data": json.dumps(data, default=str)}
    # Terminal marker so the client knows the stream is complete
    # ``records[0].run_id`` is not reliable (records may have prefix); we
    # derive from first record or empty. Caller passes run_id via closure if needed.
    # To keep this helper stateless, we emit count only; caller adds run_id wrapper.
    run_label = records[0].run_id if records else ""
    yield {"event": "done", "data": json.dumps({"run_id": run_label, "count": len(records)})}


def build_sse_events_for_run(
    db: Session,
    run_id: str,
    secrets: list[str] | None = None,
) -> list[dict[str, str]]:
    """Synchronous helper for tests: build SSE event dicts for a run.

    Returns a list of ``{"event": ..., "data": json_string}`` dicts including
    the terminal ``done`` event. Payloads are redacted.
    """
    if secrets is None:
        secrets = collect_secrets(db)
    records = get_evidence_for_run(db, run_id)
    events: list[dict[str, str]] = []
    for rec in records:
        data = evidence_to_sse_data(rec, secrets)
        events.append({"event": rec.kind, "data": json.dumps(data, default=str)})
    events.append({"event": "done", "data": json.dumps({"run_id": run_id, "count": len(records)})})
    return events
