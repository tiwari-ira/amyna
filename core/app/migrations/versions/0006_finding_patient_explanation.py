"""findings.patient_facing_explanation (task 12)

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("findings", sa.Column("patient_facing_explanation", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("findings", "patient_facing_explanation")
