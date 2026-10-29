"""Reasoning-token split on the model-call ledger (REQ-027 owner check).

Adds ``model_calls.reasoning_tokens`` and
``model_call_archive.reasoning_tokens``: reasoning/thinking tokens are a
sub-count of ``output_tokens`` (the provider bills them as completion
tokens), stored separately so a step can say a frontier call spent its
budget thinking and returned an empty message. ``server_default="0"``
keeps pre-migration databases upgrade-safe (never stamp-blindly rule).

Revision ID: 0008_model_call_reasoning_tokens
Revises: 0007_repair_knowledge_provenance
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008_model_call_reasoning_tokens"
down_revision = "0007_repair_knowledge_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_calls") as batch_op:
        batch_op.add_column(
            sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0")
        )
    with op.batch_alter_table("model_call_archive") as batch_op:
        batch_op.add_column(
            sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("model_call_archive") as batch_op:
        batch_op.drop_column("reasoning_tokens")
    with op.batch_alter_table("model_calls") as batch_op:
        batch_op.drop_column("reasoning_tokens")
