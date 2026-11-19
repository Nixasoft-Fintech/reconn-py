import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Uuid, func, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB


class ReconciliationJob(Base):
    __tablename__ = "reconciliation_jobs"

    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user = relationship(
        "User",
        back_populates="reconciliation_jobs",
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="PROCESSING",
    )

    failure_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    failure_code: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    fund_request_filename: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    va_transaction_filename: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    bank_files: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Related reconciliation records
    results = relationship(
        "ReconciliationResult",
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    unmatched_bank_transactions = relationship(
        "UnmatchedBankTransaction",
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    summary = relationship(
        "ReconciliationSummary",
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
