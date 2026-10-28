"""Learned-knowledge provenance on repairs (REQ-021 item 6).

Adds ``repair_runs.vendor`` and ``repair_runs.drift_signature`` so the
knowledge page can attribute each learned fix (vendor, drift signature,
patch, run, rung, cost) without joining drift events and specs.

Revision ID: 0007_repair_knowledge_provenance
Revises: 0006_repair_ladder_trace
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_repair_knowledge_provenance"
down_revision = "0006_repair_ladder_trace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("repair_runs") as batch_op:
        batch_op.add_column(sa.Column("vendor", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("drift_signature", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("repair_runs") as batch_op:
        batch_op.drop_column("drift_signature")
        batch_op.drop_column("vendor")
