"""Permanent deletion of unmatched bank statement rows.

Only a genuinely unmatched bank row may be removed.  Rows that are claimed by
a manual settlement, referenced by settlement history, or linked to a
persisted reconciliation result are rejected so the deletion can never remove
settlement or matching evidence as a side effect.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db_models import (
    ManualSettlementTarget,
    ReconciliationJob,
    ReconciliationResult,
    UnmatchedBankTransaction,
)
from app.services.errors import ConflictError, NotFoundError
from app.services.reconciliation_query_service import COMPLETED_JOB_STATUS
from app.services.summary_service import recompute_job_summary


def _require_completed_job(
    session: Session,
    job_id: uuid.UUID,
) -> ReconciliationJob:
    """Lock and validate the job that owns the bank row."""

    job = session.get(
        ReconciliationJob,
        job_id,
        with_for_update=True,
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


def _bank_row_is_linked(
    session: Session,
    job_id: uuid.UUID,
    row: UnmatchedBankTransaction,
) -> bool:
    """Return whether settlement history or a result references ``row``."""

    settlement_target = session.scalar(
        select(ManualSettlementTarget.id).where(
            ManualSettlementTarget.job_id == job_id,
            ManualSettlementTarget.unmatched_bank_id == row.id,
        )
    )

    if settlement_target is not None:
        return True

    # Unmatched rows normally have no linked result.  Guard the overlap anyway
    # so a row that was linked by a manual settlement can never be deleted.
    if row.source_filename is None or row.statement_row_number is None:
        return False

    linked_result = session.scalar(
        select(ReconciliationResult.id).where(
            ReconciliationResult.job_id == job_id,
            ReconciliationResult.bank_source_filename == row.source_filename,
            ReconciliationResult.bank_row_number == row.statement_row_number,
        )
    )

    return linked_result is not None


def delete_unmatched_bank_transaction(
    session: Session,
    job_id: uuid.UUID,
    bank_transaction_id: int,
) -> int:
    """Hard delete one unmatched bank row and return its id.

    The job row is locked first, matching manual settlement, so a concurrent
    settlement and deletion cannot both succeed.  The target row is then
    locked and re-validated before it is removed.
    """

    try:
        _require_completed_job(session, job_id)

        row = session.scalar(
            select(UnmatchedBankTransaction)
            .where(
                UnmatchedBankTransaction.id == bank_transaction_id,
                UnmatchedBankTransaction.job_id == job_id,
            )
            .with_for_update()
        )

        if row is None:
            raise NotFoundError(
                f"Bank transaction '{bank_transaction_id}' was not found "
                f"in job '{job_id}'."
            )

        if row.active_settlement_id is not None:
            raise ConflictError(
                f"Bank transaction '{bank_transaction_id}' is settled and "
                "cannot be deleted."
            )

        if _bank_row_is_linked(session, job_id, row):
            raise ConflictError(
                f"Bank transaction '{bank_transaction_id}' is linked to a "
                "settlement or reconciliation result and cannot be deleted."
            )

        session.delete(row)
        session.flush()
        recompute_job_summary(session, job_id)
        session.commit()

        return bank_transaction_id

    except Exception:
        session.rollback()
        raise
