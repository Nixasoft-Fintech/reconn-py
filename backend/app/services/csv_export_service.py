"""Streaming CSV exports for persisted reconciliation results.

Exports read the same persisted rows as the list APIs but deliberately bypass
the project's pagination limits.  Rows are fetched in batches with
``yield_per`` and written to the HTTP response incrementally so large result
sets are never held in memory as a single list or string.

Nothing in this module writes to the database.  The reconciliation engine,
matching logic, adapters, normalization and persistence layer remain untouched.
"""

from __future__ import annotations

import csv
import io

from collections.abc import Callable, Iterator
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.db_models import ReconciliationResult
from app.services.reconciliation_query_service import (
    iter_fund_request_results,
    iter_matched_bank_transactions,
    iter_unmatched_bank_transactions,
    iter_va_transaction_results,
)

Column = tuple[str, Callable[[Any], Any]]
Bind = Engine | Connection

# Flush the CSV buffer to the response after roughly this many characters.
CSV_CHUNK_SIZE = 64 * 1024

_BOM = "\ufeff"

# Text values beginning with these characters can be interpreted as formulas by
# spreadsheet applications.  Numeric values are never prefixed because they are
# serialized from ``Decimal`` before the text check is applied.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _source_reference(row: ReconciliationResult) -> str | None:
    """Prefer the stored UTR, falling back to the reference number."""

    return row.source_utr or row.source_reference


def _match_evidence(row: ReconciliationResult) -> str | None:
    """Build a readable, non-JSON summary of the matching evidence."""

    parts = [
        f"{label}={value}"
        for label, value in (
            ("Reference", row.reference_status),
            ("Amount", row.amount_status),
            ("User", row.user_status),
            ("Date", row.date_status),
        )
        if value
    ]

    return "; ".join(parts) or None


FUND_REQUEST_COLUMNS: tuple[Column, ...] = (
    ("Fund Request ID", lambda row: row.source_transaction_id),
    ("Source Row Number", lambda row: row.source_row_number),
    ("Customer / User Name", lambda row: row.source_user),
    ("Amount", lambda row: row.source_amount),
    ("Reference Number", lambda row: row.source_reference),
    ("UTR", lambda row: row.source_utr),
    ("Transaction Date", lambda row: row.source_date),
    ("Source Bank", lambda row: row.source_bank),
    ("Payment Mode", lambda row: row.source_payment_mode),
    ("Source Status", lambda row: row.source_status),
    ("Bank Transaction Row Number", lambda row: row.bank_row_number),
    ("Bank Name", lambda row: row.bank_name),
    ("Bank Amount", lambda row: row.bank_amount),
    ("Bank Transaction Date", lambda row: row.bank_date),
    ("Bank Reference", lambda row: row.bank_reference),
    ("Bank UTR", lambda row: row.bank_utr),
    ("Bank Narration", lambda row: row.bank_narration),
    ("Bank Account Number", lambda row: row.bank_account_number),
    ("Match Status", lambda row: row.final_status),
    ("Reference Status", lambda row: row.reference_status),
    ("Amount Status", lambda row: row.amount_status),
    ("User Status", lambda row: row.user_status),
    ("Date Status", lambda row: row.date_status),
    ("Exception Reason", lambda row: row.exception_reason),
)


VA_TRANSACTION_COLUMNS: tuple[Column, ...] = (
    ("VA Transaction ID", lambda row: row.source_transaction_id),
    ("Source Row Number", lambda row: row.source_row_number),
    ("Virtual Account Number", lambda row: row.source_virtual_account_number),
    ("Customer / Account Holder", lambda row: row.source_user),
    ("Amount", lambda row: row.source_amount),
    ("UTR", lambda row: row.source_utr),
    ("Reference Number", lambda row: row.source_reference),
    ("Transaction Date", lambda row: row.source_date),
    ("Source Bank", lambda row: row.source_bank),
    ("Bank Transaction Row Number", lambda row: row.bank_row_number),
    ("Bank Name", lambda row: row.bank_name),
    ("Bank Amount", lambda row: row.bank_amount),
    ("Bank Transaction Date", lambda row: row.bank_date),
    ("Bank Reference", lambda row: row.bank_reference),
    ("Bank UTR", lambda row: row.bank_utr),
    ("Bank Narration", lambda row: row.bank_narration),
    ("Bank Account Number", lambda row: row.bank_account_number),
    ("Match Status", lambda row: row.final_status),
    ("Match Evidence", _match_evidence),
    ("Exception Reason", lambda row: row.exception_reason),
)


BANK_MATCHED_COLUMNS: tuple[Column, ...] = (
    ("Bank Transaction Row Number", lambda row: row.bank_row_number),
    ("Bank Name", lambda row: row.bank_name),
    ("Bank Amount", lambda row: row.bank_amount),
    ("Signed Amount", lambda row: row.bank_signed_amount),
    ("Transaction Date", lambda row: row.bank_date),
    ("Value Date", lambda row: row.bank_value_date),
    ("Bank Reference", lambda row: row.bank_reference),
    ("Bank UTR", lambda row: row.bank_utr),
    ("Bank Narration", lambda row: row.bank_narration),
    ("Debit / Credit", lambda row: row.bank_direction),
    ("Bank Account Number", lambda row: row.bank_account_number),
    ("Account Holder", lambda row: row.bank_account_holder),
    ("Source Filename", lambda row: row.bank_source_filename),
    ("Matched Source Type", lambda row: row.source_type),
    ("Matched Source Transaction ID", lambda row: row.source_transaction_id),
    ("Matched Source Reference", _source_reference),
    ("Matched Source Amount", lambda row: row.source_amount),
    ("Match Status", lambda row: row.final_status),
    ("Match Evidence", _match_evidence),
)


BANK_UNMATCHED_COLUMNS: tuple[Column, ...] = (
    ("Bank Transaction Row Number", lambda row: row.statement_row_number),
    ("Bank Name", lambda row: row.bank_name),
    ("Bank Amount", lambda row: row.amount),
    ("Signed Amount", lambda row: row.signed_amount),
    ("Transaction Date", lambda row: row.transaction_date),
    ("Value Date", lambda row: row.value_date),
    ("Bank Reference", lambda row: row.reference),
    ("Bank UTR", lambda row: row.utr),
    ("Bank Narration", lambda row: row.narration),
    ("Debit / Credit", lambda row: row.direction),
    ("Bank Account Number", lambda row: row.bank_account_number),
    ("Account Holder", lambda row: row.account_holder),
    ("Source Filename", lambda row: row.source_filename),
    # The unmatched bank table has no status column: every stored row is a
    # genuine NOT_FOUND result.
    ("Match Status", lambda row: "NOT_FOUND"),
    ("Exception Reason", lambda row: None),
)


def _format_value(value: Any) -> str:
    """Convert a persisted value into a safe CSV cell.

    Nulls become empty cells.  Dates use ISO 8601 and decimals are rendered
    from ``Decimal`` without any float conversion.  Untrusted text that starts
    with a spreadsheet formula character is prefixed with an apostrophe so
    Excel and Google Sheets treat it as text.
    """

    if value is None:
        return ""

    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"

    if isinstance(value, Decimal):
        return format(value, "f")

    if isinstance(value, float):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, int):
        return str(value)

    text = str(value)

    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text

    return text


def _stream_csv(
    bind: Bind,
    columns: tuple[Column, ...],
    fetch_rows: Callable[[Session], Iterator[Any]],
) -> Iterator[str]:
    """Yield a UTF-8 CSV document in bounded chunks.

    A dedicated session is opened on the existing engine so FastAPI dependency
    teardown cannot close the session while the response is still streaming.
    """

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")

    buffer.write(_BOM)
    writer.writerow([header for header, _ in columns])
    yield buffer.getvalue()

    buffer.seek(0)
    buffer.truncate(0)

    with Session(bind=bind) as session:
        for row in fetch_rows(session):
            writer.writerow(
                [_format_value(extract(row)) for _, extract in columns]
            )

            if buffer.tell() >= CSV_CHUNK_SIZE:
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)

    if buffer.tell():
        yield buffer.getvalue()


def build_export_filename(
    export_type: str,
    status: str,
    job_id: Any,
    *,
    now: datetime | None = None,
) -> str:
    """Return a filesystem-safe, unique download filename."""

    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"{export_type}_{status}_{job_id}_{timestamp}.csv"


def fund_request_csv(
    bind: Bind,
    job_id: Any,
    internal_status: str,
) -> Iterator[str]:
    """Stream the complete Fund Request export for one job and status."""

    return _stream_csv(
        bind,
        FUND_REQUEST_COLUMNS,
        lambda session: iter_fund_request_results(
            session,
            job_id,
            internal_status,
        ),
    )


def va_transaction_csv(
    bind: Bind,
    job_id: Any,
    internal_status: str,
) -> Iterator[str]:
    """Stream the complete VA Transaction export for one job and status."""

    return _stream_csv(
        bind,
        VA_TRANSACTION_COLUMNS,
        lambda session: iter_va_transaction_results(
            session,
            job_id,
            internal_status,
        ),
    )


def bank_matched_csv(
    bind: Bind,
    job_id: Any,
) -> Iterator[str]:
    """Stream the complete matched bank export for one job."""

    return _stream_csv(
        bind,
        BANK_MATCHED_COLUMNS,
        lambda session: iter_matched_bank_transactions(session, job_id),
    )


def bank_unmatched_csv(
    bind: Bind,
    job_id: Any,
) -> Iterator[str]:
    """Stream the complete unmatched bank export for one job."""

    return _stream_csv(
        bind,
        BANK_UNMATCHED_COLUMNS,
        lambda session: iter_unmatched_bank_transactions(session, job_id),
    )
