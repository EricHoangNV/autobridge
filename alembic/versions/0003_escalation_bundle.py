"""Escalation bundle on RepairRun (P1.W6.3, plan §2.5 + §4 W6).

Adds ``repair_runs.escalation_bundle`` JSON column: evidence bundle + one
precise question for a human. Portable types only (SQLite now, Postgres-ready).

Revision ID: 0003_escalation_bundle
Revises: 0002_job_queue
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_escalation_bundle"
down_revision = "0002_job_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("repair_runs") as batch_op:
        batch_op.add_column(sa.Column("escalation_bundle", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("repair_runs") as batch_op:
        batch_op.drop_column("escalation_bundle")
