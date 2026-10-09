"""call_sessions.xai_call_ref (task 15 SIP attach)

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("call_sessions", sa.Column("xai_call_ref", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("call_sessions", "xai_call_ref")
