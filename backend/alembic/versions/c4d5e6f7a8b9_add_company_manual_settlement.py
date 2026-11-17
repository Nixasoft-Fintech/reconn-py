"""add company management and manual settlement

Revision ID: c4d5e6f7a8b9
Revises: 81759b898d57
Create Date: 2026-11-17 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "81759b898d57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("name_key", sa.String(length=255), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
        sa.UniqueConstraint("name_key", name=op.f("uq_companies_name_key")),
    )
    op.create_index(
        op.f("ix_companies_name_key"), "companies", ["name_key"], unique=False
    )

    op.add_column(
        "reconciliation_results",
        sa.Column("match_type", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "reconciliation_results",
        sa.Column("company_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "reconciliation_results",
        sa.Column("settlement_remarks", sa.String(length=2000), nullable=True),
    )
    op.create_index(
        op.f("ix_reconciliation_results_company_id"),
        "reconciliation_results",
        ["company_id"],
        unique=False,
    )

    op.add_column(
        "unmatched_bank_transactions",
        sa.Column("active_settlement_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        op.f("ix_unmatched_bank_transactions_active_settlement_id"),
        "unmatched_bank_transactions",
        ["active_settlement_id"],
        unique=False,
    )

    op.create_table(
        "manual_settlements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "primary_transaction_type", sa.String(length=30), nullable=False
        ),
        sa.Column("primary_record_key", sa.String(length=500), nullable=False),
        sa.Column(
            "counterpart_transaction_type",
            sa.String(length=30),
            nullable=False,
        ),
        sa.Column(
            "counterpart_record_key", sa.String(length=500), nullable=False
        ),
        sa.Column("remarks", sa.String(length=2000), nullable=True),
        sa.Column(
            "settlement_remarks", sa.String(length=2000), nullable=False
        ),
        sa.Column("settled_by", sa.String(length=255), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reversed_by", sa.String(length=255), nullable=True),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversal_reason", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["reconciliation_jobs.job_id"],
            name=op.f("fk_manual_settlements_job_id_reconciliation_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_manual_settlements_company_id_companies"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_manual_settlements")),
    )
    op.create_index(
        op.f("ix_manual_settlements_job_id"),
        "manual_settlements",
        ["job_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_manual_settlements_company_id"),
        "manual_settlements",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_manual_settlements_status"),
        "manual_settlements",
        ["status"],
        unique=False,
    )

    op.create_table(
        "manual_settlement_targets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("settlement_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("transaction_type", sa.String(length=30), nullable=False),
        sa.Column("record_type", sa.String(length=20), nullable=False),
        sa.Column("record_id", sa.String(length=255), nullable=False),
        sa.Column("record_key", sa.String(length=500), nullable=False),
        sa.Column("result_id", sa.Integer(), nullable=True),
        sa.Column("unmatched_bank_id", sa.Integer(), nullable=True),
        sa.Column("original_status", sa.String(length=50), nullable=True),
        sa.Column("original_match_type", sa.String(length=30), nullable=True),
        sa.Column("original_company_id", sa.Uuid(), nullable=True),
        sa.Column(
            "original_settlement_remarks",
            sa.String(length=2000),
            nullable=True,
        ),
        sa.Column(
            "original_bank_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "original_active_settlement_id", sa.Uuid(), nullable=True
        ),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["manual_settlements.id"],
            name=op.f(
                "fk_manual_settlement_targets_settlement_id_manual_settlements"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_manual_settlement_targets")
        ),
    )
    op.create_index(
        op.f("ix_manual_settlement_targets_settlement_id"),
        "manual_settlement_targets",
        ["settlement_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_manual_settlement_targets_job_id"),
        "manual_settlement_targets",
        ["job_id"],
        unique=False,
    )
    op.create_index(
        "uq_manual_settlement_targets_active",
        "manual_settlement_targets",
        ["job_id", "transaction_type", "record_type", "record_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_manual_settlement_targets_active",
        table_name="manual_settlement_targets",
    )
    op.drop_index(
        op.f("ix_manual_settlement_targets_job_id"),
        table_name="manual_settlement_targets",
    )
    op.drop_index(
        op.f("ix_manual_settlement_targets_settlement_id"),
        table_name="manual_settlement_targets",
    )
    op.drop_table("manual_settlement_targets")

    op.drop_index(
        op.f("ix_manual_settlements_status"),
        table_name="manual_settlements",
    )
    op.drop_index(
        op.f("ix_manual_settlements_company_id"),
        table_name="manual_settlements",
    )
    op.drop_index(
        op.f("ix_manual_settlements_job_id"),
        table_name="manual_settlements",
    )
    op.drop_table("manual_settlements")

    op.drop_index(
        op.f("ix_unmatched_bank_transactions_active_settlement_id"),
        table_name="unmatched_bank_transactions",
    )
    op.drop_column("unmatched_bank_transactions", "active_settlement_id")

    op.drop_index(
        op.f("ix_reconciliation_results_company_id"),
        table_name="reconciliation_results",
    )
    op.drop_column("reconciliation_results", "settlement_remarks")
    op.drop_column("reconciliation_results", "company_id")
    op.drop_column("reconciliation_results", "match_type")

    op.drop_index(op.f("ix_companies_name_key"), table_name="companies")
    op.drop_table("companies")
