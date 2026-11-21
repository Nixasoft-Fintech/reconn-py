"""Contract tests for the company edit and delete APIs.

These tests override both the database session and the admin dependency so the
new routes are exercised directly instead of failing on the repository's
pre-existing auth fixture gap.
"""

from __future__ import annotations

import uuid

from datetime import datetime
from types import SimpleNamespace

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_current_user, require_admin
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


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kwargs):
    return "JSON"


@compiles(UUID, "sqlite")
def _compile_uuid_for_sqlite(type_, compiler, **kwargs):
    return "CHAR(32)"


JOB = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
COMPANY_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
OTHER_COMPANY_ID = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
ADMIN = SimpleNamespace(id=uuid.uuid4(), role="admin")


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
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    app.dependency_overrides[require_admin] = lambda: ADMIN

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def _add_company(
    session,
    *,
    company_id=COMPANY_ID,
    name="Acme Corp",
    alias="ACME",
    updated_at=None,
):
    values = {
        "id": company_id,
        "name": name,
        "name_key": name.strip().casefold(),
        "alias": alias,
    }
    if updated_at is not None:
        values["updated_at"] = updated_at

    session.add(Company(**values))
    session.commit()


def _add_settlement(session, *, company_id=COMPANY_ID):
    settlement_id = uuid.uuid4()
    session.add(
        ManualSettlement(
            id=settlement_id,
            job_id=JOB,
            company_id=company_id,
            status="ACTIVE",
            primary_transaction_type="fund_request",
            primary_record_key="FR-1",
            counterpart_transaction_type="bank",
            counterpart_record_key="bank.csv#1",
            settlement_remarks="Manually Settled",
            settled_at=datetime(2026, 11, 10, 10, 0),
        )
    )
    session.commit()
    return settlement_id


def _add_result(session, *, company_id=COMPANY_ID):
    session.add(
        ReconciliationResult(
            job_id=JOB,
            source_type="fund_request",
            source_transaction_id="FR-1",
            reference_status="NOT_USED",
            amount_status="NOT_FOUND",
            user_status="NOT_FOUND",
            date_status="NOT_USED",
            final_status="NOT_FOUND",
            company_id=company_id,
        )
    )
    session.commit()


def _add_target(session, *, settlement_id, company_id=COMPANY_ID):
    session.add(
        ManualSettlementTarget(
            settlement_id=settlement_id,
            job_id=JOB,
            role="PRIMARY",
            transaction_type="fund_request",
            record_type="result",
            record_id="FR-1",
            record_key="FR-1",
            original_company_id=company_id,
        )
    )
    session.commit()


def _company_row(session, company_id=COMPANY_ID):
    return session.get(Company, company_id)


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------


def test_update_company_name_and_regenerate_name_key(client, session_factory):
    with session_factory() as session:
        _add_company(session)

    response = client.put(
        f"/api/companies/{COMPANY_ID}",
        json={"name": "  New Acme Ltd  "},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["company_id"] == str(COMPANY_ID)
    assert payload["name"] == "New Acme Ltd"
    assert payload["alias"] == "ACME"

    with session_factory() as session:
        company = _company_row(session)
        assert company.name == "New Acme Ltd"
        assert company.name_key == "new acme ltd"


def test_update_company_alias(client, session_factory):
    with session_factory() as session:
        _add_company(session)

    response = client.put(
        f"/api/companies/{COMPANY_ID}",
        json={"name": "Acme Corp", "alias": "  NEW-ALIAS  "},
    )

    assert response.status_code == 200, response.text
    assert response.json()["alias"] == "NEW-ALIAS"
    with session_factory() as session:
        assert _company_row(session).alias == "NEW-ALIAS"


def test_update_company_alias_to_null(client, session_factory):
    with session_factory() as session:
        _add_company(session)

    response = client.put(
        f"/api/companies/{COMPANY_ID}",
        json={"name": "Acme Corp", "alias": None},
    )

    assert response.status_code == 200, response.text
    assert response.json()["alias"] is None
    with session_factory() as session:
        assert _company_row(session).alias is None


def test_update_company_blank_alias_clears_it(client, session_factory):
    with session_factory() as session:
        _add_company(session)

    response = client.put(
        f"/api/companies/{COMPANY_ID}",
        json={"name": "Acme Corp", "alias": "   "},
    )

    assert response.status_code == 200, response.text
    assert response.json()["alias"] is None


def test_update_company_id_never_changes(client, session_factory):
    with session_factory() as session:
        _add_company(session)

    response = client.put(
        f"/api/companies/{COMPANY_ID}",
        json={"name": "Renamed", "alias": "RENAMED"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["company_id"] == str(COMPANY_ID)

    with session_factory() as session:
        assert _company_row(session).id == COMPANY_ID


def test_update_company_updates_updated_at(client, session_factory):
    old = datetime(2020, 1, 1, 0, 0, 0)
    with session_factory() as session:
        _add_company(session, updated_at=old)

    response = client.put(
        f"/api/companies/{COMPANY_ID}",
        json={"name": "Acme Corp", "alias": "ACME"},
    )

    assert response.status_code == 200, response.text
    with session_factory() as session:
        assert _company_row(session).updated_at > old


@pytest.mark.parametrize("blank_name", ["", "   ", "\t\n"])
def test_update_company_rejects_blank_name(client, session_factory, blank_name):
    with session_factory() as session:
        _add_company(session)

    response = client.put(
        f"/api/companies/{COMPANY_ID}",
        json={"name": blank_name},
    )

    assert response.status_code == 422
    with session_factory() as session:
        assert _company_row(session).name == "Acme Corp"


def test_update_company_rejects_duplicate_name(client, session_factory):
    other_id = uuid.uuid4()
    with session_factory() as session:
        _add_company(session)
        _add_company(
            session,
            company_id=other_id,
            name="Beta Corp",
            alias="BETA",
        )

    response = client.put(
        f"/api/companies/{COMPANY_ID}",
        json={"name": "  beta CORP  "},
    )

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"].lower()

    with session_factory() as session:
        assert _company_row(session).name == "Acme Corp"
        assert _company_row(session).name_key == "acme corp"
        assert _company_row(session, other_id).name == "Beta Corp"


def test_update_company_keeps_own_name_when_unchanged(client, session_factory):
    with session_factory() as session:
        _add_company(session)

    response = client.put(
        f"/api/companies/{COMPANY_ID}",
        json={"name": "  ACME CORP  ", "alias": None},
    )

    assert response.status_code == 200, response.text
    with session_factory() as session:
        assert _company_row(session).name == "ACME CORP"


def test_update_company_returns_404_for_unknown_id(client):
    response = client.put(
        f"/api/companies/{uuid.uuid4()}",
        json={"name": "Nobody"},
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_unreferenced_company(client, session_factory):
    with session_factory() as session:
        _add_company(session)

    response = client.delete(f"/api/companies/{COMPANY_ID}")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "company_id": str(COMPANY_ID),
        "deleted": True,
    }

    with session_factory() as session:
        assert _company_row(session) is None
    assert client.get(f"/api/companies/{COMPANY_ID}").status_code == 404


def test_delete_company_returns_404_for_unknown_id(client):
    response = client.delete(f"/api/companies/{uuid.uuid4()}")

    assert response.status_code == 404


def test_delete_company_referenced_by_manual_settlement(
    client,
    session_factory,
):
    with session_factory() as session:
        _add_company(session)
        settlement_id = _add_settlement(session)

    response = client.delete(f"/api/companies/{COMPANY_ID}")

    assert response.status_code == 409
    assert "manual settlements" in response.json()["detail"]

    with session_factory() as session:
        assert _company_row(session) is not None
        assert session.get(ManualSettlement, settlement_id) is not None


def test_delete_company_referenced_by_reconciliation_result(
    client,
    session_factory,
):
    with session_factory() as session:
        _add_company(session)
        _add_result(session)

    response = client.delete(f"/api/companies/{COMPANY_ID}")

    assert response.status_code == 409
    assert "reconciliation results" in response.json()["detail"]

    with session_factory() as session:
        assert _company_row(session) is not None
        assert session.scalar(select(ReconciliationResult.id)) is not None


def test_delete_company_referenced_by_settlement_target(
    client,
    session_factory,
):
    with session_factory() as session:
        _add_company(session)
        _add_company(
            session,
            company_id=OTHER_COMPANY_ID,
            name="Beta Corp",
            alias=None,
        )
        settlement_id = _add_settlement(session, company_id=OTHER_COMPANY_ID)
        _add_target(session, settlement_id=settlement_id)

    response = client.delete(f"/api/companies/{COMPANY_ID}")

    assert response.status_code == 409
    assert "manual settlement targets" in response.json()["detail"]

    with session_factory() as session:
        assert _company_row(session) is not None
        assert session.scalar(select(ManualSettlementTarget.id)) is not None
        assert session.get(ManualSettlement, settlement_id) is not None


def test_deprecated_company_reference_still_blocks_delete(
    client,
    session_factory,
):
    """A reversed settlement's original company pointer is still history."""

    with session_factory() as session:
        _add_company(session)
        settlement_id = _add_settlement(session)
        _add_target(session, settlement_id=settlement_id)
        session.get(ManualSettlement, settlement_id).status = "REVERSED"
        session.commit()

    response = client.delete(f"/api/companies/{COMPANY_ID}")

    assert response.status_code == 409
    with session_factory() as session:
        assert _company_row(session) is not None


def test_delete_conflict_leaves_other_companies_and_history_intact(
    client,
    session_factory,
):
    other_id = uuid.uuid4()
    with session_factory() as session:
        _add_company(session)
        _add_company(session, company_id=other_id, name="Beta Corp", alias=None)
        _add_result(session)

    response = client.delete(f"/api/companies/{COMPANY_ID}")

    assert response.status_code == 409
    with session_factory() as session:
        assert _company_row(session) is not None
        assert _company_row(session, other_id) is not None
        assert session.scalar(select(ReconciliationResult.id)) is not None


# ---------------------------------------------------------------------------
# Regression: existing company APIs
# ---------------------------------------------------------------------------


def test_existing_company_list_create_get_still_work(client):
    created = client.post(
        "/api/companies",
        json={"name": "  Zeta Ltd  ", "alias": "  ZED  "},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company_id"]

    listed = client.get("/api/companies")
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1

    fetched = client.get(f"/api/companies/{company_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["name"] == "Zeta Ltd"
    assert fetched.json()["alias"] == "ZED"


def test_delete_then_create_same_name_is_allowed(client, session_factory):
    with session_factory() as session:
        _add_company(session)

    assert client.delete(f"/api/companies/{COMPANY_ID}").status_code == 200

    recreated = client.post(
        "/api/companies",
        json={"name": "Acme Corp", "alias": None},
    )
    assert recreated.status_code == 201, recreated.text
    assert recreated.json()["company_id"] != str(COMPANY_ID)


# ---------------------------------------------------------------------------
# Listing with a client-controlled page size
# ---------------------------------------------------------------------------


def _seed_paginated_companies(session):
    names = [f"Acme {i:03d}" for i in range(103)] + [
        f"Beta {i:03d}" for i in range(103, 105)
    ]
    session.add_all(
        Company(
            id=uuid.uuid4(),
            name=name,
            name_key=name.casefold(),
            alias="ZZTOKEN" if name == "Beta 104" else None,
        )
        for name in names
    )
    session.commit()
    return names


def test_list_companies_defaults_to_100_when_limit_omitted(
    client,
    session_factory,
):
    with session_factory() as session:
        _seed_paginated_companies(session)

    response = client.get("/api/companies", params={"page": 1})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 105
    assert payload["limit"] == 100
    assert len(payload["items"]) == 100
    assert payload["items"][0]["name"] == "Acme 000"
    assert payload["items"][-1]["name"] == "Acme 099"


def test_list_companies_page_two_returns_next_page(client, session_factory):
    with session_factory() as session:
        _seed_paginated_companies(session)

    response = client.get("/api/companies", params={"page": 2})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["page"] == 2
    assert len(payload["items"]) == 5
    assert [item["name"] for item in payload["items"]] == [
        "Acme 100",
        "Acme 101",
        "Acme 102",
        "Beta 103",
        "Beta 104",
    ]


def test_list_companies_search_works_with_pagination(client, session_factory):
    with session_factory() as session:
        _seed_paginated_companies(session)

    first = client.get(
        "/api/companies",
        params={"search": "acme", "page": 1},
    )
    assert first.status_code == 200, first.text
    assert first.json()["total"] == 103
    assert len(first.json()["items"]) == 100

    second = client.get(
        "/api/companies",
        params={"search": "acme", "page": 2},
    )
    assert [item["name"] for item in second.json()["items"]] == [
        "Acme 100",
        "Acme 101",
        "Acme 102",
    ]

    beta = client.get("/api/companies", params={"search": "beta"})
    assert beta.json()["total"] == 2
    assert [item["name"] for item in beta.json()["items"]] == [
        "Beta 103",
        "Beta 104",
    ]

    by_alias = client.get("/api/companies", params={"search": "zztoken"})
    assert [item["name"] for item in by_alias.json()["items"]] == ["Beta 104"]


def test_list_companies_limit_100(client, session_factory):
    with session_factory() as session:
        _seed_paginated_companies(session)

    response = client.get("/api/companies", params={"limit": 100})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["limit"] == 100
    assert len(payload["items"]) == 100
    assert payload["total_pages"] == 2


@pytest.mark.parametrize("limit", [500, 1000])
def test_list_companies_large_limit_returns_all_available(
    client,
    session_factory,
    limit,
):
    with session_factory() as session:
        _seed_paginated_companies(session)

    response = client.get("/api/companies", params={"limit": limit})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["limit"] == limit
    assert payload["total"] == 105
    assert len(payload["items"]) == 105
    assert payload["total_pages"] == 1
    assert payload["has_next"] is False


def _seed_many_companies(session, count):
    session.add_all(
        Company(
            id=uuid.uuid4(),
            name=name,
            name_key=name.casefold(),
            alias=None,
        )
        for name in (f"Acme {i:04d}" for i in range(count))
    )
    session.commit()


def test_list_companies_page_two_with_large_limit(client, session_factory):
    with session_factory() as session:
        _seed_many_companies(session, 1005)

    response = client.get(
        "/api/companies",
        params={"page": 2, "limit": 500},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["page"] == 2
    assert payload["limit"] == 500
    assert payload["total"] == 1005
    assert payload["total_pages"] == 3
    assert payload["has_next"] is True
    assert payload["has_previous"] is True
    assert len(payload["items"]) == 500
    assert payload["items"][0]["name"] == "Acme 0500"
    assert payload["items"][-1]["name"] == "Acme 0999"


def test_list_companies_search_with_custom_limit(client, session_factory):
    with session_factory() as session:
        _seed_paginated_companies(session)

    first = client.get(
        "/api/companies",
        params={"search": "acme", "limit": 50},
    )
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["total"] == 103
    assert payload["limit"] == 50
    assert payload["total_pages"] == 3
    assert payload["has_next"] is True
    assert len(payload["items"]) == 50

    third = client.get(
        "/api/companies",
        params={"search": "acme", "limit": 50, "page": 3},
    )
    assert [item["name"] for item in third.json()["items"]] == [
        "Acme 100",
        "Acme 101",
        "Acme 102",
    ]
    assert third.json()["has_next"] is False
    assert third.json()["has_previous"] is True

    by_alias = client.get(
        "/api/companies",
        params={"search": "zztoken", "limit": 500},
    )
    assert by_alias.json()["limit"] == 500
    assert [item["name"] for item in by_alias.json()["items"]] == ["Beta 104"]


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": -1}, {"page": 0}, {"page": 0, "limit": 5}],
)
def test_list_companies_rejects_non_positive_pagination(client, params):
    response = client.get("/api/companies", params=params)

    assert response.status_code == 422


def test_openapi_exposes_unbounded_limit_for_company_list():
    parameters = app.openapi()["paths"]["/api/companies"]["get"][
        "parameters"
    ]
    names = {parameter["name"] for parameter in parameters}

    assert {"page", "search", "limit"} <= names

    limit_schema = next(
        parameter["schema"]
        for parameter in parameters
        if parameter["name"] == "limit"
    )
    assert limit_schema.get("minimum") == 1
    assert "maximum" not in limit_schema


def test_list_companies_pagination_metadata(client, session_factory):
    with session_factory() as session:
        _seed_paginated_companies(session)

    first = client.get("/api/companies", params={"page": 1}).json()
    assert {
        "total": first["total"],
        "page": first["page"],
        "limit": first["limit"],
        "total_pages": first["total_pages"],
        "has_next": first["has_next"],
        "has_previous": first["has_previous"],
    } == {
        "total": 105,
        "page": 1,
        "limit": 100,
        "total_pages": 2,
        "has_next": True,
        "has_previous": False,
    }

    second = client.get("/api/companies", params={"page": 2}).json()
    assert second["total"] == 105
    assert second["page"] == 2
    assert second["limit"] == 100
    assert second["total_pages"] == 2
    assert second["has_next"] is False
    assert second["has_previous"] is True

    empty = client.get("/api/companies", params={"page": 3}).json()
    assert empty["items"] == []
    assert empty["has_next"] is False
    assert empty["has_previous"] is True


@pytest.mark.parametrize(
    "limit,expected_pages,expected_count",
    [(50, 3, 50), (100, 2, 100), (500, 1, 105), (1000, 1, 105)],
)
def test_list_companies_metadata_for_custom_limits(
    client,
    session_factory,
    limit,
    expected_pages,
    expected_count,
):
    with session_factory() as session:
        _seed_paginated_companies(session)

    first = client.get("/api/companies", params={"limit": limit}).json()
    assert first["total"] == 105
    assert first["page"] == 1
    assert first["limit"] == limit
    assert first["total_pages"] == expected_pages
    assert first["has_next"] is (expected_pages > 1)
    assert first["has_previous"] is False
    assert len(first["items"]) == expected_count

    last = client.get(
        "/api/companies",
        params={"page": expected_pages, "limit": limit},
    ).json()
    assert last["page"] == expected_pages
    assert last["limit"] == limit
    assert last["total_pages"] == expected_pages
    assert last["has_next"] is False
    assert last["has_previous"] is (expected_pages > 1)
