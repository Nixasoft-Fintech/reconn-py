"""add failure code to reconciliation jobs

Revision ID: 1bd8f70a87e2
Revises: 8dd06217ffb0
Create Date: 2026-11-17 12:55:33.687391

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "1bd8f70a87e2"
down_revision: Union[str, Sequence[str], None] = "8dd06217ffb0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reconciliation_jobs",
        sa.Column("failure_code", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reconciliation_jobs", "failure_code")
