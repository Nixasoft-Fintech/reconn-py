from __future__ import annotations

import uuid

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
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


JOB_VA = uuid.UUID("77777777-7777-7777-7777-777777777777")
JOB_PAGINATION = uuid.UUID("88888888-8888-8888-8888-888888888888")
JOB_EMPTY = uuid.UUID("99999999-9999-9999-9999-999999999999")
JOB_PROCESSING = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


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
    source_type="va_transaction",
    final_status,
    source_transaction_id,
    source_row_number=2,
    source_utr=None,
    source_reference=None,
    source_amount=Decimal("200.00"),
    source_user="ACME PVT LTD",
    source_status="success",
    source_virtual_account_number="VA001",
    reference_status="MATCH",
    amount_status="MATCH",
    user_status="NOT_USED",
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
            source_payment_mode="NEFT",
            source_virtual_account_number=source_virtual_account_number,
            source_data=source_data
            or {
                "ID": source_transaction_id,
                "UTR": source_utr,
                "VA No": source_virtual_account_number,
                "Status": source_status,
            },
            reference_status=reference_status,
            amount_status=amount_status,
            user_status=user_status,
            date_status=date_status,
            final_status=final_status,
            exception_reason=exception_reason,
            **bank_fields,
        )
    )


def _seed_va_job(session):
    _add_job(session, JOB_VA)

    _add_result(
        session,
        JOB_VA,
        final_status="MATCHED",
        source_transaction_id="VA-MATCHED",
        source_utr="UTRVA00000001",
        source_reference="REFVA00000001",
        exception_reason="Reference and exact amount matched.",
        source_data={
            "ID": "VA-MATCHED",
            "UTR": "UTRVA00000001",
            "VA No": "VA001",
            "Amount": "200.00",
            "Status": "success",
        },
        bank_row_number=7,
        bank_name="AXIS",
        bank_utr="UTRVA00000001",
        bank_reference="BANKREFVA1",
        bank_amount=Decimal("200.00"),
        bank_signed_amount=Decimal("200.00"),
        bank_user="ACME PVT LTD",
        bank_date=datetime(2026, 11, 10, 10, 2, tzinfo=timezone.utc),
        bank_narration="NEFT CR ACME PVT LTD",
        bank_direction="CREDIT",
        bank_account_number="1234567890",
        bank_account_holder="ACME PVT LTD",
        bank_format="AXIS",
        bank_source_filename="bank.csv",
        bank_extracted_references=["UTRVA00000001"],
        bank_data={"UTR": "UTRVA00000001", "Customer": "ACME PVT LTD"},
    )

    _add_result(
        session,
        JOB_VA,
        final_status="NOT_FOUND",
        source_transaction_id="VA-NOT-FOUND",
        source_utr="UTRVA00000002",
        reference_status="NOT_FOUND",
        amount_status="NOT_CHECKED",
        exception_reason=(
            "No exact or strong partial bank reference match found."
        ),
    )

    _add_result(
        session,
        JOB_VA,
        final_status="AMBIGUOUS",
        source_transaction_id="VA-AMBIGUOUS",
        source_utr="UTRVA00000003",
        reference_status="MULTIPLE_SOURCE_CANDIDATES",
        exception_reason="Multiple source records match the same bank row.",
    )

    _add_result(
        session,
        JOB_VA,
        final_status="REVIEW_REQUIRED",
        source_transaction_id="VA-REVIEW",
        source_utr="UTRVA00000004",
        exception_reason="Manual review is required.",
    )

    _add_result(
        session,
        JOB_VA,
        final_status="EXCLUDED_BY_STATUS",
        source_transaction_id="VA-EXCLUDED",
        source_status="failed",
        source_utr="UTRVA00000005",
        reference_status="NOT_CHECKED",
        amount_status="NOT_CHECKED",
        exception_reason="VA Transaction status is not success.",
    )

    _add_result(
        session,
        JOB_VA,
        final_status="EXCLUDED",
        source_transaction_id="VA-EXCLUDED-PLAIN",
        source_utr="UTRVA00000006",
        exception_reason="Record was excluded.",
    )

    _add_result(
        session,
        JOB_VA,
        final_status="MATCHED_AFTER_REVIEW",
        source_transaction_id="VA-MATCHED-AFTER-REVIEW",
        source_utr="UTRVA00000007",
        exception_reason="Group settlement does not confirm an individual match.",
    )

    # A Fund Request in the same job must not leak into VA responses.
    _add_result(
        session,
        JOB_VA,
        source_type="fund_request",
        final_status="MATCHED",
        source_transaction_id="FR-SAME-JOB",
        source_utr="UTRFR00000001",
        reference_status="NOT_USED",
        amount_status="MATCH",
        user_status="MATCH",
    )

    session.commit()


def _seed_pagination_job(session):
    _add_job(session, JOB_PAGINATION)

    for index in range(1, 251):
        _add_result(
            session,
            JOB_PAGINATION,
            final_status="MATCHED",
            source_transaction_id=f"VA-PAGE-{index:03d}",
            source_utr=f"UTRVA{index:08d}",
            bank_row_number=index,
            bank_utr=f"UTRVA{index:08d}",
        )

    for index in range(1, 6):
        _add_result(
            session,
            JOB_PAGINATION,
            final_status="NOT_FOUND",
            source_transaction_id=f"VA-PAGE-NF-{index:03d}",
            source_utr=f"UTRNF{index:08d}",
            reference_status="NOT_FOUND",
            amount_status="NOT_CHECKED",
            exception_reason="No bank reference match.",
        )

    for index in range(1, 4):
        _add_result(
            session,
            JOB_PAGINATION,
            source_type="fund_request",
            final_status="MATCHED",
            source_transaction_id=f"FR-PAGE-{index:03d}",
            source_utr=f"UTRFRPAGE{index:03d}",
        )

    session.commit()


def test_va_matched_returns_confirmed_matches_with_bank_details(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_va_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_VA}/va-transactions",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 200, response.text

    payload = response.json()

    assert payload["job_id"] == str(JOB_VA)
    assert payload["status"] == "MATCHED"
    assert payload["total"] == 1
    assert payload["page"] == 1
    assert payload["limit"] == 100
    assert payload["total_pages"] == 1
    assert payload["has_next"] is False
    assert payload["has_previous"] is False
    assert len(payload["items"]) == 1

    item = payload["items"][0]

    assert item["source_type"] == "va_transaction"
    assert item["source_transaction_id"] == "VA-MATCHED"
    assert item["final_status"] == "MATCHED"

    # VA transaction metadata is preserved.
    assert item["source_utr"] == "UTRVA00000001"
    assert item["source_reference_number"] == "REFVA00000001"
    assert item["source_virtual_account_number"] == "VA001"
    assert item["source_status"] == "success"
    assert Decimal(item["source_amount"]) == Decimal("200.00")
    assert item["source_data"]["VA No"] == "VA001"

    # Matched bank details and matching evidence are preserved.
    assert item["bank_row_number"] == 7
    assert item["bank_utr"] == "UTRVA00000001"
    assert item["bank_reference"] == "BANKREFVA1"
    assert Decimal(item["bank_amount"]) == Decimal("200.00")
    assert item["bank_user"] == "ACME PVT LTD"
    assert item["bank_narration"] == "NEFT CR ACME PVT LTD"
    assert item["bank_account_number"] == "1234567890"
    assert item["bank_extracted_references"] == ["UTRVA00000001"]
    assert item["reference_status"] == "MATCH"
    assert item["amount_status"] == "MATCH"
    assert item["exception_reason"] == "Reference and exact amount matched."


def test_va_matched_excludes_non_confirmed_and_fund_requests(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_va_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_VA}/va-transactions",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 200, response.text
    returned_ids = {
        item["source_transaction_id"] for item in response.json()["items"]
    }

    assert returned_ids == {"VA-MATCHED"}
    assert "VA-AMBIGUOUS" not in returned_ids
    assert "VA-REVIEW" not in returned_ids
    assert "VA-MATCHED-AFTER-REVIEW" not in returned_ids
    assert "VA-EXCLUDED" not in returned_ids
    assert "VA-EXCLUDED-PLAIN" not in returned_ids
    assert "VA-NOT-FOUND" not in returned_ids
    assert "FR-SAME-JOB" not in returned_ids


def test_va_unmatched_returns_only_genuine_not_found_records(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_va_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_VA}/va-transactions",
        params={"status": "NOT_FOUND"},
    )

    assert response.status_code == 200, response.text

    payload = response.json()

    assert payload["total"] == 1
    assert payload["page"] == 1
    assert payload["limit"] == 100
    assert payload["total_pages"] == 1

    item = payload["items"][0]

    assert item["source_type"] == "va_transaction"
    assert item["source_transaction_id"] == "VA-NOT-FOUND"
    assert item["final_status"] == "NOT_FOUND"
    assert item["source_utr"] == "UTRVA00000002"
    assert item["source_virtual_account_number"] == "VA001"
    assert item["reference_status"] == "NOT_FOUND"
    assert item["amount_status"] == "NOT_CHECKED"
    assert item["bank_row_number"] is None
    assert item["bank_utr"] is None
    assert (
        item["exception_reason"]
        == "No exact or strong partial bank reference match found."
    )


def test_va_unmatched_excludes_other_statuses(client, session_factory):
    with session_factory() as session:
        _seed_va_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_VA}/va-transactions",
        params={"status": "NOT_FOUND"},
    )

    assert response.status_code == 200, response.text
    returned_ids = {
        item["source_transaction_id"] for item in response.json()["items"]
    }

    assert returned_ids == {"VA-NOT-FOUND"}
    assert "VA-MATCHED" not in returned_ids
    assert "VA-AMBIGUOUS" not in returned_ids
    assert "VA-REVIEW" not in returned_ids
    assert "VA-MATCHED-AFTER-REVIEW" not in returned_ids
    assert "VA-EXCLUDED" not in returned_ids
    assert "VA-EXCLUDED-PLAIN" not in returned_ids
    assert "FR-SAME-JOB" not in returned_ids


def test_va_pagination_first_page_uses_defaults(client, session_factory):
    with session_factory() as session:
        _seed_pagination_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/va-transactions",
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
    ] == [f"VA-PAGE-{index:03d}" for index in range(1, 101)]
    assert {item["source_type"] for item in payload["items"]} == {
        "va_transaction"
    }


def test_va_pagination_second_page(client, session_factory):
    with session_factory() as session:
        _seed_pagination_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/va-transactions",
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
    ] == [f"VA-PAGE-{index:03d}" for index in range(101, 201)]


def test_va_pagination_final_page(client, session_factory):
    with session_factory() as session:
        _seed_pagination_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/va-transactions",
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
    ] == [f"VA-PAGE-{index:03d}" for index in range(201, 251)]


def test_va_pagination_custom_page_size(client, session_factory):
    with session_factory() as session:
        _seed_pagination_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/va-transactions",
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
    ] == [f"VA-PAGE-{index:03d}" for index in range(51, 101)]


def test_va_completed_job_with_no_matching_records_returns_empty_success(
    client,
    session_factory,
):
    with session_factory() as session:
        _add_job(session, JOB_EMPTY)
        _add_result(
            session,
            JOB_EMPTY,
            source_type="fund_request",
            final_status="MATCHED",
            source_transaction_id="FR-ONLY-MATCHED",
            source_utr="UTRFRONLY",
        )
        session.commit()

    response = client.get(
        f"/api/reconciliation/{JOB_EMPTY}/va-transactions",
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


def test_va_invalid_job_id_is_rejected(client):
    response = client.get(
        "/api/reconciliation/not-a-uuid/va-transactions",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 422


def test_va_unknown_job_id_returns_404(client):
    unknown_job = uuid.uuid4()

    response = client.get(
        f"/api/reconciliation/{unknown_job}/va-transactions",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 404
    assert str(unknown_job) in response.json()["detail"]


def test_va_processing_job_returns_409(client, session_factory):
    with session_factory() as session:
        _add_job(session, JOB_PROCESSING, status="PROCESSING")
        session.commit()

    response = client.get(
        f"/api/reconciliation/{JOB_PROCESSING}/va-transactions",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 409
    assert "not completed" in response.json()["detail"]


@pytest.mark.parametrize(
    "status",
    ["AUTO_MATCHED", "MATCHED_AFTER_REVIEW", "matched", "UNKNOWN", ""],
)
def test_va_invalid_status_is_rejected(client, status):
    response = client.get(
        f"/api/reconciliation/{JOB_VA}/va-transactions",
        params={"status": status},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "params",
    [
        {"status": "MATCHED", "page": ""},
        {"status": "MATCHED", "limit": ""},
        {"status": "MATCHED", "page": "", "limit": ""},
    ],
)
def test_va_blank_pagination_parameters_use_defaults(
    client,
    session_factory,
    params,
):
    with session_factory() as session:
        _seed_pagination_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_PAGINATION}/va-transactions",
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
    ] == [f"VA-PAGE-{index:03d}" for index in range(1, 101)]


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
def test_va_invalid_pagination_parameters_are_rejected(client, params):
    response = client.get(
        f"/api/reconciliation/{JOB_VA}/va-transactions",
        params=params,
    )

    assert response.status_code == 422


def test_existing_fund_request_api_still_works(client, session_factory):
    with session_factory() as session:
        _seed_va_job(session)

    response = client.get(
        f"/api/reconciliation/{JOB_VA}/fund-requests",
        params={"status": "MATCHED"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert [
        item["source_transaction_id"] for item in response.json()["items"]
    ] == ["FR-SAME-JOB"]
