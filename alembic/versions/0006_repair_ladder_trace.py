"""Persist the repair rung ledger source (REQ-021 item 3).

Adds ``repair_runs.ladder_trace``: every ladder trace entry (level, action,
one-line reason) for the walk, so the repair page and the demo table can
show why each rung was tried without replaying the run.

Revision ID: 0006_repair_ladder_trace
Revises: 0005_model_call_archive
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006_repair_ladder_trace"
down_revision = "0005_model_call_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("repair_runs") as batch_op:
        batch_op.add_column(sa.Column("ladder_trace", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("repair_runs") as batch_op:
        batch_op.drop_column("ladder_trace")
