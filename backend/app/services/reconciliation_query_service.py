"""Read-only access to completed reconciliation results.

The reconciliation engine and the persistence layer remain unchanged.  These
helpers only query the rows that ``save_reconciliation`` already stored.
"""

from __future__ import annotations

import uuid

from collections.abc import Iterator
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.db_models import (
    ReconciliationJob,
    ReconciliationResult,
    UnmatchedBankTransaction,
)

FUND_REQUEST_SOURCE_TYPE = "fund_request"
VA_TRANSACTION_SOURCE_TYPE = "va_transaction"
COMPLETED_JOB_STATUS = "COMPLETED"

# Exports stream rows in batches instead of loading the complete result set
# into memory.  The value only controls how many rows are fetched per database
# round trip.
EXPORT_BATCH_SIZE = 500

# Frontend status -> internal engine status.  ``MATCHED`` is only the
# individually confirmed automatic match produced by the matching engine.
FRONTEND_STATUS_MAP: dict[str, str] = {
    "MATCHED": "MATCHED",
    "NOT_FOUND": "NOT_FOUND",
}


def get_db_session() -> Iterator[Session]:
    """Yield a database session for read-only request handling."""

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_reconciliation_job(
    session: Session,
    job_id: uuid.UUID,
) -> ReconciliationJob | None:
    """Return the job with ``job_id``, or ``None`` when it does not exist."""

    return session.get(ReconciliationJob, job_id)


def _source_result_filters(
    job_id: uuid.UUID,
    source_type: str,
    internal_status: str,
) -> tuple:
    """Return the filters shared by counting and listing source results."""

    return (
        ReconciliationResult.job_id == job_id,
        ReconciliationResult.source_type == source_type,
        ReconciliationResult.final_status == internal_status,
    )


def _count_source_results(
    session: Session,
    job_id: uuid.UUID,
    source_type: str,
    internal_status: str,
) -> int:
    statement = (
        select(func.count())
        .select_from(ReconciliationResult)
        .where(*_source_result_filters(job_id, source_type, internal_status))
    )

    return int(session.scalar(statement) or 0)


def _list_source_results(
    session: Session,
    job_id: uuid.UUID,
    source_type: str,
    internal_status: str,
    *,
    offset: int = 0,
    limit: int | None = None,
) -> list[ReconciliationResult]:
    statement = (
        select(ReconciliationResult)
        .where(*_source_result_filters(job_id, source_type, internal_status))
        .order_by(ReconciliationResult.id.asc())
    )

    if offset:
        statement = statement.offset(offset)

    if limit is not None:
        statement = statement.limit(limit)

    return list(session.scalars(statement))


def count_fund_request_results(
    session: Session,
    job_id: uuid.UUID,
    internal_status: str,
) -> int:
    """Count one job's Fund Request results for an exact internal status."""

    return _count_source_results(
        session,
        job_id,
        FUND_REQUEST_SOURCE_TYPE,
        internal_status,
    )


def list_fund_request_results(
    session: Session,
    job_id: uuid.UUID,
    internal_status: str,
    *,
    offset: int = 0,
    limit: int | None = None,
) -> list[ReconciliationResult]:
    """Return one job's Fund Request results for an exact internal status.

    Filters are applied before ``offset``/``limit`` so pagination never mixes
    jobs or statuses.  Ordering follows the insertion order used by
    ``save_reconciliation`` so the API preserves the engine's result ordering.
    """

    return _list_source_results(
        session,
        job_id,
        FUND_REQUEST_SOURCE_TYPE,
        internal_status,
        offset=offset,
        limit=limit,
    )


def count_va_transaction_results(
    session: Session,
    job_id: uuid.UUID,
    internal_status: str,
) -> int:
    """Count one job's VA Transaction results for an exact internal status."""

    return _count_source_results(
        session,
        job_id,
        VA_TRANSACTION_SOURCE_TYPE,
        internal_status,
    )


def list_va_transaction_results(
    session: Session,
    job_id: uuid.UUID,
    internal_status: str,
    *,
    offset: int = 0,
    limit: int | None = None,
) -> list[ReconciliationResult]:
    """Return one job's VA Transaction results for an exact internal status.

    Filters are applied before ``offset``/``limit`` so pagination never mixes
    jobs, source types or statuses.  Ordering follows the insertion order used
    by ``save_reconciliation``.
    """

    return _list_source_results(
        session,
        job_id,
        VA_TRANSACTION_SOURCE_TYPE,
        internal_status,
        offset=offset,
        limit=limit,
    )


def _matched_bank_row_ranking(job_id: uuid.UUID):
    """Rank matched bank rows so each statement row is returned once.

    A bank row is identified by its source filename and statement row number.
    The engine allocates each bank row to at most one source result, but this
    ranking keeps the read API resilient to duplicate persisted rows.
    """

    return (
        select(
            ReconciliationResult.id.label("result_id"),
            func.row_number()
            .over(
                partition_by=(
                    ReconciliationResult.bank_source_filename,
                    ReconciliationResult.bank_row_number,
                ),
                order_by=ReconciliationResult.id.asc(),
            )
            .label("bank_row_rank"),
        )
        .where(
            ReconciliationResult.job_id == job_id,
            ReconciliationResult.final_status == "MATCHED",
            ReconciliationResult.bank_row_number.is_not(None),
        )
        .subquery()
    )


def count_matched_bank_transactions(
    session: Session,
    job_id: uuid.UUID,
) -> int:
    """Count distinct matched bank statement rows for one job."""

    ranked = _matched_bank_row_ranking(job_id)
    statement = (
        select(func.count()).select_from(ranked).where(ranked.c.bank_row_rank == 1)
    )

    return int(session.scalar(statement) or 0)


def list_matched_bank_transactions(
    session: Session,
    job_id: uuid.UUID,
    *,
    offset: int = 0,
    limit: int | None = None,
) -> list[ReconciliationResult]:
    """Return distinct matched bank statement rows for one job.

    Only confirmed ``MATCHED`` results with an attached bank row are selected.
    The job and status filters are applied before the duplicate ranking and
    pagination.  Each returned result includes its linked Fund Request or VA
    transaction so callers can see which source record the bank row matched.
    """

    ranked = _matched_bank_row_ranking(job_id)
    statement = (
        select(ReconciliationResult)
        .join(ranked, ReconciliationResult.id == ranked.c.result_id)
        .where(ranked.c.bank_row_rank == 1)
        .order_by(ReconciliationResult.id.asc())
    )

    if offset:
        statement = statement.offset(offset)

    if limit is not None:
        statement = statement.limit(limit)

    return list(session.scalars(statement))


def count_unmatched_bank_transactions(
    session: Session,
    job_id: uuid.UUID,
) -> int:
    """Count one job's genuinely unmatched bank statement rows."""

    statement = (
        select(func.count())
        .select_from(UnmatchedBankTransaction)
        .where(
            UnmatchedBankTransaction.job_id == job_id,
            UnmatchedBankTransaction.active_settlement_id.is_(None),
        )
    )

    return int(session.scalar(statement) or 0)


def list_unmatched_bank_transactions(
    session: Session,
    job_id: uuid.UUID,
    *,
    offset: int = 0,
    limit: int | None = None,
) -> list[UnmatchedBankTransaction]:
    """Return one job's genuinely unmatched bank statement rows.

    Unmatched bank rows are persisted separately by ``save_reconciliation``
    only when the engine did not allocate them to any Fund Request or VA
    transaction.  Pagination is applied at the database level in the original
    insertion order.
    """

    statement = (
        select(UnmatchedBankTransaction)
        .where(
            UnmatchedBankTransaction.job_id == job_id,
            UnmatchedBankTransaction.active_settlement_id.is_(None),
        )
        .order_by(UnmatchedBankTransaction.id.asc())
    )

    if offset:
        statement = statement.offset(offset)

    if limit is not None:
        statement = statement.limit(limit)

    return list(session.scalars(statement))


HISTORY_SOURCE_TYPES = (
    FUND_REQUEST_SOURCE_TYPE,
    VA_TRANSACTION_SOURCE_TYPE,
)


def _normalize_history_status_filter(status: str | None) -> str | None:
    """Return an uppercase status filter, or ``None`` for no filtering."""

    if status is None:
        return None

    normalized = status.strip().upper()
    return normalized or None


def _aggregate_source_status_counts(
    session: Session,
    job_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict[str, dict[str, int]]]:
    """Group Fund Request and VA result counts by job, source and status."""

    if not job_ids:
        return {}

    statement = (
        select(
            ReconciliationResult.job_id,
            ReconciliationResult.source_type,
            ReconciliationResult.final_status,
            func.count().label("record_count"),
        )
        .where(
            ReconciliationResult.job_id.in_(job_ids),
            ReconciliationResult.source_type.in_(HISTORY_SOURCE_TYPES),
        )
        .group_by(
            ReconciliationResult.job_id,
            ReconciliationResult.source_type,
            ReconciliationResult.final_status,
        )
    )

    counts: dict[uuid.UUID, dict[str, dict[str, int]]] = {}

    for job_id, source_type, final_status, record_count in session.execute(statement):
        source_counts = counts.setdefault(job_id, {})
        status_counts = source_counts.setdefault(source_type, {})
        status_counts[final_status] = int(record_count)

    return counts


def _aggregate_bank_status_counts(
    session: Session,
    job_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict[str, int]]:
    """Count each persisted bank statement row once per job and status.

    A bank row is identified by its source filename and statement row number,
    matching the deduplication used by the existing bank result APIs.  When the
    same identity is persisted more than once, a confirmed ``MATCHED`` row is
    preferred over any other status so a bank row is never counted twice.
    """

    if not job_ids:
        return {}

    ranked = (
        select(
            ReconciliationResult.job_id.label("job_id"),
            ReconciliationResult.final_status.label("final_status"),
            func.row_number()
            .over(
                partition_by=(
                    ReconciliationResult.job_id,
                    ReconciliationResult.bank_source_filename,
                    ReconciliationResult.bank_row_number,
                ),
                order_by=(
                    case(
                        (ReconciliationResult.final_status == "MATCHED", 0),
                        else_=1,
                    ),
                    ReconciliationResult.id.asc(),
                ),
            )
            .label("bank_row_rank"),
        )
        .where(
            ReconciliationResult.job_id.in_(job_ids),
            ReconciliationResult.bank_row_number.is_not(None),
        )
        .subquery()
    )

    statement = (
        select(
            ranked.c.job_id,
            ranked.c.final_status,
            func.count().label("record_count"),
        )
        .where(ranked.c.bank_row_rank == 1)
        .group_by(ranked.c.job_id, ranked.c.final_status)
    )

    counts: dict[uuid.UUID, dict[str, int]] = {}

    for job_id, final_status, record_count in session.execute(statement):
        status_counts = counts.setdefault(job_id, {})
        status_counts[final_status] = int(record_count)

    return counts


def _aggregate_unmatched_bank_counts(
    session: Session,
    job_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    """Group genuinely unmatched bank row counts by job."""

    if not job_ids:
        return {}

    statement = (
        select(
            UnmatchedBankTransaction.job_id,
            func.count().label("record_count"),
        )
        .where(UnmatchedBankTransaction.job_id.in_(job_ids))
        .where(UnmatchedBankTransaction.active_settlement_id.is_(None))
        .group_by(UnmatchedBankTransaction.job_id)
    )

    return {
        job_id: int(record_count) for job_id, record_count in session.execute(statement)
    }


def _history_source_counts(
    status_counts: dict[str, int],
) -> dict[str, int]:
    """Serialize one source category without merging other statuses."""

    total = sum(int(value) for value in status_counts.values())
    matched = int(status_counts.get("MATCHED", 0))
    not_found = int(status_counts.get("NOT_FOUND", 0))

    return {
        "total_records": total,
        "matched_records": matched,
        "not_found_records": not_found,
        "other_records": total - matched - not_found,
    }


def _history_bank_counts(
    status_counts: dict[str, int],
    unmatched_count: int,
) -> dict[str, int]:
    """Serialize bank counts using the existing bank result semantics."""

    counts = dict(status_counts)
    matched = int(counts.pop("MATCHED", 0))
    other = sum(int(value) for value in counts.values())
    not_found = int(unmatched_count)

    return {
        "total_records": matched + other + not_found,
        "matched_records": matched,
        "not_found_records": not_found,
        "other_records": other,
    }


def _history_processing_duration(
    job: ReconciliationJob,
) -> float | None:
    """Derive processing duration only when both timestamps make it possible."""

    created_at = job.created_at
    completed_at = job.completed_at

    if created_at is None or completed_at is None:
        return None

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)

    duration = (completed_at - created_at).total_seconds()

    if duration < 0:
        return None

    return round(duration, 3)


def _serialize_history_job(
    job: ReconciliationJob,
    source_counts: dict[str, dict[str, int]],
    bank_status_counts: dict[str, int],
    unmatched_bank_count: int,
) -> dict[str, Any]:
    """Serialize a persisted job without running reconciliation."""

    return {
        "job_id": str(job.job_id),
        "status": job.status,
        "created_at": (job.created_at.isoformat() if job.created_at else None),
        "completed_at": (job.completed_at.isoformat() if job.completed_at else None),
        "processing_duration_seconds": _history_processing_duration(job),
        "fund_request_filename": job.fund_request_filename,
        "va_transaction_filename": job.va_transaction_filename,
        "bank_files": job.bank_files,
        "fund_request": _history_source_counts(
            source_counts.get(FUND_REQUEST_SOURCE_TYPE, {})
        ),
        "va_transaction": _history_source_counts(
            source_counts.get(VA_TRANSACTION_SOURCE_TYPE, {})
        ),
        "bank_transaction": _history_bank_counts(
            bank_status_counts,
            unmatched_bank_count,
        ),
    }


def _serialize_history_jobs(
    session: Session,
    jobs: list[ReconciliationJob],
) -> list[dict[str, Any]]:
    """Serialize a page of jobs using a fixed number of aggregate queries."""

    if not jobs:
        return []

    job_ids = [job.job_id for job in jobs]
    source_counts = _aggregate_source_status_counts(session, job_ids)
    bank_counts = _aggregate_bank_status_counts(session, job_ids)
    unmatched_counts = _aggregate_unmatched_bank_counts(session, job_ids)

    return [
        _serialize_history_job(
            job,
            source_counts.get(job.job_id, {}),
            bank_counts.get(job.job_id, {}),
            unmatched_counts.get(job.job_id, 0),
        )
        for job in jobs
    ]


def list_reconciliation_history(
    session: Session,
    *,
    page: int = 1,
    limit: int = 100,
    status: str | None = None,
    user_id: uuid.UUID | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """Return persisted reconciliation jobs, newest first."""

    filters = []

    normalized_status = _normalize_history_status_filter(status)

    if normalized_status:
        filters.append(func.upper(ReconciliationJob.status) == normalized_status)

    # Apply ownership filtering for non-admin users.
    if user_id is not None:
        filters.append(ReconciliationJob.user_id == user_id)

    total = int(
        session.scalar(
            select(func.count()).select_from(ReconciliationJob).where(*filters)
        )
        or 0
    )

    if total == 0:
        return 0, []

    offset = (page - 1) * limit

    statement = (
        select(ReconciliationJob)
        .where(*filters)
        .order_by(
            ReconciliationJob.created_at.desc(),
            ReconciliationJob.job_id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )

    jobs = list(session.scalars(statement))

    return total, _serialize_history_jobs(session, jobs)


def get_reconciliation_history_job(
    session: Session,
    job_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Return one persisted job for the history detail API."""

    job = get_reconciliation_job(session, job_id)

    if job is None:
        return None

    return _serialize_history_jobs(session, [job])[0]


def _stream_results(session: Session, statement) -> Iterator[ReconciliationResult]:
    """Yield ORM rows in batches without applying API pagination."""

    statement = statement.execution_options(yield_per=EXPORT_BATCH_SIZE)
    yield from session.scalars(statement)


def iter_fund_request_results(
    session: Session,
    job_id: uuid.UUID,
    internal_status: str,
) -> Iterator[ReconciliationResult]:
    """Stream every Fund Request result for one job and exact status.

    The same filters and ordering as the paginated API are used, but no
    ``offset``/``limit`` is applied so exports contain the complete dataset.
    """

    statement = (
        select(ReconciliationResult)
        .where(
            *_source_result_filters(
                job_id,
                FUND_REQUEST_SOURCE_TYPE,
                internal_status,
            )
        )
        .order_by(ReconciliationResult.id.asc())
    )

    yield from _stream_results(session, statement)


def iter_va_transaction_results(
    session: Session,
    job_id: uuid.UUID,
    internal_status: str,
) -> Iterator[ReconciliationResult]:
    """Stream every VA Transaction result for one job and exact status."""

    statement = (
        select(ReconciliationResult)
        .where(
            *_source_result_filters(
                job_id,
                VA_TRANSACTION_SOURCE_TYPE,
                internal_status,
            )
        )
        .order_by(ReconciliationResult.id.asc())
    )

    yield from _stream_results(session, statement)


def iter_matched_bank_transactions(
    session: Session,
    job_id: uuid.UUID,
) -> Iterator[ReconciliationResult]:
    """Stream every distinct matched bank row for one job.

    The duplicate ranking is identical to the paginated API, so exports include
    each ``(bank_source_filename, bank_row_number)`` pair once.
    """

    ranked = _matched_bank_row_ranking(job_id)
    statement = (
        select(ReconciliationResult)
        .join(ranked, ReconciliationResult.id == ranked.c.result_id)
        .where(ranked.c.bank_row_rank == 1)
        .order_by(ReconciliationResult.id.asc())
    )

    yield from _stream_results(session, statement)


def iter_unmatched_bank_transactions(
    session: Session,
    job_id: uuid.UUID,
) -> Iterator[UnmatchedBankTransaction]:
    """Stream every genuinely unmatched bank row for one job."""

    statement = (
        select(UnmatchedBankTransaction)
        .where(
            UnmatchedBankTransaction.job_id == job_id,
            UnmatchedBankTransaction.active_settlement_id.is_(None),
        )
        .order_by(UnmatchedBankTransaction.id.asc())
        .execution_options(yield_per=EXPORT_BATCH_SIZE)
    )

    yield from session.scalars(statement)


def _text(value: Decimal | date | datetime | None) -> str | None:
    """Serialize stored scalar values using the existing API conventions."""

    if value is None:
        return None

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    return str(value)


def serialize_result(row: ReconciliationResult) -> dict[str, Any]:
    """Serialize a persisted result using the existing engine field names.

    Fields mirror ``MatchResult.to_dict``.  Additional stored Fund Request and
    bank statement details are included without renaming existing fields.
    """

    return {
        "source_type": row.source_type,
        "source_transaction_id": row.source_transaction_id,
        "source_row_number": row.source_row_number,
        "source_reference": row.source_utr or row.source_reference,
        "source_utr": row.source_utr,
        "source_reference_number": row.source_reference,
        "source_amount": _text(row.source_amount),
        "source_user": row.source_user,
        "source_date": _text(row.source_date),
        "source_status": row.source_status,
        "source_bank": row.source_bank,
        "source_payment_mode": row.source_payment_mode,
        "source_virtual_account_number": row.source_virtual_account_number,
        "source_data": row.source_data,
        "bank_row_number": row.bank_row_number,
        "bank_name": row.bank_name,
        "bank_utr": row.bank_utr,
        "bank_reference": row.bank_reference,
        "bank_amount": _text(row.bank_amount),
        "bank_signed_amount": _text(row.bank_signed_amount),
        "bank_user": row.bank_user,
        "bank_date": _text(row.bank_date),
        "bank_value_date": _text(row.bank_value_date),
        "bank_narration": row.bank_narration,
        "bank_direction": row.bank_direction,
        "bank_account_number": row.bank_account_number,
        "bank_account_holder": row.bank_account_holder,
        "bank_format": row.bank_format,
        "bank_source_filename": row.bank_source_filename,
        "bank_extracted_references": row.bank_extracted_references,
        "bank_data": row.bank_data,
        "reference_status": row.reference_status,
        "amount_status": row.amount_status,
        "user_status": row.user_status,
        "date_status": row.date_status,
        "final_status": row.final_status,
        "exception_reason": row.exception_reason,
        "match_type": row.match_type,
        "company_id": str(row.company_id) if row.company_id else None,
        "settlement_remarks": row.settlement_remarks,
    }


def serialize_unmatched_bank(
    row: UnmatchedBankTransaction,
) -> dict[str, Any]:
    """Serialize an unmatched bank row with the existing bank field names.

    Unmatched bank rows have no linked source record, so the source and
    matching-evidence fields are returned as ``None`` while the bank details
    keep the same shape used by the Fund Request and VA APIs.
    """

    return {
        "bank_transaction_id": row.id,
        "source_type": None,
        "source_transaction_id": None,
        "source_row_number": None,
        "source_reference": None,
        "source_utr": None,
        "source_reference_number": None,
        "source_amount": None,
        "source_user": None,
        "source_date": None,
        "source_status": None,
        "source_bank": None,
        "source_payment_mode": None,
        "source_virtual_account_number": None,
        "source_data": None,
        "bank_row_number": row.statement_row_number,
        "bank_name": row.bank_name,
        "bank_utr": row.utr,
        "bank_reference": row.reference,
        "bank_amount": _text(row.amount),
        "bank_signed_amount": _text(row.signed_amount),
        "bank_user": None,
        "bank_date": _text(row.transaction_date),
        "bank_value_date": _text(row.value_date),
        "bank_narration": row.narration,
        "bank_direction": row.direction,
        "bank_account_number": row.bank_account_number,
        "bank_account_holder": row.account_holder,
        "bank_format": row.bank_format,
        "bank_source_filename": row.source_filename,
        "bank_extracted_references": row.extracted_references,
        "bank_data": row.raw_data,
        "bank_debit": _text(row.debit),
        "bank_credit": _text(row.credit),
        "bank_balance": _text(row.balance),
        "reference_status": None,
        "amount_status": None,
        "user_status": None,
        "date_status": None,
        "final_status": "NOT_FOUND",
        "exception_reason": None,
        "match_type": None,
        "company_id": None,
        "settlement_remarks": None,
    }
