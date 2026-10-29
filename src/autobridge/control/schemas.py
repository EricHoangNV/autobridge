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
#: The ``/edge/*`` proxy answers 502 when the edge-relay queue is
#: unreachable or errors (REQ-022 B): a dead relay must not look like an
#: empty queue. Documented so schemathesis validates the 502 shape; never a
#: silent empty list, never a crash.
EDGE_RELAY_502: dict[int | str, Any] = {
    502: {
        "description": "Edge relay unreachable or errored",
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
    vendor: str | None = None
    drift_signature: str | None = None
    candidate_patch: list[dict[str, Any]] | None = None
    rule_id: str | None = None
    hypothesis_ids: list[str]
    visible_test_results: list[dict[str, Any]]
    verifier_report_id: str | None = None
    decision: str
    model_usage: dict[str, Any]
    ladder_trace: list[dict[str, Any]] = Field(default_factory=list)
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
    spec_vocabulary: Literal["v1", "v2", "v3"] | None = Field(
        default=None,
        description="ConnectorSpec vocabulary the build may use (REQ-021 item 2, REQ-022 C). "
        "Resolves to SPEC_VOCABULARY env, else v1: older vocabularies refuse newer docs "
        "deterministically at 0 model calls; pin v3 for event-driven (framed) docs.",
    )

    model_config = {"extra": "allow"}


class BuildResponse(BaseModel):
    job_id: str
    build_run_id: str
    state: str
    connector_id: str


class SyncRequest(BaseModel):
    release_id: str | None = Field(default=None, description="Release to sync; defaults to active")
    checkpoint: dict[str, Any] | None = None
    spec_vocabulary: Literal["v1", "v2", "v3"] | None = Field(
        default=None,
        description="Cap the runtime vocabulary for this sync (REQ-021 item 2, REQ-022 C). "
        "A newer release synced under an older pin ends Blocked with a named reason. "
        "Defaults to the release's build vocabulary, else the runtime default.",
    )


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


def _demo_target_ids() -> list[str]:
    """Accepted demo target ids for the OpenAPI description (file-derived).

    Lazy import keeps ``schemas`` free of route imports; a missing registry
    degrades the description (the endpoint itself fails closed at import).
    """
    try:
        from autobridge.control.demo_targets import known_target_ids  # noqa: PLC0415

        return known_target_ids()
    except Exception:
        return []


class InjectFaultRequest(BaseModel):
    target: str = Field(
        description=f"Simulator target id (see config/demo_targets.yaml): {', '.join(_demo_target_ids())}"
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


class SimulatorConnectorRef(BaseModel):
    id: str
    name: str


class SimulatorInfo(BaseModel):
    """One simulator row for the index (REQ-022 E): registry display data
    plus live status probed server-side by the control plane."""

    id: str
    display_name: str
    api_style: str
    public_port: int
    docs_source: str
    developers_url: str
    live: bool
    active_scenario: str | None = None
    connectors: list[SimulatorConnectorRef] = Field(default_factory=list)


class SimulatorsOut(BaseModel):
    simulators: list[SimulatorInfo]


class RungCallOut(BaseModel):
    tier: str
    purpose: str
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int = 0
    cost: str


class RungAttemptOut(BaseModel):
    rung: int
    action: str
    reason: str
    calls: list[RungCallOut] = Field(default_factory=list)


class RungLedgerOut(BaseModel):
    repair_run_id: str
    decision: str
    resolved_rung: int
    attempts: list[RungAttemptOut] = Field(default_factory=list)
    total_calls: int = 0
    small_tokens: int = 0
    frontier_tokens: int = 0
    total_cost: str = "0"


class CapabilityVersionOut(BaseModel):
    methods: list[str] = Field(default_factory=list)
    auth_types: list[str] = Field(default_factory=list)
    pagination_strategies: list[str] = Field(default_factory=list)
    protocols: list[str] = Field(default_factory=list)
    joins: bool = False
    body_templates: bool = False
    event_feeds: bool = False
    command_templates: bool = False
    multi_path_mapping: bool = False


class CapabilityRuleOut(BaseModel):
    id: str
    fixes: str = ""
    confidence: float = 0.0
    verification_level: str = ""
    produces_patch: bool = False


class CapabilityToolOut(BaseModel):
    name: str
    description: str = ""
    inputSchema: dict[str, Any] = Field(default_factory=dict)


class CapabilitiesOut(BaseModel):
    versions: dict[str, CapabilityVersionOut] = Field(default_factory=dict)
    transforms: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    rules: list[CapabilityRuleOut] = Field(default_factory=list)
    v1_v2_diff: list[str] = Field(default_factory=list)
    v2_v3_diff: list[str] = Field(default_factory=list)
    tools: list[CapabilityToolOut] = Field(default_factory=list)


class KnowledgeLearnedFromOut(BaseModel):
    run_id: str
    rung: int = 0
    tokens: int = 0
    cost: str = "0"
    at: UtcDatetime | None = None


class KnowledgeEntryOut(BaseModel):
    vendor: str = ""
    drift_class: str = ""
    drift_signature: str = ""
    rule_id: str = ""
    patch: list[dict[str, Any]] = Field(default_factory=list)
    learned_from: KnowledgeLearnedFromOut
    times_reused: int = 0
    tokens_saved: int = 0
    cost_saved: str = "0"
    approvals: int = 0


class KnowledgeConnectorOut(BaseModel):
    connector_id: str
    name: str = ""
    vocabulary: str = ""
    refused_vocabularies: list[str] = Field(default_factory=list)
    builds: int = 0
    confirmed_hypotheses: list[str] = Field(default_factory=list)
    last_built_at: UtcDatetime | None = None


class KnowledgeOut(BaseModel):
    entries: list[KnowledgeEntryOut] = Field(default_factory=list)
    connectors: list[KnowledgeConnectorOut] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class ModelUsageEntry(BaseModel):
    provider: str
    model: str
    tier: str
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_reasoning_tokens: int = 0
    total_cost: str
    by_run: list[dict[str, Any]] = Field(default_factory=list)


class ModelUsageResponse(BaseModel):
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_reasoning_tokens: int = 0
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


class EdgeCommandRequest(BaseModel):
    """Enqueue a command for an on-premise agent (REQ-022 B)."""

    agent_id: str = Field(min_length=1, description="Agent id (e.g. grand-plaza-pabx)")
    kind: str = Field(
        min_length=1,
        description="Declarative command kind from the agent bundle (wakeup.set, guest.in, ...)",
    )
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None)
    expires_at: str | None = Field(
        default=None, description="ISO-8601 UTC deadline; the agent never sends past it"
    )

    model_config = {"extra": "forbid"}


class EdgeCommandResponse(BaseModel):
    request_id: str
    existed: bool = False


class EdgeResultRow(BaseModel):
    request_id: str
    agent_id: str
    kind: str
    state: str
    result_json: str | None = None

    model_config = {"extra": "allow"}


class EdgeResultsResponse(BaseModel):
    results: list[EdgeResultRow]


class EdgePostingRow(BaseModel):
    idempotency_key: str
    agent_id: str
    record_type: str
    fields_json: str
    occurred_at: str | None = None
    received_at: str

    model_config = {"extra": "allow"}


class EdgePostingsResponse(BaseModel):
    postings: list[EdgePostingRow]


class EdgeAgentRow(BaseModel):
    agent_id: str
    link_up: bool = False
    link_detail: str = ""
    outbox_depth: int = 0
    inbox_depth: int = 0
    updated_at: str

    model_config = {"extra": "allow"}


class EdgeAgentsResponse(BaseModel):
    agents: list[EdgeAgentRow]
