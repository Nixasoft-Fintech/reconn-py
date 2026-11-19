"""add unmatched bank transaction unique index

Revision ID: b31c26b78dad
Revises: 8ca5b8276b8e
Create Date: 2026-11-19 13:54:59.553608

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b31c26b78dad"
down_revision: Union[str, Sequence[str], None] = "8ca5b8276b8e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "uq_unmatched_bank_transactions_job_file_row",
        "unmatched_bank_transactions",
        ["job_id", "bank_file_id", "statement_row_number"],
        unique=True,
        postgresql_where=sa.text(
            "bank_file_id IS NOT NULL AND statement_row_number IS NOT NULL"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_unmatched_bank_transactions_job_file_row",
        table_name="unmatched_bank_transactions",
    )
