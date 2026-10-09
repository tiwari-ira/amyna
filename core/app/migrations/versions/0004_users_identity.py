"""patient identity: users, sessions, finch links, case ownership (tasks 5/19)

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("auth_subject", sa.String(length=160), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index("ix_users_auth_subject", ["auth_subject"], unique=True)

    op.create_table(
        "patient_sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    with op.batch_alter_table("patient_sessions", schema=None) as batch_op:
        batch_op.create_index("ix_patient_sessions_user_id", ["user_id"], unique=False)

    op.create_table(
        "finch_links",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("finchnode_patient_id", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("finch_links", schema=None) as batch_op:
        batch_op.create_index("ix_finch_links_user_id", ["user_id"], unique=False)
        batch_op.create_index(
            "ix_finch_links_finchnode_patient_id", ["finchnode_patient_id"], unique=False
        )

    op.add_column("cases", sa.Column("user_id", sa.String(length=40), nullable=True))
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.create_foreign_key("fk_cases_user_id", "users", ["user_id"], ["id"])
        batch_op.create_index("ix_cases_user_id", ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.drop_index("ix_cases_user_id")
        batch_op.drop_constraint("fk_cases_user_id", type_="foreignkey")
    op.drop_column("cases", "user_id")
    op.drop_table("finch_links")
    op.drop_table("patient_sessions")
    op.drop_table("users")
