"""REQ-031 owner check: scheduler pause flag surviving restarts.

* ``scheduler_state`` table — one row (``id == "scheduler"``): the sync
  scheduler skips passes while ``paused`` is true and the pause is fresh.
  ``POST /demo/reset``, the pause/resume demo endpoints and sims-only
  heal set it while they run, so no scheduled sync races a reset, a seed
  or a heal (half-built connectors, doubled drift rows, locked SQLite).

Revision ID: 0010_scheduler_pause
Revises: 0009_req030_persistence
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010_scheduler_pause"
down_revision = "0009_req030_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduler_state",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_scheduler_state"),
    )


def downgrade() -> None:
    op.drop_table("scheduler_state")
