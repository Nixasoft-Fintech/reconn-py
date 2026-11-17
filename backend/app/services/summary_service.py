"""Recompute the persisted summary for a reconciliation job.

The reconciliation engine is not invoked.  Counts are derived from the current
persisted results and unmatched bank rows so manual settlements and reversals
are reflected immediately by the existing summary API and dashboard.
"""

from __future__ import annotations

import uuid

from collections import Counter
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db_models import (
    ReconciliationResult,
    ReconciliationSummary,
    UnmatchedBankTransaction,
)


def _decimal(value: Decimal | int | float | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def _empty_source_stats() -> dict[str, Decimal | int]:
    return {
        "total_records": 0,
        "matched_records": 0,
        "matched_amount": Decimal("0"),
        "unmatched_records": 0,
        "unmatched_amount": Decimal("0"),
    }


def build_persisted_summary(
    session: Session,
    job_id: uuid.UUID,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Build the frontend summary shape from persisted rows only."""

    results = list(
        session.scalars(
            select(ReconciliationResult).where(
                ReconciliationResult.job_id == job_id
            )
        )
    )

    unmatched_bank_count = int(
        session.scalar(
            select(func.count())
            .select_from(UnmatchedBankTransaction)
            .where(
                UnmatchedBankTransaction.job_id == job_id,
                UnmatchedBankTransaction.active_settlement_id.is_(None),
            )
        )
        or 0
    )

    unmatched_bank_amount = session.scalar(
        select(
            func.coalesce(
                func.sum(func.abs(UnmatchedBankTransaction.amount)),
                0,
            )
        ).where(
            UnmatchedBankTransaction.job_id == job_id,
            UnmatchedBankTransaction.active_settlement_id.is_(None),
        )
    )

    fund_request = _empty_source_stats()
    va_transaction = _empty_source_stats()
    fund_request["excluded_records"] = 0
    fund_request["excluded_amount"] = Decimal("0")

    total_matched_amount = Decimal("0")
    total_matched_records = 0
    status_counts: Counter[str] = Counter()
    linked_bank_rows: set[tuple[str | None, int | None]] = set()

    for result in results:
        source_type = (result.source_type or "").lower()
        status = (result.final_status or "").upper()
        source_amount = _decimal(result.source_amount)

        status_counts[status] += 1

        if result.bank_row_number is not None:
            linked_bank_rows.add(
                (result.bank_source_filename, result.bank_row_number)
            )

        if source_type == "fund_request":
            stats = fund_request
        elif source_type == "va_transaction":
            stats = va_transaction
        else:
            continue

        stats["total_records"] = int(stats["total_records"]) + 1

        if status == "MATCHED":
            stats["matched_records"] = int(stats["matched_records"]) + 1
            stats["matched_amount"] = (
                _decimal(stats["matched_amount"]) + source_amount
            )
            total_matched_records += 1
            total_matched_amount += source_amount
        elif status == "NOT_FOUND":
            stats["unmatched_records"] = int(stats["unmatched_records"]) + 1
            stats["unmatched_amount"] = (
                _decimal(stats["unmatched_amount"]) + source_amount
            )
        elif status == "EXCLUDED_BY_STATUS" and source_type == "fund_request":
            stats["excluded_records"] = int(stats["excluded_records"]) + 1
            stats["excluded_amount"] = (
                _decimal(stats["excluded_amount"]) + source_amount
            )

    def serialize_source(stats: dict[str, Decimal | int]) -> dict[str, Any]:
        return {
            key: (_money(value) if isinstance(value, Decimal) else int(value))
            for key, value in stats.items()
        }

    fund_request_data = serialize_source(fund_request)
    va_transaction_data = serialize_source(va_transaction)

    total_unmatched_bank_amount = abs(_decimal(unmatched_bank_amount))

    total_bank_records = len(linked_bank_rows) + unmatched_bank_count

    summary = {
        "overall": {
            "total_source_records": len(results),
            "total_bank_records": total_bank_records,
            "total_matched_records": total_matched_records,
            "total_matched_amount": _money(total_matched_amount),
            "total_unmatched_bank_records": unmatched_bank_count,
            "total_unmatched_bank_amount": _money(
                total_unmatched_bank_amount
            ),
        },
        "fund_request": fund_request_data,
        "va_transaction": va_transaction_data,
        "unmatched_bank": {
            "total_records": unmatched_bank_count,
            "total_amount": _money(total_unmatched_bank_amount),
        },
    }

    return summary, dict(status_counts)


def recompute_job_summary(session: Session, job_id: uuid.UUID) -> None:
    """Update the stored summary row for ``job_id`` without committing."""

    summary_data, status_counts = build_persisted_summary(session, job_id)
    overall = summary_data["overall"]

    record = session.get(ReconciliationSummary, job_id)

    if record is None:
        record = ReconciliationSummary(job_id=job_id)
        session.add(record)

    record.source_record_count = overall["total_source_records"]
    record.bank_record_count = overall["total_bank_records"]
    record.matched_count = overall["total_matched_records"]
    record.matched_amount = Decimal(str(overall["total_matched_amount"]))
    record.unmatched_bank_count = overall["total_unmatched_bank_records"]
    record.unmatched_bank_amount = Decimal(
        str(overall["total_unmatched_bank_amount"])
    )
    record.status_counts = status_counts
    record.by_source = {
        "fund_request": summary_data["fund_request"],
        "va_transaction": summary_data["va_transaction"],
    }
    record.summary_data = summary_data
