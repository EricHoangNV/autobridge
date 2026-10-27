"""Job queue for the DB-backed JobRunner (P1.W1.6, plan §4 W1).

Adds the ``jobs`` table: one row per BuildRun / SyncRun / RepairRun
execution attempt series. Portable types only (SQLite now, Postgres-ready).

Revision ID: 0002_job_queue
Revises: 0001_core_data_model
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_job_queue"
down_revision = "0001_core_data_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('build', 'repair', 'sync')",
            name="ck_jobs_kind",
        ),
        sa.CheckConstraint(
            "state IN ('failed', 'queued', 'running', 'succeeded')",
            name="ck_jobs_state",
        ),
    )
    op.create_index("ix_jobs_run_id", "jobs", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_run_id", table_name="jobs")
    op.drop_table("jobs")
