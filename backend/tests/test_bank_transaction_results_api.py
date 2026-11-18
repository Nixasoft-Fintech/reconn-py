from __future__ import annotations

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


JOB_BANK = uuid.UUID("11111111-1111-1111-1111-111111111111")
JOB_PAGINATION = uuid.UUID("22222222-2222-2222-2222-222222222222")
JOB_EMPTY = uuid.UUID("33333333-3333-3333-3333-333333333333")
JOB_PROCESSING = uuid.UUID("44444444-4444-4444-4444-444444444444")


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
    source_amount=Decimal("100.00"),
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
    bank_extracted_references=None,
    bank_data=None,
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
            source_data={
                "ID": source_transaction_id,
                "UTR": source_utr,
                "Amount": str(source_amount),
            },
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
            bank_extracted_references=bank_extracted_references,
            bank_data=bank_data,
            reference_status=reference_status,
            amount_status=amount_status,
            user_status=user_status,
            date_status=date_status,
            final_status=final_status,
            exception_reason=exception_reason,
        )
    )


def _add_unmatched_bank(
    session,
    job_id,
    *,
    row_number,
    utr=None,
    reference=None,
    amount=Decimal("100.00"),
    signed_amount=Decimal("100.00"),
    direction="CREDIT",
    narration=None,
    debit=None,
    credit=Decimal("100.00"),
    balance=None,
    raw_data=None,
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
            raw_data=raw_data or {"UTR": utr, "Amount": str(amount)},
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


def _seed_bank_job(session):
    _add_job(session, JOB_BANK)

    # Bank row 2 matched to a Fund Request.
    _add_result(
        session,
        JOB_BANK,
        source_type="fund_request",
        final_status="MATCHED",
        source_transaction_id="FR-BANK-1",
        source_utr="UTRFRBANK0001",
        source_amount=Decimal("100.00"),
        source_reference="REF-FR-1",
        reference_status="NOT_USED",
        amount_status="MATCH",
        user_status="MATCH",
        exception_reason="Matched by exact amount and party name.",
        bank_row_number=2,
        bank_name="AXIS",
        bank_utr="UTRFRBANK0001",
        bank_reference="REF-FR-1",
        bank_amount=Decimal("100.00"),
        bank_signed_amount=Decimal("100.00"),
        bank_user="ACME PVT LTD",
        bank_date=datetime(2026, 11, 10, 10, 2, tzinfo=timezone.utc),
        bank_value_date=datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc),
        bank_narration="NEFT CR ACME PVT LTD",
        bank_direction="CREDIT",
        bank_account_number="1234567890",
        bank_account_holder="ACME PVT LTD",
        bank_format="AXIS",
        bank_source_filename="bank.csv",
        bank_extracted_references=["UTRFRBANK0001"],
        bank_data={"UTR": "UTRFRBANK0001", "Customer": "ACME PVT LTD"},
    )

    # Bank row 5 matched to a VA transaction.
    _add_result(
        session,
        JOB_BANK,
        source_type="va_transaction",
        final_status="MATCHED",
        source_transaction_id="VA-BANK-1",
        source_utr="UTRVA00000001",
        source_reference="REFVA00000001",
        source_amount=Decimal("200.00"),
        source_virtual_account_number="VA001",
        source_status="success",
        exception_reason="Reference and exact amount matched.",
        bank_row_number=5,
        bank_name="AXIS",
        bank_utr="UTRVA00000001",
        bank_reference="BANKREFVA1",
        bank_amount=Decimal("200.00"),
        bank_signed_amount=Decimal("200.00"),
        bank_user="ACME PVT LTD",
        bank_date=datetime(2026, 11, 10, 10, 4, tzinfo=timezone.utc),
        bank_value_date=datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc),
        bank_narration="NEFT CR ACME PVT LTD",
        bank_direction="CREDIT",
        bank_account_number="1234567890",
        bank_account_holder="ACME PVT LTD",
        bank_format="AXIS",
        bank_source_filename="bank.csv",
        bank_extracted_references=["UTRVA00000001"],
        bank_data={"UTR": "UTRVA00000001", "Customer": "ACME PVT LTD"},
    )

    # A duplicate persisted link for bank row 5 must not duplicate the row.
    _add_result(
        session,
        JOB_BANK,
        source_type="va_transaction",
        final_status="MATCHED",
        source_transaction_id="VA-BANK-DUPLICATE",
        source_utr="UTRVA00000001",
        source_reference="REFVA00000001",
        source_amount=Decimal("200.00"),
        source_virtual_account_number="VA001",
        source_status="success",
        exception_reason="Duplicate persisted link for the same bank row.",
        bank_row_number=5,
        bank_name="AXIS",
        bank_utr="UTRVA00000001",
        bank_reference="BANKREFVA1",
        bank_amount=Decimal("200.00"),
        bank_signed_amount=Decimal("200.00"),
        bank_user="ACME PVT LTD",
        bank_date=datetime(2026, 11, 10, 10, 4, tzinfo=timezone.utc),
        bank_value_date=datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc),
        bank_narration="NEFT CR ACME PVT LTD",
        bank_direction="CREDIT",
        bank_account_number="1234567890",
        bank_account_holder="ACME PVT LTD",
        bank_format="AXIS",
        bank_source_filename="bank.csv",
        bank_extracted_references=["UTRVA00000001"],
        bank_data={"UTR": "UTRVA00000001", "Customer": "ACME PVT LTD"},
    )

    # Non-confirmed statuses must not appear in either bank API.
    _add_result(
        session,
        JOB_BANK,
        source_type="va_transaction",
        final_status="AMOUNT_MISMATCH",
        source_transaction_id="VA-AMOUNT-MISMATCH",
        source_utr="UTRVA00000002",
        source_amount=Decimal("300.00"),
        amount_status="MISMATCH",
        exception_reason="Reference matched, but amount is not exactly equal.",
        bank_row_number=7,
        bank_name="AXIS",
        bank_utr="UTRVA00000002",
        bank_amount=Decimal("250.00"),
        bank_direction="CREDIT",
        bank_source_filename="bank.csv",
    )
    _add_result(
        session,
        JOB_BANK,
        source_type="va_transaction",
        final_status="AMBIGUOUS",
        source_transaction_id="VA-AMBIGUOUS",
        source_utr="UTRVA00000003",
        reference_status="MULTIPLE_SOURCE_CANDIDATES",
        exception_reason="This bank row matches multiple source records.",
        bank_row_number=8,
        bank_name="AXIS",
        bank_utr="UTRVA00000003",
        bank_amount=Decimal("400.00"),
        bank_direction="CREDIT",
        bank_source_filename="bank.csv",
    )
    _add_result(
        session,
        JOB_BANK,
        source_type="va_transaction",
        final_status="REVIEW_REQUIRED",
        source_transaction_id="VA-REVIEW",
        source_utr="UTRVA00000004",
        exception_reason="Manual review is required.",
        bank_row_number=9,
        bank_name="AXIS",
        bank_utr="UTRVA00000004",
        bank_amount=Decimal("500.00"),
        bank_direction="CREDIT",
        bank_source_filename="bank.csv",
    )

    # A source match without a persisted bank row is not a bank transaction.
    _add_result(
        session,
        JOB_BANK,
        source_type="fund_request",
        final_status="MATCHED",
        source_transaction_id="FR-MATCHED-NO-BANK",
        source_utr="UTRFRNONBANK",
        source_amount=Decimal("600.00"),
    )

    # Fund Request NOT_FOUND is a source result, not an unmatched bank row.
    _add_result(
        session,
        JOB_BANK,
        source_type="fund_request",
        final_status="NOT_FOUND",
        source_transaction_id="FR-NOT-FOUND",
        source_utr="UTRFRNOTFOUND",
        source_amount=Decimal("700.00"),
        amount_status="NOT_FOUND",
        user_status="NOT_FOUND",
        exception_reason="No available bank credit matched.",
    )

    _add_unmatched_bank(
        session,
        JOB_BANK,
        row_number=10,
        utr="UTRUNMATCHED10",
        reference="REF-UNMATCHED-10",
        amount=Decimal("800.00"),
        narration="Salary payment",
        direction="DEBIT",
        debit=Decimal("800.00"),
        credit=None,
        signed_amount=Decimal("-800.00"),
        balance=Decimal("1200.00"),
        raw_data={"UTR": "UTRUNMATCHED10", "Narration": "Salary payment"},
    )
    _add_unmatched_bank(
        session,
        JOB_BANK,
        row_number=11,
        utr="UTRUNMATCHED11",
        reference="REF-UNMATCHED-11",
        amount=Decimal("900.00"),
        signed_amount=Decimal("900.00"),
        narration="Incoming transfer",
        direction="CREDIT",
        credit=Decimal("900.00"),
        raw_data={"UTR": "UTRUNMATCHED11", "Narration": "Incoming transfer"},
    )

    session.commit()


def _seed_pagination_job(session):
    _add_job(session, JOB_PAGINATION)

    for index in range(1, 251):
        _add_result(
            session,
            JOB_PAGINATION,
            source_type=(
                "fund_request" if index % 2 else "va_transaction"
            ),
            final_status="MATCHED",
            source_transaction_id=f"BANK-MATCH-{index:03d}",
            source_utr=f"UTRBANK{index:08d}",
            bank_row_number=index,
            bank_name="AXIS",
            bank_utr=f"UTRBANK{index:08d}",
            bank_amount=Decimal("100.00"),
            bank_signed_amount=Decimal("100.00"),
            bank_user="ACME PVT LTD",
            bank_direction="CREDIT",
            bank_source_filename="bank.csv",
            bank_data={"UTR": f"UTRBANK{index:08d}"},
        )

    for index in range(1, 6):
        _add_unmatched_bank(
            session,
            JOB_PAGINATION,
            row_number=300 + index,
            utr=f"UTRUNMATCHED{index:03d}",
            amount=Decimal("50.00"),
        )

    session.commit()


def test_bank_matched_returns_fund_request_and_va_matches(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_bank_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_BANK}/bank-transactions",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 200, response.text

    payload = response.json()

    assert payload["job_id"] == str(JOB_BANK)
    assert payload["status"] == "MATCHED"
    assert payload["total"] == 2
    assert payload["page"] == 1
    assert payload["limit"] == 100
    assert payload["total_pages"] == 1
    assert payload["has_next"] is False
    assert payload["has_previous"] is False
    assert len(payload["items"]) == 2

    by_row = {item["bank_row_number"]: item for item in payload["items"]}

    assert set(by_row) == {2, 5}
    assert by_row[2]["source_type"] == "fund_request"
    assert by_row[2]["source_transaction_id"] == "FR-BANK-1"
    assert by_row[5]["source_type"] == "va_transaction"
    assert by_row[5]["source_transaction_id"] == "VA-BANK-1"

    for item in payload["items"]:
        assert item["final_status"] == "MATCHED"
        assert item["bank_source_filename"] == "bank.csv"
        assert item["bank_name"] == "AXIS"
        assert item["bank_amount"] is not None
        assert item["bank_signed_amount"] is not None
        assert item["bank_date"] is not None
        assert item["bank_value_date"] is not None
        assert item["bank_narration"] == "NEFT CR ACME PVT LTD"
        assert item["bank_direction"] == "CREDIT"
        assert item["bank_account_number"] == "1234567890"
        assert item["bank_account_holder"] == "ACME PVT LTD"
        assert item["bank_data"] is not None
        assert item["reference_status"] is not None
        assert item["amount_status"] is not None
        assert item["user_status"] is not None
        assert item["date_status"] is not None


def test_bank_matched_preserves_matched_bank_details(client, session_factory):
    with session_factory() as session:
        _seed_bank_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_BANK}/bank-transactions",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 200, response.text

    item = next(
        item
        for item in response.json()["items"]
        if item["bank_row_number"] == 2
    )

    assert item["bank_utr"] == "UTRFRBANK0001"
    assert item["bank_reference"] == "REF-FR-1"
    assert Decimal(item["bank_amount"]) == Decimal("100.00")
    assert Decimal(item["bank_signed_amount"]) == Decimal("100.00")
    assert item["bank_user"] == "ACME PVT LTD"
    assert item["bank_extracted_references"] == ["UTRFRBANK0001"]
    assert item["source_utr"] == "UTRFRBANK0001"
    assert item["source_reference_number"] == "REF-FR-1"
    assert item["bank_data"]["Customer"] == "ACME PVT LTD"
    assert item["exception_reason"] == (
        "Matched by exact amount and party name."
    )


def test_bank_matched_does_not_return_duplicate_bank_rows(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_bank_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_BANK}/bank-transactions",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 200, response.text

    items = response.json()["items"]
    identities = [
        (item["bank_source_filename"], item["bank_row_number"])
        for item in items
    ]

    assert len(identities) == len(set(identities))
    assert "VA-BANK-DUPLICATE" not in {
        item["source_transaction_id"] for item in items
    }


def test_bank_matched_excludes_non_confirmed_statuses_and_source_rows(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_bank_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_BANK}/bank-transactions",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 200, response.text

    returned_ids = {
        item["source_transaction_id"] for item in response.json()["items"]
    }

    assert "VA-AMOUNT-MISMATCH" not in returned_ids
    assert "VA-AMBIGUOUS" not in returned_ids
    assert "VA-REVIEW" not in returned_ids
    assert "FR-MATCHED-NO-BANK" not in returned_ids
    assert "FR-NOT-FOUND" not in returned_ids


def test_bank_unmatched_returns_only_persisted_unmatched_rows(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_bank_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_BANK}/bank-transactions",
        params={"status": "NOT_FOUND"},
    )

    assert response.status_code == 200, response.text

    payload = response.json()

    assert payload["job_id"] == str(JOB_BANK)
    assert payload["status"] == "NOT_FOUND"
    assert payload["total"] == 2
    assert payload["page"] == 1
    assert payload["limit"] == 100
    assert payload["total_pages"] == 1
    assert payload["has_next"] is False
    assert payload["has_previous"] is False

    by_row = {item["bank_row_number"]: item for item in payload["items"]}

    assert set(by_row) == {10, 11}
    assert all(
        isinstance(item["bank_transaction_id"], int)
        for item in payload["items"]
    )
    assert (
        by_row[10]["bank_transaction_id"]
        != by_row[11]["bank_transaction_id"]
    )

    for item in payload["items"]:
        assert item["final_status"] == "NOT_FOUND"
        assert item["source_type"] is None
        assert item["source_transaction_id"] is None
        assert item["bank_name"] == "AXIS"
        assert item["bank_account_number"] == "1234567890"
        assert item["bank_account_holder"] == "ACME PVT LTD"
        assert item["bank_source_filename"] == "unmatched-bank.csv"
        assert item["bank_data"] is not None
        assert item["exception_reason"] is None

    assert by_row[10]["bank_utr"] == "UTRUNMATCHED10"
    assert by_row[10]["bank_reference"] == "REF-UNMATCHED-10"
    assert Decimal(by_row[10]["bank_amount"]) == Decimal("800.00")
    assert Decimal(by_row[10]["bank_signed_amount"]) == Decimal("-800.00")
    assert by_row[10]["bank_direction"] == "DEBIT"
    assert Decimal(by_row[10]["bank_debit"]) == Decimal("800.00")
    assert by_row[10]["bank_credit"] is None
    assert Decimal(by_row[10]["bank_balance"]) == Decimal("1200.00")
    assert by_row[10]["bank_narration"] == "Salary payment"
    assert by_row[10]["bank_date"] is not None
    assert by_row[10]["bank_value_date"] is not None

    assert by_row[11]["bank_utr"] == "UTRUNMATCHED11"
    assert by_row[11]["bank_direction"] == "CREDIT"
    assert Decimal(by_row[11]["bank_credit"]) == Decimal("900.00")


def test_bank_unmatched_excludes_matched_and_non_not_found_statuses(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_bank_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_BANK}/bank-transactions",
        params={"status": "NOT_FOUND"},
    )

    assert response.status_code == 200, response.text

    returned_rows = {
        item["bank_row_number"] for item in response.json()["items"]
    }

    assert 2 not in returned_rows
    assert 5 not in returned_rows
    assert 7 not in returned_rows
    assert 8 not in returned_rows
    assert 9 not in returned_rows


def test_bank_pagination_first_page_uses_defaults(client, session_factory):
    with session_factory() as session:
        _seed_pagination_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/bank-transactions",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 200, response.text

    payload = response.json()

    assert payload["total"] == 250
    assert payload["page"] == 1
    assert payload["limit"] == 100
    assert payload["total_pages"] == 3
    assert payload["has_next"] is True
    assert payload["has_previous"] is False
    assert [
        item["source_transaction_id"] for item in payload["items"]
    ] == [f"BANK-MATCH-{index:03d}" for index in range(1, 101)]


def test_bank_pagination_second_page(client, session_factory):
    with session_factory() as session:
        _seed_pagination_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/bank-transactions",
        params={"status": "MATCHED", "page": 2, "limit": 100},
    )

    assert response.status_code == 200, response.text

    payload = response.json()

    assert payload["total"] == 250
    assert payload["page"] == 2
    assert payload["limit"] == 100
    assert payload["total_pages"] == 3
    assert payload["has_next"] is True
    assert payload["has_previous"] is True
    assert [
        item["source_transaction_id"] for item in payload["items"]
    ] == [f"BANK-MATCH-{index:03d}" for index in range(101, 201)]


def test_bank_pagination_final_page(client, session_factory):
    with session_factory() as session:
        _seed_pagination_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/bank-transactions",
        params={"status": "MATCHED", "page": 3, "limit": 100},
    )

    assert response.status_code == 200, response.text

    payload = response.json()

    assert payload["total"] == 250
    assert payload["page"] == 3
    assert payload["limit"] == 100
    assert payload["total_pages"] == 3
    assert payload["has_next"] is False
    assert payload["has_previous"] is True
    assert [
        item["source_transaction_id"] for item in payload["items"]
    ] == [f"BANK-MATCH-{index:03d}" for index in range(201, 251)]


def test_bank_unmatched_pagination(client, session_factory):
    with session_factory() as session:
        _seed_pagination_job(session)

    first = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/bank-transactions",
        params={"status": "NOT_FOUND", "page": 1, "limit": 2},
    )
    second = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/bank-transactions",
        params={"status": "NOT_FOUND", "page": 2, "limit": 2},
    )
    final = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/bank-transactions",
        params={"status": "NOT_FOUND", "page": 3, "limit": 2},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert final.status_code == 200, final.text

    assert first.json()["total"] == 5
    assert first.json()["total_pages"] == 3
    assert first.json()["has_next"] is True
    assert first.json()["has_previous"] is False
    assert second.json()["has_next"] is True
    assert second.json()["has_previous"] is True
    assert final.json()["has_next"] is False
    assert final.json()["has_previous"] is True

    assert [
        item["bank_row_number"] for item in first.json()["items"]
    ] == [301, 302]
    assert [
        item["bank_row_number"] for item in second.json()["items"]
    ] == [303, 304]
    assert [
        item["bank_row_number"] for item in final.json()["items"]
    ] == [305]


@pytest.mark.parametrize("status", ["MATCHED", "NOT_FOUND"])
def test_bank_blank_pagination_parameters_use_defaults(
    client,
    session_factory,
    status,
):
    with session_factory() as session:
        _seed_pagination_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/bank-transactions",
        params={"status": status, "page": "", "limit": ""},
    )

    assert response.status_code == 200, response.text

    payload = response.json()

    assert payload["page"] == 1
    assert payload["limit"] == 100


def test_bank_completed_job_with_no_results_returns_empty_success(
    client,
    session_factory,
):
    with session_factory() as session:
        _add_job(session, JOB_EMPTY)
        _add_result(
            session,
            JOB_EMPTY,
            final_status="MATCHED",
            source_transaction_id="FR-NO-BANK",
            source_utr="UTRFRNOBANK",
        )
        _add_result(
            session,
            JOB_EMPTY,
            final_status="NOT_FOUND",
            source_transaction_id="FR-NOT-FOUND",
            source_utr="UTRFRNOTFOUND",
        )
        session.commit()

    for status in ("MATCHED", "NOT_FOUND"):
        response = client.get(
            f"/api/reconciliation/{JOB_EMPTY}/bank-transactions",
            params={"status": status},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "job_id": str(JOB_EMPTY),
            "status": status,
            "total": 0,
            "page": 1,
            "limit": 100,
            "total_pages": 0,
            "has_next": False,
            "has_previous": False,
            "items": [],
        }


def test_bank_invalid_job_id_is_rejected(client):
    response = client.get(
        "/api/reconciliation/not-a-uuid/bank-transactions",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 422


def test_bank_unknown_job_id_returns_404(client):
    unknown_job = uuid.uuid4()

    response = client.get(
        f"/api/reconciliation/{unknown_job}/bank-transactions",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 404
    assert str(unknown_job) in response.json()["detail"]


def test_bank_processing_job_returns_409(client, session_factory):
    with session_factory() as session:
        _add_job(session, JOB_PROCESSING, status="PROCESSING")
        session.commit()

    response = client.get(
        f"/api/reconciliation/{JOB_PROCESSING}/bank-transactions",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 409
    assert "not completed" in response.json()["detail"]


@pytest.mark.parametrize(
    "status",
    ["AUTO_MATCHED", "MATCHED_AFTER_REVIEW", "matched", "UNKNOWN", ""],
)
def test_bank_invalid_status_is_rejected(client, status):
    response = client.get(
        f"/api/reconciliation/{JOB_BANK}/bank-transactions",
        params={"status": status},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "params",
    [
        {"status": "MATCHED", "page": 0},
        {"status": "MATCHED", "page": -1},
        {"status": "MATCHED", "limit": 0},
        {"status": "MATCHED", "limit": -1},
        {"status": "MATCHED", "limit": 101},
        {"status": "MATCHED", "page": "abc"},
        {"status": "MATCHED", "limit": "abc"},
        {"status": "MATCHED", "page": 1.5},
        {"status": "MATCHED", "limit": 1.5},
        {"status": "MATCHED", "page": " "},
        {"status": "MATCHED", "limit": " "},
        {"status": "NOT_FOUND", "page": 0},
        {"status": "NOT_FOUND", "limit": 101},
    ],
)
def test_bank_invalid_pagination_parameters_are_rejected(client, params):
    response = client.get(
        f"/api/reconciliation/{JOB_BANK}/bank-transactions",
        params=params,
    )

    assert response.status_code == 422


def test_existing_fund_request_and_va_apis_still_work(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_bank_job(session)

    fund_request_matched = client.get(
        f"/api/reconciliation/{JOB_BANK}/fund-requests",
        params={"status": "MATCHED"},
    )
    fund_request_unmatched = client.get(
        f"/api/reconciliation/{JOB_BANK}/fund-requests",
        params={"status": "NOT_FOUND"},
    )
    va_matched = client.get(
        f"/api/reconciliation/{JOB_BANK}/va-transactions",
        params={"status": "MATCHED"},
    )

    assert fund_request_matched.status_code == 200, fund_request_matched.text
    assert {
        item["source_transaction_id"]
        for item in fund_request_matched.json()["items"]
    } == {"FR-BANK-1", "FR-MATCHED-NO-BANK"}

    assert fund_request_unmatched.status_code == 200, fund_request_unmatched.text
    assert [
        item["source_transaction_id"]
        for item in fund_request_unmatched.json()["items"]
    ] == ["FR-NOT-FOUND"]

    assert va_matched.status_code == 200, va_matched.text
    assert {
        item["source_transaction_id"]
        for item in va_matched.json()["items"]
    } == {"VA-BANK-1", "VA-BANK-DUPLICATE"}
