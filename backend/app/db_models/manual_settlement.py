import uuid

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class ManualSettlement(Base):
    """Header record for a manual settlement between a source and a bank row.

    Settlement history is append-only.  A reversal updates this row instead of
    deleting it so the complete audit trail remains available.
    """

    __tablename__ = "manual_settlements"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("reconciliation_jobs.job_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="ACTIVE",
        index=True,
    )

    primary_transaction_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )
    primary_record_key: Mapped[str] = mapped_column(String(500), nullable=False)

    counterpart_transaction_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )
    counterpart_record_key: Mapped[str] = mapped_column(
        String(500), nullable=False
    )

    # User supplied remarks, kept separately from the generated settlement text.
    remarks: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # The text stored on both affected records.
    settlement_remarks: Mapped[str] = mapped_column(String(2000), nullable=False)

    settled_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    settled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    reversed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reversed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reversal_reason: Mapped[str | None] = mapped_column(
        String(1000), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    company = relationship("Company")
    targets = relationship(
        "ManualSettlementTarget",
        back_populates="settlement",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ManualSettlementTarget.id.asc()",
    )


class ManualSettlementTarget(Base):
    """One affected persisted record plus the values required to reverse it.

    ``active`` participates in a partial unique index so no two active
    settlements can claim the same persisted record.  Reversal marks targets
    inactive, which allows the same records to be settled again later.
    """

    __tablename__ = "manual_settlement_targets"

    __table_args__ = (
        Index(
            "uq_manual_settlement_targets_active",
            "job_id",
            "transaction_type",
            "record_type",
            "record_id",
            unique=True,
            postgresql_where=text("active"),
            sqlite_where=text("active"),
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    settlement_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("manual_settlements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, index=True
    )

    role: Mapped[str] = mapped_column(String(20), nullable=False)

    transaction_type: Mapped[str] = mapped_column(String(30), nullable=False)

    record_type: Mapped[str] = mapped_column(String(20), nullable=False)

    # Stable identifier used by the partial unique index.
    record_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Human readable identity, for example a source transaction id or
    # ``bank.csv#12``.
    record_key: Mapped[str] = mapped_column(String(500), nullable=False)

    result_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unmatched_bank_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    original_status: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    original_match_type: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )
    original_company_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    original_settlement_remarks: Mapped[str | None] = mapped_column(
        String(2000), nullable=True
    )
    original_bank_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    original_active_settlement_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    settlement = relationship(
        "ManualSettlement",
        back_populates="targets",
    )
