"""REQ-030 persistence: repair delta/class, model-call serving columns, PABX events.

* ``repair_runs.drift_class`` + ``repair_runs.fingerprint_delta`` — the full
  L0 key inputs (class plus wire before/after), so the approve path can
  learn without re-deriving them and the repair page can render what
  changed without recomputation.
* ``model_calls.mode`` / ``captured_at`` / ``outcome`` (and the same three
  on ``model_call_archive``) — structured live-vs-replay serving info plus
  what each call produced, for the model-call box.
* ``pabx_link_events`` table — scheduler link snapshots (``link``) and
  demo restart reports (``restart``) for the PABX panel.

Revision ID: 0009_req030_persistence
Revises: 0008_model_call_reasoning_tokens
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_req030_persistence"
down_revision = "0008_model_call_reasoning_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("repair_runs") as batch_op:
        batch_op.add_column(sa.Column("drift_class", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("fingerprint_delta", sa.JSON(), nullable=True))
    with op.batch_alter_table("model_calls") as batch_op:
        batch_op.add_column(
            sa.Column("mode", sa.String(16), nullable=False, server_default="live")
        )
        batch_op.add_column(
            sa.Column("captured_at", sa.String(32), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("outcome", sa.String(64), nullable=True))
    with op.batch_alter_table("model_call_archive") as batch_op:
        batch_op.add_column(
            sa.Column("mode", sa.String(16), nullable=False, server_default="live")
        )
        batch_op.add_column(
            sa.Column("captured_at", sa.String(32), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("outcome", sa.String(64), nullable=True))
    op.create_table(
        "pabx_link_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("connector_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["connector_id"], ["connectors.id"]),
        sa.PrimaryKeyConstraint("id", name="pk_pabx_link_events"),
        sa.CheckConstraint(
            "kind IN ('link', 'restart')", name="ck_pabx_link_events_kind"
        ),
    )
    with op.batch_alter_table("pabx_link_events") as batch_op:
        batch_op.create_index(
            "ix_pabx_link_events_connector_id", ["connector_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("pabx_link_events") as batch_op:
        batch_op.drop_index("ix_pabx_link_events_connector_id")
    op.drop_table("pabx_link_events")
    with op.batch_alter_table("model_call_archive") as batch_op:
        batch_op.drop_column("outcome")
        batch_op.drop_column("captured_at")
        batch_op.drop_column("mode")
    with op.batch_alter_table("model_calls") as batch_op:
        batch_op.drop_column("outcome")
        batch_op.drop_column("captured_at")
        batch_op.drop_column("mode")
    with op.batch_alter_table("repair_runs") as batch_op:
        batch_op.drop_column("fingerprint_delta")
        batch_op.drop_column("drift_class")
