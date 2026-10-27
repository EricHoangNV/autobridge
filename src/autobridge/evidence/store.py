"""EvidenceStore (P1.W1.7, plan §2.3).

Append-only store over the ``EvidenceRecord`` table (``control.models``):
every probe, observation, hypothesis, decision, patch, verification and
approval is a record with ``parent_ids[]`` linking it into the chain the
dashboard renders. Payloads are redacted *before* they reach the database —
credential values never land here (DIRECTIVES §6).

SQLite now, Postgres-ready: only portable queries (primary-key lookups and
ordered scans; ``parent_ids`` filtering is done in Python because the
column is a portable JSON list, not a native array).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from autobridge.control.models import EVIDENCE_KINDS, EvidenceRecord
from autobridge.evidence.redact import redact_payload

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class UnknownKindError(ValueError):
    """Raised when ``kind`` is not one of the plan §2.3 evidence kinds."""


class UnknownParentError(LookupError):
    """Raised when a ``parent_ids[]`` entry names no stored record."""


def _check_jsonable(payload: Any) -> None:
    try:
        json.dumps(payload)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"evidence payload must be JSON-serializable: {exc}") from exc


class EvidenceStore:
    """Append-only evidence chain on a SQLAlchemy session (caller commits)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        run_id: str,
        parent_ids: list[str] | tuple[str, ...] = (),
        secrets: list[str] | tuple[str, ...] = (),
    ) -> EvidenceRecord:
        """Append one record; parents must already exist. Returns the row.

        ``payload`` is redacted against ``secrets`` (credential values)
        before persistence; the caller's object is never mutated.
        """
        if kind not in EVIDENCE_KINDS:
            raise UnknownKindError(
                f"kind must be one of {sorted(EVIDENCE_KINDS)}, got {kind!r}"
            )
        if not run_id:
            raise ValueError("run_id must be non-empty")
        parents = list(parent_ids)
        for pid in parents:
            if self._session.get(EvidenceRecord, pid) is None:
                raise UnknownParentError(f"unknown parent evidence record: {pid!r}")
        _check_jsonable(payload)
        record = EvidenceRecord(
            kind=kind,
            payload=redact_payload(payload, list(secrets)),
            parent_ids=parents,
            run_id=run_id,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def get(self, record_id: str) -> EvidenceRecord | None:
        """Fetch one record by id (``None`` when unknown)."""
        return self._session.get(EvidenceRecord, record_id)

    def list_for_run(self, run_id: str) -> list[EvidenceRecord]:
        """All records of one run, oldest first."""
        rows = self._session.scalars(
            select(EvidenceRecord)
            .where(EvidenceRecord.run_id == run_id)
            .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
        ).all()
        return list(rows)

    def children(self, record_id: str) -> list[EvidenceRecord]:
        """Direct children of one record (it appears in their ``parent_ids``)."""
        rows = self._session.scalars(select(EvidenceRecord)).all()
        return [r for r in rows if record_id in (r.parent_ids or [])]

    def chain(self, record_id: str) -> list[EvidenceRecord]:
        """Ancestors root-first, ending with the record itself.

        Diamond chains (two parents sharing an ancestor) list each record
        once, in first-visit order.
        """
        root = self.get(record_id)
        if root is None:
            raise KeyError(f"unknown evidence record: {record_id!r}")
        ordered: list[EvidenceRecord] = []
        seen: set[str] = set()

        def visit(rid: str) -> None:
            if rid in seen:
                return
            row = self.get(rid)
            if row is None:
                raise UnknownParentError(f"unknown parent evidence record: {rid!r}")
            seen.add(rid)
            for pid in row.parent_ids or []:
                visit(pid)
            ordered.append(row)

        visit(record_id)
        return ordered

    def dump_jsonl(self, path: str | Path, run_id: str | None = None) -> int:
        """Write records as one JSON object per line. Returns rows written."""
        if run_id is None:
            rows = self._session.scalars(
                select(EvidenceRecord).order_by(
                    EvidenceRecord.created_at, EvidenceRecord.id
                )
            ).all()
        else:
            rows = self.list_for_run(run_id)
        lines = [
            json.dumps(
                {
                    "id": r.id,
                    "kind": r.kind,
                    "payload": r.payload,
                    "parent_ids": r.parent_ids,
                    "run_id": r.run_id,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
            )
            for r in rows
        ]
        Path(path).write_text("\n".join([*lines, ""]) if lines else "")
        return len(lines)

    @classmethod
    def load_jsonl(cls, session: Session, path: str | Path) -> int:
        """Insert JSONL rows, skipping ids already present. Returns inserted."""
        from datetime import datetime  # noqa: PLC0415 (local import: datetime only needed here)

        store = cls(session)
        inserted = 0
        text = Path(path).read_text()
        for line in text.splitlines():
            if not line.strip():
                continue
            doc = json.loads(line)
            if store.get(doc["id"]) is not None:
                continue
            if doc["kind"] not in EVIDENCE_KINDS:
                raise UnknownKindError(f"kind must be one of {sorted(EVIDENCE_KINDS)}")
            for pid in doc.get("parent_ids", []):
                if store.get(pid) is None:
                    raise UnknownParentError(
                        f"unknown parent evidence record: {pid!r}"
                    )
            _check_jsonable(doc["payload"])
            kwargs: dict[str, Any] = {
                "id": doc["id"],
                "kind": doc["kind"],
                "payload": doc["payload"],
                "parent_ids": list(doc.get("parent_ids", [])),
                "run_id": doc["run_id"],
            }
            if doc.get("created_at"):
                kwargs["created_at"] = datetime.fromisoformat(doc["created_at"])
            session.add(EvidenceRecord(**kwargs))
            inserted += 1
        session.flush()
        return inserted
