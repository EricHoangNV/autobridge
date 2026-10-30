"""REQ-043: repair rows exist while the climb runs.

* ``repair_runs.decision`` gains two running states: ``climbing`` (the
  repair job started; the row is created at job start so the Repairs list
  and the repair page are reachable while the ladder runs) and ``failed``
  (the job crashed, timed out or was orphaned by a restart before
  reaching a decision, with the reason on the trace). The CHECK
  constraint is widened from (propose, escalate, reject) to all five.

Revision ID: 0011_repair_running_states
Revises: 0010_scheduler_pause
"""

from __future__ import annotations

from alembic import op

revision = "0011_repair_running_states"
down_revision = "0010_scheduler_pause"
branch_labels = None
depends_on = None

_DECISIONS = "('propose', 'escalate', 'reject', 'climbing', 'failed')"


def upgrade() -> None:
    with op.batch_alter_table("repair_runs") as batch_op:
        batch_op.drop_constraint("ck_repair_runs_decision", type_="check")
        batch_op.create_check_constraint(
            "ck_repair_runs_decision",
            f"decision IN {_DECISIONS}",
        )


def downgrade() -> None:
    with op.batch_alter_table("repair_runs") as batch_op:
        batch_op.drop_constraint("ck_repair_runs_decision", type_="check")
        batch_op.create_check_constraint(
            "ck_repair_runs_decision",
            "decision IN ('propose', 'escalate', 'reject')",
        )
