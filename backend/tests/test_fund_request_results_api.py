from __future__ import annotations

import uuid

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.db_models import ReconciliationJob, ReconciliationResult
from app.main import app
from app.services.reconciliation_query_service import get_db_session


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kwargs):
    """Allow the PostgreSQL JSONB columns to be created in the test database."""

    return "JSON"


JOB_MATCHED = uuid.UUID("11111111-1111-1111-1111-111111111111")
JOB_ISOLATED = uuid.UUID("22222222-2222-2222-2222-222222222222")
JOB_PROCESSING = uuid.UUID("33333333-3333-3333-3333-333333333333")
JOB_EMPTY = uuid.UUID("44444444-4444-4444-4444-444444444444")
JOB_ORDERING = uuid.UUID("55555555-5555-5555-5555-555555555555")
JOB_PAGINATION = uuid.UUID("66666666-6666-6666-6666-666666666666")


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
    reference_status="NOT_USED",
    amount_status="MATCH",
    user_status="MATCH",
    date_status="NOT_USED",
    exception_reason=None,
    source_data=None,
    **bank_fields,
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
            source_payment_mode="RTGS",
            source_virtual_account_number=None,
            source_data=source_data or {"ID": source_transaction_id},
            reference_status=reference_status,
            amount_status=amount_status,
            user_status=user_status,
            date_status=date_status,
            final_status=final_status,
            exception_reason=exception_reason,
            **bank_fields,
        )
    )


def _seed_main_job(session):
    _add_job(session, JOB_MATCHED)

    _add_result(
        session,
        JOB_MATCHED,
        final_status="MATCHED",
        source_transaction_id="FR-MATCHED",
        source_utr="UTRFRMATCHED",
        source_reference="REFFRMATCHED",
        amount_status="MATCH",
        user_status="MATCH",
        exception_reason=(
            "Matched by exact amount and flexible party name. "
            "Allocated sequentially using bank statement order."
        ),
        source_data={
            "ID": "FR-MATCHED",
            "User": "ACME PVT LTD",
            "Amount": "100.00",
        },
        bank_row_number=9,
        bank_name="AXIS",
        bank_utr="UTRFRMATCHED",
        bank_reference="BANKREFFR1",
        bank_amount=Decimal("100.00"),
        bank_signed_amount=Decimal("100.00"),
        bank_user="ACME PVT LTD",
        bank_date=datetime(2026, 11, 10, 10, 2, tzinfo=timezone.utc),
        bank_narration="NEFT CR ACME PVT LTD",
        bank_direction="CREDIT",
        bank_account_number="1234567890",
        bank_account_holder="ACME PVT LTD",
        bank_format="AXIS",
        bank_source_filename="bank.csv",
        bank_extracted_references=["UTRFRMATCHED"],
        bank_data={"UTR": "UTRFRMATCHED", "Customer": "ACME PVT LTD"},
    )

    _add_result(
        session,
        JOB_MATCHED,
        final_status="NOT_FOUND",
        source_transaction_id="FR-NOT-FOUND",
        source_reference="REFFRNF1",
        amount_status="NOT_FOUND",
        user_status="NOT_FOUND",
        exception_reason=(
            "No available bank credit matched both the exact amount and party name."
        ),
    )

    _add_result(
        session,
        JOB_MATCHED,
        final_status="AMBIGUOUS",
        source_transaction_id="FR-AMBIGUOUS",
        reference_status="MULTIPLE_SOURCE_CANDIDATES",
        exception_reason="Multiple bank transactions match the source record.",
    )

    _add_result(
        session,
        JOB_MATCHED,
        final_status="REVIEW_REQUIRED",
        source_transaction_id="FR-REVIEW",
        reference_status="MATCH",
        exception_reason="Manual review is required.",
    )

    _add_result(
        session,
        JOB_MATCHED,
        final_status="MATCHED_AFTER_REVIEW",
        source_transaction_id="FR-MATCHED-AFTER-REVIEW",
        reference_status="MATCH",
        exception_reason="Group-level review settlement is not an individual match.",
    )

    _add_result(
        session,
        JOB_MATCHED,
        final_status="EXCLUDED_BY_STATUS",
        source_transaction_id="FR-EXCLUDED",
        source_status="rejected",
        exception_reason="Fund Request status is not approved.",
    )

    _add_result(
        session,
        JOB_MATCHED,
        final_status="EXCLUDED",
        source_transaction_id="FR-EXCLUDED-PLAIN",
        exception_reason="Record was excluded.",
    )

    _add_result(
        session,
        JOB_MATCHED,
        source_type="va_transaction",
        final_status="MATCHED",
        source_transaction_id="VA-MATCHED",
        source_status="success",
        source_reference="UTRVAMATCHED",
        bank_row_number=10,
        bank_utr="UTRVAMATCHED",
        bank_amount=Decimal("200.00"),
        bank_user="ACME PVT LTD",
    )

    session.commit()


def _seed_other_jobs(session):
    _add_job(session, JOB_ISOLATED)
    _add_result(
        session,
        JOB_ISOLATED,
        final_status="MATCHED",
        source_transaction_id="FR-OTHER-JOB",
        source_utr="UTRFRJOBB",
        bank_row_number=11,
        bank_utr="UTRFRJOBB",
    )
    _add_result(
        session,
        JOB_ISOLATED,
        final_status="NOT_FOUND",
        source_transaction_id="FR-OTHER-NOT-FOUND",
        amount_status="NOT_FOUND",
        user_status="NOT_FOUND",
        exception_reason="No bank match in the second job.",
    )

    _add_job(session, JOB_PROCESSING, status="PROCESSING")

    _add_job(session, JOB_EMPTY)
    _add_result(
        session,
        JOB_EMPTY,
        final_status="MATCHED",
        source_transaction_id="FR-EMPTY-MATCHED",
        bank_row_number=12,
        bank_utr="UTREMPTY",
    )

    session.commit()


def _seed_pagination_job(session):
    _add_job(session, JOB_PAGINATION)

    for index in range(1, 251):
        _add_result(
            session,
            JOB_PAGINATION,
            final_status="MATCHED",
            source_transaction_id=f"FR-PAGE-{index:03d}",
            source_utr=f"UTRPAGE{index:03d}",
            bank_row_number=index,
            bank_utr=f"UTRPAGE{index:03d}",
        )

    for index in range(1, 6):
        _add_result(
            session,
            JOB_PAGINATION,
            final_status="NOT_FOUND",
            source_transaction_id=f"FR-PAGE-NF-{index:03d}",
            amount_status="NOT_FOUND",
            user_status="NOT_FOUND",
            exception_reason="No bank match.",
        )

    session.commit()


def test_matched_returns_only_individually_matched_fund_requests(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_main_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_MATCHED}/fund-requests",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 200, response.text

    payload = response.json()

    assert payload["job_id"] == str(JOB_MATCHED)
    assert payload["status"] == "MATCHED"
    assert payload["total"] == 1
    assert len(payload["items"]) == 1

    item = payload["items"][0]

    assert item["source_type"] == "fund_request"
    assert item["source_transaction_id"] == "FR-MATCHED"
    assert item["final_status"] == "MATCHED"

    # Original Fund Request details are preserved.
    assert item["source_user"] == "ACME PVT LTD"
    assert item["source_utr"] == "UTRFRMATCHED"
    assert item["source_reference_number"] == "REFFRMATCHED"
    assert item["source_reference"] == "UTRFRMATCHED"
    assert Decimal(item["source_amount"]) == Decimal("100.00")
    assert item["source_status"] == "approved"
    assert item["source_bank"] == "AXIS"
    assert item["source_payment_mode"] == "RTGS"
    assert item["source_data"]["ID"] == "FR-MATCHED"

    # Matched bank transaction details and matching evidence are preserved.
    assert item["bank_row_number"] == 9
    assert item["bank_utr"] == "UTRFRMATCHED"
    assert item["bank_reference"] == "BANKREFFR1"
    assert Decimal(item["bank_amount"]) == Decimal("100.00")
    assert item["bank_user"] == "ACME PVT LTD"
    assert item["bank_narration"] == "NEFT CR ACME PVT LTD"
    assert item["bank_account_number"] == "1234567890"
    assert item["bank_extracted_references"] == ["UTRFRMATCHED"]
    assert item["bank_data"]["UTR"] == "UTRFRMATCHED"

    assert item["reference_status"] == "NOT_USED"
    assert item["amount_status"] == "MATCH"
    assert item["user_status"] == "MATCH"
    assert item["date_status"] == "NOT_USED"
    assert "exact amount and flexible party name" in item["exception_reason"]


def test_matched_excludes_va_ambiguous_review_excluded_and_unmatched(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_main_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_MATCHED}/fund-requests",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 200, response.text
    returned_ids = {
        item["source_transaction_id"] for item in response.json()["items"]
    }

    assert returned_ids == {"FR-MATCHED"}
    assert "VA-MATCHED" not in returned_ids
    assert "FR-AMBIGUOUS" not in returned_ids
    assert "FR-REVIEW" not in returned_ids
    assert "FR-MATCHED-AFTER-REVIEW" not in returned_ids
    assert "FR-EXCLUDED" not in returned_ids
    assert "FR-EXCLUDED-PLAIN" not in returned_ids
    assert "FR-NOT-FOUND" not in returned_ids


def test_unmatched_returns_only_genuine_not_found_records(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_main_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_MATCHED}/fund-requests",
        params={"status": "NOT_FOUND"},
    )

    assert response.status_code == 200, response.text

    payload = response.json()

    assert payload["job_id"] == str(JOB_MATCHED)
    assert payload["status"] == "NOT_FOUND"
    assert payload["total"] == 1

    item = payload["items"][0]

    assert item["source_transaction_id"] == "FR-NOT-FOUND"
    assert item["final_status"] == "NOT_FOUND"
    assert item["source_type"] == "fund_request"
    assert item["source_reference_number"] == "REFFRNF1"
    assert item["amount_status"] == "NOT_FOUND"
    assert item["user_status"] == "NOT_FOUND"
    assert item["bank_row_number"] is None
    assert item["bank_utr"] is None
    assert "No available bank credit" in item["exception_reason"]


def test_unmatched_excludes_matched_ambiguous_review_and_excluded(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_main_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_MATCHED}/fund-requests",
        params={"status": "NOT_FOUND"},
    )

    assert response.status_code == 200, response.text
    returned_ids = {
        item["source_transaction_id"] for item in response.json()["items"]
    }

    assert returned_ids == {"FR-NOT-FOUND"}
    assert "FR-MATCHED" not in returned_ids
    assert "FR-AMBIGUOUS" not in returned_ids
    assert "FR-REVIEW" not in returned_ids
    assert "FR-MATCHED-AFTER-REVIEW" not in returned_ids
    assert "FR-EXCLUDED" not in returned_ids
    assert "FR-EXCLUDED-PLAIN" not in returned_ids


def test_completed_job_with_no_matching_records_returns_empty_success(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_other_jobs(session)

    response = client.get(
        f"/api/reconciliation/{JOB_EMPTY}/fund-requests",
        params={"status": "NOT_FOUND"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "job_id": str(JOB_EMPTY),
        "status": "NOT_FOUND",
        "total": 0,
        "page": 1,
        "limit": 100,
        "total_pages": 0,
        "has_next": False,
        "has_previous": False,
        "items": [],
    }


def test_jobs_are_isolated(client, session_factory):
    with session_factory() as session:
        _seed_main_job(session)
        _seed_other_jobs(session)

    first_job = client.get(
        f"/api/reconciliation/{JOB_MATCHED}/fund-requests",
        params={"status": "MATCHED"},
    )
    second_job = client.get(
        f"/api/reconciliation/{JOB_ISOLATED}/fund-requests",
        params={"status": "MATCHED"},
    )

    assert first_job.status_code == 200, first_job.text
    assert second_job.status_code == 200, second_job.text

    assert [
        item["source_transaction_id"] for item in first_job.json()["items"]
    ] == ["FR-MATCHED"]
    assert [
        item["source_transaction_id"] for item in second_job.json()["items"]
    ] == ["FR-OTHER-JOB"]


def test_result_order_is_preserved(client, session_factory):
    with session_factory() as session:
        _add_job(session, JOB_ORDERING)
        _add_result(
            session,
            JOB_ORDERING,
            final_status="MATCHED",
            source_transaction_id="FR-ORDER-1",
            bank_row_number=20,
            bank_utr="UTRORDER1",
        )
        _add_result(
            session,
            JOB_ORDERING,
            final_status="MATCHED",
            source_transaction_id="FR-ORDER-2",
            bank_row_number=21,
            bank_utr="UTRORDER2",
        )
        session.commit()

    response = client.get(
        f"/api/reconciliation/{JOB_ORDERING}/fund-requests",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 200, response.text
    assert [
        item["source_transaction_id"] for item in response.json()["items"]
    ] == ["FR-ORDER-1", "FR-ORDER-2"]


def test_pagination_first_page_uses_defaults(client, session_factory):
    with session_factory() as session:
        _seed_pagination_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/fund-requests",
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
    ] == [f"FR-PAGE-{index:03d}" for index in range(1, 101)]
    assert {item["final_status"] for item in payload["items"]} == {"MATCHED"}


def test_pagination_next_page(client, session_factory):
    with session_factory() as session:
        _seed_pagination_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/fund-requests",
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
    ] == [f"FR-PAGE-{index:03d}" for index in range(101, 201)]


def test_pagination_last_page(client, session_factory):
    with session_factory() as session:
        _seed_pagination_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/fund-requests",
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
    ] == [f"FR-PAGE-{index:03d}" for index in range(201, 251)]


def test_pagination_uses_custom_page_size(client, session_factory):
    with session_factory() as session:
        _seed_pagination_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/fund-requests",
        params={"status": "MATCHED", "page": 2, "limit": 50},
    )

    assert response.status_code == 200, response.text

    payload = response.json()

    assert payload["total"] == 250
    assert payload["page"] == 2
    assert payload["limit"] == 50
    assert payload["total_pages"] == 5
    assert payload["has_next"] is True
    assert payload["has_previous"] is True
    assert [
        item["source_transaction_id"] for item in payload["items"]
    ] == [f"FR-PAGE-{index:03d}" for index in range(51, 101)]


@pytest.mark.parametrize(
    "params",
    [
        {"status": "MATCHED", "page": ""},
        {"status": "MATCHED", "limit": ""},
        {"status": "MATCHED", "page": "", "limit": ""},
    ],
)
def test_blank_pagination_parameters_use_defaults(
    client,
    session_factory,
    params,
):
    with session_factory() as session:
        _seed_pagination_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/fund-requests",
        params=params,
    )

    assert response.status_code == 200, response.text

    payload = response.json()

    assert payload["page"] == 1
    assert payload["limit"] == 100
    assert payload["total"] == 250
    assert payload["total_pages"] == 3
    assert [
        item["source_transaction_id"] for item in payload["items"]
    ] == [f"FR-PAGE-{index:03d}" for index in range(1, 101)]


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
    ],
)
def test_invalid_pagination_parameters_are_rejected(client, params):
    response = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/fund-requests",
        params=params,
    )

    assert response.status_code == 422


def test_invalid_job_id_is_rejected(client):
    response = client.get(
        "/api/reconciliation/not-a-uuid/fund-requests",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 422


def test_unknown_job_id_returns_404(client):
    unknown_job = uuid.uuid4()

    response = client.get(
        f"/api/reconciliation/{unknown_job}/fund-requests",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 404
    assert str(unknown_job) in response.json()["detail"]


def test_processing_job_returns_409(client, session_factory):
    with session_factory() as session:
        _seed_other_jobs(session)

    response = client.get(
        f"/api/reconciliation/{JOB_PROCESSING}/fund-requests",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 409
    assert "not completed" in response.json()["detail"]


@pytest.mark.parametrize(
    "status",
    ["AUTO_MATCHED", "MATCHED_AFTER_REVIEW", "matched", "UNKNOWN", ""],
)
def test_invalid_status_is_rejected(client, session_factory, status):
    with session_factory() as session:
        _seed_main_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_MATCHED}/fund-requests",
        params={"status": status},
    )

    assert response.status_code == 422


def test_repeated_requests_are_consistent(client, session_factory):
    with session_factory() as session:
        _seed_main_job(session)

    url = f"/api/reconciliation/{JOB_MATCHED}/fund-requests"
    first = client.get(url, params={"status": "MATCHED"})
    second = client.get(url, params={"status": "MATCHED"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()

    first_unmatched = client.get(url, params={"status": "NOT_FOUND"})
    second_unmatched = client.get(url, params={"status": "NOT_FOUND"})

    assert first_unmatched.json() == second_unmatched.json()


def test_get_does_not_rerun_engine_or_modify_stored_results(
    client,
    session_factory,
    monkeypatch,
):
    with session_factory() as session:
        _seed_main_job(session)

    def fail_if_engine_runs(*args, **kwargs):
        raise AssertionError("GET must not run reconciliation again.")

    monkeypatch.setattr("app.main.reconcile_transactions", fail_if_engine_runs)

    def snapshot():
        with session_factory() as session:
            rows = session.scalars(
                select(ReconciliationResult).order_by(ReconciliationResult.id.asc())
            ).all()
            return [
                (
                    row.id,
                    row.job_id,
                    row.source_transaction_id,
                    row.final_status,
                    row.bank_utr,
                    row.bank_amount,
                    row.exception_reason,
                )
                for row in rows
            ]

    before = snapshot()

    matched = client.get(
        f"/api/reconciliation/{JOB_MATCHED}/fund-requests",
        params={"status": "MATCHED"},
    )
    unmatched = client.get(
        f"/api/reconciliation/{JOB_MATCHED}/fund-requests",
        params={"status": "NOT_FOUND"},
    )

    assert matched.status_code == 200, matched.text
    assert unmatched.status_code == 200, unmatched.text
    assert snapshot() == before
