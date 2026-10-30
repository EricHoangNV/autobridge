"""REQ-055 item 1: enforce the terminal-decision CHECK on every existing DB.

Owner keyed check (2026-10-30): a live database at ``alembic_version 0014``
still rejects ``SET decision='approved'`` — its
``ck_repair_runs_ck_repair_runs_decision`` still holds the 0011 value set
``('propose', 'escalate', 'reject', 'climbing', 'failed')``. Root causes,
both fixed here and in ``control/migrate.py``:

* The ``Base`` naming convention (``ck_%(table_name)s_%(constraint_name)s``,
  in place since P1.W1.5) stores every ``_check()`` constraint under the
  doubled name, so 0011/0013's ``drop_constraint("ck_repair_runs_decision")``
  only works because Alembic expands the name through the convention. The
  migration below therefore keeps passing the canonical single name and
  lets the convention resolve it — and inspects first so it is a safe
  no-op when there is nothing to replace.
* Worse, 0013's upgrade could be skipped entirely: ``_REVISION_EFFECTS``
  recorded 0011/0013 as ``{"tables": [], "columns": []}`` (always
  satisfied), so ``ensure_migrated`` detecting a 0012-era database stamped
  0013 without running its DDL, then upgraded only to 0014. The detection
  table now verifies CHECK contents too, so a CHECK-only migration can no
  longer be stamped over.

This migration re-applies the 0013 value set (plus 0013's approval
backfill, which stuck databases also missed) under the new revision so
every database at 0014 actually runs the DDL. Idempotent: already-correct
databases drop and recreate the identical constraint.

Revision ID: 0015_repair_decision_check_repair
Revises: 0014_refused_build_attempts
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0015_repair_decision_check_repair"
down_revision = "0014_refused_build_attempts"
branch_labels = None
depends_on = None

_DECISIONS = "('propose', 'escalate', 'reject', 'climbing', 'failed', 'approved', 'rolled_back')"

#: Canonical single name — Alembic expands it through the ``Base`` naming
#: convention to the stored doubled name (see module docstring).
_CONSTRAINT = "ck_repair_runs_decision"


def _decision_check_name() -> str | None:
    """Name of the existing ``decision`` CHECK on ``repair_runs``, if any.

    Matched on the column name, not the expression shape: SQLite stores
    ``decision IN (...)`` while Postgres reflects ``ANY (ARRAY[...])``.
    """
    import re as _re

    bind = op.get_bind()
    try:
        checks = sa.inspect(bind).get_check_constraints("repair_runs")
    except Exception:
        return None
    for chk in checks or []:
        sql = str(chk.get("sqltext") or "")
        if _re.search(r"\bdecision\b", sql) is not None:
            return str(chk.get("name") or "")
    return None


def upgrade() -> None:
    if _decision_check_name() is not None:
        with op.batch_alter_table("repair_runs") as batch_op:
            batch_op.drop_constraint(_CONSTRAINT, type_="check")
            batch_op.create_check_constraint(
                _CONSTRAINT,
                f"decision IN {_DECISIONS}",
            )
    else:  # pragma: no cover — every real database carries the CHECK
        with op.batch_alter_table("repair_runs") as batch_op:
            batch_op.create_check_constraint(
                _CONSTRAINT,
                f"decision IN {_DECISIONS}",
            )
    # 0013's backfill (stuck databases skipped it with the DDL): repairs
    # decided before the terminal states existed keep the waiting decision
    # otherwise. Latest approval wins, as in 0013.
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
    # 0014 did not touch the CHECK; downgrading restores the same value set
    # 0013 introduced (kept identical so the round-trip is a no-op).
    if _decision_check_name() is not None:
        with op.batch_alter_table("repair_runs") as batch_op:
            batch_op.drop_constraint(_CONSTRAINT, type_="check")
            batch_op.create_check_constraint(
                _CONSTRAINT,
                f"decision IN {_DECISIONS}",
            )
