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
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class ReconciliationResult(Base):
    __tablename__ = "reconciliation_results"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("reconciliation_jobs.job_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Source information
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    source_transaction_id: Mapped[str | None] = mapped_column(String(255))
    source_row_number: Mapped[int | None] = mapped_column(Integer)
    source_utr: Mapped[str | None] = mapped_column(String(255))
    source_reference: Mapped[str | None] = mapped_column(String(255))
    source_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    source_user: Mapped[str | None] = mapped_column(String(500))
    source_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_status: Mapped[str | None] = mapped_column(String(50))

    source_bank: Mapped[str | None] = mapped_column(String(100))
    source_payment_mode: Mapped[str | None] = mapped_column(String(100))
    source_virtual_account_number: Mapped[str | None] = mapped_column(String(255))

    source_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Matched bank information
    bank_row_number: Mapped[int | None] = mapped_column(Integer)
    bank_name: Mapped[str | None] = mapped_column(String(100))
    bank_utr: Mapped[str | None] = mapped_column(String(255))
    bank_reference: Mapped[str | None] = mapped_column(String(255))
    bank_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    bank_signed_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    bank_user: Mapped[str | None] = mapped_column(String(500))
    bank_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bank_value_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bank_narration: Mapped[str | None] = mapped_column(String(2000))
    bank_direction: Mapped[str | None] = mapped_column(String(30))

    bank_account_number: Mapped[str | None] = mapped_column(String(100))
    bank_account_holder: Mapped[str | None] = mapped_column(String(255))
    bank_format: Mapped[str | None] = mapped_column(String(100))
    bank_source_filename: Mapped[str | None] = mapped_column(String(255))

    bank_extracted_references: Mapped[list | None] = mapped_column(JSONB)

    bank_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Matching results
    reference_status: Mapped[str] = mapped_column(String(50), nullable=False)
    amount_status: Mapped[str] = mapped_column(String(50), nullable=False)
    user_status: Mapped[str] = mapped_column(String(50), nullable=False)
    date_status: Mapped[str] = mapped_column(String(50), nullable=False)

    final_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )

    exception_reason: Mapped[str | None] = mapped_column(String(1000))

    # Manual settlement overlay.  Automatic evidence above is never rewritten.
    match_type: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Deliberately a plain UUID pointer rather than a foreign key.  Existing
    # read-only test fixtures create this table without the companies table.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )

    settlement_remarks: Mapped[str | None] = mapped_column(
        String(2000), nullable=True
    )

    # Record creation timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    job = relationship(
        "ReconciliationJob",
        back_populates="results",
    )
