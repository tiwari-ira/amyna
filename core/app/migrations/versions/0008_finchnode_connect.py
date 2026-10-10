"""FinchNode Connect sessions, FinchNode webhook ledger, patient callback phone

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cases") as batch_op:
        batch_op.add_column(sa.Column("patient_phone", sa.String(length=32), nullable=True))
    op.create_table(
        "finch_connect_sessions",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=80), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=True),
        sa.Column("expires_at", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("finch_connect_sessions") as batch_op:
        batch_op.create_index("ix_finch_connect_sessions_case_id", ["case_id"], unique=False)
        batch_op.create_index("ix_finch_connect_sessions_user_id", ["user_id"], unique=False)
        batch_op.create_index("ix_finch_connect_sessions_subject", ["subject"], unique=False)
    op.create_table(
        "finchnode_webhook_events",
        sa.Column("event_id", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )


def downgrade() -> None:
    op.drop_table("finchnode_webhook_events")
    with op.batch_alter_table("finch_connect_sessions") as batch_op:
        batch_op.drop_index("ix_finch_connect_sessions_subject")
        batch_op.drop_index("ix_finch_connect_sessions_user_id")
        batch_op.drop_index("ix_finch_connect_sessions_case_id")
    op.drop_table("finch_connect_sessions")
    with op.batch_alter_table("cases") as batch_op:
        batch_op.drop_column("patient_phone")
