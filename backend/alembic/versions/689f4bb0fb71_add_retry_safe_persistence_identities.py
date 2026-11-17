"""add retry safe persistence identities

Revision ID: 689f4bb0fb71
Revises: c4d5e6f7a8b9
Create Date: 2026-11-17 11:12:05.738508

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "689f4bb0fb71"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add bank file identity to both persistence tables.
    op.add_column(
        "reconciliation_results", sa.Column("bank_file_id", sa.Integer(), nullable=True)
    )

    op.add_column(
        "unmatched_bank_transactions",
        sa.Column("bank_file_id", sa.Integer(), nullable=True),
    )

    # Prevent duplicate source result rows when retrying persistence.
    op.create_index(
        "uq_reconciliation_result_identity",
        "reconciliation_results",
        ["job_id", "source_type", "source_row_number"],
        unique=True,
        postgresql_where=sa.text("source_row_number IS NOT NULL"),
    )

    # Prevent duplicate unmatched bank rows when retrying persistence.
    op.create_index(
        "uq_unmatched_bank_identity",
        "unmatched_bank_transactions",
        ["job_id", "bank_file_id", "statement_row_number"],
        unique=True,
        postgresql_where=sa.text(
            "bank_file_id IS NOT NULL AND statement_row_number IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_unmatched_bank_identity", table_name="unmatched_bank_transactions"
    )

    op.drop_index(
        "uq_reconciliation_result_identity", table_name="reconciliation_results"
    )

    op.drop_column("unmatched_bank_transactions", "bank_file_id")
    op.drop_column("reconciliation_results", "bank_file_id")
