from __future__ import annotations

from fileinput import filename
import json
import re

import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Path as PathParam,
    Query,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.adapters.base import AdapterError
from app.adapters.bank_statement_adapter import (
    BankColumnMapping,
    BankMappingError,
    BankStatementAdapter,
    normalize_bank_statement,
)
from app.adapters.fund_request_adapter import FundRequestAdapter
from app.adapters.va_transaction_adapter import VATransactionAdapter
from app.core.file_reader import (
    FileReadError,
    SUPPORTED_EXTENSIONS,
    read_tabular_file,
    read_bank_statement_file,
)
from app.core.inspection import inspect_report
from app.core.pagination import DEFAULT_LIMIT, DEFAULT_PAGE, LimitParam, PageParam
from app.matching.engine import MatchingConfig, reconcile_transactions
from app.services.csv_export_service import (
    bank_matched_csv,
    bank_unmatched_csv,
    build_export_filename,
    fund_request_csv,
    va_transaction_csv,
)
from app.services.persistence_service import (
    create_pending_job,
    save_reconciliation_background,
    build_frontend_summary,
    save_reconciliation,
    retry_outputs,
)
from app.schemas.company import CompanyCreate
from app.schemas.manual_settlement import (
    ManualSettlementCreate,
    ManualSettlementReversal,
)
from app.services.bank_transaction_service import (
    delete_unmatched_bank_transaction,
)
from app.services.company_service import (
    create_company,
    get_company,
    list_companies,
    serialize_company,
)
from app.services.errors import ServiceError
from app.services.manual_settlement_service import (
    create_manual_settlement,
    list_manual_settlements,
    reverse_manual_settlement,
    serialize_settlement,
)
from sqlalchemy import select
from app.db_models.reconciliation_job import ReconciliationJob
from app.db_models.reconciliation_summary import ReconciliationSummary
from app.services.reconciliation_query_service import (
    COMPLETED_JOB_STATUS,
    FRONTEND_STATUS_MAP,
    count_fund_request_results,
    count_matched_bank_transactions,
    count_unmatched_bank_transactions,
    count_va_transaction_results,
    get_db_session,
    get_reconciliation_history_job,
    get_reconciliation_job,
    list_fund_request_results,
    list_matched_bank_transactions,
    list_reconciliation_history,
    list_unmatched_bank_transactions,
    list_va_transaction_results,
    serialize_result,
    serialize_unmatched_bank,
)
from time import perf_counter, sleep
from app.services.failure_classifier import get_failure_message
from contextlib import asynccontextmanager
from app.database.connection import SessionLocal
import logging

APP_TITLE = "Bank Statement Reconciliation API"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Recover reconciliation jobs interrupted by server restart."""

    logger.info("Checking for interrupted reconciliation jobs...")

    try:
        with SessionLocal.begin() as session:
            interrupted_jobs = (
                session.query(ReconciliationJob)
                .filter(ReconciliationJob.status.in_(["PROCESSING", "SAVING"]))
                .all()
            )

            for job in interrupted_jobs:
                job.status = "FAILED"
                job.failure_code = "SERVER_RESTARTED"
                job.failure_message = "Job interrupted because the server restarted."

            logger.info(
                "Startup recovery completed. Interrupted jobs: %s",
                len(interrupted_jobs),
            )

    except Exception:
        logger.exception("Could not recover interrupted reconciliation jobs.")

    yield

    logger.info("Application shutting down.")


app = FastAPI(title=APP_TITLE, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "transaction_date": (
        "transaction date",
        "txn date",
        "date time",
        "date",
        "value date",
    ),
    "utr": ("utr", "utr number", "unique transaction reference", "rrn"),
    "reference_number": (
        "reference number",
        "ref no",
        "bank reference",
        "transaction reference",
    ),
    "narration": (
        "narration",
        "particulars",
        "description",
        "remarks",
        "transaction details",
    ),
    "debit_amount": ("debit amount", "debit", "withdrawal", "withdrawal amount"),
    "credit_amount": ("credit amount", "credit", "deposit", "deposit amount"),
    "amount": ("amount", "transaction amount"),
    "transaction_type": ("transaction type", "debit credit", "dr cr", "type"),
    "user": ("user", "customer name", "account holder"),
    "beneficiary_name": (
        "beneficiary name",
        "beneficiary",
        "payee name",
        "sender name",
    ),
    "account_number": ("account number", "account no", "a c no"),
    "virtual_account_number": ("va no", "virtual account number", "virtual account"),
}


def _safe_filename(filename: str | None) -> str:
    name = (filename or "uploaded_file").replace("\\", "/").split("/")[-1].strip()
    return name or "uploaded_file"


async def _save_upload(upload: UploadFile, folder: Path) -> Path:
    original_name = _safe_filename(upload.filename)
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type for '{original_name}'. Use CSV, XLSX or XLS.",
        )

    target = folder / f"{uuid.uuid4().hex}{suffix}"
    total = 0
    try:
        with target.open("wb") as output:
            while chunk := await upload.read(CHUNK_SIZE):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"'{original_name}' exceeds the 50 MB upload limit.",
                    )
                output.write(chunk)
    finally:
        await upload.close()
    if total == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"'{original_name}' is empty.")
    return target


def _json_sample(frame, limit: int = 5) -> list[dict[str, Any]]:
    sample = frame.head(limit).fillna("")
    return [
        {str(key): (None if value == "" else str(value)) for key, value in row.items()}
        for row in sample.to_dict(orient="records")
    ]


def _normalized_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _suggest_column_candidates(columns: list[str]) -> dict[str, list[str]]:
    normalized = {_normalized_header(column): column for column in columns}
    suggestions: dict[str, list[str]] = {}
    for field_name, aliases in FIELD_ALIASES.items():
        matches = [normalized[alias] for alias in aliases if alias in normalized]
        suggestions[field_name] = matches
    return suggestions


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": APP_TITLE}


@app.post("/api/inspect-reports")
async def inspect_reports(
    fund_request_file: UploadFile = File(...),
    va_transaction_file: UploadFile = File(...),
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="bank-recon-inspect-") as temp_dir:
        folder = Path(temp_dir)
        try:
            fund_path = await _save_upload(fund_request_file, folder)
            va_path = await _save_upload(va_transaction_file, folder)
            fund_frame, fund_transactions = FundRequestAdapter().read(str(fund_path))
            va_frame, va_transactions = VATransactionAdapter().read(str(va_path))
        except (FileReadError, AdapterError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        return {
            "fund_request": {
                "file_name": _safe_filename(fund_request_file.filename),
                "report_type": "fund_request",
                "inspection": inspect_report(fund_frame, fund_transactions),
            },
            "va_transaction": {
                "file_name": _safe_filename(va_transaction_file.filename),
                "report_type": "va_transaction",
                "inspection": inspect_report(va_frame, va_transactions),
            },
        }


@app.post("/api/inspect-bank-statement")
async def inspect_bank_statement(
    bank_statement_file: UploadFile = File(...),
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="bank-recon-bank-inspect-") as temp_dir:
        folder = Path(temp_dir)
        try:
            bank_path = await _save_upload(bank_statement_file, folder)
            frame = read_tabular_file(bank_path)
        except FileReadError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        columns = [str(column) for column in frame.columns]
        return {
            "file_name": _safe_filename(bank_statement_file.filename),
            "record_count": int(len(frame)),
            "columns": columns,
            "sample_rows": _json_sample(frame),
            "possible_column_matches": _suggest_column_candidates(columns),
            "notice": (
                "These are column-name suggestions only. Confirm mappings and transaction "
                "direction against the real bank statement before reconciliation."
            ),
        }


@app.post("/api/reconcile")
async def reconcile(
    background_tasks: BackgroundTasks,
    bank_statement_file: list[UploadFile] = File(...),
    fund_request_file: UploadFile = File(...),
    va_transaction_file: UploadFile = File(...),
    bank_mapping: str | None = Form(None),
) -> dict[str, Any]:
    """
    Reconcile Fund Request and VA Transaction reports against
    one or multiple bank statements.

    Automatic bank detection is used when bank_mapping is omitted.
    Manual mapping is retained for a single custom bank statement.
    """
    api_start = perf_counter()

    if not bank_statement_file:
        raise HTTPException(
            status_code=422,
            detail="Upload at least one bank statement.",
        )

    if len(bank_statement_file) > 10:
        raise HTTPException(
            status_code=422,
            detail="A maximum of 10 bank statements can be uploaded at once.",
        )

    # Manual mapping is supported for one custom statement only.
    mapping = None

    if bank_mapping and bank_mapping.strip():
        if len(bank_statement_file) != 1:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Manual bank_mapping supports only one bank statement. "
                    "For multiple statements, leave bank_mapping empty "
                    "and use automatic bank detection."
                ),
            )

        try:
            mapping_payload = json.loads(bank_mapping)

            if not isinstance(mapping_payload, dict):
                raise BankMappingError("bank_mapping must be a JSON object.")

            mapping = BankColumnMapping.from_dict(mapping_payload)

        except (
            json.JSONDecodeError,
            BankMappingError,
            TypeError,
        ) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid bank mapping: {exc}",
            ) from exc

    with tempfile.TemporaryDirectory(prefix="bank-recon-run-") as temp_dir:

        folder = Path(temp_dir)

        try:
            read_start = perf_counter()
            # Read source reports.
            fund_path = await _save_upload(fund_request_file, folder)

            va_path = await _save_upload(va_transaction_file, folder)

            _, fund_transactions = FundRequestAdapter().read(str(fund_path))

            _, va_transactions = VATransactionAdapter().read(str(va_path))

            # Normalize every bank statement separately.
            bank_transactions = []
            bank_files_processed = []

            for bank_file_id, upload in enumerate(bank_statement_file, start=1):
                bank_path = await _save_upload(upload, folder)

                filename = _safe_filename(upload.filename)

                if mapping is not None:
                    # Manual mapping for one custom statement.
                    bank_frame = read_tabular_file(bank_path)

                    adapter = BankStatementAdapter(
                        mapping,
                        bank_name="Manual mapping",
                        bank_format="Manual",
                        source_filename=filename,
                    )

                    transactions = adapter.normalize(bank_frame)

                    profile_name = "Manual mapping"
                    bank_format = "Manual"
                    metadata = {}

                else:
                    # Automatic bank detection.
                    read_result = read_bank_statement_file(bank_path)

                    profile, transactions = normalize_bank_statement(read_result)

                    profile_name = profile.bank_name
                    bank_format = profile.bank_format
                    metadata = read_result.metadata

                # Always preserve the original uploaded filename.
                # Always preserve the original uploaded filename and file ID.
                for transaction in transactions:
                    transaction.source_filename = filename
                    transaction.bank_file_id = bank_file_id

                # Keep transaction records from all uploaded files.
                bank_transactions.extend(transactions)

                bank_files_processed.append(
                    {
                        "bank_file_id": bank_file_id,
                        "file_name": filename,
                        "bank_name": profile_name,
                        "bank_format": bank_format,
                        "account_number": metadata.get("account_number"),
                        "account_holder": metadata.get("account_holder"),
                        "transaction_count": len(transactions),
                    }
                )

            read_time = perf_counter() - read_start
            print(f"[PERF] File upload + reading + normalization: {read_time:.2f}s")

        except (
            FileReadError,
            AdapterError,
            BankMappingError,
            ValueError,
        ) as exc:
            raise HTTPException(
                status_code=422,
                detail=str(exc),
            ) from exc

        reconcile_start = perf_counter()
        # Run reconciliation once using all bank transactions.
        output = reconcile_transactions(
            [
                *fund_transactions,
                *va_transactions,
            ],
            bank_transactions,
            MatchingConfig(),
        )

        reconcile_time = perf_counter() - reconcile_start
        print(f"[PERF] Matching engine: {reconcile_time:.2f}s")

        # Build frontend summary before scheduling persistence.
        summary_start = perf_counter()

        summary = build_frontend_summary(output)

        summary_time = perf_counter() - summary_start
        print(f"[PERF] Summary generation: {summary_time:.2f}s")

        # Create the job before starting background persistence.
        job_id = create_pending_job(
            fund_request_filename=_safe_filename(fund_request_file.filename),
            va_transaction_filename=_safe_filename(va_transaction_file.filename),
            bank_files=bank_files_processed,
            output=output,
        )
        # Schedule database persistence after returning the response.
        background_tasks.add_task(
            save_reconciliation_background,
            job_id,
            output,
        )

        print("[PERF] Database persistence: scheduled in background")

        # Total endpoint execution time.
        total_time = perf_counter() - api_start

        print(f"[PERF] Total API execution: {total_time:.2f}s")

        return {
            "job_id": str(job_id),
            "status": "PENDING",
            "summary": summary,
            "bank_files_processed": bank_files_processed,
            "notice": (
                "All uploaded bank statements were normalized separately "
                "and combined for reconciliation. Automatic bank detection "
                "was used unless manual mapping was supplied. Review "
                "unmatched transactions, duplicates and exceptions."
            ),
        }


@app.post("/api/reconciliation/{job_id}/retry")
def retry_reconciliation_persistence(
    job_id: uuid.UUID,
) -> dict[str, Any]:
    """Retry persistence using the original in-memory reconciliation output."""

    # Check whether the job exists.
    with SessionLocal() as session:
        job = session.get(ReconciliationJob, job_id)

        if job is None:
            raise HTTPException(
                status_code=404,
                detail="Reconciliation job not found.",
            )

        if job.status == "COMPLETED":
            return {
                "job_id": str(job_id),
                "status": "COMPLETED",
                "message": "This reconciliation job is already completed.",
            }

    # Retrieve the original reconciliation output.
    output = retry_outputs.get(job_id)

    if output is None:
        raise HTTPException(
            status_code=410,
            detail=(
                "Original reconciliation output is no longer available. "
                "The backend may have restarted. Please upload the original "
                "files and run reconciliation again."
            ),
        )

    try:
        save_reconciliation(job_id, output)

        return {
            "job_id": str(job_id),
            "status": "COMPLETED",
            "message": "Reconciliation data persisted successfully.",
            "summary": build_frontend_summary(output),
        }

    except Exception as exc:
        try:
            with SessionLocal.begin() as session:
                job = session.get(ReconciliationJob, job_id)

                if job is not None and job.status != "COMPLETED":
                    job.status = "FAILED"

        except Exception:
            pass

        raise HTTPException(
            status_code=503,
            detail=f"Database persistence retry failed: {str(exc)}",
        ) from exc


@app.get("/api/reconciliation/{job_id}/events")
def reconciliation_status_events(job_id: uuid.UUID):
    """
    Stream reconciliation job status changes using Server-Sent Events.
    The connection closes when the job reaches COMPLETED or FAILED.
    """

    def event_stream():
        last_status = None

        while True:
            try:
                # Use a fresh database session for every status check.
                with SessionLocal() as session:
                    job = session.get(ReconciliationJob, job_id)

                    if job is None:
                        payload = {
                            "job_id": str(job_id),
                            "status": "NOT_FOUND",
                            "message": "Reconciliation job not found.",
                        }

                        yield ("event: status\n" f"data: {json.dumps(payload)}\n\n")
                        return

                    # Copy values before closing the session.
                    status = job.status
                    failure_code = job.failure_code
                    completed_at = (
                        job.completed_at.isoformat() if job.completed_at else None
                    )

            except Exception:
                logger.exception(
                    "Failed to retrieve reconciliation status. Job ID: %s",
                    job_id,
                )

                payload = {
                    "job_id": str(job_id),
                    "status": "STATUS_CHECK_ERROR",
                    "message": (
                        "We couldn't retrieve your reconciliation status because "
                        "the database is temporarily unavailable. We'll try again."
                    ),
                }

                yield ("event: status\n" f"data: {json.dumps(payload)}\n\n")

                sleep(2)
                continue

            # Send only when the status changes.
            if status != last_status:
                if status == "COMPLETED":
                    message = (
                        "Reconciliation completed successfully. "
                        "Your results are ready."
                    )
                elif status == "FAILED":
                    message = get_failure_message(failure_code)
                else:
                    message = "Reconciliation is being processed..."

                payload = {
                    "job_id": str(job_id),
                    "status": status,
                    "message": message,
                    "failure_code": (failure_code if status == "FAILED" else None),
                    "completed_at": completed_at,
                }

                yield ("event: status\n" f"data: {json.dumps(payload)}\n\n")

                last_status = status

            # Close after a terminal status.
            if status in {"COMPLETED", "FAILED"}:
                return

            sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/reconciliation/history")
def get_reconciliation_history(
    status: str | None = Query(None, max_length=30),
    page: PageParam = DEFAULT_PAGE,
    limit: LimitParam = DEFAULT_LIMIT,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return persisted reconciliation jobs, newest first.

    The optional status filter is matched against the persisted job status.
    A blank value is treated as omitted, matching the shared pagination
    parameter convention.  No reconciliation is executed.
    """

    total, items = list_reconciliation_history(
        session,
        page=page,
        limit=limit,
        status=status,
    )
    total_pages = (total + limit - 1) // limit

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_previous": page > 1,
        "items": items,
    }


@app.get("/api/reconciliation/history/{job_id}")
def get_reconciliation_history_detail(
    job_id: uuid.UUID,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return one persisted reconciliation job without rerunning it."""

    item = get_reconciliation_history_job(session, job_id)

    if item is None:
        raise HTTPException(
            status_code=404,
            detail=f"Reconciliation job '{job_id}' was not found.",
        )

    return item


@app.get("/api/reconciliation/{job_id}/summary")
async def get_reconciliation_summary(
    job_id: uuid.UUID,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Retrieve the saved summary for a reconciliation job.
    """

    job = session.get(ReconciliationJob, job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Reconciliation job not found.",
        )

    summary_record = session.scalar(
        select(ReconciliationSummary).where(ReconciliationSummary.job_id == job_id)
    )

    if summary_record is None:
        raise HTTPException(
            status_code=404,
            detail="Summary not found for this reconciliation job.",
        )

    return {
        "job_id": str(job.job_id),
        "status": job.status,
        "created_at": (job.created_at.isoformat() if job.created_at else None),
        "completed_at": (job.completed_at.isoformat() if job.completed_at else None),
        "summary": summary_record.summary_data,
    }


def _raise_service_error(exc: ServiceError) -> None:
    """Translate an expected service failure into an HTTP response."""

    raise HTTPException(status_code=exc.status_code, detail=str(exc))


@app.post("/api/companies", status_code=201)
def create_company_endpoint(
    payload: CompanyCreate,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Create a reusable company record."""

    try:
        company = create_company(
            session,
            name=payload.name,
            alias=payload.alias,
        )
    except ServiceError as exc:
        _raise_service_error(exc)

    return serialize_company(company)


@app.get("/api/companies")
def list_companies_endpoint(
    search: str | None = Query(None, max_length=255),
    page: PageParam = DEFAULT_PAGE,
    limit: LimitParam = DEFAULT_LIMIT,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return companies sorted alphabetically by name."""

    total, items = list_companies(
        session,
        page=page,
        limit=limit,
        search=search,
    )
    total_pages = (total + limit - 1) // limit

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_previous": page > 1,
        "items": [serialize_company(company) for company in items],
    }


@app.get("/api/companies/{company_id}")
def get_company_endpoint(
    company_id: uuid.UUID,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return one company by id."""

    try:
        company = get_company(session, company_id)
    except ServiceError as exc:
        _raise_service_error(exc)

    return serialize_company(company)


@app.post(
    "/api/reconciliation/{job_id}/manual-settlements",
    status_code=201,
)
def create_manual_settlement_endpoint(
    job_id: uuid.UUID,
    payload: ManualSettlementCreate,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Settle one source record against one bank row."""

    try:
        settlement = create_manual_settlement(session, job_id, payload)
    except ServiceError as exc:
        _raise_service_error(exc)

    return serialize_settlement(session, settlement)


@app.get("/api/reconciliation/{job_id}/manual-settlements")
def list_manual_settlements_endpoint(
    job_id: uuid.UUID,
    page: PageParam = DEFAULT_PAGE,
    limit: LimitParam = DEFAULT_LIMIT,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return manual settlements for one job, newest first."""

    try:
        total, items = list_manual_settlements(
            session,
            job_id,
            page=page,
            limit=limit,
        )
    except ServiceError as exc:
        _raise_service_error(exc)

    total_pages = (total + limit - 1) // limit

    return {
        "job_id": str(job_id),
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_previous": page > 1,
        "items": [serialize_settlement(session, item) for item in items],
    }


@app.post(
    "/api/reconciliation/manual-settlements/{settlement_id}/reverse",
)
def reverse_manual_settlement_endpoint(
    settlement_id: uuid.UUID,
    payload: ManualSettlementReversal | None = None,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Reverse an active manual settlement and restore both records."""

    reason = payload.reason if payload is not None else None

    try:
        settlement = reverse_manual_settlement(
            session,
            settlement_id,
            reason=reason,
        )
    except ServiceError as exc:
        _raise_service_error(exc)

    return serialize_settlement(session, settlement)


FundRequestStatus = Literal["MATCHED", "NOT_FOUND"]


@app.get("/api/reconciliation/{job_id}/fund-requests")
def get_fund_request_results(
    job_id: uuid.UUID,
    status: FundRequestStatus = Query(...),
    page: PageParam = DEFAULT_PAGE,
    limit: LimitParam = DEFAULT_LIMIT,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return stored Fund Request results for a completed reconciliation job.

    ``MATCHED`` maps to the engine's individually confirmed ``MATCHED`` status.
    ``NOT_FOUND`` maps directly to the engine's ``NOT_FOUND`` status.  All other
    engine statuses are intentionally excluded.

    Results are paginated at the database level after applying the job and
    status filters.
    """

    job = get_reconciliation_job(session, job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Reconciliation job '{job_id}' was not found.",
        )

    if (job.status or "").strip().upper() != COMPLETED_JOB_STATUS:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Reconciliation job '{job_id}' is not completed "
                f"(status: {job.status})."
            ),
        )

    internal_status = FRONTEND_STATUS_MAP[status]
    total = count_fund_request_results(session, job_id, internal_status)
    offset = (page - 1) * limit
    rows = list_fund_request_results(
        session,
        job_id,
        internal_status,
        offset=offset,
        limit=limit,
    )
    total_pages = (total + limit - 1) // limit

    return {
        "job_id": str(job.job_id),
        "status": status,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_previous": page > 1,
        "items": [serialize_result(row) for row in rows],
    }


VATransactionStatus = Literal["MATCHED", "NOT_FOUND"]


@app.get("/api/reconciliation/{job_id}/va-transactions")
def get_va_transaction_results(
    job_id: uuid.UUID,
    status: VATransactionStatus = Query(...),
    page: PageParam = DEFAULT_PAGE,
    limit: LimitParam = DEFAULT_LIMIT,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return stored VA Transaction results for a completed reconciliation job.

    ``MATCHED`` maps to the engine's confirmed ``MATCHED`` status and
    ``NOT_FOUND`` maps directly to the engine's ``NOT_FOUND`` status.  All other
    engine statuses are intentionally excluded.

    Results are paginated at the database level after applying the job, source
    type and status filters.
    """

    job = get_reconciliation_job(session, job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Reconciliation job '{job_id}' was not found.",
        )

    if (job.status or "").strip().upper() != COMPLETED_JOB_STATUS:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Reconciliation job '{job_id}' is not completed "
                f"(status: {job.status})."
            ),
        )

    internal_status = FRONTEND_STATUS_MAP[status]
    total = count_va_transaction_results(session, job_id, internal_status)
    offset = (page - 1) * limit
    rows = list_va_transaction_results(
        session,
        job_id,
        internal_status,
        offset=offset,
        limit=limit,
    )
    total_pages = (total + limit - 1) // limit

    return {
        "job_id": str(job.job_id),
        "status": status,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_previous": page > 1,
        "items": [serialize_result(row) for row in rows],
    }


BankTransactionStatus = Literal["MATCHED", "NOT_FOUND"]


@app.get("/api/reconciliation/{job_id}/bank-transactions")
def get_bank_transaction_results(
    job_id: uuid.UUID,
    status: BankTransactionStatus = Query(...),
    page: PageParam = DEFAULT_PAGE,
    limit: LimitParam = DEFAULT_LIMIT,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return stored Bank Transaction results for a completed job.

    ``MATCHED`` returns confirmed bank rows that were linked to a Fund Request
    or VA transaction by the matching engine.  ``NOT_FOUND`` returns bank rows
    that were persisted in the unmatched bank table, meaning no Fund Request or
    VA transaction was allocated to them.

    Job and status filters are applied before database-level pagination.
    """

    job = get_reconciliation_job(session, job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Reconciliation job '{job_id}' was not found.",
        )

    if (job.status or "").strip().upper() != COMPLETED_JOB_STATUS:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Reconciliation job '{job_id}' is not completed "
                f"(status: {job.status})."
            ),
        )

    offset = (page - 1) * limit

    if status == "MATCHED":
        total = count_matched_bank_transactions(session, job_id)
        rows = list_matched_bank_transactions(
            session,
            job_id,
            offset=offset,
            limit=limit,
        )
        items = [serialize_result(row) for row in rows]
    else:
        total = count_unmatched_bank_transactions(session, job_id)
        rows = list_unmatched_bank_transactions(
            session,
            job_id,
            offset=offset,
            limit=limit,
        )
        items = [serialize_unmatched_bank(row) for row in rows]

    total_pages = (total + limit - 1) // limit

    return {
        "job_id": str(job.job_id),
        "status": status,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_previous": page > 1,
        "items": items,
    }


@app.delete(
    "/api/reconciliation/{job_id}/bank-transactions/{bank_transaction_id}"
)
def delete_bank_transaction_result(
    job_id: uuid.UUID,
    bank_transaction_id: int = PathParam(..., ge=1),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Permanently delete one unmatched bank statement row.

    Only the persisted ``UnmatchedBankTransaction`` row is removed.  The
    original uploaded bank statement file and every reconciliation result are
    left untouched.  Settled or linked rows are rejected.
    """

    try:
        delete_unmatched_bank_transaction(
            session,
            job_id,
            bank_transaction_id,
        )
    except ServiceError as exc:
        _raise_service_error(exc)

    return {
        "job_id": str(job_id),
        "bank_transaction_id": bank_transaction_id,
        "deleted": True,
    }


CSV_MEDIA_TYPE = "text/csv; charset=utf-8"


def _require_completed_job(session: Session, job_id: uuid.UUID):
    """Validate the job used by an export without changing existing routes."""

    job = get_reconciliation_job(session, job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Reconciliation job '{job_id}' was not found.",
        )

    if (job.status or "").strip().upper() != COMPLETED_JOB_STATUS:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Reconciliation job '{job_id}' is not completed "
                f"(status: {job.status})."
            ),
        )

    return job


def _csv_download(content, filename: str) -> StreamingResponse:
    """Return a streamed CSV attachment with the shared download headers."""

    return StreamingResponse(
        content,
        media_type=CSV_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.get("/api/reconciliation/{job_id}/fund-requests/export")
def export_fund_request_results(
    job_id: uuid.UUID,
    status: FundRequestStatus = Query(...),
    session: Session = Depends(get_db_session),
) -> StreamingResponse:
    """Download every persisted Fund Request result for one status."""

    _require_completed_job(session, job_id)

    return _csv_download(
        fund_request_csv(
            session.get_bind(),
            job_id,
            FRONTEND_STATUS_MAP[status],
        ),
        build_export_filename("fund_requests", status, job_id),
    )


@app.get("/api/reconciliation/{job_id}/va-transactions/export")
def export_va_transaction_results(
    job_id: uuid.UUID,
    status: VATransactionStatus = Query(...),
    session: Session = Depends(get_db_session),
) -> StreamingResponse:
    """Download every persisted VA Transaction result for one status."""

    _require_completed_job(session, job_id)

    return _csv_download(
        va_transaction_csv(
            session.get_bind(),
            job_id,
            FRONTEND_STATUS_MAP[status],
        ),
        build_export_filename("va_transactions", status, job_id),
    )


@app.get("/api/reconciliation/{job_id}/bank-transactions/export")
def export_bank_transaction_results(
    job_id: uuid.UUID,
    status: BankTransactionStatus = Query(...),
    session: Session = Depends(get_db_session),
) -> StreamingResponse:
    """Download every persisted bank transaction result for one status."""

    _require_completed_job(session, job_id)

    bind = session.get_bind()

    if status == "MATCHED":
        content = bank_matched_csv(bind, job_id)
    else:
        content = bank_unmatched_csv(bind, job_id)

    return _csv_download(
        content,
        build_export_filename("bank_transactions", status, job_id),
    )
