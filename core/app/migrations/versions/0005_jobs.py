"""durable jobs table (task 28)

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=True),
        sa.Column("correlation_id", sa.String(length=80), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("timeout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_jobs_job_type"), ["job_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_jobs_case_id"), ["case_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_jobs_correlation_id"), ["correlation_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_jobs_state"), ["state"], unique=False)
        batch_op.create_index(batch_op.f("ix_jobs_next_run_at"), ["next_run_at"], unique=False)


def downgrade() -> None:
    op.drop_table("jobs")
