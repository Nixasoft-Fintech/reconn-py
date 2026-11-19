from __future__ import annotations

import uuid

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_current_user
from app.database.base import Base
from app.db_models import (
    ReconciliationJob,
    ReconciliationResult,
    UnmatchedBankTransaction,
    User,
)
from app.main import app
from app.services.reconciliation_query_service import get_db_session


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kwargs):
    return "JSON"


@compiles(UUID, "sqlite")
def _compile_uuid_for_sqlite(type_, compiler, **kwargs):
    return "CHAR(32)"


JOB = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
ADMIN_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

APIS = {
    "fund_matched": ("fund-requests", "MATCHED"),
    "fund_unmatched": ("fund-requests", "NOT_FOUND"),
    "va_matched": ("va-transactions", "MATCHED"),
    "va_unmatched": ("va-transactions", "NOT_FOUND"),
    "bank_matched": ("bank-transactions", "MATCHED"),
    "bank_unmatched": ("bank-transactions", "NOT_FOUND"),
}

EXPECTED_TOTALS = {
    "fund_matched": 2,
    "fund_unmatched": 1,
    "va_matched": 1,
    "va_unmatched": 2,
    "bank_matched": 2,
    "bank_unmatched": 3,
}


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
            User.__table__,
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
def seeded_session(session_factory):
    with session_factory() as session:
        session.add(
            User(
                id=ADMIN_ID,
                mobile_number="9999999999",
                hashed_password="not-used",
                role="admin",
            )
        )
        session.add(
            ReconciliationJob(
                job_id=JOB,
                user_id=ADMIN_ID,
                status="COMPLETED",
                fund_request_filename="fund.csv",
                va_transaction_filename="va.csv",
                completed_at=datetime(2026, 11, 10, tzinfo=timezone.utc),
            )
        )

        _add_result(
            session,
            source_type="fund_request",
            final_status="MATCHED",
            source_transaction_id="FR-1",
            source_utr="UTRFRMATCH1",
            source_reference="REF-FR-1",
            source_amount=Decimal("100.0000"),
            source_user="MONEXIS PVT LTD",
            source_bank="KOTAK",
            bank_row_number=19,
            bank_name="KOTAK",
            bank_utr="UTRBANK19",
            bank_reference="BANKREF19",
            bank_amount=Decimal("100.0000"),
            bank_user="MONEXIS",
            bank_account_holder="MONEXIS",
        )
        # Duplicate persisted link for the same bank row: bank APIs must dedupe.
        _add_result(
            session,
            source_type="fund_request",
            final_status="MATCHED",
            source_transaction_id="FR-DUP",
            source_utr="UTRFRDUP",
            source_reference="REF-FR-DUP",
            source_amount=Decimal("100.0000"),
            source_user="OTHER",
            source_bank="KOTAK",
            bank_row_number=19,
            bank_name="KOTAK",
            bank_utr="UTRBANK19",
            bank_reference="BANKREF19",
            bank_amount=Decimal("100.0000"),
            bank_user="MONEXIS",
            bank_account_holder="MONEXIS",
        )
        _add_result(
            session,
            source_type="fund_request",
            final_status="NOT_FOUND",
            source_transaction_id="FR-UNM",
            source_utr="UTRFRUNM",
            source_reference="REF-FR-UNM",
            source_amount=Decimal("50000.0000"),
            source_user="ALPHA",
            source_bank="HDFC",
        )
        _add_result(
            session,
            source_type="va_transaction",
            final_status="MATCHED",
            source_transaction_id="VA-1",
            source_utr="UTRVAMATCH",
            source_reference="REF-VA-1",
            source_amount=Decimal("200.0000"),
            source_user="MONEXIS",
            source_bank="KOTAK",
            source_virtual_account_number="VA123",
            bank_row_number=20,
            bank_name="KOTAK",
            bank_utr="UTRBANK20",
            bank_reference="BANKREF20",
            bank_amount=Decimal("200.0000"),
            bank_user="MONEXIS",
            bank_account_holder="MONEXIS",
        )
        _add_result(
            session,
            source_type="va_transaction",
            final_status="NOT_FOUND",
            source_transaction_id="VA-UNM",
            source_utr="UTRVAUNM1",
            source_reference="REF-VA-UNM1",
            source_amount=Decimal("50000.0000"),
            source_user="BETA",
            source_bank="HDFC",
        )
        _add_result(
            session,
            source_type="va_transaction",
            final_status="NOT_FOUND",
            source_transaction_id="VA-UNM2",
            source_utr="UTRVAUNM2",
            source_reference="REF-VA-UNM2",
            source_amount=Decimal("50000.0000"),
            source_user="BETA",
            source_bank="HDFC",
        )

        _add_unmatched_bank(
            session,
            row_number=21,
            utr="UTRUNMATCHED21",
            reference="REF-U21",
            amount=Decimal("300.0000"),
            account_holder="MONEXIS",
            bank_name="KOTAK",
        )
        _add_unmatched_bank(
            session,
            row_number=22,
            utr="UTRUNMATCHED22",
            reference="REF-U22",
            amount=Decimal("50000.0000"),
            account_holder="OTHER",
            bank_name="HDFC",
        )
        _add_unmatched_bank(
            session,
            row_number=23,
            utr="UTRUNMATCHED23",
            reference="REF-U23",
            amount=Decimal("50000.0000"),
            account_holder="OTHER",
            bank_name="HDFC",
        )

        session.commit()


@pytest.fixture()
def client(session_factory):
    def override_get_db_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def override_get_current_user():
        return SimpleNamespace(id=ADMIN_ID, role="admin")

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def _add_result(
    session,
    *,
    source_type,
    final_status,
    source_transaction_id,
    source_utr,
    source_reference,
    source_amount,
    source_user,
    source_bank,
    source_virtual_account_number=None,
    bank_row_number=None,
    bank_name=None,
    bank_utr=None,
    bank_reference=None,
    bank_amount=None,
    bank_user=None,
    bank_account_holder=None,
):
    session.add(
        ReconciliationResult(
            job_id=JOB,
            source_type=source_type,
            final_status=final_status,
            source_transaction_id=source_transaction_id,
            source_row_number=1,
            source_utr=source_utr,
            source_reference=source_reference,
            source_amount=source_amount,
            source_user=source_user,
            source_date=datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc),
            source_status="approved",
            source_bank=source_bank,
            source_payment_mode="NEFT",
            source_virtual_account_number=source_virtual_account_number,
            source_data={"ID": source_transaction_id},
            bank_row_number=bank_row_number,
            bank_name=bank_name,
            bank_utr=bank_utr,
            bank_reference=bank_reference,
            bank_amount=bank_amount,
            bank_user=bank_user,
            bank_account_holder=bank_account_holder,
            bank_source_filename="bank.csv" if bank_row_number else None,
            bank_data={"UTR": bank_utr} if bank_utr else None,
            reference_status="MATCH",
            amount_status="MATCH",
            user_status="MATCH",
            date_status="NOT_USED",
        )
    )


def _add_unmatched_bank(
    session,
    *,
    row_number,
    utr,
    reference,
    amount,
    account_holder,
    bank_name,
):
    session.add(
        UnmatchedBankTransaction(
            job_id=JOB,
            bank_name=bank_name,
            statement_row_number=row_number,
            transaction_date=datetime(2026, 11, 10, tzinfo=timezone.utc),
            utr=utr,
            reference=reference,
            amount=amount,
            signed_amount=amount,
            credit=amount,
            direction="CREDIT",
            account_holder=account_holder,
            source_filename="unmatched-bank.csv",
            raw_data={"UTR": utr},
            extracted_references=[utr],
        )
    )


def _get(client, api, **filters):
    path, status = APIS[api]
    return client.get(
        f"/api/reconciliation/{JOB}/{path}",
        params={"status": status, **filters},
    )


@pytest.mark.parametrize("api", list(APIS))
def test_without_filters_preserves_existing_behavior(client, seeded_session, api):
    response = _get(client, api)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == EXPECTED_TOTALS[api]
    assert len(payload["items"]) == min(payload["total"], 100)
    assert set(payload) == {
        "job_id",
        "status",
        "total",
        "page",
        "limit",
        "total_pages",
        "has_next",
        "has_previous",
        "items",
    }
    assert payload["page"] == 1
    assert payload["limit"] == 100


@pytest.mark.parametrize(
    ("api", "value"),
    [
        ("fund_matched", "UTRFRMATCH1"),
        ("fund_unmatched", "UTRFRUNM"),
        ("va_matched", "UTRVAMATCH"),
        ("va_unmatched", "UTRVAUNM2"),
        ("bank_matched", "UTRBANK19"),
        ("bank_unmatched", "UTRUNMATCHED21"),
    ],
)
def test_reference_filter(client, seeded_session, api, value):
    response = _get(client, api, reference=value)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1


@pytest.mark.parametrize(
    ("api", "value", "expected"),
    [
        ("fund_matched", "utrfrmatch", 1),
        ("fund_unmatched", "ref-fr-unm", 1),
        ("va_matched", "utrvamatch", 1),
        ("va_unmatched", "utrvaunm", 2),
        ("bank_matched", "utrbank19", 1),
        ("bank_unmatched", "ref-u21", 1),
    ],
)
def test_partial_case_insensitive_reference(
    client,
    seeded_session,
    api,
    value,
    expected,
):
    response = _get(client, api, reference=value)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize("api", list(APIS))
def test_reference_wildcards_are_literal(client, seeded_session, api):
    response = _get(client, api, reference="%")

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0


@pytest.mark.parametrize(
    ("api", "amount", "expected"),
    [
        ("fund_matched", "100.0000", 2),
        ("fund_unmatched", "50000", 1),
        ("va_matched", "200", 1),
        ("va_unmatched", "50000.00", 2),
        ("bank_matched", "100", 1),
        ("bank_unmatched", "300", 1),
    ],
)
def test_amount_filter(client, seeded_session, api, amount, expected):
    response = _get(client, api, amount=amount)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize(
    "api",
    ["fund_matched", "fund_unmatched", "va_matched", "bank_matched"],
)
def test_amount_filter_is_exact_decimal(client, seeded_session, api):
    response = _get(client, api, amount="100.01")

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0


@pytest.mark.parametrize(
    ("api", "name", "expected"),
    [
        ("fund_matched", "monexis", 1),
        ("fund_unmatched", "ALPHA", 1),
        ("va_matched", "MONEXIS", 1),
        ("va_unmatched", "beta", 2),
        ("bank_matched", "monexis", 2),
        ("bank_unmatched", "MONEXIS", 1),
    ],
)
def test_name_filter(client, seeded_session, api, name, expected):
    response = _get(client, api, name=name)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize(
    ("api", "bank", "expected"),
    [
        ("fund_matched", "kotak", 2),
        ("fund_unmatched", "hdfc", 1),
        ("va_matched", "KOTAK", 1),
        ("va_unmatched", "HDFC", 2),
        ("bank_matched", "kot", 2),
        ("bank_unmatched", "KOTAK", 1),
    ],
)
def test_bank_filter(client, seeded_session, api, bank, expected):
    response = _get(client, api, bank=bank)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize(
    ("api", "row_number", "expected"),
    [
        ("fund_matched", 19, 2),
        ("fund_unmatched", 19, 0),
        ("va_matched", 20, 1),
        ("va_unmatched", 20, 0),
        ("bank_matched", 19, 1),
        ("bank_unmatched", 21, 1),
    ],
)
def test_bank_row_number_filter(
    client,
    seeded_session,
    api,
    row_number,
    expected,
):
    response = _get(client, api, bank_row_number=row_number)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize("api", list(APIS))
def test_status_filter_is_preserved(client, seeded_session, api):
    response = _get(client, api)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == APIS[api][1]

    invalid = client.get(
        f"/api/reconciliation/{JOB}/{APIS[api][0]}",
        params={"status": "UNKNOWN"},
    )
    assert invalid.status_code == 422


@pytest.mark.parametrize(
    ("api", "filters", "expected"),
    [
        (
            "fund_matched",
            {
                "reference": "UTRFRMATCH1",
                "amount": "100",
                "bank": "KOTAK",
                "name": "MONEXIS",
            },
            1,
        ),
        (
            "bank_unmatched",
            {
                "reference": "UTRUNMATCHED21",
                "amount": "300",
                "bank": "KOTAK",
                "name": "MONEXIS",
            },
            1,
        ),
    ],
)
def test_multiple_filters_combine_with_and(
    client,
    seeded_session,
    api,
    filters,
    expected,
):
    response = _get(client, api, **filters)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected

    conflicting = _get(client, api, **{**filters, "name": "NOBODY"})
    assert conflicting.json()["total"] == 0


@pytest.mark.parametrize("api", list(APIS))
def test_blank_filters_are_ignored(client, seeded_session, api):
    response = _get(
        client,
        api,
        reference="",
        amount="",
        name="",
        bank="",
        bank_row_number="",
    )

    assert response.status_code == 200, response.text
    assert response.json()["total"] == EXPECTED_TOTALS[api]


@pytest.mark.parametrize("api", list(APIS))
def test_empty_search_result(client, seeded_session, api):
    response = _get(client, api, reference="DOES-NOT-EXIST")

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0
    assert response.json()["items"] == []


@pytest.mark.parametrize("api", list(APIS))
def test_invalid_filter_values_are_rejected(client, seeded_session, api):
    assert _get(client, api, amount="not-a-number").status_code == 422
    assert _get(client, api, bank_row_number="abc").status_code == 422
    assert _get(client, api, bank_row_number=-1).status_code == 422


@pytest.mark.parametrize(
    ("api", "filters"),
    [
        ("fund_matched", {"bank": "KOTAK"}),
        ("va_unmatched", {"amount": "50000"}),
        ("bank_matched", {"bank": "KOTAK"}),
        ("bank_unmatched", {"bank": "HDFC"}),
    ],
)
def test_pagination_with_active_filters(client, seeded_session, api, filters):
    first = _get(client, api, page=1, limit=1, **filters)
    second = _get(client, api, page=2, limit=1, **filters)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    first_payload = first.json()
    second_payload = second.json()

    assert first_payload["total"] == 2
    assert first_payload["total_pages"] == 2
    assert first_payload["has_next"] is True
    assert first_payload["has_previous"] is False
    assert len(first_payload["items"]) == 1

    assert second_payload["total"] == 2
    assert second_payload["has_next"] is False
    assert second_payload["has_previous"] is True
    assert len(second_payload["items"]) == 1


@pytest.mark.parametrize("api", list(APIS))
def test_response_fields_unchanged(client, seeded_session, api):
    item = _get(client, api).json()["items"][0]

    assert "source_transaction_id" in item
    assert "bank_name" in item
    assert "bank_row_number" in item

    if api == "bank_unmatched":
        assert isinstance(item["bank_transaction_id"], int)


def test_matched_bank_filters_do_not_duplicate_rows(client, seeded_session):
    by_reference = _get(client, "bank_matched", reference="UTRBANK19")
    by_row = _get(client, "bank_matched", bank_row_number=19)

    assert by_reference.json()["total"] == 1
    assert by_row.json()["total"] == 1
    assert len(by_row.json()["items"]) == 1
