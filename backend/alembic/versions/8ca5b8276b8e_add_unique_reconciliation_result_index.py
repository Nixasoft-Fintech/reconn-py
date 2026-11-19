"""add unique reconciliation result index

Revision ID: 8ca5b8276b8e
Revises: 735d82ccae3e
Create Date: 2026-11-19 13:28:40.528734
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "8ca5b8276b8e"
down_revision: Union[str, Sequence[str], None] = "735d82ccae3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "uq_reconciliation_results_job_source_row",
        "reconciliation_results",
        ["job_id", "source_type", "source_row_number"],
        unique=True,
        postgresql_where=sa.text("source_row_number IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_reconciliation_results_job_source_row",
        table_name="reconciliation_results",
    )
