# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Control plane schemas (P1.W7.1, plan §2.11)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


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
    created_at: datetime


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
    created_at: datetime


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
    created_at: datetime
    updated_at: datetime
    kind: Literal["build"] = "build"


class SyncRunOut(BaseModel):
    id: str
    release_id: str
    started_at: datetime
    finished_at: datetime | None = None
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
    created_at: datetime
    kind: Literal["repair"] = "repair"


class DriftEventOut(BaseModel):
    id: str
    connector_id: str
    drift_class: str
    severity: str
    evidence_ids: list[str]
    source_sync_run_id: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class DependencyOut(BaseModel):
    id: str
    consumer_id: str
    connector_id: str
    entity: str
    policy: str
    paused: bool
    created_at: datetime


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
    created_at: datetime
    updated_at: datetime


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
    created_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EvidenceOut(BaseModel):
    id: str
    kind: str
    payload: dict[str, Any]
    parent_ids: list[str]
    run_id: str
    created_at: datetime


class RepairDecisionRequest(BaseModel):
    # Accepted for backwards compatibility but ignored: the server always
    # records Approval.actor from AUTOBRIDGE_OPERATOR (REQ-001).
    actor: str | None = Field(default=None, description="Ignored; server sets the actor")
    from_release_id: str | None = None
    to_release_id: str | None = None
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
    created_at: datetime
    updated_at: datetime


class InjectFaultRequest(BaseModel):
    target: str = Field(description="Simulator target id: sim-atlas, sim-harbor, atlas, harbor, atlaspms, harborpms")
    scenario_id: str = Field(description="Scenario id to inject (e.g. rename_guest_id, baseline)")
    seed: int | None = Field(default=None, description="Optional seed override")

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
