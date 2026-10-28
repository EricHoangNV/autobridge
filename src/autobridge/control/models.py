"""Core data model (P1.W1.5, plan §2.3; job queue added in P1.W1.6).

SQLAlchemy 2.0 models for the control plane. SQLite now, Postgres-ready:
only portable column types are used (``String``, ``Text``, ``Integer``,
``Numeric``, ``Boolean``, ``DateTime(timezone=True)``, ``JSON``,
``ForeignKey``) — no SQLite-only and no Postgres-only types (no ``ARRAY``,
no native ``UUID`` or ``ENUM``; closed vocabularies are ``String`` plus a
``CHECK`` constraint so the DDL compiles on both dialects).

Standing rules honored here (DIRECTIVES Quick Reference 4–6):

- ``Connector.credential_ref`` names an env key; the secret value is never
  stored (there is no column for it anywhere in this module).
- Money is ``Decimal`` end to end: ``ModelCall.cost_estimate`` is
  ``Numeric`` and round-trips as ``Decimal``, never float.
- ``ConnectorRelease`` is immutable once persisted: mapper-level
  ``before_update`` / ``before_delete`` listeners raise
  :class:`ImmutableReleaseError`. Rollback creates a *new* release (see
  ``Approval.rollback_of_release_id``), it never mutates one.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON as GenericJSON
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
    validates,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Env var naming the database URL (SQLite file by default, Postgres-ready).
DATABASE_URL_ENV = "DATABASE_URL"
DEFAULT_SQLITE_URL = "sqlite:///./autobridge.db"

#: Closed vocabularies fixed by plan §2.3 (stored as String + CHECK).
HEALTH_STATUSES = frozenset(
    {"healthy", "drift", "investigating", "review_required", "blocked"}
)
SYNC_OUTCOMES = frozenset({"Complete", "Degraded", "Blocked"})
DRIFT_STATUSES = frozenset({"open", "investigating", "repaired", "escalated", "dismissed"})
REPAIR_DECISIONS = frozenset({"propose", "escalate", "reject"})
APPROVAL_DECISIONS = frozenset({"approve", "reject", "rollback"})
EVIDENCE_KINDS = frozenset(
    {
        "probe",
        "observation",
        "hypothesis",
        "decision",
        "patch",
        "verification",
        "approval",
        "model_call",
    }
)
MODEL_TIERS = frozenset({"small", "frontier"})
DEPENDENCY_POLICIES = frozenset({"pause_on_release_change", "notify"})
#: Discovery lifecycle states (Phase 2 S3, plan §5 S3): static import only,
#: transitions happen exclusively via the discovery endpoints. Stored as
#: String + CHECK so the DDL stays portable (SQLite now, Postgres-ready).
DISCOVERY_STATUSES = frozenset(
    {"discovered", "needs_confirmation", "authorized", "connected", "healing"}
)
#: Allowed explicit transitions: current -> {endpoint-name -> next}.
DISCOVERY_TRANSITIONS: dict[str, dict[str, str]] = {
    "discovered": {"request-confirmation": "needs_confirmation"},
    "needs_confirmation": {"authorize": "authorized"},
    "authorized": {"connect": "connected"},
    "connected": {"mark-healing": "healing"},
    "healing": {"resolve": "connected"},
}
#: Job queue vocabularies (P1.W1.6): one job per BuildRun / SyncRun /
#: RepairRun execution attempt series. ``run_id`` points at the domain row
#: the job drives (``<kind>_runs.id``); it is null until a sync/repair
#: handler creates its row at execution time.
JOB_KINDS = frozenset({"build", "sync", "repair"})
JOB_STATES = frozenset({"queued", "running", "succeeded", "failed"})


class ImmutableReleaseError(Exception):
    """Raised when code attempts to UPDATE or DELETE a ConnectorRelease."""


def new_id() -> str:
    """Random row id (uuid4 hex); plain String so it works on every backend."""
    return uuid.uuid4().hex


def utcnow() -> datetime:
    """Timezone-aware creation timestamp (business dates stay property-local)."""
    return datetime.now(UTC)


#: Env var carrying the HMAC key that turns a release integrity checksum into
#: an unforgeable execution authorization (AR-2-4). The signer (control /
#: worker, via :func:`spec_signature`) and the verifier (edge worker, via
#: ``autobridge.runtime.targets.verify_release_signature``) read the same
#: variable, so one compose entry covers both ends. Never logged, never
#: stored in a spec, release, evidence record or API response (DIRECTIVES §6).
EDGE_RELEASE_SECRET_ENV = "EDGE_RELEASE_SECRET"


def _canonical_spec_bytes(spec: Mapping[str, Any]) -> bytes:
    return json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _signing_key(secret: str | None = None) -> bytes | None:
    """HMAC key bytes, or ``None`` when no key is configured (legacy mode)."""
    key = secret if secret is not None else os.environ.get(EDGE_RELEASE_SECRET_ENV)
    if not key:
        return None
    return key.encode("utf-8")


def spec_signature(spec: Mapping[str, Any], *, secret: str | None = None) -> str:
    """HMAC-SHA256 over the canonical JSON of a ConnectorSpec document.

    ``ConnectorRelease.signature`` stores this; any spec drift changes it.
    When ``EDGE_RELEASE_SECRET`` (or *secret*) is set, the digest is keyed:
    only the holder of the secret can mint a signature the edge worker
    accepts, so a self-computed hash over a foreign spec is rejected
    (AR-2-4). When no key is configured the legacy unkeyed sha256 is
    returned: an integrity checksum only, suitable for local dev and
    content-addressing but not an authorization decision — set the secret
    before exposing the stack beyond localhost.
    """
    payload = _canonical_spec_bytes(spec)
    key = _signing_key(secret)
    if key is None:
        return hashlib.sha256(payload).hexdigest()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def default_database_url() -> str:
    """Database URL from the environment, SQLite file by default."""
    return os.environ.get(DATABASE_URL_ENV, DEFAULT_SQLITE_URL)


def engine_from_url(url: str) -> Engine:
    """Create an engine; the models use only portable types, so any URL works."""
    from sqlalchemy import create_engine

    return create_engine(url)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Session factory bound to *engine* (W1.6 JobRunner builds on this)."""
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create all tables (tests and first boot; migrations own prod schema)."""
    Base.metadata.create_all(engine)


class Base(DeclarativeBase):
    """Declarative base with stable constraint names for Alembic + Postgres."""

    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def _check(table: str, name: str, column: str, values: frozenset[str]) -> CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in sorted(values))
    return CheckConstraint(f"{column} IN ({allowed})", name=f"ck_{table}_{name}")


class Connector(Base):
    """A vendor connection; owns releases, runs and dependency registrations."""

    __tablename__ = "connectors"
    __table_args__ = (
        _check("connectors", "health_status", "health_status", HEALTH_STATUSES),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    vendor_docs_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    #: Env key name only — the secret value is read at runtime, never stored.
    credential_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    target_entities: Mapped[list[str]] = mapped_column(
        GenericJSON, nullable=False, default=list
    )
    runtime_target: Mapped[dict[str, Any]] = mapped_column(
        GenericJSON, nullable=False, default=dict
    )
    active_release_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("connector_releases.id"), nullable=True
    )
    health_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="healthy"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    releases: Mapped[list[ConnectorRelease]] = relationship(
        back_populates="connector",
        foreign_keys="ConnectorRelease.connector_id",
        cascade="save-update, merge",
    )
    active_release: Mapped[ConnectorRelease | None] = relationship(
        foreign_keys=[active_release_id],
        post_update=True,
    )

    @validates("health_status")
    def _validate_health(self, key: str, value: str) -> str:
        if value not in HEALTH_STATUSES:
            raise ValueError(f"{key} must be one of {sorted(HEALTH_STATUSES)}")
        return value


class ConnectorRelease(Base):
    """An immutable verified snapshot of a ConnectorSpec.

    Rows are insert-only: any UPDATE or DELETE raises
    :class:`ImmutableReleaseError` at flush time. Rollback, approve and
    reject all create *new* rows (see :class:`Approval`).
    """

    __tablename__ = "connector_releases"
    __table_args__ = (
        UniqueConstraint("connector_id", "version", name="uq_connector_releases_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connector_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("connectors.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The full ConnectorSpec document (JSON); validated against
    #: ``schemas/connector-spec.v1.json`` before it is stored.
    spec: Mapped[dict[str, Any]] = mapped_column(GenericJSON, nullable=False)
    parent_release_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("connector_releases.id"), nullable=True
    )
    provenance_build_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("build_runs.id"), nullable=True
    )
    provenance_repair_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("repair_runs.id"), nullable=True
    )
    verification_report_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    #: ``spec_signature(spec)`` at insert time: HMAC-signed when
    #: ``EDGE_RELEASE_SECRET`` is set, integrity checksum otherwise (AR-2-4).
    signature: Mapped[str] = mapped_column(String(64), nullable=False)

    connector: Mapped[Connector] = relationship(
        back_populates="releases", foreign_keys=[connector_id]
    )


def _block_release_write(
    _mapper: Any, _connection: Any, target: ConnectorRelease
) -> None:
    raise ImmutableReleaseError(
        "ConnectorRelease rows are immutable; create a new release instead "
        f"(release id={target.id!r})"
    )


event.listens_for(ConnectorRelease, "before_update")(_block_release_write)
event.listens_for(ConnectorRelease, "before_delete")(_block_release_write)


class BuildRun(Base):
    """One docs → draft spec → compile → verify build (W1.6 executes these)."""

    __tablename__ = "build_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connector_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("connectors.id"), nullable=False, index=True
    )
    #: Lifecycle state; the JobRunner (W1.6) owns the transition diagram.
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    inputs: Mapped[dict[str, Any]] = mapped_column(
        GenericJSON, nullable=False, default=dict
    )
    ladder_trace: Mapped[list[dict[str, Any]]] = mapped_column(
        GenericJSON, nullable=False, default=list
    )
    model_usage: Mapped[dict[str, Any]] = mapped_column(
        GenericJSON, nullable=False, default=dict
    )
    evidence_ids: Mapped[list[str]] = mapped_column(
        GenericJSON, nullable=False, default=list
    )
    draft_spec: Mapped[dict[str, Any] | None] = mapped_column(
        GenericJSON, nullable=True
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(GenericJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class SyncRun(Base):
    """One execution of a release; always ends Complete | Degraded | Blocked."""

    __tablename__ = "sync_runs"
    __table_args__ = (
        _check("sync_runs", "outcome", "outcome", SYNC_OUTCOMES),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    release_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("connector_releases.id"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    records_observed: Mapped[dict[str, int]] = mapped_column(
        GenericJSON, nullable=False, default=dict
    )
    records_emitted: Mapped[dict[str, int]] = mapped_column(
        GenericJSON, nullable=False, default=dict
    )
    checkpoint: Mapped[dict[str, Any]] = mapped_column(
        GenericJSON, nullable=False, default=dict
    )
    invariant_results: Mapped[list[dict[str, Any]]] = mapped_column(
        GenericJSON, nullable=False, default=list
    )
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    unmet_criteria: Mapped[list[str]] = mapped_column(
        GenericJSON, nullable=False, default=list
    )
    data_readiness: Mapped[dict[str, Any]] = mapped_column(
        GenericJSON, nullable=False, default=dict
    )
    fingerprint_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )

    release: Mapped[ConnectorRelease] = relationship()

    @validates("outcome")
    def _validate_outcome(self, key: str, value: str) -> str:
        if value not in SYNC_OUTCOMES:
            raise ValueError(f"{key} must be one of {sorted(SYNC_OUTCOMES)}")
        return value


class DriftEvent(Base):
    """A detected drift of one of the plan §2.5 classes (W4.2 classifies)."""

    __tablename__ = "drift_events"
    __table_args__ = (
        _check("drift_events", "status", "status", DRIFT_STATUSES),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connector_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("connectors.id"), nullable=False, index=True
    )
    drift_class: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="medium")
    evidence_ids: Mapped[list[str]] = mapped_column(
        GenericJSON, nullable=False, default=list
    )
    source_sync_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("sync_runs.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    @validates("status")
    def _validate_status(self, key: str, value: str) -> str:
        if value not in DRIFT_STATUSES:
            raise ValueError(f"{key} must be one of {sorted(DRIFT_STATUSES)}")
        return value


class RepairRun(Base):
    """One ladder walk for a set of drift events; patch or escalation."""

    __tablename__ = "repair_runs"
    __table_args__ = (
        _check("repair_runs", "decision", "decision", REPAIR_DECISIONS),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    drift_event_ids: Mapped[list[str]] = mapped_column(
        GenericJSON, nullable=False, default=list
    )
    ladder_level_reached: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Candidate JSON Patch (list of RFC 6902 ops) on the ConnectorSpec.
    candidate_patch: Mapped[list[dict[str, Any]] | None] = mapped_column(
        GenericJSON, nullable=True
    )
    rule_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hypothesis_ids: Mapped[list[str]] = mapped_column(
        GenericJSON, nullable=False, default=list
    )
    visible_test_results: Mapped[list[dict[str, Any]]] = mapped_column(
        GenericJSON, nullable=False, default=list
    )
    verifier_report_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    model_usage: Mapped[dict[str, Any]] = mapped_column(
        GenericJSON, nullable=False, default=dict
    )
    #: Escalation bundle (L4): evidence + one precise question for a human.
    #: Only set when decision == "escalate"; otherwise None. Stored as JSON
    #: dict with keys ``reason``, ``question``, ``evidence_ids``,
    #: ``evidence_payloads`` (optional), ``suggested_action`` (optional).
    #: Every escalate must have exactly one non-empty question (ends with ?).
    escalation_bundle: Mapped[dict[str, Any] | None] = mapped_column(
        GenericJSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    @validates("decision")
    def _validate_decision(self, key: str, value: str) -> str:
        if value not in REPAIR_DECISIONS:
            raise ValueError(f"{key} must be one of {sorted(REPAIR_DECISIONS)}")
        return value


class Approval(Base):
    """Human (or policy) decision on a repair; rollback points at releases."""

    __tablename__ = "approvals"
    __table_args__ = (
        _check("approvals", "decision", "decision", APPROVAL_DECISIONS),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    repair_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repair_runs.id"), nullable=False, index=True
    )
    actor: Mapped[str] = mapped_column(String(256), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    from_release_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("connector_releases.id"), nullable=True
    )
    to_release_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("connector_releases.id"), nullable=True
    )
    #: For a rollback approval: the release superseded by this rollback.
    rollback_of_release_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("connector_releases.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    @validates("decision")
    def _validate_decision(self, key: str, value: str) -> str:
        if value not in APPROVAL_DECISIONS:
            raise ValueError(f"{key} must be one of {sorted(APPROVAL_DECISIONS)}")
        return value


class EvidenceRecord(Base):
    """One node of the evidence chain (probes → decisions → verifications)."""

    __tablename__ = "evidence_records"
    __table_args__ = (
        _check("evidence_records", "kind", "kind", EVIDENCE_KINDS),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Already redacted payload — credential values never land here (W1.7).
    payload: Mapped[dict[str, Any]] = mapped_column(
        GenericJSON, nullable=False, default=dict
    )
    parent_ids: Mapped[list[str]] = mapped_column(
        GenericJSON, nullable=False, default=list
    )
    #: Polymorphic owner: "<build_runs|sync_runs|repair_runs>:<id>".
    run_id: Mapped[str] = mapped_column(String(72), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    @validates("kind")
    def _validate_kind(self, key: str, value: str) -> str:
        if value not in EVIDENCE_KINDS:
            raise ValueError(f"{key} must be one of {sorted(EVIDENCE_KINDS)}")
        return value


class ModelCall(Base):
    """Ledger row for one LLM call (plan §2.9; every model call lands here)."""

    __tablename__ = "model_calls"
    __table_args__ = (
        _check("model_calls", "tier", "tier", MODEL_TIERS),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(String(72), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[float] = mapped_column(nullable=False, default=0.0)
    #: Never float: Decimal end to end (DIRECTIVES Quick Reference 5).
    cost_estimate: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0")
    )
    purpose: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    @validates("tier")
    def _validate_tier(self, key: str, value: str) -> str:
        if value not in MODEL_TIERS:
            raise ValueError(f"{key} must be one of {sorted(MODEL_TIERS)}")
        return value


class DependencyRegistration(Base):
    """A downstream consumer; release activation may pause or notify it."""

    __tablename__ = "dependency_registrations"
    __table_args__ = (
        UniqueConstraint(
            "consumer_id",
            "connector_id",
            "entity",
            name="uq_dependency_registrations_consumer",
        ),
        _check(
            "dependency_registrations",
            "policy",
            "policy",
            DEPENDENCY_POLICIES,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    consumer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    connector_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("connectors.id"), nullable=False, index=True
    )
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    policy: Mapped[str] = mapped_column(String(32), nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    @validates("policy")
    def _validate_policy(self, key: str, value: str) -> str:
        if value not in DEPENDENCY_POLICIES:
            raise ValueError(f"{key} must be one of {sorted(DEPENDENCY_POLICIES)}")
        return value


class DiscoveryRecord(Base):
    """A statically imported discovery candidate (Phase 2 S3, plan §5 S3).

    Reserved ``DiscoveryResult`` schema rows imported via
    ``POST /discovery/import``. The v1 engine neither produces nor consumes
    these rows; status moves only through the explicit discovery endpoints
    (see ``DISCOVERY_TRANSITIONS``). No secret values are stored: the record
    carries ``base_url`` and candidate entity names only.
    """

    __tablename__ = "discovery_records"
    __table_args__ = (
        _check(
            "discovery_records", "status", "status", DISCOVERY_STATUSES
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    discovery_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    vendor: Mapped[str] = mapped_column(String(256), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_entities: Mapped[list[str]] = mapped_column(
        GenericJSON, nullable=False, default=list
    )
    evidence_refs: Mapped[list[str]] = mapped_column(
        GenericJSON, nullable=False, default=list
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="discovered")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    @validates("status")
    def _validate_status(self, key: str, value: str) -> str:
        if value not in DISCOVERY_STATUSES:
            raise ValueError(f"{key} must be one of {sorted(DISCOVERY_STATUSES)}")
        return value


class Job(Base):
    """One queued execution of a BuildRun / SyncRun / RepairRun (P1.W1.6).

    The ``JobRunner`` (``control/jobs.py``) owns the state diagram
    ``queued → running → succeeded | queued (retry) | failed``. All
    transitions are committed to this row, so a worker killed mid-job
    leaves a ``running`` row behind that a restarted worker reclaims via
    ``reclaim_orphaned`` and re-runs — no in-memory state is trusted.

    ``run_id`` names the domain row (``<kind>_runs.id``). Build jobs get
    their ``BuildRun`` row at submit time; sync/repair handlers create
    their row at execution time (a ``SyncRun`` needs its terminal
    ``outcome`` at insert), so ``run_id`` stays null until then.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        _check("jobs", "kind", "kind", JOB_KINDS),
        _check("jobs", "state", "state", JOB_STATES),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    #: Executions started so far (each claim increments it).
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    payload: Mapped[dict[str, Any]] = mapped_column(
        GenericJSON, nullable=False, default=dict
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(GenericJSON, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Worker that currently owns a ``running`` job (None once terminal).
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    @validates("kind")
    def _validate_kind(self, key: str, value: str) -> str:
        if value not in JOB_KINDS:
            raise ValueError(f"{key} must be one of {sorted(JOB_KINDS)}")
        return value

    @validates("state")
    def _validate_state(self, key: str, value: str) -> str:
        if value not in JOB_STATES:
            raise ValueError(f"{key} must be one of {sorted(JOB_STATES)}")
        return value
