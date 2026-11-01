"""REQ-075: persisted per-connector money-value baselines.

New table ``connector_value_baselines``: one row per connector holding
the bounded money sketch of the last clean ``Complete`` sync, so live
detection can feed ``baseline_summary`` to ``signals_from_sync`` and
``money_scale_shift`` fires outside bench/beacon too.

Revision ID: 0016_connector_value_baseline
Revises: 0015_repair_decision_check_repair
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0016_connector_value_baseline"
down_revision = "0015_repair_decision_check_repair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connector_value_baselines",
        sa.Column("connector_id", sa.String(36), primary_key=True),
        sa.Column("release_id", sa.String(36), nullable=False),
        sa.Column("sketch", sa.JSON(), nullable=False),
        sa.Column("sync_run_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["connector_id"], ["connectors.id"]),
    )


def downgrade() -> None:
    op.drop_table("connector_value_baselines")
