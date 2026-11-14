import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class ReconciliationSummary(Base):
    __tablename__ = "reconciliation_summaries"

    # One summary per reconciliation job
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reconciliation_jobs.job_id", ondelete="CASCADE"),
        primary_key=True,
    )

    source_record_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    bank_record_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    matched_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    matched_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 4),
        nullable=False,
        default=Decimal("0"),
    )

    unmatched_bank_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    unmatched_bank_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 4),
        nullable=False,
        default=Decimal("0"),
    )

    # Preserve the existing engine's status_counts and by_source structures
    status_counts: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    by_source: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    # Complete original summary returned by the reconciliation engine
    summary_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    job = relationship(
        "ReconciliationJob",
        back_populates="summary",
    )
