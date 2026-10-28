# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Control plane schemas (P1.W7.1, plan §2.11)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, StrictInt


def _coerce_naive_to_utc(value: Any) -> Any:
    """Attach UTC to naive datetimes read from SQLite (which drops tzinfo).

    The OpenAPI schema declares these fields ``format: date-time`` (RFC 3339),
    which requires an explicit offset; naive ``isoformat()`` output violates
    it (P1.W7.9 schemathesis contract).
    """
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


#: Response-model timestamps: always RFC 3339 with an explicit offset.
UtcDatetime = Annotated[datetime, BeforeValidator(_coerce_naive_to_utc)]

#: Shared OpenAPI error responses (P1.W7.9 schemathesis contract).
#: FastAPI auto-documents only request-validation 422s (array-shaped
#: ``HTTPValidationError``); routes that raise domain errors with a string
#: ``detail`` declare both shapes so every documented response validates.
STRING_DETAIL_OBJECT: dict[str, Any] = {
    "type": "object",
    "properties": {"detail": {"type": "string"}},
    "required": ["detail"],
}
NOT_FOUND_404: dict[int | str, Any] = {
    404: {
        "description": "Resource not found",
        "content": {"application/json": {"schema": STRING_DETAIL_OBJECT}},
    }
}
#: FastAPI itself returns 400 when the request body cannot be decoded
#: (e.g. non-UTF-8 bytes under an ``application/json`` content type).
BAD_REQUEST_400: dict[int | str, Any] = {
    400: {
        "description": "Malformed request body",
        "content": {"application/json": {"schema": STRING_DETAIL_OBJECT}},
    }
}
CONFLICT_409: dict[int | str, Any] = {
    409: {
        "description": "Conflict (e.g. dependency already exists)",
        "content": {"application/json": {"schema": STRING_DETAIL_OBJECT}},
    }
}
DOMAIN_ERROR_422: dict[int | str, Any] = {
    422: {
        "description": "Request validation failure or domain error",
        "content": {
            "application/json": {
                "schema": {
                    "oneOf": [
                        {
                            "type": "object",
                            "properties": {"detail": {"type": "array", "items": {"type": "object"}}},
                            "required": ["detail"],
                        },
                        STRING_DETAIL_OBJECT,
                    ]
                }
            }
        },
    }
}
#: Write routes guarded by ``AUTOBRIDGE_CONTROL_TOKEN`` (REQ-012) answer 401
#: without a valid ``Authorization: Bearer <token>`` header when the token is
#: set. Documented so schemathesis validates the 401 shape.
AUTH_401: dict[int | str, Any] = {
    401: {
        "description": "Missing or invalid control token",
        "content": {"application/json": {"schema": STRING_DETAIL_OBJECT}},
    }
}


class HealthResponse(BaseModel):
    status: str = Field(description="ok when process is up")


class ReadyResponse(BaseModel):
    status: str = Field(description="ok when all checks pass, degraded otherwise")
    checks: dict[str, str]


class ReleaseOut(BaseModel):
    id: str
    connector_id: str
    version: int
    spec: dict[str, Any]
    parent_release_id: str | None = None
    provenance_build_run_id: str | None = None
    provenance_repair_run_id: str | None = None
    verification_report_id: str | None = None
    signature: str
    approved_by: str | None = Field(default=None, description="Actor of the latest approval for this release (REQ-001)")
    created_at: UtcDatetime


class OperatorOut(BaseModel):
    name: str = Field(description="Server-configured operator name (AUTOBRIDGE_OPERATOR)")


class ApprovalOut(BaseModel):
    id: str
    repair_run_id: str
    actor: str
    decision: str
    from_release_id: str | None = None
    to_release_id: str | None = None
    rollback_of_release_id: str | None = None
    created_at: UtcDatetime


class RollbackRequest(BaseModel):
    target_release_id: str = Field(description="Release to rollback to (its spec is copied)")
    # Accepted for backwards compatibility but ignored: the server always
    # records Approval.actor from AUTOBRIDGE_OPERATOR (REQ-001).
    actor: str | None = Field(default=None, description="Ignored; server sets the actor")
    repair_run_id: str = Field(description="RepairRun for audit trail (any existing run)")

    model_config = {"extra": "forbid"}


class RollbackResponse(BaseModel):
    new_release: ReleaseOut
    approval: ApprovalOut


class BuildRunOut(BaseModel):
    id: str
    connector_id: str
    state: str
    inputs: dict[str, Any]
    ladder_trace: list[dict[str, Any]]
    model_usage: dict[str, Any]
    evidence_ids: list[str]
    draft_spec: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    kind: Literal["build"] = "build"


class SyncRunOut(BaseModel):
    id: str
    release_id: str
    started_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    records_observed: dict[str, int]
    records_emitted: dict[str, int]
    checkpoint: dict[str, Any]
    invariant_results: list[dict[str, Any]]
    outcome: str
    unmet_criteria: list[str]
    data_readiness: dict[str, Any]
    fingerprint_id: str | None = None
    kind: Literal["sync"] = "sync"


class RepairRunOut(BaseModel):
    id: str
    drift_event_ids: list[str]
    ladder_level_reached: int
    candidate_patch: list[dict[str, Any]] | None = None
    rule_id: str | None = None
    hypothesis_ids: list[str]
    visible_test_results: list[dict[str, Any]]
    verifier_report_id: str | None = None
    decision: str
    model_usage: dict[str, Any]
    escalation_bundle: dict[str, Any] | None = None
    created_at: UtcDatetime
    kind: Literal["repair"] = "repair"


class DriftEventOut(BaseModel):
    id: str
    connector_id: str
    drift_class: str
    severity: str
    evidence_ids: list[str]
    source_sync_run_id: str | None = None
    status: str
    created_at: UtcDatetime
    updated_at: UtcDatetime


class DependencyOut(BaseModel):
    id: str
    consumer_id: str
    connector_id: str
    entity: str
    policy: str
    paused: bool
    created_at: UtcDatetime


class DependencyCreate(BaseModel):
    consumer_id: str = Field(min_length=1)
    entity: str = Field(min_length=1)
    policy: str = Field(default="pause_on_release_change")

    model_config = {"extra": "forbid"}


class ConnectorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256, description="Human name")
    vendor_docs_ref: str | None = Field(default=None, description="Docs URL or file ref")
    base_url: str = Field(min_length=1, description="Base URL for the vendor API (may contain ${ENV} placeholder)")
    credential_ref: str = Field(min_length=1, max_length=128, description="Env key name for the credential (e.g. ATLAS_API_KEY)")
    target_entities: list[str] = Field(default_factory=list, description="Canonical entities to sync")
    runtime_target: dict[str, Any] | None = Field(default=None, description="RuntimeTarget config; defaults to local_process")


class ConnectorOut(BaseModel):
    id: str
    name: str
    vendor_docs_ref: str | None = None
    base_url: str
    credential_ref: str
    target_entities: list[str]
    runtime_target: dict[str, Any]
    active_release_id: str | None = None
    health_status: str
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ConnectorDetail(ConnectorOut):
    releases: list[ReleaseOut] = Field(default_factory=list)
    last_sync: SyncRunOut | None = None
    open_drift: list[DriftEventOut] = Field(default_factory=list)
    dependencies: list[DependencyOut] = Field(default_factory=list)


class BuildRequest(BaseModel):
    docs_text: str | None = Field(default=None, description="Inline docs content")
    docs_url: str | None = Field(default=None, description="URL to fetch docs from")
    docs_path: str | None = Field(default=None, description="Local path (for demo)")
    base_url_override: str | None = None
    vendor: str | None = None
    credential_ref: str | None = None
    scenario_id: str = Field(default="baseline")
    verifier_url: str | None = None

    model_config = {"extra": "allow"}


class BuildResponse(BaseModel):
    job_id: str
    build_run_id: str
    state: str
    connector_id: str


class SyncRequest(BaseModel):
    release_id: str | None = Field(default=None, description="Release to sync; defaults to active")
    checkpoint: dict[str, Any] | None = None


class SyncResponse(BaseModel):
    job_id: str
    sync_run_id: str | None = None
    state: str
    release_id: str


class RunOut(BaseModel):
    id: str
    kind: str
    state: str | None = None
    outcome: str | None = None
    decision: str | None = None
    created_at: UtcDatetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EvidenceOut(BaseModel):
    id: str
    kind: str
    payload: dict[str, Any]
    parent_ids: list[str]
    run_id: str
    created_at: UtcDatetime


class RepairDecisionRequest(BaseModel):
    # Accepted for backwards compatibility but ignored: the server always
    # records Approval.actor from AUTOBRIDGE_OPERATOR (REQ-001).
    actor: str | None = Field(default=None, description="Ignored; server sets the actor")
    from_release_id: str | None = None
    to_release_id: str | None = Field(
        default=None,
        description="Approve only: must equal this repair run's candidate release "
        "(consistency assertion, not a selector; mismatch is 422)",
    )
    connector_id: str | None = None

    model_config = {"extra": "forbid"}


class JobOut(BaseModel):
    id: str
    kind: str
    run_id: str | None = None
    state: str
    attempts: int
    max_attempts: int
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    last_error: str | None = None
    worker_id: str | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class InjectFaultRequest(BaseModel):
    target: Literal["sim-atlas", "sim-harbor", "sim-meridian", "atlas", "harbor", "meridian", "atlaspms", "harborpms", "meridianpms", "atlas-pms", "harbor-pms", "meridian-pms"] = Field(
        description="Simulator target id: sim-atlas, sim-harbor, sim-meridian, atlas, harbor, meridian, atlaspms, harborpms, meridianpms"
    )
    scenario_id: str = Field(description="Scenario id to inject (e.g. rename_guest_id, baseline)")
    seed: StrictInt | None = Field(default=None, description="Optional seed override")

    model_config = {"extra": "allow"}


class InjectFaultResponse(BaseModel):
    labelled: Literal[True] = True
    demo: Literal[True] = True
    target: str
    scenario_id: str
    seed: int | None = None
    admin_response: dict[str, Any] | None = None
    message: str


class ModelUsageEntry(BaseModel):
    provider: str
    model: str
    tier: str
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost: str
    by_run: list[dict[str, Any]] = Field(default_factory=list)


class ModelUsageResponse(BaseModel):
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost: str
    frontier_tokens: int
    small_tokens: int
    by_tier: dict[str, int]
    by_provider: dict[str, int]
    entries: list[ModelUsageEntry] = Field(default_factory=list)
    by_purpose: dict[str, int] = Field(default_factory=dict)


class ScorecardEntry(BaseModel):
    scenario_id: str | None = None
    path: str
    verdict: str | None = None
    score: float | None = None
    created_at: str | None = None
    content_preview: str | None = None


class ScorecardsResponse(BaseModel):
    scorecards: list[ScorecardEntry]
    total: int


#: Closed vocabularies for the reserved DiscoveryResult import (Phase 2 S3).
#: They mirror ``autobridge.spec.entities.DISCOVERY_STATUSES`` /
#: ``CandidateEntity`` exactly so the OpenAPI schema only generates
#: contract-valid values (unknown values are rejected by request validation,
#: never guessed). A unit test pins the two sides together.
DiscoveryImportStatus = Literal[
    "discovered", "needs_confirmation", "authorized", "connected", "healing"
]
DiscoveryImportEntity = Literal["Reservation", "FolioCharge"]


def _reject_non_string_instant(value: Any) -> Any:
    """Reject non-string datetimes before pydantic coercion runs.

    Pydantic datetimes accept int/float unix timestamps, which would admit
    schema-violating input; only RFC3339 strings (or null) pass through to
    the datetime parser, which rejects anything unparseable with a 422.
    """
    if value is not None and not isinstance(value, str):
        raise ValueError("must be an RFC3339 instant string with a UTC offset")
    return value


class DiscoveryRecordIn(BaseModel):
    """One reserved DiscoveryResult record for static import (Phase 2 S3).

    Closed Literals mirror ``schemas/discovery-result.v0.json``; unknown
    ``status`` or entity values are rejected by request validation (422,
    never guessed).
    """

    discovery_id: str = Field(min_length=1, max_length=128)
    status: DiscoveryImportStatus = Field(description="Lifecycle state; transitions only via explicit endpoints")
    vendor: str = Field(min_length=1, max_length=256)
    base_url: str | None = Field(default=None, min_length=1)
    candidate_entities: list[DiscoveryImportEntity] = Field(default_factory=list)
    evidence_refs: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list)
    # Aware instant: the BeforeValidator rejects ints/floats/bools (which
    # pydantic datetimes would otherwise coerce from unix timestamps), so the
    # OpenAPI ``date-time`` shape matches the acceptance set exactly and
    # anything unparseable is a 422, never guessed.
    created_at: Annotated[UtcDatetime | None, BeforeValidator(_reject_non_string_instant)] = Field(default=None)

    model_config = {"extra": "forbid"}


class DiscoveryImportRequest(BaseModel):
    records: list[DiscoveryRecordIn] = Field(min_length=1)

    model_config = {"extra": "forbid"}


class DiscoveryRecordOut(BaseModel):
    id: str
    discovery_id: str
    vendor: str
    base_url: str | None = None
    candidate_entities: list[str]
    evidence_refs: list[str]
    status: str
    created_at: UtcDatetime
    updated_at: UtcDatetime


class DiscoveryImportResponse(BaseModel):
    imported: list[DiscoveryRecordOut]
    total: int
