"""Authorization recordings move out of the database (encrypted file store)

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("authorization_recordings") as batch_op:
        batch_op.alter_column("audio", existing_type=sa.LargeBinary(), nullable=True)
        batch_op.add_column(sa.Column("storage_key", sa.String(length=120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("authorization_recordings") as batch_op:
        batch_op.drop_column("storage_key")
        batch_op.alter_column("audio", existing_type=sa.LargeBinary(), nullable=False)
