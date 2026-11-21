import uuid
from datetime import datetime, timedelta, timezone

from app.database.connection import SessionLocal
from app.db_models.reconciliation_job import ReconciliationJob
from app.db_models.reconciliation_result import ReconciliationResult
from app.db_models.unmatched_bank import UnmatchedBankTransaction
from decimal import Decimal
from app.db_models.manual_settlement import (
    ManualSettlement,
    ManualSettlementTarget,
)

test_job_id = uuid.uuid4()
now = datetime.now(timezone.utc)

with SessionLocal.begin() as session:
    job = ReconciliationJob(
        job_id=test_job_id,
        user_id="6330bd76-26d7-46fd-951d-f1be2984d132",
        status="COMPLETED",
        fund_request_filename="test_fund.xlsx",
        va_transaction_filename="test_va.xlsx",
        bank_files=[],
        created_at=now - timedelta(days=35),
        expires_at=now - timedelta(days=1),
    )

    session.add(job)

    session.flush()

    result = ReconciliationResult(
        job_id=test_job_id,
        source_type="TEST",
        reference_status="MATCHED",
        amount_status="MATCHED",
        user_status="MATCHED",
        date_status="MATCHED",
        final_status="MATCHED",
    )

    session.add(result)

    unmatched_transaction = UnmatchedBankTransaction(
        job_id=test_job_id,
        bank_name="TEST BANK",
        statement_row_number=1,
        utr="TEST-UTR-001",
        amount=Decimal("500.00"),
        direction="CREDIT",
        raw_data={"test": True},
    )

    session.add(unmatched_transaction)

    # Create a manual settlement
    settlement = ManualSettlement(
        job_id=test_job_id,
        company_id=uuid.UUID("b7583701-7ccd-467e-b84f-a27dba0c7360"),
        status="ACTIVE",
        primary_transaction_type="FUND_REQUEST",
        primary_record_key="TEST-FUND-001",
        counterpart_transaction_type="BANK",
        counterpart_record_key="TEST-BANK-001",
        settlement_remarks="Test settlement for cleanup",
        settled_at=now,
    )

    session.add(settlement)
    session.flush()

    # Create a manual settlement target
    settlement_target = ManualSettlementTarget(
        settlement_id=settlement.id,
        job_id=test_job_id,
        role="PRIMARY",
        transaction_type="FUND_REQUEST",
        record_type="TEST",
        record_id="TEST-RECORD-001",
        record_key="TEST-RECORD-001",
    )

    session.add(settlement_target)

print("Test job:", test_job_id)
print("Test result created successfully.")
