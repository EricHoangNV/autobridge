"""REQ-045: prompt id/version/hash on the model-call ledger.

* ``model_calls`` and ``model_call_archive`` gain ``prompt_id``,
  ``prompt_version`` and ``prompt_hash`` (all non-empty-defaulted
  strings; empty for calls that predate prompts-in-files).

Revision ID: 0012_model_call_prompt_ref
Revises: 0011_repair_running_states
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0012_model_call_prompt_ref"
down_revision = "0011_repair_running_states"
branch_labels = None
depends_on = None

_TABLES = ("model_calls", "model_call_archive")
_COLUMNS = (
    ("prompt_id", sa.String(64)),
    ("prompt_version", sa.String(16)),
    ("prompt_hash", sa.String(64)),
)


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            for name, type_ in _COLUMNS:
                batch_op.add_column(sa.Column(name, type_, nullable=False, server_default=""))


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            for name, _ in _COLUMNS:
                batch_op.drop_column(name)
