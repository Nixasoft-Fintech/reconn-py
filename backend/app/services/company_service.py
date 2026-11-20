"""Company master-data operations.

Company names are unique after trimming and case folding.  The ``name_key``
column carries the database-level guarantee; the service also performs a
friendly pre-check and translates an integrity error into a 409 response.
"""

from __future__ import annotations

import uuid

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db_models import (
    Company,
    ManualSettlement,
    ManualSettlementTarget,
    ReconciliationResult,
)
from app.services.errors import ConflictError, NotFoundError


def normalize_company_name(name: str) -> str:
    """Return the canonical uniqueness key for a company name."""

    return name.strip().casefold()


def serialize_company(company: Company) -> dict[str, Any]:
    return {
        "company_id": str(company.id),
        "name": company.name,
        "alias": company.alias,
        "created_at": company.created_at.isoformat()
        if company.created_at
        else None,
        "updated_at": company.updated_at.isoformat()
        if company.updated_at
        else None,
    }


def create_company(
    session: Session,
    *,
    name: str,
    alias: str | None,
) -> Company:
    """Create one company, rejecting a case-insensitive duplicate name."""

    clean_name = name.strip()
    name_key = normalize_company_name(clean_name)

    existing = session.scalar(
        select(Company).where(Company.name_key == name_key)
    )

    if existing is not None:
        raise ConflictError(
            f"A company named '{clean_name}' already exists."
        )

    company = Company(
        id=uuid.uuid4(),
        name=clean_name,
        name_key=name_key,
        alias=alias or None,
    )
    session.add(company)

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(
            f"A company named '{clean_name}' already exists."
        ) from exc
    except Exception:
        session.rollback()
        raise

    session.refresh(company)
    return company


def get_company(
    session: Session,
    company_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Company:
    statement = select(Company).where(Company.id == company_id)

    if for_update:
        statement = statement.with_for_update()

    company = session.scalar(statement)

    if company is None:
        raise NotFoundError(f"Company '{company_id}' was not found.")

    return company


def list_companies(
    session: Session,
    *,
    page: int = 1,
    limit: int = 100,
    search: str | None = None,
) -> tuple[int, list[Company]]:
    """Return a page of companies, sorted alphabetically by name."""

    filters = []

    if search is not None and search.strip():
        pattern = f"%{search.strip().casefold()}%"
        filters.append(
            func.lower(Company.name).like(pattern)
            | func.lower(func.coalesce(Company.alias, "")).like(pattern)
        )

    total = int(
        session.scalar(
            select(func.count()).select_from(Company).where(*filters)
        )
        or 0
    )

    offset = (page - 1) * limit
    statement = (
        select(Company)
        .where(*filters)
        .order_by(func.lower(Company.name).asc(), Company.id.asc())
        .offset(offset)
        .limit(limit)
    )

    return total, list(session.scalars(statement))


def update_company(
    session: Session,
    company_id: uuid.UUID,
    *,
    name: str,
    alias: str | None,
    alias_supplied: bool,
) -> Company:
    """Rename one company and optionally change its alias.

    The company id is never changed.  ``name_key`` is regenerated from the
    trimmed name so the database uniqueness guarantee stays in sync.  An alias
    that was omitted from the request is left untouched; an explicit ``null``
    (or blank string) clears it.
    """

    clean_name = name.strip()
    name_key = normalize_company_name(clean_name)

    try:
        company = get_company(session, company_id, for_update=True)

        duplicate = session.scalar(
            select(Company.id).where(
                Company.name_key == name_key,
                Company.id != company_id,
            )
        )

        if duplicate is not None:
            raise ConflictError(
                f"A company named '{clean_name}' already exists."
            )

        company.name = clean_name
        company.name_key = name_key
        if alias_supplied:
            company.alias = alias or None

        # Set explicitly so even a PUT with unchanged values bumps the
        # timestamp; a plain ``onupdate`` would skip a value-less UPDATE.
        company.updated_at = func.now()

        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(
            f"A company named '{clean_name}' already exists."
        ) from exc
    except Exception:
        session.rollback()
        raise

    session.refresh(company)
    return company


def delete_company(
    session: Session,
    company_id: uuid.UUID,
) -> uuid.UUID:
    """Delete one company only when no reconciliation history references it.

    Reconciliation jobs do not carry a company column of their own; their link
    is represented by the results and manual settlements that belong to them.
    Those records (plus the original company pointer kept on settlement
    targets) are all checked before the delete so no history is ever cascaded.
    """

    references = (
        (ManualSettlement.company_id, "manual settlements"),
        (
            ManualSettlementTarget.original_company_id,
            "manual settlement targets",
        ),
        (ReconciliationResult.company_id, "reconciliation results"),
    )

    try:
        company = get_company(session, company_id, for_update=True)

        for column, label in references:
            found = session.scalar(
                select(column).where(column == company_id).limit(1)
            )
            if found is not None:
                raise ConflictError(
                    f"Company '{company_id}' is referenced by {label} "
                    "and cannot be deleted."
                )

        session.delete(company)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(
            f"Company '{company_id}' is referenced by existing records "
            "and cannot be deleted."
        ) from exc
    except Exception:
        session.rollback()
        raise

    return company_id
