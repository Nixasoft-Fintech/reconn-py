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

        # Rejected Fund Requests are persisted by the engine with the internal
        # final status EXCLUDED_BY_STATUS and a non-approved source status.
        _add_result(
            session,
            source_type="fund_request",
            final_status="EXCLUDED_BY_STATUS",
            source_status="rejected",
            exception_reason="Fund Request status is not approved.",
            source_transaction_id="FR-REJ-1",
            source_utr="UTRFRREJ1",
            source_reference="REF-FR-REJ1",
            source_amount=Decimal("700.0000"),
            source_user="REJECTED CO",
            source_bank="ICICI",
        )
        _add_result(
            session,
            source_type="fund_request",
            final_status="EXCLUDED_BY_STATUS",
            source_status="rejected",
            exception_reason="Fund Request status is not approved.",
            source_transaction_id="FR-REJ-2",
            source_utr="UTRFRREJ2",
            source_reference="REF-FR-REJ2",
            source_amount=Decimal("800.0000"),
            source_user="REJECTED CO",
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
    source_status="approved",
    exception_reason=None,
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
            source_status=source_status,
            exception_reason=exception_reason,
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


# ---------------------------------------------------------------------------
# Universal ``search`` parameter (one search box across all six APIs)
# ---------------------------------------------------------------------------

UNIVERSAL_REFERENCE_CASES = [
    ("fund_matched", "UTRFRMATCH1", 1),
    ("fund_unmatched", "UTRFRUNM", 1),
    ("va_matched", "UTRVAMATCH", 1),
    ("va_unmatched", "UTRVAUNM2", 1),
    ("bank_matched", "UTRBANK19", 1),
    ("bank_unmatched", "UTRUNMATCHED21", 1),
]

UNIVERSAL_NAME_CASES = [
    ("fund_matched", "MONEXIS", 1),
    ("fund_unmatched", "ALPHA", 1),
    ("va_matched", "MONEXIS", 1),
    ("va_unmatched", "BETA", 2),
    ("bank_matched", "MONEXIS", 2),
    ("bank_unmatched", "MONEXIS", 1),
]

UNIVERSAL_BANK_CASES = [
    ("fund_matched", "KOTAK", 2),
    ("fund_unmatched", "HDFC", 1),
    ("va_matched", "KOTAK", 1),
    ("va_unmatched", "HDFC", 2),
    ("bank_matched", "KOTAK", 2),
    ("bank_unmatched", "HDFC", 2),
]

UNIVERSAL_AMOUNT_CASES = [
    ("fund_matched", "100.0000", 2),
    ("fund_unmatched", "50000", 1),
    ("va_matched", "200", 1),
    ("va_unmatched", "50000", 2),
    ("bank_matched", "100", 1),
    ("bank_unmatched", "300", 1),
]

UNIVERSAL_ROW_CASES = [
    ("fund_matched", "0019", 2),
    ("fund_unmatched", "0019", 0),
    ("va_matched", "0020", 1),
    ("va_unmatched", "0020", 0),
    ("bank_matched", "0019", 1),
    ("bank_unmatched", "0021", 1),
]

UNIVERSAL_CASE_INSENSITIVE_CASES = [
    ("fund_matched", "utrfrmatch1", 1),
    ("fund_unmatched", "utrfrunm", 1),
    ("va_matched", "utrvamatch", 1),
    ("va_unmatched", "utrvaunm2", 1),
    ("bank_matched", "utrbank19", 1),
    ("bank_unmatched", "ref-u21", 1),
]

UNIVERSAL_PARTIAL_CASES = [
    ("fund_matched", "FRMATCH", 1),
    ("fund_unmatched", "FR-UNM", 1),
    ("va_matched", "VAMATCH", 1),
    ("va_unmatched", "UNM2", 1),
    ("bank_matched", "ANK19", 1),
    ("bank_unmatched", "MATCHED21", 1),
]

UNIVERSAL_NUMERIC_REFERENCE_CASES = [
    ("va_matched", "123", 1),
    ("fund_matched", "1", 2),
    ("va_unmatched", "1", 1),
    ("bank_matched", "19", 1),
    ("bank_unmatched", "23", 1),
]

UNIVERSAL_NULL_FIELD_CASES = [
    ("fund_unmatched", "UTRBANK", 0),
    ("va_unmatched", "BANKREF", 0),
    ("fund_matched", "VA123", 0),
    ("bank_unmatched", "%", 0),
    ("bank_unmatched", "_", 0),
]

UNIVERSAL_STATUS_CASES = [
    ("fund-requests", "MATCHED", "MONEXIS", 1),
    ("fund-requests", "NOT_FOUND", "MONEXIS", 0),
    ("va-transactions", "MATCHED", "MONEXIS", 1),
    ("va-transactions", "NOT_FOUND", "MONEXIS", 0),
    ("bank-transactions", "MATCHED", "MONEXIS", 2),
    ("bank-transactions", "NOT_FOUND", "MONEXIS", 1),
]

UNIVERSAL_WITH_INDIVIDUAL_CASES = [
    ("fund_matched", {"reference": "UTRFRMATCH1"}, 1),
    ("fund_matched", {"amount": "100"}, 1),
    ("fund_matched", {"name": "monexis"}, 1),
    ("fund_matched", {"bank": "KOTAK"}, 1),
    ("fund_matched", {"bank_row_number": 19}, 1),
    ("bank_unmatched", {"reference": "UTRUNMATCHED21"}, 1),
    ("bank_unmatched", {"amount": "300"}, 1),
    ("bank_unmatched", {"name": "MONEXIS"}, 1),
    ("bank_unmatched", {"bank": "KOTAK"}, 1),
    ("bank_unmatched", {"bank_row_number": 21}, 1),
]

UNIVERSAL_AND_CASES = [
    ("fund_matched", "MONEXIS", {"bank": "HDFC"}, 0),
    ("fund_matched", "KOTAK", {"amount": "100"}, 2),
    ("va_unmatched", "HDFC", {"amount": "100"}, 0),
    ("bank_unmatched", "MONEXIS", {"bank": "HDFC"}, 0),
    ("bank_matched", "KOTAK", {"bank_row_number": 20}, 1),
]

UNIVERSAL_PAGINATION_CASES = [
    ("fund_matched", "KOTAK", 2),
    ("va_unmatched", "50000", 2),
    ("bank_matched", "MONEXIS", 2),
    ("bank_unmatched", "HDFC", 2),
]

UNIVERSAL_LINKED_SOURCE_CASES = [
    ("UTRFRMATCH1", 1),
    ("REF-FR-1", 1),
    ("FR-1", 1),
    ("MONEXIS PVT", 1),
    ("UTRVAMATCH", 1),
]

UNIVERSAL_INDIVIDUAL_FILTER_CASES = [
    ("fund_matched", {"reference": "UTRFRMATCH1"}, 1),
    ("fund_unmatched", {"amount": "50000"}, 1),
    ("va_matched", {"name": "MONEXIS"}, 1),
    ("va_unmatched", {"bank": "HDFC"}, 2),
    ("bank_matched", {"bank_row_number": 19}, 1),
    ("bank_unmatched", {"reference": "REF-U22"}, 1),
]


@pytest.mark.parametrize(("api", "term", "expected"), UNIVERSAL_REFERENCE_CASES)
def test_universal_search_by_reference(client, seeded_session, api, term, expected):
    response = _get(client, api, search=term)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize(("api", "term", "expected"), UNIVERSAL_NAME_CASES)
def test_universal_search_by_name(client, seeded_session, api, term, expected):
    response = _get(client, api, search=term)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize(("api", "term", "expected"), UNIVERSAL_BANK_CASES)
def test_universal_search_by_bank(client, seeded_session, api, term, expected):
    response = _get(client, api, search=term)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize(("api", "term", "expected"), UNIVERSAL_AMOUNT_CASES)
def test_universal_search_matches_exact_amount(
    client,
    seeded_session,
    api,
    term,
    expected,
):
    response = _get(client, api, search=term)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize(("api", "term", "expected"), UNIVERSAL_ROW_CASES)
def test_universal_search_matches_exact_bank_row_number(
    client,
    seeded_session,
    api,
    term,
    expected,
):
    # Zero-padded terms parse as the same integer but never appear literally
    # in the UTR text, proving the integer interpretation is exercised.
    response = _get(client, api, search=term)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize(
    ("api", "term", "expected"), UNIVERSAL_NUMERIC_REFERENCE_CASES
)
def test_universal_numeric_search_also_matches_references(
    client,
    seeded_session,
    api,
    term,
    expected,
):
    response = _get(client, api, search=term)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize(
    ("api", "term", "expected"), UNIVERSAL_CASE_INSENSITIVE_CASES
)
def test_universal_search_is_case_insensitive(
    client,
    seeded_session,
    api,
    term,
    expected,
):
    response = _get(client, api, search=term)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize(("api", "term", "expected"), UNIVERSAL_PARTIAL_CASES)
def test_universal_search_is_partial(client, seeded_session, api, term, expected):
    response = _get(client, api, search=term)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize(("api", "term", "expected"), UNIVERSAL_NULL_FIELD_CASES)
def test_universal_search_handles_null_fields_and_literal_wildcards(
    client,
    seeded_session,
    api,
    term,
    expected,
):
    response = _get(client, api, search=term)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected
    assert response.json()["items"] == []


@pytest.mark.parametrize("api", list(APIS))
def test_universal_blank_search_is_ignored(client, seeded_session, api):
    empty = _get(client, api, search="")
    whitespace = _get(client, api, search="   ")

    assert empty.status_code == 200, empty.text
    assert whitespace.status_code == 200, whitespace.text
    assert empty.json()["total"] == EXPECTED_TOTALS[api]
    assert whitespace.json()["total"] == EXPECTED_TOTALS[api]


@pytest.mark.parametrize("api", list(APIS))
def test_universal_search_trims_whitespace(client, seeded_session, api):
    plain = _get(client, api, search="MONEXIS")
    trimmed = _get(client, api, search="  MONEXIS  ")

    assert trimmed.status_code == 200, trimmed.text
    assert trimmed.json() == plain.json()


@pytest.mark.parametrize(
    ("path", "status", "term", "expected"), UNIVERSAL_STATUS_CASES
)
def test_universal_search_preserves_status_classification(
    client,
    seeded_session,
    path,
    status,
    term,
    expected,
):
    response = client.get(
        f"/api/reconciliation/{JOB}/{path}",
        params={"status": status, "search": term},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == status
    assert payload["total"] == expected


@pytest.mark.parametrize(("api", "extra", "expected"), UNIVERSAL_WITH_INDIVIDUAL_CASES)
def test_universal_search_combines_with_each_individual_filter(
    client,
    seeded_session,
    api,
    extra,
    expected,
):
    response = _get(client, api, search="MONEXIS", **extra)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize(
    ("api", "term", "extra", "expected"), UNIVERSAL_AND_CASES
)
def test_universal_search_and_individual_filters_use_and(
    client,
    seeded_session,
    api,
    term,
    extra,
    expected,
):
    response = _get(client, api, search=term, **extra)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize(("api", "term", "expected"), UNIVERSAL_PAGINATION_CASES)
def test_universal_search_has_accurate_totals_and_pagination(
    client,
    seeded_session,
    api,
    term,
    expected,
):
    first = _get(client, api, search=term, page=1, limit=1)
    second = _get(client, api, search=term, page=2, limit=1)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    first_payload = first.json()
    second_payload = second.json()

    assert first_payload["total"] == expected
    assert first_payload["total_pages"] == expected
    assert first_payload["limit"] == 1
    assert first_payload["has_next"] is True
    assert first_payload["has_previous"] is False
    assert len(first_payload["items"]) == 1

    assert second_payload["total"] == expected
    assert second_payload["has_next"] is False
    assert second_payload["has_previous"] is True
    assert len(second_payload["items"]) == 1

    assert first_payload["items"][0] != second_payload["items"][0]


@pytest.mark.parametrize(("term", "expected"), UNIVERSAL_LINKED_SOURCE_CASES)
def test_universal_matched_bank_search_covers_linked_source_fields(
    client,
    seeded_session,
    term,
    expected,
):
    response = _get(client, "bank_matched", search=term)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("MONEXIS", 2),
        ("UTRBANK19", 1),
        ("0019", 1),
        ("100", 1),
        ("KOTAK", 2),
    ],
)
def test_universal_search_does_not_duplicate_matched_bank_rows(
    client,
    seeded_session,
    term,
    expected,
):
    response = _get(client, "bank_matched", search=term)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == expected
    assert len(payload["items"]) == expected

    rows = [item["bank_row_number"] for item in payload["items"]]
    assert len(rows) == len(set(rows))


@pytest.mark.parametrize("api", list(APIS))
def test_universal_empty_result(client, seeded_session, api):
    response = _get(client, api, search="DOES-NOT-EXIST")

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0
    assert response.json()["items"] == []
    assert response.json()["total_pages"] == 0
    assert response.json()["has_next"] is False


@pytest.mark.parametrize(("api", "extra", "expected"), UNIVERSAL_INDIVIDUAL_FILTER_CASES)
def test_existing_individual_filters_still_work_without_search(
    client,
    seeded_session,
    api,
    extra,
    expected,
):
    response = _get(client, api, **extra)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize("api", list(APIS))
def test_universal_search_keeps_response_structure(client, seeded_session, api):
    response = _get(client, api, search="MONEXIS")

    assert response.status_code == 200, response.text
    payload = response.json()
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
    assert payload["status"] == APIS[api][1]
    assert payload["limit"] == 100
    for item in payload["items"]:
        assert "source_transaction_id" in item
        assert "bank_name" in item
        assert "bank_row_number" in item


@pytest.mark.parametrize("api", list(APIS))
def test_optional_search_does_not_change_no_filter_behavior(
    client,
    seeded_session,
    api,
):
    without_search = _get(client, api)
    with_blank_search = _get(client, api, search="   ")

    assert without_search.json() == with_blank_search.json()
    assert without_search.json()["total"] == EXPECTED_TOTALS[api]


# ---------------------------------------------------------------------------
# REJECTED status on the Fund Request results API only
# ---------------------------------------------------------------------------

REJECTED_IDS = {"FR-REJ-1", "FR-REJ-2"}

REJECTED_SEARCH_CASES = [
    ("UTRFRREJ1", 1),
    ("REF-FR-REJ2", 1),
    ("FR-REJ-1", 1),
    ("rejected co", 2),
    ("ICICI", 1),
    ("800", 1),
    ("rej", 2),
]

REJECTED_FILTER_CASES = [
    ({"reference": "UTRFRREJ1"}, 1),
    ({"amount": "700"}, 1),
    ({"name": "rejected"}, 2),
    ({"bank": "ICICI"}, 1),
    # Rejected Fund Requests have no bank row attached.
    ({"bank_row_number": 21}, 0),
]


def _get_fund(client, status, **filters):
    return client.get(
        f"/api/reconciliation/{JOB}/fund-requests",
        params={"status": status, **filters},
    )


def test_rejected_status_returns_only_rejected_fund_requests(
    client,
    seeded_session,
):
    response = _get_fund(client, "REJECTED")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "REJECTED"
    assert payload["total"] == 2
    assert len(payload["items"]) == 2
    assert {
        item["source_transaction_id"] for item in payload["items"]
    } == REJECTED_IDS


def test_rejected_records_absent_from_matched_and_not_found(
    client,
    seeded_session,
):
    for status in ("MATCHED", "NOT_FOUND"):
        payload = _get_fund(client, status).json()
        ids = {item["source_transaction_id"] for item in payload["items"]}
        assert not (ids & REJECTED_IDS)

    assert (
        _get_fund(client, "MATCHED").json()["total"]
        == EXPECTED_TOTALS["fund_matched"]
    )
    assert (
        _get_fund(client, "NOT_FOUND").json()["total"]
        == EXPECTED_TOTALS["fund_unmatched"]
    )


def test_rejected_status_is_scoped_to_fund_request_results_api(
    client,
    seeded_session,
):
    for path in ("va-transactions", "bank-transactions"):
        response = client.get(
            f"/api/reconciliation/{JOB}/{path}",
            params={"status": "REJECTED"},
        )
        assert response.status_code == 422

    # The existing Fund Request export endpoint keeps its original statuses.
    export = client.get(
        f"/api/reconciliation/{JOB}/fund-requests/export",
        params={"status": "REJECTED"},
    )
    assert export.status_code == 422


@pytest.mark.parametrize(("term", "expected"), REJECTED_SEARCH_CASES)
def test_universal_search_with_rejected_status(
    client,
    seeded_session,
    term,
    expected,
):
    response = _get_fund(client, "REJECTED", search=term)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize(("extra", "expected"), REJECTED_FILTER_CASES)
def test_individual_filters_with_rejected_status(
    client,
    seeded_session,
    extra,
    expected,
):
    response = _get_fund(client, "REJECTED", **extra)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


@pytest.mark.parametrize(
    ("search", "extra", "expected"),
    [
        ("REJECTED", {"bank": "ICICI"}, 1),
        ("REJECTED", {"bank": "KOTAK"}, 0),
        ("REJECTED", {"amount": "800"}, 1),
        ("ICICI", {"name": "rejected"}, 1),
    ],
)
def test_rejected_search_and_filters_use_and(
    client,
    seeded_session,
    search,
    extra,
    expected,
):
    response = _get_fund(client, "REJECTED", search=search, **extra)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


def test_rejected_pagination_and_totals(client, seeded_session):
    first = _get_fund(client, "REJECTED", page=1, limit=1)
    second = _get_fund(client, "REJECTED", page=2, limit=1)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    first_payload = first.json()
    second_payload = second.json()

    assert first_payload["total"] == 2
    assert first_payload["total_pages"] == 2
    assert first_payload["page"] == 1
    assert first_payload["limit"] == 1
    assert first_payload["has_next"] is True
    assert first_payload["has_previous"] is False
    assert len(first_payload["items"]) == 1

    assert second_payload["total"] == 2
    assert second_payload["page"] == 2
    assert second_payload["has_next"] is False
    assert second_payload["has_previous"] is True
    assert len(second_payload["items"]) == 1

    assert first_payload["items"][0] != second_payload["items"][0]


def test_rejected_pagination_with_active_filters(client, seeded_session):
    first = _get_fund(client, "REJECTED", search="REJECTED", page=1, limit=1)
    second = _get_fund(client, "REJECTED", search="REJECTED", page=2, limit=1)

    assert first.json()["total"] == 2
    assert first.json()["total_pages"] == 2
    assert len(first.json()["items"]) == 1
    assert second.json()["total"] == 2
    assert len(second.json()["items"]) == 1
    assert first.json()["items"] != second.json()["items"]


def test_rejected_response_structure_unchanged(client, seeded_session):
    response = _get_fund(client, "REJECTED")

    assert response.status_code == 200, response.text
    payload = response.json()
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
    assert payload["job_id"] == str(JOB)
    assert payload["page"] == 1
    assert payload["limit"] == 100
    for item in payload["items"]:
        assert "source_transaction_id" in item
        assert "source_status" in item
        assert "bank_name" in item
        assert "bank_row_number" in item


def test_rejected_empty_result(client, seeded_session):
    response = _get_fund(client, "REJECTED", search="DOES-NOT-EXIST")

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0
    assert response.json()["items"] == []
    assert response.json()["total_pages"] == 0
    assert response.json()["has_next"] is False


@pytest.mark.parametrize(
    "status",
    ["AUTO_MATCHED", "MATCHED_AFTER_REVIEW", "matched", "UNKNOWN", ""],
)
def test_invalid_status_values_are_still_rejected(
    client,
    seeded_session,
    status,
):
    assert _get_fund(client, status).status_code == 422


def test_missing_status_is_still_rejected(client, seeded_session):
    response = client.get(f"/api/reconciliation/{JOB}/fund-requests")
    assert response.status_code == 422
