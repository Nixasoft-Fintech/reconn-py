from __future__ import annotations

import csv
import io
import uuid

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.db_models import (
    Company,
    ManualSettlement,
    ManualSettlementTarget,
    ReconciliationJob,
    ReconciliationResult,
    ReconciliationSummary,
    UnmatchedBankTransaction,
)
from app.main import app
from app.services.reconciliation_query_service import get_db_session
from app.services.summary_service import recompute_job_summary


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kwargs):
    return "JSON"


@compiles(UUID, "sqlite")
def _compile_uuid_for_sqlite(type_, compiler, **kwargs):
    return "CHAR(32)"


JOB = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
JOB_PROCESSING = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
COMPANY_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
OTHER_COMPANY_ID = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


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
            ReconciliationSummary.__table__,
            Company.__table__,
            ManualSettlement.__table__,
            ManualSettlementTarget.__table__,
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


def _add_job(session, job_id=JOB, status="COMPLETED"):
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


def _add_company(session, *, company_id=COMPANY_ID, name="Acme Corp"):
    session.add(
        Company(
            id=company_id,
            name=name,
            name_key=name.strip().casefold(),
            alias="ACME",
        )
    )


def _add_result(
    session,
    *,
    source_type="fund_request",
    final_status,
    source_transaction_id,
    source_amount=Decimal("100.0000"),
    source_user="ACME PVT LTD",
    exception_reason=None,
    bank_row_number=None,
    bank_source_filename=None,
    bank_name=None,
    bank_utr=None,
    bank_reference=None,
    bank_amount=None,
    bank_signed_amount=None,
    bank_narration=None,
    bank_direction=None,
    bank_account_number=None,
    bank_account_holder=None,
    bank_format=None,
):
    session.add(
        ReconciliationResult(
            job_id=JOB,
            source_type=source_type,
            source_transaction_id=source_transaction_id,
            source_row_number=2,
            source_utr=source_transaction_id,
            source_reference=source_transaction_id,
            source_amount=source_amount,
            source_user=source_user,
            source_date=datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc),
            source_status="approved",
            source_bank="AXIS",
            source_payment_mode="NEFT",
            source_data={"ID": source_transaction_id},
            reference_status="NOT_USED",
            amount_status="NOT_FOUND",
            user_status="NOT_FOUND",
            date_status="NOT_USED",
            final_status=final_status,
            exception_reason=exception_reason,
            bank_row_number=bank_row_number,
            bank_name=bank_name,
            bank_utr=bank_utr,
            bank_reference=bank_reference,
            bank_amount=bank_amount,
            bank_signed_amount=bank_signed_amount,
            bank_user=None,
            bank_date=(
                datetime(2026, 11, 10, 11, 0, tzinfo=timezone.utc)
                if bank_row_number is not None
                else None
            ),
            bank_value_date=None,
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
    *,
    row_number,
    amount=Decimal("100.0000"),
    utr="UTR-BANK",
    source_filename="axis.csv",
):
    session.add(
        UnmatchedBankTransaction(
            job_id=JOB,
            bank_name="AXIS",
            statement_row_number=row_number,
            transaction_date=datetime(
                2026, 11, 10, 11, 0, tzinfo=timezone.utc
            ),
            utr=utr,
            reference=f"REF-{row_number}",
            narration="Bank narration",
            credit=amount,
            amount=amount,
            direction="CREDIT",
            raw_data={"UTR": utr},
            bank_account_number="1234567890",
            account_holder="ACME PVT LTD",
            bank_format="AXIS",
            source_filename=source_filename,
            signed_amount=amount,
            value_date=datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc),
            extracted_references=[utr],
        )
    )


def _seed(session):
    _add_job(session)
    _add_job(session, JOB_PROCESSING, status="PROCESSING")
    _add_company(session)

    _add_result(
        session,
        final_status="NOT_FOUND",
        source_transaction_id="FR-NOT-FOUND-1",
        exception_reason="No bank match.",
    )
    _add_result(
        session,
        final_status="NOT_FOUND",
        source_transaction_id="FR-NOT-FOUND-2",
        source_amount=Decimal("200.0000"),
        exception_reason="No bank match.",
    )
    _add_result(
        session,
        source_type="va_transaction",
        final_status="AMBIGUOUS",
        source_transaction_id="VA-AMBIGUOUS",
        source_amount=Decimal("300.0000"),
        exception_reason="Multiple candidates.",
        bank_row_number=7,
        bank_source_filename="axis.csv",
        bank_name="AXIS",
        bank_utr="UTR-LINKED",
        bank_reference="REF-LINKED",
        bank_amount=Decimal("300.0000"),
        bank_signed_amount=Decimal("300.0000"),
        bank_narration="Linked bank narration",
        bank_direction="CREDIT",
        bank_account_number="1234567890",
        bank_account_holder="ACME PVT LTD",
        bank_format="AXIS",
    )
    _add_result(
        session,
        final_status="MATCHED",
        source_transaction_id="FR-MATCHED",
        exception_reason="Automatic match.",
        bank_row_number=9,
        bank_source_filename="axis.csv",
        bank_name="AXIS",
        bank_amount=Decimal("100.0000"),
        bank_direction="CREDIT",
    )
    _add_result(
        session,
        final_status="EXCLUDED_BY_STATUS",
        source_transaction_id="FR-EXCLUDED",
        exception_reason="Source status excluded.",
    )

    _add_unmatched_bank(session, row_number=2, amount=Decimal("100.0000"))
    _add_unmatched_bank(session, row_number=3, amount=Decimal("200.0000"))

    session.flush()
    recompute_job_summary(session, JOB)
    session.commit()


def _source_ref(source_type, transaction_id):
    return {
        "transaction_type": source_type,
        "transaction_id": transaction_id,
    }


def _bank_ref(filename="axis.csv", row_number=2):
    return {
        "transaction_type": "bank",
        "bank_source_filename": filename,
        "bank_row_number": row_number,
    }


def _payload(primary, counterpart, company_id=COMPANY_ID, remarks=None):
    return {
        "primary": primary,
        "company_id": str(company_id),
        "counterpart": counterpart,
        "remarks": remarks,
    }


def _fresh(session_factory):
    with session_factory() as session:
        _seed(session)


def _result(session, transaction_id):
    return session.scalar(
        select(ReconciliationResult).where(
            ReconciliationResult.job_id == JOB,
            ReconciliationResult.source_transaction_id == transaction_id,
        )
    )


def _unmatched(session, row_number):
    return session.scalar(
        select(UnmatchedBankTransaction).where(
            UnmatchedBankTransaction.job_id == JOB,
            UnmatchedBankTransaction.statement_row_number == row_number,
        )
    )


def _summary(session):
    return session.get(ReconciliationSummary, JOB)


def _settle(client, primary, counterpart, **kwargs):
    return client.post(
        f"/api/reconciliation/{JOB}/manual-settlements",
        json=_payload(primary, counterpart, **kwargs),
    )


# ---------------------------------------------------------------------------
# Company management
# ---------------------------------------------------------------------------


def test_company_create_trims_and_returns_201(client):
    response = client.post(
        "/api/companies",
        json={"name": "  Acme Corp  ", "alias": "  ACME  "},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["name"] == "Acme Corp"
    assert payload["alias"] == "ACME"
    assert payload["company_id"]


def test_company_name_is_unique_case_insensitively(client):
    first = client.post("/api/companies", json={"name": "Acme Corp"})
    assert first.status_code == 201, first.text

    duplicate = client.post("/api/companies", json={"name": "  acme corp  "})

    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["detail"].lower()


def test_company_list_supports_search_and_pagination(client):
    client.post("/api/companies", json={"name": "Zeta Ltd", "alias": "ZED"})
    client.post("/api/companies", json={"name": "Alpha Ltd", "alias": "ALP"})
    client.post("/api/companies", json={"name": "Beta Corp"})

    response = client.get("/api/companies", params={"limit": 2})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 3
    assert payload["page"] == 1
    assert payload["limit"] == 2
    assert payload["total_pages"] == 2
    assert payload["has_next"] is True
    assert payload["has_previous"] is False
    assert [item["name"] for item in payload["items"]] == [
        "Alpha Ltd",
        "Beta Corp",
    ]

    search = client.get("/api/companies", params={"search": "zed"})
    assert [item["name"] for item in search.json()["items"]] == ["Zeta Ltd"]

    search_by_name = client.get("/api/companies", params={"search": "alpha"})
    assert [item["name"] for item in search_by_name.json()["items"]] == [
        "Alpha Ltd"
    ]


def test_company_get_returns_404_for_unknown_id(client):
    response = client.get(f"/api/companies/{uuid.uuid4()}")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Successful settlement
# ---------------------------------------------------------------------------


def test_source_primary_settlement_updates_both_sides(
    client,
    session_factory,
):
    _fresh(session_factory)

    response = _settle(
        client,
        _source_ref("fund_request", "FR-NOT-FOUND-1"),
        _bank_ref(row_number=2),
        remarks="Reviewed by ops",
    )

    assert response.status_code == 201, response.text
    payload = response.json()

    assert payload["status"] == "ACTIVE"
    assert payload["match_type"] == "MANUAL"
    assert payload["company"]["name"] == "Acme Corp"
    assert payload["settlement_remarks"] == (
        "Manually Settled: Reviewed by ops"
    )
    assert payload["settled_by"] is None
    assert {target["role"] for target in payload["targets"]} == {
        "PRIMARY",
        "COUNTERPART",
    }

    with session_factory() as session:
        source = _result(session, "FR-NOT-FOUND-1")
        assert source.final_status == "MATCHED"
        assert source.match_type == "MANUAL"
        assert source.company_id == COMPANY_ID
        assert "Manually Settled" in source.settlement_remarks
        assert source.exception_reason == "No bank match."
        assert source.bank_row_number == 2
        assert source.bank_source_filename == "axis.csv"
        assert source.bank_name == "AXIS"
        assert source.bank_utr == "UTR-BANK"

        bank = _unmatched(session, 2)
        assert bank.active_settlement_id is not None

        settlement = session.scalar(select(ManualSettlement))
        assert settlement is not None
        assert settlement.status == "ACTIVE"

    matched = client.get(
        f"/api/reconciliation/{JOB}/fund-requests",
        params={"status": "MATCHED"},
    )
    matched_ids = {
        item["source_transaction_id"] for item in matched.json()["items"]
    }
    assert "FR-NOT-FOUND-1" in matched_ids

    unmatched = client.get(
        f"/api/reconciliation/{JOB}/fund-requests",
        params={"status": "NOT_FOUND"},
    )
    unmatched_ids = {
        item["source_transaction_id"] for item in unmatched.json()["items"]
    }
    assert "FR-NOT-FOUND-1" not in unmatched_ids

    matched_item = next(
        item
        for item in matched.json()["items"]
        if item["source_transaction_id"] == "FR-NOT-FOUND-1"
    )
    assert matched_item["match_type"] == "MANUAL"
    assert matched_item["company_id"] == str(COMPANY_ID)

    bank_unmatched = client.get(
        f"/api/reconciliation/{JOB}/bank-transactions",
        params={"status": "NOT_FOUND"},
    )
    bank_rows = {
        item["bank_row_number"] for item in bank_unmatched.json()["items"]
    }
    assert 2 not in bank_rows
    assert 3 in bank_rows

    bank_matched = client.get(
        f"/api/reconciliation/{JOB}/bank-transactions",
        params={"status": "MATCHED"},
    )
    assert 2 in {
        item["bank_row_number"] for item in bank_matched.json()["items"]
    }


def test_bank_primary_settlement_requires_and_updates_source_counterpart(
    client,
    session_factory,
):
    _fresh(session_factory)

    response = _settle(
        client,
        _bank_ref(row_number=3),
        _source_ref("fund_request", "FR-NOT-FOUND-2"),
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["primary"]["transaction_type"] == "bank"
    assert payload["counterpart"]["transaction_type"] == "fund_request"

    with session_factory() as session:
        source = _result(session, "FR-NOT-FOUND-2")
        assert source.final_status == "MATCHED"
        assert source.bank_row_number == 3
        assert source.company_id == COMPANY_ID
        assert _unmatched(session, 3).active_settlement_id is not None


def test_settlement_supports_already_linked_bank_row(
    client,
    session_factory,
):
    _fresh(session_factory)

    response = _settle(
        client,
        _source_ref("va_transaction", "VA-AMBIGUOUS"),
        _bank_ref(row_number=7),
    )

    assert response.status_code == 201, response.text

    with session_factory() as session:
        source = _result(session, "VA-AMBIGUOUS")
        assert source.final_status == "MATCHED"
        assert source.match_type == "MANUAL"
        assert source.exception_reason == "Multiple candidates."


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "primary,counterpart",
    [
        (
            _source_ref("fund_request", "FR-NOT-FOUND-1"),
            _source_ref("va_transaction", "VA-AMBIGUOUS"),
        ),
        (_bank_ref(), _bank_ref(row_number=3)),
    ],
)
def test_settlement_requires_exactly_one_bank_side(
    client,
    session_factory,
    primary,
    counterpart,
):
    _fresh(session_factory)

    response = _settle(client, primary, counterpart)

    assert response.status_code == 422


def test_bank_reference_requires_composite_identity(client, session_factory):
    _fresh(session_factory)

    response = client.post(
        f"/api/reconciliation/{JOB}/manual-settlements",
        json=_payload(
            {
                "transaction_type": "bank",
                "bank_row_number": 2,
            },
            _source_ref("fund_request", "FR-NOT-FOUND-1"),
        ),
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "transaction_id",
    ["FR-MATCHED", "FR-EXCLUDED"],
)
def test_ineligible_source_status_is_rejected(
    client,
    session_factory,
    transaction_id,
):
    _fresh(session_factory)

    response = _settle(
        client,
        _source_ref("fund_request", transaction_id),
        _bank_ref(row_number=2),
    )

    assert response.status_code == 422
    assert "not eligible" in response.json()["detail"].lower()


def test_settlement_rejects_non_completed_job(client, session_factory):
    _fresh(session_factory)

    response = client.post(
        f"/api/reconciliation/{JOB_PROCESSING}/manual-settlements",
        json=_payload(
            _source_ref("fund_request", "FR-NOT-FOUND-1"),
            _bank_ref(row_number=2),
        ),
    )

    assert response.status_code == 409


def test_settlement_returns_404_for_unknown_records(
    client,
    session_factory,
):
    _fresh(session_factory)

    unknown_source = _settle(
        client,
        _source_ref("fund_request", "FR-UNKNOWN"),
        _bank_ref(row_number=2),
    )
    assert unknown_source.status_code == 404

    unknown_bank = _settle(
        client,
        _source_ref("fund_request", "FR-NOT-FOUND-1"),
        _bank_ref(row_number=99),
    )
    assert unknown_bank.status_code == 404

    unknown_company = _settle(
        client,
        _source_ref("fund_request", "FR-NOT-FOUND-1"),
        _bank_ref(row_number=2),
        company_id=OTHER_COMPANY_ID,
    )
    assert unknown_company.status_code == 404


def test_source_with_different_linked_bank_rejects_counterpart(
    client,
    session_factory,
):
    _fresh(session_factory)

    response = _settle(
        client,
        _source_ref("va_transaction", "VA-AMBIGUOUS"),
        _bank_ref(row_number=2),
    )

    assert response.status_code == 422


def test_bank_linked_to_another_source_rejects_counterpart(
    client,
    session_factory,
):
    _fresh(session_factory)

    response = _settle(
        client,
        _source_ref("fund_request", "FR-NOT-FOUND-2"),
        _bank_ref(row_number=7),
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Atomicity and duplicate prevention
# ---------------------------------------------------------------------------


def test_settlement_rolls_back_when_a_later_step_fails(
    client,
    session_factory,
    monkeypatch,
):
    _fresh(session_factory)

    def fail(*args, **kwargs):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(
        "app.services.manual_settlement_service.recompute_job_summary",
        fail,
    )

    with pytest.raises(RuntimeError):
        _settle(
            client,
            _source_ref("fund_request", "FR-NOT-FOUND-1"),
            _bank_ref(row_number=2),
        )

    with session_factory() as session:
        source = _result(session, "FR-NOT-FOUND-1")
        assert source.final_status == "NOT_FOUND"
        assert source.match_type is None
        assert source.company_id is None
        assert source.settlement_remarks is None
        assert source.bank_row_number is None
        assert _unmatched(session, 2).active_settlement_id is None
        assert session.scalar(select(ManualSettlement)) is None


def test_duplicate_active_settlement_is_prevented(
    client,
    session_factory,
):
    _fresh(session_factory)

    first = _settle(
        client,
        _source_ref("fund_request", "FR-NOT-FOUND-1"),
        _bank_ref(row_number=2),
    )
    assert first.status_code == 201, first.text

    duplicate_source = _settle(
        client,
        _source_ref("fund_request", "FR-NOT-FOUND-1"),
        _bank_ref(row_number=3),
    )
    assert duplicate_source.status_code == 409

    duplicate_bank = _settle(
        client,
        _source_ref("fund_request", "FR-NOT-FOUND-2"),
        _bank_ref(row_number=2),
    )
    assert duplicate_bank.status_code == 409


# ---------------------------------------------------------------------------
# Reversal and re-settlement
# ---------------------------------------------------------------------------


def test_reversal_restores_both_records_and_summary(
    client,
    session_factory,
):
    _fresh(session_factory)

    settled = _settle(
        client,
        _source_ref("fund_request", "FR-NOT-FOUND-1"),
        _bank_ref(row_number=2),
        remarks="Reviewed",
    )
    settlement_id = settled.json()["settlement_id"]

    summary_after_settle = client.get(
        f"/api/reconciliation/{JOB}/summary"
    ).json()["summary"]
    assert summary_after_settle["overall"]["total_matched_records"] == 2
    assert summary_after_settle["overall"]["total_unmatched_bank_records"] == 1

    reversed_response = client.post(
        f"/api/reconciliation/manual-settlements/{settlement_id}/reverse",
        json={"reason": "Wrong company"},
    )

    assert reversed_response.status_code == 200, reversed_response.text
    payload = reversed_response.json()
    assert payload["status"] == "REVERSED"
    assert payload["reversed_at"] is not None
    assert payload["reversal_reason"] == "Wrong company"

    with session_factory() as session:
        source = _result(session, "FR-NOT-FOUND-1")
        assert source.final_status == "NOT_FOUND"
        assert source.match_type is None
        assert source.company_id is None
        assert source.settlement_remarks is None
        assert source.bank_row_number is None
        assert source.exception_reason == "No bank match."
        assert _unmatched(session, 2).active_settlement_id is None

        settlement = session.get(ManualSettlement, uuid.UUID(settlement_id))
        assert settlement.status == "REVERSED"
        assert all(target.active is False for target in settlement.targets)

    summary_after_reverse = client.get(
        f"/api/reconciliation/{JOB}/summary"
    ).json()["summary"]
    assert summary_after_reverse["overall"]["total_matched_records"] == 1
    assert summary_after_reverse["overall"]["total_unmatched_bank_records"] == 2
    assert summary_after_reverse["fund_request"]["unmatched_records"] == 2

    bank_unmatched = client.get(
        f"/api/reconciliation/{JOB}/bank-transactions",
        params={"status": "NOT_FOUND"},
    )
    assert 2 in {
        item["bank_row_number"] for item in bank_unmatched.json()["items"]
    }

    second_reversal = client.post(
        f"/api/reconciliation/manual-settlements/{settlement_id}/reverse",
        json={},
    )
    assert second_reversal.status_code == 409

    resettled = _settle(
        client,
        _source_ref("fund_request", "FR-NOT-FOUND-1"),
        _bank_ref(row_number=2),
    )
    assert resettled.status_code == 201, resettled.text
    assert resettled.json()["settlement_id"] != settlement_id


def test_settlement_history_is_listed_and_preserved(
    client,
    session_factory,
):
    _fresh(session_factory)

    first = _settle(
        client,
        _source_ref("fund_request", "FR-NOT-FOUND-1"),
        _bank_ref(row_number=2),
    )
    first_id = first.json()["settlement_id"]

    client.post(
        f"/api/reconciliation/manual-settlements/{first_id}/reverse",
        json={"reason": "Retry"},
    )

    second = _settle(
        client,
        _source_ref("fund_request", "FR-NOT-FOUND-2"),
        _bank_ref(row_number=3),
    )
    assert second.status_code == 201, second.text

    history = client.get(
        f"/api/reconciliation/{JOB}/manual-settlements",
        params={"page": 1, "limit": 1},
    )

    assert history.status_code == 200, history.text
    payload = history.json()
    assert payload["total"] == 2
    assert payload["total_pages"] == 2
    assert payload["has_next"] is True
    assert payload["items"][0]["settlement_id"] == second.json()[
        "settlement_id"
    ]
    assert payload["items"][0]["status"] == "ACTIVE"


# ---------------------------------------------------------------------------
# Exports and summary counts
# ---------------------------------------------------------------------------


def _csv_rows(response):
    text = response.content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def test_exports_reflect_settlement(client, session_factory):
    _fresh(session_factory)

    settled = _settle(
        client,
        _source_ref("fund_request", "FR-NOT-FOUND-1"),
        _bank_ref(row_number=2),
    )
    assert settled.status_code == 201, settled.text

    matched_export = client.get(
        f"/api/reconciliation/{JOB}/fund-requests/export",
        params={"status": "MATCHED"},
    )
    matched_ids = {
        row["Fund Request ID"] for row in _csv_rows(matched_export)
    }
    assert "FR-NOT-FOUND-1" in matched_ids

    unmatched_export = client.get(
        f"/api/reconciliation/{JOB}/fund-requests/export",
        params={"status": "NOT_FOUND"},
    )
    unmatched_ids = {
        row["Fund Request ID"] for row in _csv_rows(unmatched_export)
    }
    assert "FR-NOT-FOUND-1" not in unmatched_ids
    assert "FR-NOT-FOUND-2" in unmatched_ids

    bank_unmatched_export = client.get(
        f"/api/reconciliation/{JOB}/bank-transactions/export",
        params={"status": "NOT_FOUND"},
    )
    bank_rows = {
        row["Bank Transaction Row Number"]
        for row in _csv_rows(bank_unmatched_export)
    }
    assert "2" not in bank_rows
    assert "3" in bank_rows

    bank_matched_export = client.get(
        f"/api/reconciliation/{JOB}/bank-transactions/export",
        params={"status": "MATCHED"},
    )
    assert "2" in {
        row["Bank Transaction Row Number"]
        for row in _csv_rows(bank_matched_export)
    }


def test_summary_counts_update_after_settlement(client, session_factory):
    _fresh(session_factory)

    before = client.get(f"/api/reconciliation/{JOB}/summary").json()["summary"]
    assert before["overall"]["total_source_records"] == 5
    assert before["overall"]["total_bank_records"] == 4
    assert before["overall"]["total_matched_records"] == 1
    assert before["overall"]["total_unmatched_bank_records"] == 2
    assert before["fund_request"]["matched_records"] == 1
    assert before["fund_request"]["unmatched_records"] == 2

    response = _settle(
        client,
        _source_ref("fund_request", "FR-NOT-FOUND-1"),
        _bank_ref(row_number=2),
    )
    assert response.status_code == 201, response.text

    after = client.get(f"/api/reconciliation/{JOB}/summary").json()["summary"]

    assert after["overall"]["total_source_records"] == 5
    assert after["overall"]["total_bank_records"] == 4
    assert after["overall"]["total_matched_records"] == 2
    assert after["overall"]["total_matched_amount"] == 200.0
    assert after["overall"]["total_unmatched_bank_records"] == 1
    assert after["fund_request"]["matched_records"] == 2
    assert after["fund_request"]["unmatched_records"] == 1
    assert after["unmatched_bank"]["total_records"] == 1
