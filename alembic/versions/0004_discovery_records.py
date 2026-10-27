"""Discovery static import records (P2.S3.1, plan §5 S3).

Adds ``discovery_records`` for reserved DiscoveryResult static imports.
Status is String + CHECK (SQLite now, Postgres-ready); transitions happen
only via the explicit discovery endpoints.

Revision ID: 0004_discovery_records
Revises: 0003_escalation_bundle
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004_discovery_records"
down_revision = "0003_escalation_bundle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_records",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("discovery_id", sa.String(128), nullable=False),
        sa.Column("vendor", sa.String(256), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("candidate_entities", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('authorized', 'connected', 'discovered', 'healing', 'needs_confirmation')",
            name="ck_discovery_records_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_discovery_records"),
        sa.UniqueConstraint("discovery_id", name="uq_discovery_records_discovery_id"),
    )
    with op.batch_alter_table("discovery_records") as batch_op:
        batch_op.create_index(
            "ix_discovery_records_discovery_id", ["discovery_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("discovery_records") as batch_op:
        batch_op.drop_index("ix_discovery_records_discovery_id")
    op.drop_table("discovery_records")
