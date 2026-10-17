"""Conference call legs, call start media phase, patient authorization recording

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("call_sessions") as batch_op:
        batch_op.add_column(sa.Column("conference_ref", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("ai_leg_ref", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("media_phase", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("media_deadline_at", sa.DateTime(timezone=True), nullable=True)
        )
    op.create_table(
        "authorization_recordings",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("audio", sa.LargeBinary(), nullable=False),
        sa.Column("mime_type", sa.String(length=32), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("authorization_recordings") as batch_op:
        batch_op.create_index("ix_authorization_recordings_case_id", ["case_id"], unique=True)
        batch_op.create_index("ix_authorization_recordings_token_hash", ["token_hash"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("authorization_recordings") as batch_op:
        batch_op.drop_index("ix_authorization_recordings_token_hash")
        batch_op.drop_index("ix_authorization_recordings_case_id")
    op.drop_table("authorization_recordings")
    with op.batch_alter_table("call_sessions") as batch_op:
        batch_op.drop_column("media_deadline_at")
        batch_op.drop_column("media_phase")
        batch_op.drop_column("ai_leg_ref")
        batch_op.drop_column("conference_ref")
