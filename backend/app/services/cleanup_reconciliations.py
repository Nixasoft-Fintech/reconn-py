from datetime import datetime, timezone
import logging

from sqlalchemy import delete, select

from app.database.connection import SessionLocal
from app.db_models import ReconciliationJob
from app.db_models.manual_settlement import ManualSettlementTarget

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BATCH_SIZE = 100


def cleanup_expired_reconciliations():
    total_deleted = 0
    now = datetime.now(timezone.utc)

    try:
        while True:
            with SessionLocal.begin() as db:
                expired_jobs = db.scalars(
                    select(ReconciliationJob)
                    .where(ReconciliationJob.expires_at <= now)
                    .order_by(ReconciliationJob.expires_at)
                    .limit(BATCH_SIZE)
                    .with_for_update(skip_locked=True)
                ).all()

                if not expired_jobs:
                    break

                job_ids = [job.job_id for job in expired_jobs]

                # Explicitly remove settlement targets because
                # their job_id column has no foreign key.
                db.execute(
                    delete(ManualSettlementTarget).where(
                        ManualSettlementTarget.job_id.in_(job_ids)
                    )
                )

                # Delete parent jobs.
                # PostgreSQL cascades remove related records,
                # including manual settlements and their targets.
                for job in expired_jobs:
                    logger.info(
                        "Deleting expired reconciliation: %s",
                        job.job_id,
                    )
                    db.delete(job)

                batch_count = len(expired_jobs)

            total_deleted += batch_count

        logger.info(
            "Cleanup completed. Deleted %s reconciliation jobs.",
            total_deleted,
        )

    except Exception:
        logger.exception("Reconciliation cleanup failed")
        raise


if __name__ == "__main__":
    cleanup_expired_reconciliations()
