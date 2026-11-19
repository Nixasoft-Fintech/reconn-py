from __future__ import annotations
from asyncio.log import logger
import time
import threading
from sqlalchemy import text

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.dialects.postgresql import insert as pg_insert

import math
import uuid

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from app.database.connection import SessionLocal
from app.db_models import (
    ReconciliationJob,
    ReconciliationResult,
    ReconciliationSummary,
    UnmatchedBankTransaction,
)
from app.models.reconciliation import ReconciliationOutput
import logging
from app.services.failure_classifier import classify_failure
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

retry_outputs = {}
_job_locks = {}
_job_locks_guard = threading.Lock()


def get_job_lock(job_id):
    with _job_locks_guard:
        if job_id not in _job_locks:
            _job_locks[job_id] = threading.Lock()
        return _job_locks[job_id]


def is_transient_db_error(exc):
    return isinstance(exc, OperationalError) or (
        isinstance(exc, DBAPIError) and exc.connection_invalidated
    )


def _json_safe(value: Any) -> Any:
    """Convert Python, pandas and NumPy values into JSON-compatible values."""

    if value is None or isinstance(value, (str, int, bool)):
        return value

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, float):
        return value if math.isfinite(value) else None

    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]

    # Handle NumPy scalar values.
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (ValueError, TypeError):
            pass

    # Handle pandas NaN, NaT and NA values.
    try:
        import pandas as pd

        missing = pd.isna(value)
        if isinstance(missing, bool) and missing:
            return None
    except (ImportError, TypeError, ValueError):
        pass

    return str(value)


def _aware_datetime(value: datetime | None) -> datetime | None:
    """Ensure timestamps are timezone-aware before PostgreSQL insertion."""

    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value


def build_frontend_summary(output: ReconciliationOutput) -> dict[str, Any]:
    """Build the summary response for the frontend."""

    def amount(value: Any) -> Decimal:
        return Decimal(str(value)) if value is not None else Decimal("0")

    def money(value: Decimal) -> float:
        return float(value.quantize(Decimal("0.01")))

    stats = {
        "fund_request": {
            "total_records": 0,
            "matched_records": 0,
            "matched_amount": Decimal("0"),
            "unmatched_records": 0,
            "unmatched_amount": Decimal("0"),
            "excluded_records": 0,
            "excluded_amount": Decimal("0"),
        },
        "va_transaction": {
            "total_records": 0,
            "matched_records": 0,
            "matched_amount": Decimal("0"),
            "unmatched_records": 0,
            "unmatched_amount": Decimal("0"),
        },
    }

    total_matched_amount = Decimal("0")

    for result in output.results:
        source_type = result.source.source_type.lower()
        status = result.final_status.upper()
        source_amount = amount(result.source.amount)

        if source_type not in stats:
            continue

        item = stats[source_type]
        item["total_records"] += 1

        if status == "MATCHED":
            item["matched_records"] += 1
            item["matched_amount"] += source_amount
            total_matched_amount += source_amount

        elif status == "NOT_FOUND":
            item["unmatched_records"] += 1
            item["unmatched_amount"] += source_amount

        elif status == "EXCLUDED_BY_STATUS" and source_type == "fund_request":
            item["excluded_records"] += 1
            item["excluded_amount"] += source_amount

    unmatched_bank_amount = sum(
        (abs(amount(bank.amount)) for bank in output.unmatched_bank_rows),
        Decimal("0"),
    )

    summary = {
        "overall": {
            "total_source_records": len(output.results),
            "total_bank_records": len(output.bank_transactions),
            "total_matched_records": sum(
                1
                for result in output.results
                if result.final_status.upper() == "MATCHED"
            ),
            "total_matched_amount": money(total_matched_amount),
            "total_unmatched_bank_records": len(output.unmatched_bank_rows),
            "total_unmatched_bank_amount": money(unmatched_bank_amount),
        },
        "fund_request": {
            **stats["fund_request"],
        },
        "va_transaction": {
            **stats["va_transaction"],
        },
        "unmatched_bank": {
            "total_records": len(output.unmatched_bank_rows),
            "total_amount": money(unmatched_bank_amount),
        },
    }

    # Convert Decimal amounts to JSON-compatible numbers.
    for source_type in ("fund_request", "va_transaction"):
        for key, value in summary[source_type].items():
            if isinstance(value, Decimal):
                summary[source_type][key] = money(value)

    return summary


def _bank_debit_credit(bank) -> tuple[Decimal | None, Decimal | None]:
    """Separate a bank transaction amount into debit or credit."""

    amount = bank.amount

    if amount is None:
        return None, None

    direction = (bank.direction or "").strip().upper()

    debit_values = {
        "DEBIT",
        "DR",
        "D",
        "WITHDRAWAL",
        "OUTFLOW",
        "MONEY OUT",
        "DEBITED",
    }
    credit_values = {
        "CREDIT",
        "CR",
        "C",
        "DEPOSIT",
        "INFLOW",
        "MONEY IN",
        "CREDITED",
    }

    if direction in debit_values:
        return amount, None

    if direction in credit_values:
        return None, amount

    if bank.signed_amount is not None:
        if bank.signed_amount < 0:
            return amount, None
        if bank.signed_amount > 0:
            return None, amount

    return None, None


def _build_result_record(job_id: uuid.UUID, result) -> ReconciliationResult:
    source = result.source
    bank = result.bank

    return ReconciliationResult(
        job_id=job_id,
        # Source information
        source_type=source.source_type,
        source_transaction_id=source.source_transaction_id,
        source_row_number=source.source_row_number,
        source_utr=source.utr,
        source_reference=source.reference_number,
        source_amount=source.amount,
        source_user=source.user,
        source_date=_aware_datetime(source.transaction_date),
        source_status=source.status,
        source_bank=source.bank,
        source_payment_mode=source.payment_mode,
        source_virtual_account_number=source.virtual_account_number,
        source_data=_json_safe(source.raw_data),
        # Matched bank information
        bank_row_number=bank.bank_row_number if bank else None,
        bank_name=bank.bank_name if bank else None,
        bank_utr=bank.utr if bank else None,
        bank_reference=bank.reference_number if bank else None,
        bank_amount=bank.amount if bank else None,
        bank_signed_amount=bank.signed_amount if bank else None,
        bank_user=bank.user if bank else None,
        bank_date=_aware_datetime(bank.transaction_date) if bank else None,
        bank_value_date=_aware_datetime(bank.value_date) if bank else None,
        bank_narration=bank.narration if bank else None,
        bank_direction=bank.direction if bank else None,
        bank_account_number=bank.bank_account_number if bank else None,
        bank_account_holder=bank.account_holder if bank else None,
        bank_format=bank.bank_format if bank else None,
        bank_source_filename=bank.source_filename if bank else None,
        bank_file_id=bank.bank_file_id if bank else None,
        bank_extracted_references=(
            _json_safe(bank.extracted_references) if bank else None
        ),
        bank_data=_json_safe(bank.raw_data) if bank else None,
        # Matching information
        reference_status=result.reference_status,
        amount_status=result.amount_status,
        user_status=result.user_status,
        date_status=result.date_status,
        final_status=result.final_status,
        exception_reason=result.exception_reason,
    )


def _build_unmatched_bank_record(
    job_id: uuid.UUID,
    bank,
) -> UnmatchedBankTransaction:
    debit, credit = _bank_debit_credit(bank)

    return UnmatchedBankTransaction(
        job_id=job_id,
        bank_name=bank.bank_name or "Unknown",
        statement_row_number=bank.bank_row_number,
        transaction_date=_aware_datetime(bank.transaction_date),
        utr=bank.utr,
        reference=bank.reference_number,
        narration=bank.narration,
        debit=debit,
        credit=credit,
        amount=bank.amount,
        direction=bank.direction,
        raw_data=_json_safe(bank.raw_data),
        bank_account_number=bank.bank_account_number,
        account_holder=bank.account_holder,
        bank_format=bank.bank_format,
        source_filename=bank.source_filename,
        bank_file_id=bank.bank_file_id,
        signed_amount=bank.signed_amount,
        value_date=_aware_datetime(bank.value_date),
        extracted_references=_json_safe(bank.extracted_references),
    )


def create_pending_job(
    *,
    user_id: uuid.UUID,
    fund_request_filename: str | None,
    va_transaction_filename: str | None,
    bank_files: list[dict[str, Any]],
    output: ReconciliationOutput,
) -> uuid.UUID:
    """Create a pending job and save its summary immediately."""

    job_id = uuid.uuid4()

    frontend_summary = build_frontend_summary(output)
    overall = frontend_summary["overall"]
    summary = output.summary

    with SessionLocal.begin() as session:
        job = ReconciliationJob(
            job_id=job_id,
            user_id=user_id,
            status="PENDING",
            fund_request_filename=fund_request_filename,
            va_transaction_filename=va_transaction_filename,
            bank_files=_json_safe(bank_files),
        )
        session.add(job)

        session.add(
            ReconciliationSummary(
                job_id=job_id,
                source_record_count=overall["total_source_records"],
                bank_record_count=overall["total_bank_records"],
                matched_count=overall["total_matched_records"],
                matched_amount=Decimal(str(overall["total_matched_amount"])),
                unmatched_bank_count=overall["total_unmatched_bank_records"],
                unmatched_bank_amount=Decimal(
                    str(overall["total_unmatched_bank_amount"])
                ),
                status_counts=_json_safe(summary.get("status_counts", {})),
                by_source=_json_safe(
                    {
                        "fund_request": frontend_summary["fund_request"],
                        "va_transaction": frontend_summary["va_transaction"],
                    }
                ),
                summary_data=_json_safe(frontend_summary),
            )
        )

    retry_outputs[job_id] = output
    return job_id


def _record_values(records):
    values = []

    for record in records:
        row = {
            key: value
            for key, value in record.__dict__.items()
            if not key.startswith("_") and key != "id"
        }
        values.append(row)

    return values


def _save_batch_with_retry(model, records, conflict_columns, conflict_condition):
    if not records:
        return

    values = _record_values(records)
    table = model.__table__

    statement = (
        pg_insert(table)
        .values(values)
        .on_conflict_do_nothing(
            index_elements=[table.c[column.key] for column in conflict_columns],
            index_where=conflict_condition,
        )
    )

    for attempt in range(1, 4):
        try:
            with SessionLocal.begin() as session:
                session.execute(statement)

            if attempt > 1:
                logger.info(
                    "Database batch successfully saved on attempt %s/3",
                    attempt,
                )

            return

        except (OperationalError, DBAPIError) as exc:
            if not is_transient_db_error(exc) or attempt == 3:
                raise

            logger.warning(
                "Database retry %s/3 failed: %s",
                attempt,
                exc,
            )

            time.sleep(attempt * 2)


def save_reconciliation(
    job_id: uuid.UUID,
    output: ReconciliationOutput,
) -> None:
    """Persist results in retry-safe, independently committed batches."""

    lock = get_job_lock(job_id)

    with lock:
        # Check job status using a separate short transaction.
        with SessionLocal.begin() as session:
            job = session.get(ReconciliationJob, job_id)

            if job is None:
                raise ValueError(f"Reconciliation job {job_id} not found.")

            if job.status == "COMPLETED":
                retry_outputs.pop(job_id, None)
                return

            job.status = "SAVING"
            job.failure_code = None
            job.failure_message = None

        batch_size = 1000

        # Save reconciliation results.
        result_batch = []

        for result in output.results:
            result_batch.append(_build_result_record(job_id, result))

            if len(result_batch) >= batch_size:
                _save_batch_with_retry(
                    ReconciliationResult,
                    result_batch,
                    [
                        ReconciliationResult.job_id,
                        ReconciliationResult.source_type,
                        ReconciliationResult.source_row_number,
                    ],
                    text("source_row_number IS NOT NULL"),
                )
                result_batch.clear()

        if result_batch:
            _save_batch_with_retry(
                ReconciliationResult,
                result_batch,
                [
                    ReconciliationResult.job_id,
                    ReconciliationResult.source_type,
                    ReconciliationResult.source_row_number,
                ],
                text("source_row_number IS NOT NULL"),
            )

  

        # Save only unmatched credit bank transactions.
        unmatched_batch = []

        for bank in output.unmatched_bank_rows:
            debit, credit = _bank_debit_credit(bank)

            # Skip debit and unidentified transactions.
            if credit is None:
                continue

            unmatched_batch.append(
                _build_unmatched_bank_record(job_id, bank)
            )


            if len(unmatched_batch) >= batch_size:
                _save_batch_with_retry(
                    UnmatchedBankTransaction,
                    unmatched_batch,
                    [
                        UnmatchedBankTransaction.job_id,
                        UnmatchedBankTransaction.bank_file_id,
                        UnmatchedBankTransaction.statement_row_number,
                    ],
                    text(
                        "bank_file_id IS NOT NULL "
                        "AND statement_row_number IS NOT NULL"
                    ),
                )
                unmatched_batch.clear()

        if unmatched_batch:
            _save_batch_with_retry(
                UnmatchedBankTransaction,
                unmatched_batch,
                [
                    UnmatchedBankTransaction.job_id,
                    UnmatchedBankTransaction.bank_file_id,
                    UnmatchedBankTransaction.statement_row_number,
                ],
                text(
                    "bank_file_id IS NOT NULL " "AND statement_row_number IS NOT NULL"
                ),
            )

        # Mark completed only after every batch is persisted.
        with SessionLocal.begin() as session:
            job = session.get(ReconciliationJob, job_id)

            if job is None:
                raise ValueError(f"Reconciliation job {job_id} not found.")

            job.status = "COMPLETED"
            job.completed_at = datetime.now(timezone.utc)
            job.failure_code = None
            job.failure_message = None

        # Remove cached output only after successful completion.
        retry_outputs.pop(job_id, None)


def save_reconciliation_background(
    job_id: uuid.UUID,
    output: ReconciliationOutput,
) -> None:
    """Save reconciliation results and retain output if persistence fails."""

    try:
        save_reconciliation(job_id, output)

        logger.info(
            "Database persistence completed. Job ID: %s",
            job_id,
        )

    except Exception as exc:
        logger.exception(
            "Database persistence failed. Job ID: %s",
            job_id,
        )

        failure_code = classify_failure(exc)

        failure_message = f"{type(exc).__name__}: {str(exc)[:1000]}"

        logger.error(
            "Job failure classified. Job ID: %s | Code: %s",
            job_id,
            failure_code,
        )

        try:
            with SessionLocal.begin() as session:
                job = session.get(ReconciliationJob, job_id)

                if job is not None and job.status != "COMPLETED":
                    job.status = "FAILED"
                    job.failure_code = failure_code
                    job.failure_message = failure_message

        except Exception:
            logger.exception(
                "Could not update job status to FAILED. Job ID: %s",
                job_id,
            )
