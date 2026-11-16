from __future__ import annotations

import uuid

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
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


JOB_DETAIL = uuid.UUID("11111111-1111-1111-1111-111111111111")
JOB_OLD = uuid.UUID("22222222-2222-2222-2222-222222222222")
JOB_MIDDLE = uuid.UUID("33333333-3333-3333-3333-333333333333")
JOB_NEW = uuid.UUID("44444444-4444-4444-4444-444444444444")
JOB_PROCESSING = uuid.UUID("55555555-5555-5555-5555-555555555555")
JOB_EMPTY = uuid.UUID("66666666-6666-6666-6666-666666666666")
JOB_PAGINATION = uuid.UUID("77777777-7777-7777-7777-777777777777")


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


def _add_job(
    session,
    job_id,
    *,
    status="COMPLETED",
    created_at=datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc),
    completed_at=datetime(2026, 11, 10, 10, 0, 5, tzinfo=timezone.utc),
    fund_request_filename="fund.csv",
    va_transaction_filename="va.csv",
    bank_files=None,
):
    session.add(
        ReconciliationJob(
            job_id=job_id,
            status=status,
            fund_request_filename=fund_request_filename,
            va_transaction_filename=va_transaction_filename,
            bank_files=(
                bank_files
                if bank_files is not None
                else [
                    {
                        "file_name": "bank.csv",
                        "bank_name": "AXIS",
                        "bank_format": "AXIS",
                        "transaction_count": 5,
                    }
                ]
            ),
            created_at=created_at,
            completed_at=completed_at if status == "COMPLETED" else None,
        )
    )
    session.flush()


def _add_result(
    session,
    job_id,
    *,
    source_type,
    final_status,
    source_transaction_id,
    bank_row_number=None,
    bank_source_filename=None,
):
    session.add(
        ReconciliationResult(
            job_id=job_id,
            source_type=source_type,
            source_transaction_id=source_transaction_id,
            source_row_number=2,
            source_utr=f"UTR-{source_transaction_id}",
            source_reference=f"REF-{source_transaction_id}",
            source_amount=Decimal("100.00"),
            source_user="ACME PVT LTD",
            source_status="approved",
            source_data={"ID": source_transaction_id},
            bank_row_number=bank_row_number,
            bank_name="AXIS" if bank_row_number is not None else None,
            bank_utr=(
                f"BANK-{bank_row_number}"
                if bank_row_number is not None
                else None
            ),
            bank_amount=(
                Decimal("100.00")
                if bank_row_number is not None
                else None
            ),
            bank_signed_amount=(
                Decimal("100.00")
                if bank_row_number is not None
                else None
            ),
            bank_direction=(
                "CREDIT" if bank_row_number is not None else None
            ),
            bank_source_filename=bank_source_filename,
            reference_status="MATCH",
            amount_status="MATCH",
            user_status="MATCH",
            date_status="NOT_USED",
            final_status=final_status,
            exception_reason=None,
        )
    )


def _add_unmatched_bank(session, job_id, *, row_number):
    session.add(
        UnmatchedBankTransaction(
            job_id=job_id,
            bank_name="AXIS",
            statement_row_number=row_number,
            utr=f"UTR-UNMATCHED-{row_number}",
            reference=f"REF-UNMATCHED-{row_number}",
            amount=Decimal("50.00"),
            signed_amount=Decimal("50.00"),
            direction="CREDIT",
            bank_account_number="1234567890",
            account_holder="ACME PVT LTD",
            bank_format="AXIS",
            source_filename="unmatched-bank.csv",
            raw_data={"UTR": f"UTR-UNMATCHED-{row_number}"},
            value_date=datetime(
                2026, 11, 11, 9, 0, tzinfo=timezone.utc
            ),
            extracted_references=[f"UTR-UNMATCHED-{row_number}"],
        )
    )


def _seed_detail_job(session):
    _add_job(
        session,
        JOB_DETAIL,
        fund_request_filename="fund-request.csv",
        va_transaction_filename="va-transaction.csv",
        created_at=datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 11, 10, 10, 0, 5, tzinfo=timezone.utc),
    )

    # Fund Request statuses: matched, not found and three "other" statuses.
    _add_result(
        session,
        JOB_DETAIL,
        source_type="fund_request",
        final_status="MATCHED",
        source_transaction_id="FR-MATCHED",
        bank_row_number=2,
        bank_source_filename="bank.csv",
    )
    _add_result(
        session,
        JOB_DETAIL,
        source_type="fund_request",
        final_status="NOT_FOUND",
        source_transaction_id="FR-NOT-FOUND",
    )
    _add_result(
        session,
        JOB_DETAIL,
        source_type="fund_request",
        final_status="AMBIGUOUS",
        source_transaction_id="FR-AMBIGUOUS",
    )
    _add_result(
        session,
        JOB_DETAIL,
        source_type="fund_request",
        final_status="REVIEW_REQUIRED",
        source_transaction_id="FR-REVIEW",
    )
    _add_result(
        session,
        JOB_DETAIL,
        source_type="fund_request",
        final_status="EXCLUDED_BY_STATUS",
        source_transaction_id="FR-EXCLUDED",
    )

    # VA statuses: matched, a duplicate link to the same bank row, an amount
    # mismatch linked to another bank row, and one genuinely not found.
    _add_result(
        session,
        JOB_DETAIL,
        source_type="va_transaction",
        final_status="MATCHED",
        source_transaction_id="VA-MATCHED",
        bank_row_number=5,
        bank_source_filename="bank.csv",
    )
    _add_result(
        session,
        JOB_DETAIL,
        source_type="va_transaction",
        final_status="MATCHED",
        source_transaction_id="VA-MATCHED-DUPLICATE",
        bank_row_number=5,
        bank_source_filename="bank.csv",
    )
    _add_result(
        session,
        JOB_DETAIL,
        source_type="va_transaction",
        final_status="AMOUNT_MISMATCH",
        source_transaction_id="VA-AMOUNT-MISMATCH",
        bank_row_number=7,
        bank_source_filename="bank.csv",
    )
    _add_result(
        session,
        JOB_DETAIL,
        source_type="va_transaction",
        final_status="NOT_FOUND",
        source_transaction_id="VA-NOT-FOUND",
    )

    # Bank rows 2, 5 and 7 are linked to results.  Rows 10 and 11 were never
    # allocated to any source record and are persisted as NOT_FOUND.
    _add_unmatched_bank(session, JOB_DETAIL, row_number=10)
    _add_unmatched_bank(session, JOB_DETAIL, row_number=11)

    session.commit()


def _seed_history_jobs(session):
    _add_job(
        session,
        JOB_OLD,
        created_at=datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 11, 1, 9, 0, 1, tzinfo=timezone.utc),
    )
    _add_job(
        session,
        JOB_MIDDLE,
        created_at=datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 11, 2, 9, 0, 2, tzinfo=timezone.utc),
    )
    _add_job(
        session,
        JOB_NEW,
        created_at=datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 11, 3, 9, 0, 3, tzinfo=timezone.utc),
    )
    _add_job(
        session,
        JOB_PROCESSING,
        status="PROCESSING",
        created_at=datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc),
    )

    session.commit()


def test_empty_history_returns_standard_pagination_payload(client):
    response = client.get("/api/reconciliation/history")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "total": 0,
        "page": 1,
        "limit": 100,
        "total_pages": 0,
        "has_next": False,
        "has_previous": False,
        "items": [],
    }


def test_history_lists_newest_jobs_first_and_includes_metadata(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_history_jobs(session)

    response = client.get("/api/reconciliation/history")

    assert response.status_code == 200, response.text

    payload = response.json()

    assert payload["total"] == 4
    assert payload["page"] == 1
    assert payload["limit"] == 100
    assert payload["total_pages"] == 1
    assert payload["has_next"] is False
    assert payload["has_previous"] is False
    assert [item["job_id"] for item in payload["items"]] == [
        str(JOB_PROCESSING),
        str(JOB_NEW),
        str(JOB_MIDDLE),
        str(JOB_OLD),
    ]

    newest = payload["items"][1]

    assert newest["status"] == "COMPLETED"
    # SQLite stores these timestamps without timezone information.
    assert newest["created_at"] == "2026-11-03T09:00:00"
    assert newest["completed_at"] == "2026-11-03T09:00:03"
    assert newest["processing_duration_seconds"] == 3.0
    assert newest["fund_request_filename"] == "fund.csv"
    assert newest["va_transaction_filename"] == "va.csv"
    assert newest["bank_files"] == [
        {
            "file_name": "bank.csv",
            "bank_name": "AXIS",
            "bank_format": "AXIS",
            "transaction_count": 5,
        }
    ]

    for category in ("fund_request", "va_transaction", "bank_transaction"):
        assert newest[category] == {
            "total_records": 0,
            "matched_records": 0,
            "not_found_records": 0,
            "other_records": 0,
        }


def test_history_supports_status_filtering(client, session_factory):
    with session_factory() as session:
        _seed_history_jobs(session)

    completed = client.get(
        "/api/reconciliation/history",
        params={"status": "COMPLETED"},
    )
    processing = client.get(
        "/api/reconciliation/history",
        params={"status": "processing"},
    )
    blank = client.get(
        "/api/reconciliation/history",
        params={"status": ""},
    )
    unknown = client.get(
        "/api/reconciliation/history",
        params={"status": "FAILED"},
    )

    assert completed.status_code == 200, completed.text
    assert [item["job_id"] for item in completed.json()["items"]] == [
        str(JOB_NEW),
        str(JOB_MIDDLE),
        str(JOB_OLD),
    ]

    assert processing.status_code == 200, processing.text
    assert processing.json()["total"] == 1
    assert processing.json()["items"][0]["job_id"] == str(JOB_PROCESSING)

    assert blank.status_code == 200, blank.text
    assert blank.json()["total"] == 4

    assert unknown.status_code == 200, unknown.text
    assert unknown.json() == {
        "total": 0,
        "page": 1,
        "limit": 100,
        "total_pages": 0,
        "has_next": False,
        "has_previous": False,
        "items": [],
    }


def test_history_pagination_metadata_and_limits(client, session_factory):
    with session_factory() as session:
        for index in range(1, 6):
            job_id = uuid.UUID(f"90000000-0000-0000-0000-{index:012d}")
            _add_job(
                session,
                job_id,
                created_at=datetime(
                    2026, 11, index, 9, 0, tzinfo=timezone.utc
                ),
                completed_at=datetime(
                    2026, 11, index, 9, 0, 1, tzinfo=timezone.utc
                ),
            )
        session.commit()

    first = client.get(
        "/api/reconciliation/history",
        params={"page": 1, "limit": 2},
    )
    second = client.get(
        "/api/reconciliation/history",
        params={"page": 2, "limit": 2},
    )
    final = client.get(
        "/api/reconciliation/history",
        params={"page": 3, "limit": 2},
    )
    beyond = client.get(
        "/api/reconciliation/history",
        params={"page": 4, "limit": 2},
    )
    blank = client.get(
        "/api/reconciliation/history",
        params={"page": "", "limit": ""},
    )

    assert first.status_code == 200, first.text
    assert first.json()["total"] == 5
    assert first.json()["total_pages"] == 3
    assert first.json()["has_next"] is True
    assert first.json()["has_previous"] is False
    assert len(first.json()["items"]) == 2

    assert second.status_code == 200, second.text
    assert second.json()["page"] == 2
    assert second.json()["has_next"] is True
    assert second.json()["has_previous"] is True
    assert len(second.json()["items"]) == 2

    assert final.status_code == 200, final.text
    assert final.json()["page"] == 3
    assert final.json()["has_next"] is False
    assert final.json()["has_previous"] is True
    assert len(final.json()["items"]) == 1

    assert beyond.status_code == 200, beyond.text
    assert beyond.json()["total"] == 5
    assert beyond.json()["items"] == []
    assert beyond.json()["has_next"] is False
    assert beyond.json()["has_previous"] is True

    assert blank.status_code == 200, blank.text
    assert blank.json()["page"] == 1
    assert blank.json()["limit"] == 100


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page": -1},
        {"limit": 0},
        {"limit": -1},
        {"limit": 101},
        {"page": "abc"},
        {"limit": "abc"},
        {"page": 1.5},
        {"limit": 1.5},
        {"page": " "},
        {"limit": " "},
        {"status": "X" * 31},
    ],
)
def test_history_rejects_invalid_query_parameters(client, params):
    response = client.get(
        "/api/reconciliation/history",
        params=params,
    )

    assert response.status_code == 422


def test_history_detail_returns_accurate_transaction_counts(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_detail_job(session)

    response = client.get(f"/api/reconciliation/history/{JOB_DETAIL}")

    assert response.status_code == 200, response.text

    payload = response.json()

    assert payload["job_id"] == str(JOB_DETAIL)
    assert payload["status"] == "COMPLETED"
    # SQLite stores these timestamps without timezone information.
    assert payload["created_at"] == "2026-11-10T10:00:00"
    assert payload["completed_at"] == "2026-11-10T10:00:05"
    assert payload["processing_duration_seconds"] == 5.0
    assert payload["fund_request_filename"] == "fund-request.csv"
    assert payload["va_transaction_filename"] == "va-transaction.csv"
    assert payload["bank_files"][0]["file_name"] == "bank.csv"

    assert payload["fund_request"] == {
        "total_records": 5,
        "matched_records": 1,
        "not_found_records": 1,
        "other_records": 3,
    }
    assert payload["va_transaction"] == {
        "total_records": 4,
        "matched_records": 2,
        "not_found_records": 1,
        "other_records": 1,
    }
    assert payload["bank_transaction"] == {
        "total_records": 5,
        "matched_records": 2,
        "not_found_records": 2,
        "other_records": 1,
    }

    # The list API returns the same job object shape as the detail API.
    history = client.get("/api/reconciliation/history")
    assert history.status_code == 200, history.text
    assert history.json()["items"][0] == payload


def test_history_does_not_classify_ambiguous_or_review_as_not_found(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_detail_job(session)

    payload = client.get(
        f"/api/reconciliation/history/{JOB_DETAIL}"
    ).json()

    assert payload["fund_request"]["not_found_records"] == 1
    assert payload["fund_request"]["other_records"] == 3
    assert payload["va_transaction"]["not_found_records"] == 1
    assert payload["va_transaction"]["other_records"] == 1
    assert payload["bank_transaction"]["not_found_records"] == 2
    assert payload["bank_transaction"]["other_records"] == 1


def test_history_job_without_results_returns_zero_counts(
    client,
    session_factory,
):
    with session_factory() as session:
        _add_job(session, JOB_EMPTY)
        session.commit()

    response = client.get(f"/api/reconciliation/history/{JOB_EMPTY}")

    assert response.status_code == 200, response.text

    payload = response.json()

    assert payload["job_id"] == str(JOB_EMPTY)
    for category in ("fund_request", "va_transaction", "bank_transaction"):
        assert payload[category] == {
            "total_records": 0,
            "matched_records": 0,
            "not_found_records": 0,
            "other_records": 0,
        }


def test_history_includes_processing_jobs_without_completion(
    client,
    session_factory,
):
    with session_factory() as session:
        _add_job(
            session,
            JOB_PROCESSING,
            status="PROCESSING",
            created_at=datetime(
                2026, 11, 12, 9, 0, tzinfo=timezone.utc
            ),
        )
        session.commit()

    detail = client.get(f"/api/reconciliation/history/{JOB_PROCESSING}")

    assert detail.status_code == 200, detail.text

    payload = detail.json()

    assert payload["status"] == "PROCESSING"
    assert payload["completed_at"] is None
    assert payload["processing_duration_seconds"] is None
    assert payload["fund_request"]["total_records"] == 0


def test_history_duration_is_null_when_completed_before_created(
    client,
    session_factory,
):
    with session_factory() as session:
        _add_job(
            session,
            JOB_OLD,
            created_at=datetime(2026, 11, 10, 10, 0, 5, tzinfo=timezone.utc),
            completed_at=datetime(
                2026, 11, 10, 10, 0, tzinfo=timezone.utc
            ),
        )
        session.commit()

    response = client.get(f"/api/reconciliation/history/{JOB_OLD}")

    assert response.status_code == 200, response.text
    assert response.json()["processing_duration_seconds"] is None


def test_history_invalid_and_unknown_job_ids(client):
    malformed = client.get("/api/reconciliation/history/not-a-uuid")
    unknown_id = uuid.uuid4()
    unknown = client.get(f"/api/reconciliation/history/{unknown_id}")

    assert malformed.status_code == 422
    assert unknown.status_code == 404
    assert str(unknown_id) in unknown.json()["detail"]


def test_history_get_requests_do_not_reconcile_or_write(
    client,
    session_factory,
    monkeypatch,
):
    with session_factory() as session:
        _seed_detail_job(session)

    def _unexpected_reconcile(*args, **kwargs):
        raise AssertionError("History requests must not reconcile.")

    monkeypatch.setattr(
        "app.main.reconcile_transactions",
        _unexpected_reconcile,
    )

    with session_factory() as session:
        before = (
            session.scalar(
                select(func.count()).select_from(ReconciliationJob)
            ),
            session.scalar(
                select(func.count()).select_from(ReconciliationResult)
            ),
            session.scalar(
                select(func.count()).select_from(
                    UnmatchedBankTransaction
                )
            ),
        )

    assert client.get("/api/reconciliation/history").status_code == 200
    assert (
        client.get(f"/api/reconciliation/history/{JOB_DETAIL}").status_code
        == 200
    )

    with session_factory() as session:
        after = (
            session.scalar(
                select(func.count()).select_from(ReconciliationJob)
            ),
            session.scalar(
                select(func.count()).select_from(ReconciliationResult)
            ),
            session.scalar(
                select(func.count()).select_from(
                    UnmatchedBankTransaction
                )
            ),
        )

    assert after == before


def test_existing_retrieval_and_export_apis_still_work(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_detail_job(session)

    fund_matched = client.get(
        f"/api/reconciliation/{JOB_DETAIL}/fund-requests",
        params={"status": "MATCHED"},
    )
    fund_not_found = client.get(
        f"/api/reconciliation/{JOB_DETAIL}/fund-requests",
        params={"status": "NOT_FOUND"},
    )
    va_matched = client.get(
        f"/api/reconciliation/{JOB_DETAIL}/va-transactions",
        params={"status": "MATCHED"},
    )
    va_not_found = client.get(
        f"/api/reconciliation/{JOB_DETAIL}/va-transactions",
        params={"status": "NOT_FOUND"},
    )
    bank_matched = client.get(
        f"/api/reconciliation/{JOB_DETAIL}/bank-transactions",
        params={"status": "MATCHED"},
    )
    bank_not_found = client.get(
        f"/api/reconciliation/{JOB_DETAIL}/bank-transactions",
        params={"status": "NOT_FOUND"},
    )
    export = client.get(
        f"/api/reconciliation/{JOB_DETAIL}/bank-transactions/export",
        params={"status": "MATCHED"},
    )

    assert fund_matched.status_code == 200, fund_matched.text
    assert fund_matched.json()["total"] == 1
    assert fund_not_found.status_code == 200, fund_not_found.text
    assert fund_not_found.json()["total"] == 1
    assert va_matched.status_code == 200, va_matched.text
    assert va_matched.json()["total"] == 2
    assert va_not_found.status_code == 200, va_not_found.text
    assert va_not_found.json()["total"] == 1
    assert bank_matched.status_code == 200, bank_matched.text
    assert bank_matched.json()["total"] == 2
    assert bank_not_found.status_code == 200, bank_not_found.text
    assert bank_not_found.json()["total"] == 2

    assert export.status_code == 200, export.text
    assert export.headers["content-type"].startswith("text/csv")
    assert "attachment" in export.headers["content-disposition"]
