"""REQ-053 item 3: repair decisions become terminal on operator action.

* ``repair_runs.decision`` gains ``approved`` (an approve approval was
  recorded, via the propose or the business-decision path) and
  ``rolled_back`` (a manual rollback approval was recorded). Before this,
  an approved repair kept ``decision == "propose"`` forever, so the UI's
  decision-derived "waiting" bands kept offering Review/Approve on an
  already-decided repair while the header (connector state, which consults
  approvals) said nothing was waiting. Every "waiting" now derives from
  the decision — one source of truth — with an approval row always moving
  the repair out of the waiting states.
* An automatic rollback after a failed verifying sync re-opens the repair
  as ``escalate`` (REQ-053 item 6) instead of landing here.

Revision ID: 0013_repair_terminal_decisions
Revises: 0012_model_call_prompt_ref
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013_repair_terminal_decisions"
down_revision = "0012_model_call_prompt_ref"
branch_labels = None
depends_on = None

_DECISIONS = "('propose', 'escalate', 'reject', 'climbing', 'failed', 'approved', 'rolled_back')"


def upgrade() -> None:
    with op.batch_alter_table("repair_runs") as batch_op:
        batch_op.drop_constraint("ck_repair_runs_decision", type_="check")
        batch_op.create_check_constraint(
            "ck_repair_runs_decision",
            f"decision IN {_DECISIONS}",
        )
    # Backfill: repairs decided before this migration kept the waiting
    # decision ("propose"/"escalate") alongside their approval row. The
    # latest approval wins (an automatic rollback re-opens, but that path
    # did not exist before, so latest rollback ⇒ rolled_back here).
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE repair_runs SET decision = 'approved' WHERE id IN "
            "(SELECT repair_run_id FROM approvals WHERE decision = 'approve' "
            "AND (repair_run_id, created_at) IN "
            "(SELECT repair_run_id, MAX(created_at) FROM approvals GROUP BY repair_run_id))"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE repair_runs SET decision = 'reject' WHERE id IN "
            "(SELECT repair_run_id FROM approvals WHERE decision = 'reject' "
            "AND (repair_run_id, created_at) IN "
            "(SELECT repair_run_id, MAX(created_at) FROM approvals GROUP BY repair_run_id))"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE repair_runs SET decision = 'rolled_back' WHERE id IN "
            "(SELECT repair_run_id FROM approvals WHERE decision = 'rollback' "
            "AND (repair_run_id, created_at) IN "
            "(SELECT repair_run_id, MAX(created_at) FROM approvals GROUP BY repair_run_id))"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("repair_runs") as batch_op:
        batch_op.drop_constraint("ck_repair_runs_decision", type_="check")
        batch_op.create_check_constraint(
            "ck_repair_runs_decision",
            "decision IN ('propose', 'escalate', 'reject', 'climbing', 'failed')",
        )
