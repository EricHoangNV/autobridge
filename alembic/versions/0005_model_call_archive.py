"""Per-demo-session model-usage archive (REQ-021 item 1).

Adds ``model_call_archive``: ``POST /demo/reset`` copies every ``model_calls``
row here stamped with the reset's session id, then deletes the live rows.

Revision ID: 0005_model_call_archive
Revises: 0004_discovery_records
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_model_call_archive"
down_revision = "0004_discovery_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_call_archive",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(72), nullable=False),
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
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "tier IN ('frontier', 'small')",
            name="ck_model_call_archive_tier",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_call_archive"),
    )
    with op.batch_alter_table("model_call_archive") as batch_op:
        batch_op.create_index("ix_model_call_archive_session_id", ["session_id"], unique=False)
        batch_op.create_index("ix_model_call_archive_run_id", ["run_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("model_call_archive") as batch_op:
        batch_op.drop_index("ix_model_call_archive_run_id")
        batch_op.drop_index("ix_model_call_archive_session_id")
    op.drop_table("model_call_archive")
