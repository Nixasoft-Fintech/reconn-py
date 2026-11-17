import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class UnmatchedBankTransaction(Base):
    __tablename__ = "unmatched_bank_transactions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reconciliation_jobs.job_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Original file information
    bank_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )


    statement_row_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # Transaction information
    transaction_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    utr: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    reference: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    narration: Mapped[str | None] = mapped_column(
        String(2000),
        nullable=True,
    )

    debit: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 4),
        nullable=True,
    )

    credit: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 4),
        nullable=True,
    )

    amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 4),
        nullable=True,
    )

    balance: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 4),
        nullable=True,
    )

    direction: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    # Preserve the complete original bank statement row
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    job = relationship(
        "ReconciliationJob",
        back_populates="unmatched_bank_transactions",
    )

    bank_account_number: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    account_holder: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    bank_format: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    source_filename: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    signed_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 4),
        nullable=True,
    )

    value_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    extracted_references: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Set while an active manual settlement claims this bank row.  Unmatched
    # queries filter on this column so settled rows disappear without deleting
    # the original unmatched evidence.
    active_settlement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
