from __future__ import annotations

import csv
import io
import re
import uuid

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.db_models import (
    ReconciliationJob,
    ReconciliationResult,
    UnmatchedBankTransaction,
)
from app.main import app
from app.services.reconciliation_query_service import get_db_session


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kwargs):
    """Allow the PostgreSQL JSONB columns to be created in the test database."""

    return "JSON"


@compiles(UUID, "sqlite")
def _compile_uuid_for_sqlite(type_, compiler, **kwargs):
    """Keep UUID foreign keys text-affine in the test database."""

    return "CHAR(32)"


JOB_MAIN = uuid.UUID("11111111-1111-1111-1111-111111111111")
JOB_BULK = uuid.UUID("22222222-2222-2222-2222-222222222222")
JOB_EMPTY = uuid.UUID("33333333-3333-3333-3333-333333333333")
JOB_PROCESSING = uuid.UUID("44444444-4444-4444-4444-444444444444")

INJECTION_USER = '=HYPERLINK("http://example.invalid","click")'
INJECTION_HOLDER = "@SUM(A1:A2)"
INJECTION_UTR = "+1+1"
INJECTION_REFERENCE = "-2+3"

FR_HEADERS = [
    "Fund Request ID",
    "Source Row Number",
    "Customer / User Name",
    "Amount",
    "Reference Number",
    "UTR",
    "Transaction Date",
    "Source Bank",
    "Payment Mode",
    "Source Status",
    "Bank Transaction Row Number",
    "Bank Name",
    "Bank Amount",
    "Bank Transaction Date",
    "Bank Reference",
    "Bank UTR",
    "Bank Narration",
    "Bank Account Number",
    "Match Status",
    "Reference Status",
    "Amount Status",
    "User Status",
    "Date Status",
    "Exception Reason",
]

VA_HEADERS = [
    "VA Transaction ID",
    "Source Row Number",
    "Virtual Account Number",
    "Customer / Account Holder",
    "Amount",
    "UTR",
    "Reference Number",
    "Transaction Date",
    "Source Bank",
    "Bank Transaction Row Number",
    "Bank Name",
    "Bank Amount",
    "Bank Transaction Date",
    "Bank Reference",
    "Bank UTR",
    "Bank Narration",
    "Bank Account Number",
    "Match Status",
    "Match Evidence",
    "Exception Reason",
]

BANK_MATCHED_HEADERS = [
    "Bank Transaction Row Number",
    "Bank Name",
    "Bank Amount",
    "Signed Amount",
    "Transaction Date",
    "Value Date",
    "Bank Reference",
    "Bank UTR",
    "Bank Narration",
    "Debit / Credit",
    "Bank Account Number",
    "Account Holder",
    "Source Filename",
    "Matched Source Type",
    "Matched Source Transaction ID",
    "Matched Source Reference",
    "Matched Source Amount",
    "Match Status",
    "Match Evidence",
]

BANK_UNMATCHED_HEADERS = [
    "Bank Transaction Row Number",
    "Bank Name",
    "Bank Amount",
    "Signed Amount",
    "Transaction Date",
    "Value Date",
    "Bank Reference",
    "Bank UTR",
    "Bank Narration",
    "Debit / Credit",
    "Bank Account Number",
    "Account Holder",
    "Source Filename",
    "Match Status",
    "Exception Reason",
]

EXPORT_CASES = [
    ("fund-requests", "MATCHED", "fund_requests", FR_HEADERS),
    ("fund-requests", "NOT_FOUND", "fund_requests", FR_HEADERS),
    ("va-transactions", "MATCHED", "va_transactions", VA_HEADERS),
    ("va-transactions", "NOT_FOUND", "va_transactions", VA_HEADERS),
    ("bank-transactions", "MATCHED", "bank_transactions", BANK_MATCHED_HEADERS),
    (
        "bank-transactions",
        "NOT_FOUND",
        "bank_transactions",
        BANK_UNMATCHED_HEADERS,
    ),
]


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            ReconciliationJob.__table__,
            ReconciliationResult.__table__,
            UnmatchedBankTransaction.__table__,
        ],
    )

    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture()
def client(session_factory):
    def override_get_db_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_get_db_session

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def _add_job(session, job_id, status="COMPLETED"):
    session.add(
        ReconciliationJob(
            job_id=job_id,
            status=status,
            fund_request_filename="fund.csv",
            va_transaction_filename="va.csv",
            completed_at=(
                datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
                if status == "COMPLETED"
                else None
            ),
        )
    )
    session.flush()


def _add_result(
    session,
    job_id,
    *,
    source_type="fund_request",
    final_status,
    source_transaction_id,
    source_row_number=2,
    source_utr=None,
    source_reference=None,
    source_amount=Decimal("100.0000"),
    source_user="ACME PVT LTD",
    source_status="approved",
    source_virtual_account_number=None,
    reference_status="MATCH",
    amount_status="MATCH",
    user_status="MATCH",
    date_status="NOT_USED",
    exception_reason=None,
    bank_row_number=None,
    bank_name=None,
    bank_utr=None,
    bank_reference=None,
    bank_amount=None,
    bank_signed_amount=None,
    bank_user=None,
    bank_date=None,
    bank_value_date=None,
    bank_narration=None,
    bank_direction=None,
    bank_account_number=None,
    bank_account_holder=None,
    bank_format=None,
    bank_source_filename=None,
):
    session.add(
        ReconciliationResult(
            job_id=job_id,
            source_type=source_type,
            source_transaction_id=source_transaction_id,
            source_row_number=source_row_number,
            source_utr=source_utr,
            source_reference=source_reference,
            source_amount=source_amount,
            source_user=source_user,
            source_date=datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc),
            source_status=source_status,
            source_bank="AXIS",
            source_payment_mode="NEFT",
            source_virtual_account_number=source_virtual_account_number,
            source_data={"ID": source_transaction_id},
            reference_status=reference_status,
            amount_status=amount_status,
            user_status=user_status,
            date_status=date_status,
            final_status=final_status,
            exception_reason=exception_reason,
            bank_row_number=bank_row_number,
            bank_name=bank_name,
            bank_utr=bank_utr,
            bank_reference=bank_reference,
            bank_amount=bank_amount,
            bank_signed_amount=bank_signed_amount,
            bank_user=bank_user,
            bank_date=bank_date,
            bank_value_date=bank_value_date,
            bank_narration=bank_narration,
            bank_direction=bank_direction,
            bank_account_number=bank_account_number,
            bank_account_holder=bank_account_holder,
            bank_format=bank_format,
            bank_source_filename=bank_source_filename,
            bank_extracted_references=(
                [bank_utr] if bank_utr else None
            ),
            bank_data={"UTR": bank_utr} if bank_utr else None,
        )
    )


def _add_unmatched_bank(
    session,
    job_id,
    *,
    row_number,
    utr=None,
    reference=None,
    amount=Decimal("100.0000"),
    signed_amount=Decimal("100.0000"),
    direction="CREDIT",
    narration=None,
    debit=None,
    credit=Decimal("100.0000"),
    balance=None,
    source_filename="unmatched-bank.csv",
):
    session.add(
        UnmatchedBankTransaction(
            job_id=job_id,
            bank_name="AXIS",
            statement_row_number=row_number,
            transaction_date=datetime(
                2026, 11, 10, 11, 0, tzinfo=timezone.utc
            ),
            utr=utr,
            reference=reference,
            narration=narration,
            debit=debit,
            credit=credit,
            amount=amount,
            balance=balance,
            direction=direction,
            raw_data={"UTR": utr},
            bank_account_number="1234567890",
            account_holder="ACME PVT LTD",
            bank_format="AXIS",
            source_filename=source_filename,
            signed_amount=signed_amount,
            value_date=datetime(
                2026, 11, 11, 9, 0, tzinfo=timezone.utc
            ),
            extracted_references=[utr] if utr else [],
        )
    )


def _seed_main_job(session):
    _add_job(session, JOB_MAIN)

    _add_result(
        session,
        JOB_MAIN,
        final_status="MATCHED",
        source_transaction_id="FR-MATCHED-1",
        source_row_number=2,
        source_utr="UTRFR1",
        source_reference='REF,FR"1',
        source_amount=Decimal("1234.5678"),
        source_user=INJECTION_USER,
        exception_reason='Matched by amount, "party" name.\nSecond line.',
        bank_row_number=2,
        bank_name="AXIS",
        bank_utr="UTRFR1",
        bank_reference="BANKFR1",
        bank_amount=Decimal("1234.5678"),
        bank_signed_amount=Decimal("1234.5678"),
        bank_user="ACME PVT LTD",
        bank_date=datetime(2026, 11, 10, 10, 2, tzinfo=timezone.utc),
        bank_value_date=datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc),
        bank_narration='NEFT, "CR"\nACME PVT LTD',
        bank_direction="CREDIT",
        bank_account_number="1234567890",
        bank_account_holder=INJECTION_HOLDER,
        bank_format="AXIS",
        bank_source_filename="bank.csv",
    )

    _add_result(
        session,
        JOB_MAIN,
        final_status="NOT_FOUND",
        source_transaction_id="FR-NOT-FOUND-1",
        source_row_number=3,
        source_utr="UTRFR2",
        source_reference="REFFR2",
        source_amount=Decimal("50.1000"),
        source_user="Beta Ltd",
        user_status="NOT_FOUND",
        exception_reason="No available bank credit matched.",
    )

    _add_result(
        session,
        JOB_MAIN,
        source_type="va_transaction",
        final_status="MATCHED",
        source_transaction_id="VA-MATCHED-1",
        source_row_number=2,
        source_utr="UTRVA1",
        source_reference="REFVA1",
        source_amount=Decimal("200.0000"),
        source_virtual_account_number="VA001",
        source_status="success",
        exception_reason="Reference and amount matched.",
        bank_row_number=5,
        bank_name="AXIS",
        bank_utr="UTRVA1",
        bank_reference="BANKVA1",
        bank_amount=Decimal("200.0000"),
        bank_signed_amount=Decimal("200.0000"),
        bank_user="ACME PVT LTD",
        bank_date=datetime(2026, 11, 10, 10, 4, tzinfo=timezone.utc),
        bank_value_date=datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc),
        bank_narration="NEFT CR ACME PVT LTD",
        bank_direction="CREDIT",
        bank_account_number="1234567890",
        bank_account_holder="ACME PVT LTD",
        bank_format="AXIS",
        bank_source_filename="bank.csv",
    )

    _add_result(
        session,
        JOB_MAIN,
        source_type="va_transaction",
        final_status="NOT_FOUND",
        source_transaction_id="VA-NOT-FOUND-1",
        source_row_number=3,
        source_utr="UTRVA2",
        source_reference="REFVA2",
        source_amount=Decimal("250.0000"),
        source_virtual_account_number="VA002",
        source_status="success",
        reference_status="NOT_FOUND",
        amount_status="NOT_FOUND",
        user_status="NOT_USED",
        exception_reason="No bank transaction matched this VA transaction.",
    )

    # Non-confirmed statuses must never appear in matched or unmatched exports.
    _add_result(
        session,
        JOB_MAIN,
        source_type="va_transaction",
        final_status="AMBIGUOUS",
        source_transaction_id="VA-AMBIGUOUS-1",
        source_utr="UTRVA3",
        source_amount=Decimal("400.0000"),
        source_virtual_account_number="VA003",
        reference_status="MULTIPLE_SOURCE_CANDIDATES",
        bank_row_number=7,
        bank_name="AXIS",
        bank_utr="UTRVA3",
        bank_amount=Decimal("400.0000"),
        bank_direction="CREDIT",
        bank_source_filename="bank.csv",
    )
    _add_result(
        session,
        JOB_MAIN,
        source_type="va_transaction",
        final_status="REVIEW_REQUIRED",
        source_transaction_id="VA-REVIEW-1",
        source_utr="UTRVA4",
        source_amount=Decimal("500.0000"),
        source_virtual_account_number="VA004",
        bank_row_number=8,
        bank_name="AXIS",
        bank_utr="UTRVA4",
        bank_amount=Decimal("500.0000"),
        bank_direction="CREDIT",
        bank_source_filename="bank.csv",
    )

    # Duplicate persisted link for bank row 5 must be exported only once.
    _add_result(
        session,
        JOB_MAIN,
        source_type="va_transaction",
        final_status="MATCHED",
        source_transaction_id="VA-DUPLICATE-1",
        source_utr="UTRVA1",
        source_reference="REFVA1",
        source_amount=Decimal("200.0000"),
        source_virtual_account_number="VA001",
        reference_status="MATCH",
        exception_reason="Duplicate persisted link for the same bank row.",
        bank_row_number=5,
        bank_name="AXIS",
        bank_utr="UTRVA1",
        bank_reference="BANKVA1",
        bank_amount=Decimal("200.0000"),
        bank_signed_amount=Decimal("200.0000"),
        bank_direction="CREDIT",
        bank_source_filename="bank.csv",
    )

    _add_unmatched_bank(
        session,
        JOB_MAIN,
        row_number=10,
        utr=INJECTION_UTR,
        reference="REF-UNMATCHED-10",
        amount=Decimal("800.0000"),
        signed_amount=Decimal("-800.0000"),
        direction="DEBIT",
        narration='Salary, "special"\npayment',
        debit=Decimal("800.0000"),
        credit=None,
        balance=Decimal("1200.0000"),
    )
    _add_unmatched_bank(
        session,
        JOB_MAIN,
        row_number=11,
        utr="UTRUNMATCHED11",
        reference=INJECTION_REFERENCE,
        amount=Decimal("900.0000"),
        signed_amount=Decimal("900.0000"),
        direction="CREDIT",
        narration="Incoming transfer",
        credit=Decimal("900.0000"),
    )

    session.commit()


def _read_csv(response):
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\xef\xbb\xbf")
    text = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def _rows_as_dicts(rows):
    headers = rows[0]
    return [dict(zip(headers, row)) for row in rows[1:]]


@pytest.mark.parametrize("path,status,export_type,headers", EXPORT_CASES)
def test_all_export_endpoints_return_csv_downloads(
    client,
    session_factory,
    path,
    status,
    export_type,
    headers,
):
    with session_factory() as session:
        _seed_main_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_MAIN}/{path}/export",
        params={"status": status},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/csv; charset=utf-8"

    disposition = response.headers["content-disposition"]
    match = re.fullmatch(
        rf'attachment; filename="{export_type}_{status}_{JOB_MAIN}_'
        r"\d{8}T\d{6}Z\.csv" + '"',
        disposition,
    )
    assert match, disposition

    rows = _read_csv(response)

    assert rows[0] == headers
    assert len(rows) >= 2


def test_fund_request_matched_export_contains_expected_values(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_main_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_MAIN}/fund-requests/export",
        params={"status": "MATCHED"},
    )
    rows = _rows_as_dicts(_read_csv(response))

    assert [row["Fund Request ID"] for row in rows] == ["FR-MATCHED-1"]

    row = rows[0]

    assert row["Source Row Number"] == "2"
    assert row["Amount"] == "1234.5678"
    assert row["Reference Number"] == 'REF,FR"1'
    assert row["UTR"] == "UTRFR1"
    assert row["Transaction Date"] == "2026-11-10T10:00:00"
    assert row["Source Bank"] == "AXIS"
    assert row["Payment Mode"] == "NEFT"
    assert row["Source Status"] == "approved"
    assert row["Bank Transaction Row Number"] == "2"
    assert row["Bank Name"] == "AXIS"
    assert row["Bank Amount"] == "1234.5678"
    assert row["Bank Transaction Date"] == "2026-11-10T10:02:00"
    assert row["Bank Reference"] == "BANKFR1"
    assert row["Bank UTR"] == "UTRFR1"
    assert row["Bank Narration"] == 'NEFT, "CR"\nACME PVT LTD'
    assert row["Bank Account Number"] == "1234567890"
    assert row["Match Status"] == "MATCHED"
    assert row["Reference Status"] == "MATCH"
    assert row["Amount Status"] == "MATCH"
    assert row["User Status"] == "MATCH"
    assert row["Date Status"] == "NOT_USED"
    assert row["Exception Reason"] == (
        'Matched by amount, "party" name.\nSecond line.'
    )


def test_fund_request_unmatched_export_excludes_matched_and_empties_bank_cells(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_main_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_MAIN}/fund-requests/export",
        params={"status": "NOT_FOUND"},
    )
    rows = _rows_as_dicts(_read_csv(response))

    assert [row["Fund Request ID"] for row in rows] == ["FR-NOT-FOUND-1"]

    row = rows[0]

    assert row["Amount"] == "50.1000"
    assert row["Reference Number"] == "REFFR2"
    assert row["Match Status"] == "NOT_FOUND"
    assert row["Exception Reason"] == "No available bank credit matched."

    for column in (
        "Bank Transaction Row Number",
        "Bank Name",
        "Bank Amount",
        "Bank Transaction Date",
        "Bank Reference",
        "Bank UTR",
        "Bank Narration",
        "Bank Account Number",
    ):
        assert row[column] == ""


def test_va_matched_export_contains_match_evidence(client, session_factory):
    with session_factory() as session:
        _seed_main_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_MAIN}/va-transactions/export",
        params={"status": "MATCHED"},
    )
    rows = _rows_as_dicts(_read_csv(response))

    by_id = {row["VA Transaction ID"]: row for row in rows}

    assert set(by_id) == {"VA-MATCHED-1", "VA-DUPLICATE-1"}

    row = by_id["VA-MATCHED-1"]

    assert row["Virtual Account Number"] == "VA001"
    assert row["Amount"] == "200.0000"
    assert row["Bank Transaction Row Number"] == "5"
    assert row["Bank Amount"] == "200.0000"
    assert row["Match Status"] == "MATCHED"
    assert row["Match Evidence"] == (
        "Reference=MATCH; Amount=MATCH; User=MATCH; Date=NOT_USED"
    )
    assert row["Exception Reason"] == "Reference and amount matched."


def test_va_unmatched_export_excludes_matched_and_ambiguous(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_main_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_MAIN}/va-transactions/export",
        params={"status": "NOT_FOUND"},
    )
    rows = _rows_as_dicts(_read_csv(response))

    assert [row["VA Transaction ID"] for row in rows] == ["VA-NOT-FOUND-1"]

    row = rows[0]

    assert row["Amount"] == "250.0000"
    assert row["Bank Transaction Row Number"] == ""
    assert row["Bank Name"] == ""
    assert row["Match Status"] == "NOT_FOUND"


def test_bank_matched_export_includes_fund_request_and_va_matches(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_main_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_MAIN}/bank-transactions/export",
        params={"status": "MATCHED"},
    )
    rows = _rows_as_dicts(_read_csv(response))

    by_row = {row["Bank Transaction Row Number"]: row for row in rows}

    assert set(by_row) == {"2", "5"}
    assert by_row["2"]["Matched Source Type"] == "fund_request"
    assert by_row["2"]["Matched Source Transaction ID"] == "FR-MATCHED-1"
    assert by_row["2"]["Matched Source Reference"] == "UTRFR1"
    assert by_row["2"]["Matched Source Amount"] == "1234.5678"
    assert by_row["5"]["Matched Source Type"] == "va_transaction"
    assert by_row["5"]["Matched Source Transaction ID"] == "VA-MATCHED-1"

    identities = [
        (row["Source Filename"], row["Bank Transaction Row Number"])
        for row in rows
    ]
    assert len(identities) == len(set(identities))
    assert "VA-DUPLICATE-1" not in {
        row["Matched Source Transaction ID"] for row in rows
    }

    assert by_row["2"]["Signed Amount"] == "1234.5678"
    assert by_row["2"]["Value Date"] == "2026-11-11T09:00:00"
    assert by_row["2"]["Debit / Credit"] == "CREDIT"
    assert by_row["2"]["Match Status"] == "MATCHED"
    assert by_row["2"]["Match Evidence"].startswith("Reference=")


def test_bank_unmatched_export_contains_only_genuine_not_found_rows(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_main_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_MAIN}/bank-transactions/export",
        params={"status": "NOT_FOUND"},
    )
    rows = _rows_as_dicts(_read_csv(response))

    by_row = {row["Bank Transaction Row Number"]: row for row in rows}

    assert set(by_row) == {"10", "11"}
    assert by_row["10"]["Bank Amount"] == "800.0000"
    assert by_row["10"]["Signed Amount"] == "-800.0000"
    assert by_row["10"]["Debit / Credit"] == "DEBIT"
    assert by_row["10"]["Match Status"] == "NOT_FOUND"
    assert by_row["10"]["Exception Reason"] == ""
    assert by_row["11"]["Bank UTR"] == "UTRUNMATCHED11"

    assert not {"2", "5", "7", "8"} & set(by_row)


def test_exports_are_not_limited_to_one_hundred_rows(
    client,
    session_factory,
):
    with session_factory() as session:
        _add_job(session, JOB_BULK)

        for index in range(1, 151):
            _add_result(
                session,
                JOB_BULK,
                final_status="MATCHED",
                source_transaction_id=f"FR-{index:03d}",
                source_row_number=index + 1,
                source_utr=f"UTRFR{index:03d}",
                source_amount=Decimal("100.0000"),
                bank_row_number=index,
                bank_name="AXIS",
                bank_utr=f"UTRFR{index:03d}",
                bank_amount=Decimal("100.0000"),
                bank_signed_amount=Decimal("100.0000"),
                bank_direction="CREDIT",
                bank_source_filename="bank.csv",
            )
            _add_result(
                session,
                JOB_BULK,
                source_type="va_transaction",
                final_status="MATCHED",
                source_transaction_id=f"VA-{index:03d}",
                source_row_number=index + 1,
                source_utr=f"UTRVA{index:03d}",
                source_amount=Decimal("200.0000"),
                source_virtual_account_number=f"VA{index:03d}",
                bank_row_number=1000 + index,
                bank_name="AXIS",
                bank_utr=f"UTRVA{index:03d}",
                bank_amount=Decimal("200.0000"),
                bank_signed_amount=Decimal("200.0000"),
                bank_direction="CREDIT",
                bank_source_filename="bank.csv",
            )
            _add_unmatched_bank(
                session,
                JOB_BULK,
                row_number=3000 + index,
                utr=f"UTRUNMATCHED{index:03d}",
            )

        session.commit()

    for path, status, expected in (
        ("fund-requests", "MATCHED", 150),
        ("va-transactions", "MATCHED", 150),
        ("bank-transactions", "MATCHED", 300),
        ("bank-transactions", "NOT_FOUND", 150),
    ):
        response = client.get(
            f"/api/reconciliation/{JOB_BULK}/{path}/export",
            params={"status": status},
        )
        rows = _read_csv(response)

        assert len(rows) - 1 == expected, (path, status, len(rows) - 1)


def test_empty_exports_contain_headers_and_no_data_rows(
    client,
    session_factory,
):
    with session_factory() as session:
        _add_job(session, JOB_EMPTY)
        session.commit()

    for path, status, _export_type, headers in EXPORT_CASES:
        response = client.get(
            f"/api/reconciliation/{JOB_EMPTY}/{path}/export",
            params={"status": status},
        )
        rows = _read_csv(response)

        assert rows == [headers]


def test_formula_injection_is_neutralized_without_corrupting_amounts(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_main_job(session)

    fund_rows = _rows_as_dicts(
        _read_csv(
            client.get(
                f"/api/reconciliation/{JOB_MAIN}/fund-requests/export",
                params={"status": "MATCHED"},
            )
        )
    )
    bank_rows = _rows_as_dicts(
        _read_csv(
            client.get(
                f"/api/reconciliation/{JOB_MAIN}/bank-transactions/export",
                params={"status": "MATCHED"},
            )
        )
    )
    unmatched_rows = _rows_as_dicts(
        _read_csv(
            client.get(
                f"/api/reconciliation/{JOB_MAIN}/bank-transactions/export",
                params={"status": "NOT_FOUND"},
            )
        )
    )

    assert fund_rows[0]["Customer / User Name"] == "'" + INJECTION_USER
    assert bank_rows[0]["Account Holder"] == "'" + INJECTION_HOLDER
    assert unmatched_rows[0]["Bank UTR"] == "'" + INJECTION_UTR
    assert unmatched_rows[1]["Bank Reference"] == "'" + INJECTION_REFERENCE

    # Amounts remain exact numeric text, including a legitimate minus sign.
    assert fund_rows[0]["Amount"] == "1234.5678"
    assert unmatched_rows[0]["Signed Amount"] == "-800.0000"

    for row in (fund_rows[0], bank_rows[0], unmatched_rows[0]):
        for column, value in row.items():
            if column in {
                "Amount",
                "Bank Amount",
                "Signed Amount",
                "Matched Source Amount",
            }:
                assert not value.startswith("'")


def test_export_rejects_invalid_and_incomplete_jobs(client, session_factory):
    with session_factory() as session:
        _add_job(session, JOB_PROCESSING, status="PROCESSING")
        session.commit()

    for path, _status, _export_type, _headers in EXPORT_CASES:
        invalid = client.get(
            f"/api/reconciliation/not-a-uuid/{path}/export",
            params={"status": "MATCHED"},
        )
        assert invalid.status_code == 422

        unknown = client.get(
            f"/api/reconciliation/{uuid.uuid4()}/{path}/export",
            params={"status": "MATCHED"},
        )
        assert unknown.status_code == 404

        processing = client.get(
            f"/api/reconciliation/{JOB_PROCESSING}/{path}/export",
            params={"status": "MATCHED"},
        )
        assert processing.status_code == 409


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"status": "matched"},
        {"status": "AUTO_MATCHED"},
        {"status": "UNKNOWN"},
        {"status": ""},
    ],
)
def test_export_rejects_invalid_status(client, params):
    response = client.get(
        f"/api/reconciliation/{JOB_MAIN}/fund-requests/export",
        params=params,
    )

    assert response.status_code == 422


def test_retrieval_apis_remain_unchanged(client, session_factory):
    with session_factory() as session:
        _seed_main_job(session)

    fund_matched = client.get(
        f"/api/reconciliation/{JOB_MAIN}/fund-requests",
        params={"status": "MATCHED"},
    )
    fund_unmatched = client.get(
        f"/api/reconciliation/{JOB_MAIN}/fund-requests",
        params={"status": "NOT_FOUND"},
    )
    va_matched = client.get(
        f"/api/reconciliation/{JOB_MAIN}/va-transactions",
        params={"status": "MATCHED"},
    )
    va_unmatched = client.get(
        f"/api/reconciliation/{JOB_MAIN}/va-transactions",
        params={"status": "NOT_FOUND"},
    )
    bank_matched = client.get(
        f"/api/reconciliation/{JOB_MAIN}/bank-transactions",
        params={"status": "MATCHED"},
    )
    bank_unmatched = client.get(
        f"/api/reconciliation/{JOB_MAIN}/bank-transactions",
        params={"status": "NOT_FOUND"},
    )

    responses = [
        fund_matched,
        fund_unmatched,
        va_matched,
        va_unmatched,
        bank_matched,
        bank_unmatched,
    ]

    for response in responses:
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["job_id"] == str(JOB_MAIN)
        assert payload["page"] == 1
        assert payload["limit"] == 100

    assert fund_matched.json()["total"] == 1
    assert fund_unmatched.json()["total"] == 1
    assert va_matched.json()["total"] == 2
    assert va_unmatched.json()["total"] == 1
    assert bank_matched.json()["total"] == 2
    assert bank_unmatched.json()["total"] == 2


def test_export_does_not_modify_persisted_results(client, session_factory):
    with session_factory() as session:
        _seed_main_job(session)

    with session_factory() as session:
        before = session.query(ReconciliationResult).count()
        unmatched_before = session.query(UnmatchedBankTransaction).count()

    client.get(
        f"/api/reconciliation/{JOB_MAIN}/fund-requests/export",
        params={"status": "MATCHED"},
    )
    client.get(
        f"/api/reconciliation/{JOB_MAIN}/bank-transactions/export",
        params={"status": "NOT_FOUND"},
    )

    with session_factory() as session:
        assert session.query(ReconciliationResult).count() == before
        assert (
            session.query(UnmatchedBankTransaction).count()
            == unmatched_before
        )
