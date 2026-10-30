"""REQ-053 item 7: vocabulary-gate refusals without a connector.

New table ``refused_build_attempts``: the vocabulary check
(``POST /connectors/vocabulary-check``) runs before any connector is
created, so a refused build creates no connector row — the attempt
(name, vocabulary, reason) persists here instead. The connector page
merges name-matched attempts into its build history, and the knowledge
profile lists them under refused vocabularies.

Revision ID: 0014_refused_build_attempts
Revises: 0013_repair_terminal_decisions
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0014_refused_build_attempts"
down_revision = "0013_repair_terminal_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refused_build_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("connector_name", sa.String(256), nullable=False, index=True),
        sa.Column("spec_vocabulary", sa.String(8), nullable=False, server_default=""),
        sa.Column("reason", sa.String(2000), nullable=False, server_default=""),
        sa.Column("docs_source", sa.String(512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("refused_build_attempts")
