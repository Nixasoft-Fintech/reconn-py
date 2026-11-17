"""add reconciliation failure message

Revision ID: 8dd06217ffb0
Revises: 689f4bb0fb71
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "8dd06217ffb0"
down_revision: Union[str, Sequence[str], None] = "689f4bb0fb71"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reconciliation_jobs",
        sa.Column("failure_message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reconciliation_jobs", "failure_message")
