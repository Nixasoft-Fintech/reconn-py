"""Manual settlement and reversal for persisted reconciliation records.

A manual settlement links one Fund Request or VA Transaction to one bank row.
Exactly one side is a bank row.  Bank-first settlement requires the caller to
select the source counterpart explicitly.  Automatic matching evidence is never
rewritten; only the effective status and the manual overlay columns change.

Both affected records are updated in one transaction.  The original values are
stored on ``manual_settlement_targets`` so a reversal can restore the exact
pre-settlement state.
"""

from __future__ import annotations

import uuid

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db_models import (
    Company,
    ManualSettlement,
    ManualSettlementTarget,
    ReconciliationJob,
    ReconciliationResult,
    UnmatchedBankTransaction,
)
from app.schemas.manual_settlement import (
    ManualSettlementCreate,
    TransactionReference,
)
from app.services.company_service import get_company, serialize_company
from app.services.errors import ConflictError, NotFoundError, ValidationError
from app.services.reconciliation_query_service import COMPLETED_JOB_STATUS
from app.services.summary_service import recompute_job_summary

ELIGIBLE_SOURCE_STATUSES = {"NOT_FOUND", "AMBIGUOUS", "AMOUNT_MISMATCH"}
MANUAL_SETTLEMENT_PREFIX = "Manually Settled"

BANK_FIELDS = (
    "bank_row_number",
    "bank_name",
    "bank_utr",
    "bank_reference",
    "bank_amount",
    "bank_signed_amount",
    "bank_user",
    "bank_date",
    "bank_value_date",
    "bank_narration",
    "bank_direction",
    "bank_account_number",
    "bank_account_holder",
    "bank_format",
    "bank_source_filename",
    "bank_extracted_references",
    "bank_data",
)

_BANK_DATETIME_FIELDS = {"bank_date", "bank_value_date"}
_BANK_DECIMAL_FIELDS = {
    "bank_amount",
    "bank_signed_amount",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _bank_key(
    filename: str | None,
    row_number: int | None,
) -> str:
    """Build the human-readable bank key used in settlement history."""

    if row_number is None:
        return filename or "bank"

    return f"{filename or ''}#{row_number}"


def _unmatched_bank_id(ref: TransactionReference) -> int:
    """Return the numeric unmatched bank id carried by a bank reference."""

    if not ref.transaction_id:
        raise ValidationError(
            "A bank transaction that is not already linked to the selected "
            "source record must be referenced by transaction_id."
        )

    try:
        bank_id = int(ref.transaction_id)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "transaction_id must be the positive numeric id of an unmatched "
            "bank transaction."
        ) from exc

    if bank_id < 1:
        raise ValidationError(
            "transaction_id must be the positive numeric id of an unmatched "
            "bank transaction."
        )

    return bank_id


def _snapshot_from_result(result: ReconciliationResult) -> dict[str, Any]:
    return {
        field: _json_safe(getattr(result, field)) for field in BANK_FIELDS
    }


def _snapshot_from_unmatched(
    row: UnmatchedBankTransaction,
) -> dict[str, Any]:
    return {
        "bank_row_number": row.statement_row_number,
        "bank_name": row.bank_name,
        "bank_utr": row.utr,
        "bank_reference": row.reference,
        "bank_amount": _json_safe(row.amount),
        "bank_signed_amount": _json_safe(row.signed_amount),
        "bank_user": None,
        "bank_date": _json_safe(row.transaction_date),
        "bank_value_date": _json_safe(row.value_date),
        "bank_narration": row.narration,
        "bank_direction": row.direction,
        "bank_account_number": row.bank_account_number,
        "bank_account_holder": row.account_holder,
        "bank_format": row.bank_format,
        "bank_source_filename": row.source_filename,
        "bank_extracted_references": _json_safe(
            row.extracted_references
        ),
        "bank_data": _json_safe(row.raw_data),
    }


def _apply_bank_snapshot(
    result: ReconciliationResult,
    snapshot: dict[str, Any] | None,
) -> None:
    if snapshot is None:
        return

    for field in BANK_FIELDS:
        value = snapshot.get(field)

        if value is not None and field in _BANK_DATETIME_FIELDS:
            value = datetime.fromisoformat(str(value))

        if value is not None and field in _BANK_DECIMAL_FIELDS:
            value = Decimal(str(value))

        setattr(result, field, value)


def _require_completed_job(
    session: Session,
    job_id: uuid.UUID,
    *,
    for_update: bool = True,
) -> ReconciliationJob:
    job = session.get(
        ReconciliationJob,
        job_id,
        with_for_update=for_update,
    )

    if job is None:
        raise NotFoundError(
            f"Reconciliation job '{job_id}' was not found."
        )

    if (job.status or "").strip().upper() != COMPLETED_JOB_STATUS:
        raise ConflictError(
            f"Reconciliation job '{job_id}' is not completed "
            f"(status: {job.status})."
        )

    return job


def _active_target_exists(
    session: Session,
    job_id: uuid.UUID,
    *,
    transaction_type: str,
    record_type: str,
    record_id: str,
) -> bool:
    return (
        session.scalar(
            select(ManualSettlementTarget.id).where(
                ManualSettlementTarget.job_id == job_id,
                ManualSettlementTarget.transaction_type == transaction_type,
                ManualSettlementTarget.record_type == record_type,
                ManualSettlementTarget.record_id == record_id,
                ManualSettlementTarget.active.is_(True),
            )
        )
        is not None
    )


def _resolve_source(
    session: Session,
    job_id: uuid.UUID,
    ref: TransactionReference,
) -> ReconciliationResult:
    statement = (
        select(ReconciliationResult)
        .where(
            ReconciliationResult.job_id == job_id,
            ReconciliationResult.source_type == ref.transaction_type,
            ReconciliationResult.source_transaction_id
            == ref.transaction_id,
        )
        .with_for_update()
    )

    rows = list(session.scalars(statement))

    if not rows:
        raise NotFoundError(
            f"{ref.transaction_type} '{ref.transaction_id}' was not found "
            f"in job '{job_id}'."
        )

    if len(rows) > 1:
        raise ConflictError(
            f"Multiple {ref.transaction_type} records use transaction id "
            f"'{ref.transaction_id}'."
        )

    result = rows[0]

    if _active_target_exists(
        session,
        job_id,
        transaction_type=result.source_type,
        record_type="source_result",
        record_id=str(result.id),
    ):
        raise ConflictError(
            f"{ref.transaction_type} '{ref.transaction_id}' already has an "
            "active manual settlement."
        )

    status = (result.final_status or "").strip().upper()

    if status not in ELIGIBLE_SOURCE_STATUSES:
        raise ValidationError(
            f"{ref.transaction_type} '{ref.transaction_id}' has status "
            f"'{result.final_status}' and is not eligible for manual "
            "settlement. Eligible statuses are: "
            f"{', '.join(sorted(ELIGIBLE_SOURCE_STATUSES))}."
        )

    return result


def _resolve_unmatched_bank(
    session: Session,
    job_id: uuid.UUID,
    bank_id: int,
) -> UnmatchedBankTransaction:
    """Lock and return one eligible unmatched bank row for this job.

    The row is identified by its primary key, never by its filename or
    statement row number.  Rows already claimed by an active settlement are
    rejected, and rows from another job are reported as not found.
    """

    row = session.scalar(
        select(UnmatchedBankTransaction)
        .where(
            UnmatchedBankTransaction.id == bank_id,
            UnmatchedBankTransaction.job_id == job_id,
            UnmatchedBankTransaction.active_settlement_id.is_(None),
        )
        .with_for_update()
    )

    if row is not None:
        return row

    existing = session.scalar(
        select(UnmatchedBankTransaction).where(
            UnmatchedBankTransaction.id == bank_id,
            UnmatchedBankTransaction.job_id == job_id,
        )
    )

    if existing is not None:
        raise ConflictError(
            f"Bank transaction '{bank_id}' already has an active manual "
            "settlement."
        )

    raise NotFoundError(
        f"Bank transaction '{bank_id}' was not found in job '{job_id}'."
    )


def _guard_bank_target(
    session: Session,
    job_id: uuid.UUID,
    *,
    unmatched_id: int | None = None,
    linked_key: str | None = None,
) -> None:
    """Reject a bank row that an active settlement already claims."""

    if unmatched_id is not None and _active_target_exists(
        session,
        job_id,
        transaction_type="bank",
        record_type="unmatched_bank",
        record_id=str(unmatched_id),
    ):
        raise ConflictError(
            f"Bank transaction '{unmatched_id}' already has an active manual "
            "settlement."
        )

    if linked_key is not None and _active_target_exists(
        session,
        job_id,
        transaction_type="bank",
        record_type="linked_bank",
        record_id=linked_key,
    ):
        raise ConflictError(
            f"Bank transaction '{linked_key}' already has an active manual "
            "settlement."
        )


def _resolve_bank_counterpart(
    session: Session,
    job_id: uuid.UUID,
    ref: TransactionReference,
    source: ReconciliationResult,
) -> tuple[str, UnmatchedBankTransaction | None, dict[str, Any], str]:
    """Resolve the bank side for a source-primary settlement.

    A source that already carries a linked bank row is settled against that
    row.  Otherwise the caller must supply the unmatched bank id.
    """

    if source.bank_row_number is not None:
        if ref.transaction_id is not None:
            raise ValidationError(
                "The selected source record already has a linked bank row. "
                "Omit transaction_id from the bank reference."
            )

        linked_key = _bank_key(
            source.bank_source_filename,
            source.bank_row_number,
        )
        _guard_bank_target(session, job_id, linked_key=linked_key)

        return (
            "linked_bank",
            None,
            _snapshot_from_result(source),
            linked_key,
        )

    bank_id = _unmatched_bank_id(ref)
    unmatched = _resolve_unmatched_bank(session, job_id, bank_id)
    bank_key = _bank_key(
        unmatched.source_filename,
        unmatched.statement_row_number,
    )
    _guard_bank_target(
        session,
        job_id,
        unmatched_id=unmatched.id,
        linked_key=bank_key,
    )

    return (
        "unmatched_bank",
        unmatched,
        _snapshot_from_unmatched(unmatched),
        bank_key,
    )


def _resolve_bank_primary(
    session: Session,
    job_id: uuid.UUID,
    ref: TransactionReference,
) -> tuple[UnmatchedBankTransaction, dict[str, Any], str]:
    """Resolve the bank side for a bank-primary settlement."""

    bank_id = _unmatched_bank_id(ref)
    unmatched = _resolve_unmatched_bank(session, job_id, bank_id)
    bank_key = _bank_key(
        unmatched.source_filename,
        unmatched.statement_row_number,
    )
    _guard_bank_target(
        session,
        job_id,
        unmatched_id=unmatched.id,
        linked_key=bank_key,
    )

    return unmatched, _snapshot_from_unmatched(unmatched), bank_key


def _effective_remarks(remarks: str | None) -> str:
    if remarks:
        return f"{MANUAL_SETTLEMENT_PREFIX}: {remarks}"
    return MANUAL_SETTLEMENT_PREFIX


def _add_source_target(
    session: Session,
    *,
    settlement_id: uuid.UUID,
    job_id: uuid.UUID,
    role: str,
    source: ReconciliationResult,
    original_status: str | None,
    original_match_type: str | None,
    original_company_id: uuid.UUID | None,
    original_settlement_remarks: str | None,
    original_bank_snapshot: dict[str, Any],
) -> None:
    session.add(
        ManualSettlementTarget(
            settlement_id=settlement_id,
            job_id=job_id,
            role=role,
            transaction_type=source.source_type,
            record_type="source_result",
            record_id=str(source.id),
            record_key=(
                source.source_transaction_id or f"result:{source.id}"
            ),
            result_id=source.id,
            original_status=original_status,
            original_match_type=original_match_type,
            original_company_id=original_company_id,
            original_settlement_remarks=original_settlement_remarks,
            original_bank_snapshot=original_bank_snapshot,
            original_active_settlement_id=None,
            active=True,
        )
    )


def serialize_target(
    session: Session,
    target: ManualSettlementTarget,
    settlement_status: str,
) -> dict[str, Any]:
    if target.record_type == "source_result":
        result = (
            session.get(ReconciliationResult, target.result_id)
            if target.result_id is not None
            else None
        )
        current_status = result.final_status if result is not None else None
    elif target.record_type == "unmatched_bank":
        current_status = (
            "MATCHED" if settlement_status == "ACTIVE" else "NOT_FOUND"
        )
    elif target.record_type == "linked_bank":
        current_status = "MATCHED" if settlement_status == "ACTIVE" else "LINKED"
    else:
        current_status = None

    return {
        "role": target.role,
        "transaction_type": target.transaction_type,
        "record_type": target.record_type,
        "record_id": target.record_id,
        "record_key": target.record_key,
        "original_status": target.original_status,
        "current_status": current_status,
        "active": target.active,
    }


def serialize_settlement(
    session: Session,
    settlement: ManualSettlement,
) -> dict[str, Any]:
    company = session.get(Company, settlement.company_id)

    return {
        "settlement_id": str(settlement.id),
        "job_id": str(settlement.job_id),
        "status": settlement.status,
        "match_type": "MANUAL",
        "company": (
            serialize_company(company) if company is not None else None
        ),
        "primary": {
            "transaction_type": settlement.primary_transaction_type,
            "record_key": settlement.primary_record_key,
        },
        "counterpart": {
            "transaction_type": settlement.counterpart_transaction_type,
            "record_key": settlement.counterpart_record_key,
        },
        "remarks": settlement.remarks,
        "settlement_remarks": settlement.settlement_remarks,
        "settled_by": settlement.settled_by,
        "settled_at": settlement.settled_at.isoformat()
        if settlement.settled_at
        else None,
        "reversed_by": settlement.reversed_by,
        "reversed_at": settlement.reversed_at.isoformat()
        if settlement.reversed_at
        else None,
        "reversal_reason": settlement.reversal_reason,
        "targets": [
            serialize_target(session, target, settlement.status)
            for target in settlement.targets
        ],
    }


def create_manual_settlement(
    session: Session,
    job_id: uuid.UUID,
    payload: ManualSettlementCreate,
) -> ManualSettlement:
    """Settle one source record against one bank row atomically."""

    try:
        _require_completed_job(session, job_id)
        company = get_company(
            session,
            payload.company_id,
            for_update=True,
        )

        primary = payload.primary
        counterpart = payload.counterpart

        if primary.transaction_type == "bank":
            bank_ref = primary
            source_ref = counterpart
            source_role = "COUNTERPART"
            bank_role = "PRIMARY"

            source = _resolve_source(session, job_id, source_ref)

            if source.bank_row_number is not None:
                raise ValidationError(
                    "The selected source record already has a linked bank "
                    "row and cannot be settled against a different bank."
                )

            (
                unmatched,
                unmatched_snapshot,
                bank_key,
            ) = _resolve_bank_primary(session, job_id, bank_ref)
            bank_kind = "unmatched_bank"
        else:
            source_ref = primary
            bank_ref = counterpart
            source_role = "PRIMARY"
            bank_role = "COUNTERPART"

            source = _resolve_source(session, job_id, source_ref)
            (
                bank_kind,
                unmatched,
                unmatched_snapshot,
                bank_key,
            ) = _resolve_bank_counterpart(session, job_id, bank_ref, source)

        original_status = source.final_status
        original_match_type = source.match_type
        original_company_id = source.company_id
        original_settlement_remarks = source.settlement_remarks
        original_bank_snapshot = _snapshot_from_result(source)

        if bank_kind == "unmatched_bank":
            _apply_bank_snapshot(source, unmatched_snapshot)

        settlement_id = uuid.uuid4()
        effective_remarks = _effective_remarks(payload.remarks)
        now = _now()

        settlement = ManualSettlement(
            id=settlement_id,
            job_id=job_id,
            company_id=company.id,
            status="ACTIVE",
            primary_transaction_type=primary.transaction_type,
            primary_record_key=(
                bank_key
                if primary.transaction_type == "bank"
                else (primary.transaction_id or "")
            ),
            counterpart_transaction_type=counterpart.transaction_type,
            counterpart_record_key=(
                bank_key
                if counterpart.transaction_type == "bank"
                else (counterpart.transaction_id or "")
            ),
            remarks=payload.remarks,
            settlement_remarks=effective_remarks,
            settled_by=None,
            settled_at=now,
        )
        session.add(settlement)

        source.final_status = "MATCHED"
        source.match_type = "MANUAL"
        source.company_id = company.id
        source.settlement_remarks = effective_remarks

        if bank_kind == "unmatched_bank":
            assert unmatched is not None
            unmatched.active_settlement_id = settlement_id

        _add_source_target(
            session,
            settlement_id=settlement_id,
            job_id=job_id,
            role=source_role,
            source=source,
            original_status=original_status,
            original_match_type=original_match_type,
            original_company_id=original_company_id,
            original_settlement_remarks=original_settlement_remarks,
            original_bank_snapshot=original_bank_snapshot,
        )

        if bank_kind == "unmatched_bank":
            assert unmatched is not None
            session.add(
                ManualSettlementTarget(
                    settlement_id=settlement_id,
                    job_id=job_id,
                    role=bank_role,
                    transaction_type="bank",
                    record_type="unmatched_bank",
                    record_id=str(unmatched.id),
                    record_key=bank_key,
                    unmatched_bank_id=unmatched.id,
                    original_status="NOT_FOUND",
                    original_active_settlement_id=None,
                    active=True,
                )
            )
        else:
            session.add(
                ManualSettlementTarget(
                    settlement_id=settlement_id,
                    job_id=job_id,
                    role=bank_role,
                    transaction_type="bank",
                    record_type="linked_bank",
                    record_id=bank_key,
                    record_key=bank_key,
                    result_id=source.id,
                    original_status=original_status,
                    active=True,
                )
            )

        session.flush()
        recompute_job_summary(session, job_id)
        session.commit()
        session.refresh(settlement)
        return settlement

    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(
            "One of the selected transactions already has an active manual "
            "settlement."
        ) from exc
    except Exception:
        session.rollback()
        raise


def list_manual_settlements(
    session: Session,
    job_id: uuid.UUID,
    *,
    page: int = 1,
    limit: int = 100,
) -> tuple[int, list[ManualSettlement]]:
    _require_completed_job(session, job_id, for_update=False)

    total = int(
        session.scalar(
            select(func.count())
            .select_from(ManualSettlement)
            .where(ManualSettlement.job_id == job_id)
        )
        or 0
    )

    offset = (page - 1) * limit
    items = list(
        session.scalars(
            select(ManualSettlement)
            .where(ManualSettlement.job_id == job_id)
            .order_by(
                ManualSettlement.settled_at.desc(),
                ManualSettlement.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
    )

    return total, items


def reverse_manual_settlement(
    session: Session,
    settlement_id: uuid.UUID,
    *,
    reason: str | None,
) -> ManualSettlement:
    """Restore both records and preserve the settlement audit trail."""

    try:
        settlement = session.scalar(
            select(ManualSettlement)
            .where(ManualSettlement.id == settlement_id)
            .with_for_update()
        )

        if settlement is None:
            raise NotFoundError(
                f"Manual settlement '{settlement_id}' was not found."
            )

        if (settlement.status or "").upper() != "ACTIVE":
            raise ConflictError(
                f"Manual settlement '{settlement_id}' is not active "
                f"(status: {settlement.status})."
            )

        for target in settlement.targets:
            if target.record_type == "source_result":
                result = (
                    session.get(
                        ReconciliationResult,
                        target.result_id,
                        with_for_update=True,
                    )
                    if target.result_id is not None
                    else None
                )

                if result is None:
                    raise ConflictError(
                        "The settled source record no longer exists and cannot "
                        "be reversed."
                    )

                result.final_status = target.original_status
                result.match_type = target.original_match_type
                result.company_id = target.original_company_id
                result.settlement_remarks = (
                    target.original_settlement_remarks
                )
                _apply_bank_snapshot(result, target.original_bank_snapshot)

            elif target.record_type == "unmatched_bank":
                unmatched = (
                    session.get(
                        UnmatchedBankTransaction,
                        target.unmatched_bank_id,
                        with_for_update=True,
                    )
                    if target.unmatched_bank_id is not None
                    else None
                )

                if unmatched is None:
                    raise ConflictError(
                        "The settled bank record no longer exists and cannot be "
                        "reversed."
                    )

                unmatched.active_settlement_id = (
                    target.original_active_settlement_id
                )

            target.active = False

        settlement.status = "REVERSED"
        settlement.reversed_by = None
        settlement.reversed_at = _now()
        settlement.reversal_reason = reason

        session.flush()
        recompute_job_summary(session, settlement.job_id)
        session.commit()
        session.refresh(settlement)
        return settlement

    except Exception:
        session.rollback()
        raise
