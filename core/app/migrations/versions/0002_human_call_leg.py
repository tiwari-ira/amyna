"""human call leg (bridged human handoff merges)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "call_sessions", sa.Column("human_call_ref", sa.String(length=120), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("call_sessions", "human_call_ref")
