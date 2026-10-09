"""call merge phase (first-class 17A human-merge lifecycle)

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("call_sessions", sa.Column("merge_phase", sa.String(length=32), nullable=True))
    op.add_column(
        "call_sessions", sa.Column("merge_deadline_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("call_sessions", "merge_deadline_at")
    op.drop_column("call_sessions", "merge_phase")
