"""add expires_at to reconciliation jobs

Revision ID: f3840606a222
Revises: b31c26b78dad
Create Date: 2026-11-21 12:17:27.171034

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f3840606a222"
down_revision: Union[str, Sequence[str], None] = "b31c26b78dad"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reconciliation_jobs",
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Backfill existing reconciliation jobs.
    op.execute("""
        UPDATE reconciliation_jobs
        SET expires_at = created_at + INTERVAL '1 month'
        WHERE expires_at IS NULL
    """)

    op.alter_column(
        "reconciliation_jobs",
        "expires_at",
        nullable=False,
    )

    op.create_index(
        "ix_reconciliation_jobs_expires_at",
        "reconciliation_jobs",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reconciliation_jobs_expires_at",
        table_name="reconciliation_jobs",
    )

    op.drop_column(
        "reconciliation_jobs",
        "expires_at",
    )
