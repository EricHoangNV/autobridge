"""Core data model (P1.W1.5, plan §2.3).

Creates the ten control-plane tables with portable types only
(SQLite now, Postgres-ready). ``connectors.active_release_id`` is added
last via ``create_foreign_key`` because the connectors/releases pair is a
reference cycle. Downgrade drops everything in reverse order.

Revision ID: 0001_core_data_model
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001_core_data_model"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connectors",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("vendor_docs_ref", sa.Text(), nullable=True),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("credential_ref", sa.String(128), nullable=False),
        sa.Column("target_entities", sa.JSON(), nullable=False),
        sa.Column("runtime_target", sa.JSON(), nullable=False),
        sa.Column("active_release_id", sa.String(36), nullable=True),
        sa.Column("health_status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "health_status IN "
            "('blocked', 'drift', 'healthy', 'investigating', 'review_required')",
            name="ck_connectors_health_status",
        ),
    )
    op.create_table(
        "build_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("connector_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("ladder_trace", sa.JSON(), nullable=False),
        sa.Column("model_usage", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("draft_spec", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["connectors.id"],
            name="fk_build_runs_connector_id_connectors",
        ),
    )
    op.create_index(
        "ix_build_runs_connector_id", "build_runs", ["connector_id"]
    )
    op.create_table(
        "repair_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("drift_event_ids", sa.JSON(), nullable=False),
        sa.Column("ladder_level_reached", sa.Integer(), nullable=False),
        sa.Column("candidate_patch", sa.JSON(), nullable=True),
        sa.Column("rule_id", sa.String(128), nullable=True),
        sa.Column("hypothesis_ids", sa.JSON(), nullable=False),
        sa.Column("visible_test_results", sa.JSON(), nullable=False),
        sa.Column("verifier_report_id", sa.String(36), nullable=True),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("model_usage", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('escalate', 'propose', 'reject')",
            name="ck_repair_runs_decision",
        ),
    )
    op.create_table(
        "connector_releases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("connector_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("parent_release_id", sa.String(36), nullable=True),
        sa.Column("provenance_build_run_id", sa.String(36), nullable=True),
        sa.Column("provenance_repair_run_id", sa.String(36), nullable=True),
        sa.Column("verification_report_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["connectors.id"],
            name="fk_connector_releases_connector_id_connectors",
        ),
        sa.ForeignKeyConstraint(
            ["parent_release_id"],
            ["connector_releases.id"],
            name="fk_connector_releases_parent_release_id_connector_releases",
        ),
        sa.ForeignKeyConstraint(
            ["provenance_build_run_id"],
            ["build_runs.id"],
            name="fk_connector_releases_provenance_build_run_id_build_runs",
        ),
        sa.ForeignKeyConstraint(
            ["provenance_repair_run_id"],
            ["repair_runs.id"],
            name="fk_connector_releases_provenance_repair_run_id_repair_runs",
        ),
        sa.UniqueConstraint(
            "connector_id", "version", name="uq_connector_releases_version"
        ),
    )
    op.create_index(
        "ix_connector_releases_connector_id", "connector_releases", ["connector_id"]
    )
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("release_id", sa.String(36), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("records_observed", sa.JSON(), nullable=False),
        sa.Column("records_emitted", sa.JSON(), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("invariant_results", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("unmet_criteria", sa.JSON(), nullable=False),
        sa.Column("data_readiness", sa.JSON(), nullable=False),
        sa.Column("fingerprint_id", sa.String(36), nullable=True),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["connector_releases.id"],
            name="fk_sync_runs_release_id_connector_releases",
        ),
        sa.CheckConstraint(
            "outcome IN ('Blocked', 'Complete', 'Degraded')",
            name="ck_sync_runs_outcome",
        ),
    )
    op.create_index("ix_sync_runs_release_id", "sync_runs", ["release_id"])
    op.create_index("ix_sync_runs_fingerprint_id", "sync_runs", ["fingerprint_id"])
    op.create_table(
        "drift_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("connector_id", sa.String(36), nullable=False),
        sa.Column("drift_class", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("source_sync_run_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["connectors.id"],
            name="fk_drift_events_connector_id_connectors",
        ),
        sa.ForeignKeyConstraint(
            ["source_sync_run_id"],
            ["sync_runs.id"],
            name="fk_drift_events_source_sync_run_id_sync_runs",
        ),
        sa.CheckConstraint(
            "status IN ('dismissed', 'escalated', 'investigating', 'open', 'repaired')",
            name="ck_drift_events_status",
        ),
    )
    op.create_index("ix_drift_events_connector_id", "drift_events", ["connector_id"])
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("repair_run_id", sa.String(36), nullable=False),
        sa.Column("actor", sa.String(256), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("from_release_id", sa.String(36), nullable=True),
        sa.Column("to_release_id", sa.String(36), nullable=True),
        sa.Column("rollback_of_release_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["repair_run_id"],
            ["repair_runs.id"],
            name="fk_approvals_repair_run_id_repair_runs",
        ),
        sa.ForeignKeyConstraint(
            ["from_release_id"],
            ["connector_releases.id"],
            name="fk_approvals_from_release_id_connector_releases",
        ),
        sa.ForeignKeyConstraint(
            ["to_release_id"],
            ["connector_releases.id"],
            name="fk_approvals_to_release_id_connector_releases",
        ),
        sa.ForeignKeyConstraint(
            ["rollback_of_release_id"],
            ["connector_releases.id"],
            name="fk_approvals_rollback_of_release_id_connector_releases",
        ),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject', 'rollback')",
            name="ck_approvals_decision",
        ),
    )
    op.create_index("ix_approvals_repair_run_id", "approvals", ["repair_run_id"])
    op.create_table(
        "evidence_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("parent_ids", sa.JSON(), nullable=False),
        sa.Column("run_id", sa.String(72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('approval', 'decision', 'hypothesis', 'model_call', "
            "'observation', 'patch', 'probe', 'verification')",
            name="ck_evidence_records_kind",
        ),
    )
    op.create_index("ix_evidence_records_run_id", "evidence_records", ["run_id"])
    op.create_table(
        "model_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(72), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("cost_estimate", sa.Numeric(12, 6), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "tier IN ('frontier', 'small')",
            name="ck_model_calls_tier",
        ),
    )
    op.create_index("ix_model_calls_run_id", "model_calls", ["run_id"])
    op.create_table(
        "dependency_registrations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("consumer_id", sa.String(128), nullable=False),
        sa.Column("connector_id", sa.String(36), nullable=False),
        sa.Column("entity", sa.String(64), nullable=False),
        sa.Column("policy", sa.String(32), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["connectors.id"],
            name="fk_dependency_registrations_connector_id_connectors",
        ),
        sa.UniqueConstraint(
            "consumer_id",
            "connector_id",
            "entity",
            name="uq_dependency_registrations_consumer",
        ),
        sa.CheckConstraint(
            "policy IN ('notify', 'pause_on_release_change')",
            name="ck_dependency_registrations_policy",
        ),
    )
    op.create_index(
        "ix_dependency_registrations_connector_id",
        "dependency_registrations",
        ["connector_id"],
    )
    # Reference cycle: connectors ↔ connector_releases; the releases table
    # exists by now, so the active-release edge is added last. Batch mode
    # keeps this step working on SQLite (which has no ALTER ADD CONSTRAINT).
    with op.batch_alter_table("connectors") as batch_op:
        batch_op.create_foreign_key(
            "fk_connectors_active_release_id_connector_releases",
            "connector_releases",
            ["active_release_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("connectors") as batch_op:
        batch_op.drop_constraint(
            "fk_connectors_active_release_id_connector_releases",
            type_="foreignkey",
        )
    op.drop_index(
        "ix_dependency_registrations_connector_id",
        table_name="dependency_registrations",
    )
    op.drop_table("dependency_registrations")
    op.drop_index("ix_model_calls_run_id", table_name="model_calls")
    op.drop_table("model_calls")
    op.drop_index("ix_evidence_records_run_id", table_name="evidence_records")
    op.drop_table("evidence_records")
    op.drop_index("ix_approvals_repair_run_id", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_drift_events_connector_id", table_name="drift_events")
    op.drop_table("drift_events")
    op.drop_index("ix_sync_runs_fingerprint_id", table_name="sync_runs")
    op.drop_index("ix_sync_runs_release_id", table_name="sync_runs")
    op.drop_table("sync_runs")
    op.drop_index(
        "ix_connector_releases_connector_id", table_name="connector_releases"
    )
    op.drop_table("connector_releases")
    op.drop_table("repair_runs")
    op.drop_index("ix_build_runs_connector_id", table_name="build_runs")
    op.drop_table("build_runs")
    op.drop_table("connectors")
